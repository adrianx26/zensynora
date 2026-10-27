"""Knowledge base usage log (2026-10-27): which agent used which notes, and why.

One record per knowledge access by an agent:

* ``context`` — the automatic search before an answer (``auto-context``);
* ``tool``    — a knowledge tool (``search_knowledge``, ``read_knowledge``,
  ``list_facts`` …);
* ``write``   — an agent write (``write_to_knowledge``, ``extraction``,
  ``session_summary``, ``assimilate`` / ``import_knowledge``).

Each record keeps the time, knowledge base, agent, channel / job, the query or
permalink, the notes returned (at most :data:`MAX_NOTES`), the number of
results, the latency, the search mode, and **what it was for**: the user
message, job task or delegated task of the turn (first :data:`REASON_CHARS`
characters, secrets redacted with the ingestion redaction helpers).

Storage: a small separate SQLite database ``~/.myclaw/knowledge_usage.db``
(WAL, ``busy_timeout``), pruned at most once a day (``knowledge.usage_retention_days``,
default 90, and ``knowledge.usage_max_rows``, default 200 000). It also keeps
one snapshot of the main counts per base and day (growth chart).

Recording never slows or breaks a turn: :func:`record` only appends a dict to
a bounded in-memory queue (a few microseconds); one daemon thread — started
on the first record, never while the log is off — redacts and inserts in
batches. A locked, unwritable or broken database only drops records.

On by default (``knowledge.usage_log``): it is local, cheap and makes no model
calls; it changes nothing an agent sees. Off, or ``knowledge.enabled`` off →
nothing is recorded and no thread is started.
"""

from __future__ import annotations

import atexit
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

MAX_NOTES = 20            # permalinks kept per record
REASON_CHARS = 200        # "for what" text kept per record
QUERY_CHARS = 300
QUEUE_MAX = 10_000        # records waiting for the writer; older ones are dropped when full
BATCH = 500
PRUNE_EVERY = 86_400.0    # seconds between prunes

_OPTIONS: Dict[str, Any] = {"usage_log": True, "kb_enabled": True,
                            "retention_days": 90, "max_rows": 200_000}

# The turn an access belongs to: {"agent", "reason", "source", "via"} (set by Agent.think).
TURN: ContextVar[Optional[Dict[str, Any]]] = ContextVar("kb_usage_turn", default=None)

KINDS = ("context", "tool", "write")
WRITE_ACTIONS = {"write_to_knowledge", "extraction", "session_summary", "assimilate",
                 "import_knowledge", "resolve_knowledge_conflict"}


# ── options ─────────────────────────────────────────────────────────────────

def set_options(usage_log: Optional[bool] = None, kb_enabled: Optional[bool] = None,
                retention_days: Optional[int] = None, max_rows: Optional[int] = None) -> None:
    """Apply ``knowledge.usage_log`` / ``usage_retention_days`` / ``usage_max_rows``."""
    if usage_log is not None:
        _OPTIONS["usage_log"] = bool(usage_log)
    if kb_enabled is not None:
        _OPTIONS["kb_enabled"] = bool(kb_enabled)
    if retention_days is not None:
        _OPTIONS["retention_days"] = max(1, int(retention_days))
    if max_rows is not None:
        _OPTIONS["max_rows"] = max(1000, int(max_rows))


def enabled() -> bool:
    return bool(_OPTIONS["usage_log"]) and bool(_OPTIONS["kb_enabled"])


def options() -> Dict[str, Any]:
    return dict(_OPTIONS)


def db_path() -> Path:
    """``~/.myclaw/knowledge_usage.db`` of the current HOME (tests change HOME)."""
    return Path.home() / ".myclaw" / "knowledge_usage.db"


# ── turn context ────────────────────────────────────────────────────────────

def begin_turn(agent: str, message: str, depth: int = 0) -> Any:
    """Remember the agent and its message for the accesses of this turn.

    Returns a token for :func:`end_turn`. Never raises (returns None then).
    """
    try:
        parent = TURN.get()
        job = _job_id()
        if parent is not None:
            source = "delegation"
        elif job:
            source = "job"
        elif depth > 0 or _delegation_depth() > 0:
            source = "delegation"
        else:
            source = "chat"
        return TURN.set({"agent": str(agent or ""), "reason": str(message or "")[:4000],
                         "source": source, "via": parent.get("agent") if parent else None})
    except Exception:
        return None


def end_turn(token: Any) -> None:
    if token is None:
        return
    try:
        TURN.reset(token)
    except (ValueError, RuntimeError):  # a generator closed from another context
        pass


def _job_id() -> Optional[str]:
    mod = sys.modules.get("myclaw.agent")
    if mod is None:
        return None
    try:
        scope = mod.MEMORY_SCOPE.get()
    except Exception:
        return None
    if scope.startswith("job-"):
        return scope[4:]
    if scope.startswith("bench-"):
        return scope[6:].split("-", 1)[0]
    return None


def _delegation_depth() -> int:
    mod = sys.modules.get("myclaw.agent_team")
    try:
        return int(mod.DELEGATION_DEPTH.get()) if mod is not None else 0
    except Exception:
        return 0


def _channel_agent() -> tuple:
    mod = sys.modules.get("myclaw.events")
    if mod is None:
        return None, None
    try:
        return mod.CURRENT_CHANNEL.get(), mod.CURRENT_AGENT.get()
    except Exception:
        return None, None


# ── recording (cheap, on the turn's path) ───────────────────────────────────

_queue: Deque[Dict[str, Any]] = deque(maxlen=QUEUE_MAX)
_wake = threading.Event()
_idle = threading.Event()
_idle.set()
_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
_warned: set = set()


def record(kind: str, action: str, *, base: str = "default", query: Optional[str] = None,
           notes: Iterable[str] = (), results: Optional[int] = None,
           latency_ms: Optional[float] = None, mode: Optional[str] = None, ok: bool = True,
           agent: Optional[str] = None, reason: Optional[str] = None) -> bool:
    """Queue one usage record; returns False when nothing was queued. Never raises."""
    if not enabled():
        return False
    try:
        turn = TURN.get() or {}
        channel, current_agent = _channel_agent()
        uniq: List[str] = []
        for p in notes or ():
            p = str(p or "").strip()
            if p and p not in uniq:
                uniq.append(p)
                if len(uniq) >= MAX_NOTES:
                    break
        from .scope import kb_user  # internal ids ("webui", "__delegate__") share "default"

        _queue.append({
            "ts": time.time(), "base": kb_user(str(base or "default")), "kind": kind, "action": action,
            "agent": str(agent or turn.get("agent") or current_agent or ""),
            "channel": channel, "job": _job_id(), "source": turn.get("source") or ("job" if _job_id() else None),
            "via": turn.get("via"),
            "query": None if query is None else str(query)[:2000],
            "notes": uniq, "results": results,
            "latency_ms": None if latency_ms is None else round(float(latency_ms), 2),
            "mode": mode, "ok": 1 if ok else 0,
            "reason": str(reason if reason is not None else turn.get("reason") or "")[:2000],
            "db": str(db_path()),
        })
        _ensure_thread()
        _idle.clear()
        _wake.set()
        return True
    except Exception as e:  # recording must never break the caller
        logger.debug("knowledge usage not recorded: %s", e)
        return False


def _ensure_thread() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_writer, name="kb-usage-log", daemon=True)
        _thread.start()


def flush(timeout: float = 2.0) -> bool:
    """Wait until the queued records are written (tests, the API). True when done."""
    if _thread is None or not _thread.is_alive():
        return not _queue
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _queue and _idle.is_set():
            return True
        _wake.set()
        _idle.wait(0.02)
    return not _queue and _idle.is_set()


atexit.register(lambda: flush(1.0))


# ── the writer thread ───────────────────────────────────────────────────────

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS kb_usage (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        day TEXT NOT NULL,
        base TEXT NOT NULL,
        agent TEXT NOT NULL DEFAULT '',
        channel TEXT,
        job TEXT,
        source TEXT,
        via TEXT,
        kind TEXT NOT NULL,
        action TEXT NOT NULL,
        query TEXT,
        notes TEXT NOT NULL DEFAULT '[]',
        results INTEGER,
        latency_ms REAL,
        mode TEXT,
        ok INTEGER NOT NULL DEFAULT 1,
        reason TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kb_usage_base_ts ON kb_usage(base, ts)",
    "CREATE INDEX IF NOT EXISTS idx_kb_usage_ts ON kb_usage(ts)",
    """CREATE TABLE IF NOT EXISTS kb_usage_notes (
        usage_id INTEGER NOT NULL,
        ts REAL NOT NULL,
        base TEXT NOT NULL,
        permalink TEXT NOT NULL,
        kind TEXT NOT NULL,
        agent TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kb_usage_notes_note ON kb_usage_notes(base, permalink, kind, ts, agent)",
    "CREATE INDEX IF NOT EXISTS idx_kb_usage_notes_ts ON kb_usage_notes(ts)",
    """CREATE TABLE IF NOT EXISTS kb_snapshots (
        base TEXT NOT NULL,
        day TEXT NOT NULL,
        notes INTEGER NOT NULL DEFAULT 0,
        facts INTEGER NOT NULL DEFAULT 0,
        links INTEGER NOT NULL DEFAULT 0,
        tags INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (base, day)
    )""",
    "CREATE TABLE IF NOT EXISTS kb_usage_meta (key TEXT PRIMARY KEY, value TEXT)",
)
_schema_done: set = set()


def connect(path: Optional[Path] = None, timeout: float = 2.0) -> sqlite3.Connection:
    """Open (and create) the usage database. Raises sqlite3.Error / OSError."""
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    if str(p) not in _schema_done:
        try:
            con.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass
        for sql in SCHEMA:
            con.execute(sql)
        con.commit()
        _schema_done.add(str(p))
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def _warn_once(key: str, text: str, *args: Any) -> None:
    if key in _warned:
        logger.debug(text, *args)
        return
    _warned.add(key)
    logger.warning(text, *args)


def _writer() -> None:
    while True:
        _wake.wait(5.0)
        _wake.clear()
        while _queue:
            batch: List[Dict[str, Any]] = []
            while _queue and len(batch) < BATCH:
                try:
                    batch.append(_queue.popleft())
                except IndexError:
                    break
            if batch:
                _write_batch(batch)
        _idle.set()


def _write_batch(batch: Sequence[Dict[str, Any]]) -> None:
    by_db: Dict[str, List[Dict[str, Any]]] = {}
    for r in batch:
        by_db.setdefault(r.pop("db"), []).append(r)
    for path, rows in by_db.items():
        try:
            con = connect(Path(path), timeout=2.0)
        except (sqlite3.Error, OSError) as e:
            _warn_once(f"open:{path}", "Knowledge usage log: cannot open %s (%s); records dropped", path, e)
            continue
        try:
            with con:
                for r in rows:
                    _insert(con, r)
            _maybe_prune(con)
        except sqlite3.Error as e:
            _warn_once(f"write:{path}", "Knowledge usage log: cannot write %s (%s); records dropped", path, e)
        except Exception as e:  # never let the thread die
            logger.debug("knowledge usage batch failed: %s", e)
        finally:
            con.close()


def _insert(con: sqlite3.Connection, r: Dict[str, Any]) -> None:
    query = r.get("query")
    if query is not None:
        query = _clean(query, QUERY_CHARS, url=r["action"] in ("assimilate", "import_knowledge"))
    reason = _clean(r.get("reason") or "", REASON_CHARS)
    day = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
    cur = con.execute(
        "INSERT INTO kb_usage (ts, day, base, agent, channel, job, source, via, kind, action, query, "
        "notes, results, latency_ms, mode, ok, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r["ts"], day, r["base"], r["agent"], r.get("channel"), r.get("job"), r.get("source"),
         r.get("via"), r["kind"], r["action"], query, json.dumps(r.get("notes") or []),
         r.get("results"), r.get("latency_ms"), r.get("mode"), r.get("ok", 1), reason))
    uid = cur.lastrowid
    if r.get("notes"):
        con.executemany(
            "INSERT INTO kb_usage_notes (usage_id, ts, base, permalink, kind, agent) VALUES (?,?,?,?,?,?)",
            [(uid, r["ts"], r["base"], p, r["kind"], r["agent"]) for p in r["notes"]])


_FALLBACK_SECRET = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")


def redact(text: str) -> str:
    """Secrets hidden with the ingestion / doctor patterns ([redacted])."""
    if not text:
        return text
    try:
        from .ingest.safety import redact as _redact

        return _redact(text)
    except Exception as e:  # the module is part of ZenSynora; fall back to a few patterns
        logger.debug("usage log redaction fallback: %s", e)
        return _FALLBACK_SECRET.sub("[redacted]", text)


def _clean(text: str, limit: int, url: bool = False) -> str:
    text = " ".join(str(text).split())[:2000]
    if url:
        try:
            from .ingest.safety import clean_source

            text = clean_source(text)
        except Exception:
            pass
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _maybe_prune(con: sqlite3.Connection, force: bool = False) -> int:
    now = time.time()
    row = con.execute("SELECT value FROM kb_usage_meta WHERE key = 'last_prune'").fetchone()
    if not force and row is not None:
        try:
            if now - float(row[0]) < PRUNE_EVERY:
                return 0
        except ValueError:
            pass
    return prune(con, now)


def prune(con: sqlite3.Connection, now: Optional[float] = None) -> int:
    """Delete records older than the retention or beyond the row limit. Returns rows deleted."""
    now = now or time.time()
    cutoff = now - float(_OPTIONS["retention_days"]) * 86_400
    deleted = 0
    with con:
        deleted += con.execute("DELETE FROM kb_usage WHERE ts < ?", (cutoff,)).rowcount or 0
        con.execute("DELETE FROM kb_usage_notes WHERE ts < ?", (cutoff,))
        row = con.execute("SELECT id FROM kb_usage ORDER BY id DESC LIMIT 1 OFFSET ?",
                          (int(_OPTIONS["max_rows"]),)).fetchone()
        if row is not None:
            deleted += con.execute("DELETE FROM kb_usage WHERE id <= ?", (row[0],)).rowcount or 0
            con.execute("DELETE FROM kb_usage_notes WHERE usage_id <= ?", (row[0],))
        con.execute("DELETE FROM kb_snapshots WHERE day < ?",
                    (time.strftime("%Y-%m-%d", time.localtime(now - 3 * 365 * 86_400)),))
        con.execute("INSERT OR REPLACE INTO kb_usage_meta (key, value) VALUES ('last_prune', ?)",
                    (str(now),))
    return deleted


# ── helpers for the hooks ───────────────────────────────────────────────────

_MEMORY_LINK = re.compile(r"memory://([\w./-]+?)(?=[)\s'\"`,]|$)", re.MULTILINE)
_ERROR_STARTS = ("Error ", "Error:", "Could not", "Note not found", "Not started", "Distillation failed",
                 "limit must", "Unknown", "Invalid")
_QUERY_ARGS = ("query", "permalink", "id", "source", "path", "conflict_id")


def _out_permalinks(out: str) -> List[str]:
    return list(dict.fromkeys(m.rstrip(".") for m in _MEMORY_LINK.findall(out or "")))


def summarise_tool(tool: str, args: Dict[str, Any], out: Any) -> Dict[str, Any]:
    """(query, notes, results, ok, mode) of a knowledge tool call from its text answer."""
    text = out if isinstance(out, str) else str(out or "")
    query = next((str(args[k]) for k in _QUERY_ARGS if args.get(k) not in (None, "")), None)
    ok = not text.lstrip().startswith(_ERROR_STARTS)
    notes: List[str] = []
    results: Optional[int] = None
    mode: Optional[str] = None
    if tool == "search_knowledge":
        if text.startswith("🔍 No results"):
            results = 0
        else:
            notes = _out_permalinks(text)
            results = len(re.findall(r"^\d+\. \*\*", text, re.MULTILINE)) or len(notes)
        mode = ("semantic" if "(search: meaning)" in text else
                "hybrid" if "(search: words + meaning)" in text else "fts")
    elif tool == "read_knowledge":
        m = re.search(r"^Permalink: (\S+)", text, re.MULTILINE)
        notes = [m.group(1)] if (m and ok) else []
        results = len(notes)
    elif tool in ("list_facts", "list_scenarios"):
        results = len(re.findall(r"^- [fs]\d+", text, re.MULTILINE))
        notes = _out_permalinks(text)
    elif tool == "list_knowledge":
        notes = _out_permalinks(text)
        results = len(notes)
    else:
        notes = _out_permalinks(text)
        if not notes and ok and tool in ("get_knowledge_context", "get_related_knowledge") and query \
                and text.strip() and "not found" not in text.lower()[:200]:
            notes = [query]
        results = len(notes) if notes else (1 if ok and text.strip() else 0)
    if not ok:
        results = 0
        if tool not in WRITE_ACTIONS:
            notes = []
    return {"query": query, "notes": notes, "results": results, "ok": ok, "mode": mode}


def record_tool(tool: str, args: Dict[str, Any], out: Any, latency_ms: float, base: str) -> None:
    """Hook for the knowledge tools (tools/kb.py, tools/ingest.py). Never raises."""
    if not enabled():
        return
    try:
        s = summarise_tool(tool, args, out)
        record("write" if tool in WRITE_ACTIONS else "tool", tool, base=base, query=s["query"],
               notes=s["notes"], results=s["results"], latency_ms=latency_ms, mode=s["mode"], ok=s["ok"])
    except Exception as e:
        logger.debug("knowledge tool usage not recorded: %s", e)


# ── reading (API / Web UI) ──────────────────────────────────────────────────

SEARCH_ACTIONS = ("auto-context", "search_knowledge")


def _open_ro() -> Optional[sqlite3.Connection]:
    """The usage database for reading, or None when there is none yet."""
    p = db_path()
    if not p.is_file():
        return None
    return connect(p, timeout=2.0)


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["notes"] = json.loads(d.get("notes") or "[]")
    except ValueError:
        d["notes"] = []
    d["ok"] = bool(d.get("ok", 1))
    return d


def query_log(base: Optional[str] = None, agent: str = "", kind: str = "", q: str = "",
              since: Optional[float] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Log rows, newest first. ``kind`` matches the kind (context/tool/write) or the action."""
    con = _open_ro()
    if con is None:
        return {"rows": [], "total": 0}
    where: List[str] = ["1=1"]
    args: List[Any] = []
    if base:
        where.append("base = ?")
        args.append(base)
    if agent:
        where.append("agent = ?")
        args.append(agent)
    if kind:
        where.append("(kind = ? OR action = ?)")
        args += [kind, kind]
    if q:
        like = f"%{q.lower()}%"
        where.append("(lower(coalesce(query,'')) LIKE ? OR lower(coalesce(reason,'')) LIKE ? "
                     "OR lower(notes) LIKE ?)")
        args += [like, like, like]
    if since:
        where.append("ts >= ?")
        args.append(float(since))
    sql = " AND ".join(where)
    try:
        total = con.execute(f"SELECT COUNT(*) FROM kb_usage WHERE {sql}", args).fetchone()[0]
        rows = [_row(r) for r in con.execute(
            f"SELECT * FROM kb_usage WHERE {sql} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            args + [max(1, min(int(limit), 500)), max(0, int(offset))])]
        return {"rows": rows, "total": total}
    finally:
        con.close()


def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))], 2)


def _days(n: int, now: float) -> List[str]:
    return [time.strftime("%Y-%m-%d", time.localtime(now - i * 86_400)) for i in range(n - 1, -1, -1)]


def stats(base: str = "default", days: int = 30, titles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Aggregates of the usage log of one knowledge base over the last ``days`` days.

    ``titles`` (permalink → title of every note, from the index) adds titles and
    the notes that were never used.
    """
    days = max(1, min(int(days or 30), 365))
    now = time.time()
    since = now - days * 86_400
    day_list = _days(days, now)
    out: Dict[str, Any] = {
        "base": base, "days": days, "enabled": enabled(), "options": options(),
        "accesses_7d": 0, "accesses_30d": 0, "writes_7d": 0, "writes_30d": 0,
        "per_day": [{"day": d, "context": 0, "tool": 0, "write": 0} for d in day_list],
        "per_agent": [], "by_action": [], "top_notes": [], "top_queries": [], "empty_queries": [],
        "searches": 0, "hits": 0, "hit_rate": None,
        "latency": {"avg": None, "p95": None, "n": 0}, "latency_per_day": [],
        "notes_used": 0, "notes_total": len(titles) if titles is not None else None,
        "never_used": None, "never_used_sample": [], "snapshots": snapshots(base),
    }
    con = _open_ro()
    if con is None:
        if titles is not None:
            out["never_used"] = len(titles)
            out["never_used_sample"] = _sample(titles, set())
        return out
    con.row_factory = None  # plain tuples: much faster on large logs
    try:
        _aggregate(con, out, base, now, since, days, day_list, titles)
        return out
    finally:
        con.close()


def _aggregate(con: sqlite3.Connection, out: Dict[str, Any], base: str, now: float, since: float,
               days: int, day_list: List[str], titles: Optional[Dict[str, str]]) -> None:
    """Fill ``out`` with one grouped scan per table (2026-10-27: < 1 s on 50 000 records)."""
    d7, d30 = now - 7 * 86_400, now - 30 * 86_400
    wide = min(since, d30)
    search = ",".join(f"'{a}'" for a in SEARCH_ACTIONS)
    per_day = {d["day"]: d for d in out["per_day"]}
    by_agent: Dict[str, Dict[str, Any]] = {}
    by_action: Dict[tuple, int] = {}
    for day, agent, kind, action, in7, in30, inwin, n, searches, hits in con.execute(
            f"SELECT day, agent, kind, action, ts >= ?, ts >= ?, ts >= ?, COUNT(*), "
            f"SUM(action IN ({search}) AND ok = 1), SUM(action IN ({search}) AND ok = 1 AND results >= 1) "
            f"FROM kb_usage WHERE base = ? AND ts >= ? GROUP BY 1, 2, 3, 4, 5, 6, 7",
            (d7, d30, since, base, wide)):
        read = kind in ("context", "tool")
        if in7:
            out["accesses_7d" if read else "writes_7d"] += n
        if in30:
            out["accesses_30d" if read else "writes_30d"] += n
        if not inwin:
            continue
        if day in per_day and kind in KINDS:
            per_day[day][kind] += n
        a = by_agent.setdefault(agent or "", {"agent": agent or "", "context": 0, "tool": 0, "write": 0, "total": 0})
        if kind in KINDS:
            a[kind] += n
        a["total"] += n
        by_action[(action, kind)] = by_action.get((action, kind), 0) + n
        out["searches"] += searches or 0
        out["hits"] += hits or 0
    out["per_agent"] = sorted(by_agent.values(), key=lambda a: -a["total"])[:15]
    out["by_action"] = [{"action": k[0], "kind": k[1], "count": v}
                        for k, v in sorted(by_action.items(), key=lambda kv: -kv[1])]
    out["hit_rate"] = round(out["hits"] / out["searches"], 3) if out["searches"] else None

    queries = con.execute(
        f"SELECT MIN(query), COUNT(*), AVG(results), MAX(ts), "
        f"SUM(action IN ({search}) AND ok = 1 AND results = 0), "
        f"MAX(CASE WHEN action IN ({search}) AND ok = 1 AND results = 0 THEN ts END), MAX(agent) "
        "FROM kb_usage WHERE base = ? AND ts >= ? AND kind != 'write' AND query IS NOT NULL "
        "AND query != '' GROUP BY lower(query)", (base, since)).fetchall()
    out["top_queries"] = [{"query": q[0], "count": q[1], "avg_results": round(q[2] or 0, 1), "last": q[3]}
                          for q in sorted(queries, key=lambda q: (-q[1], -q[3]))[:15]]
    out["empty_queries"] = [{"query": q[0], "count": q[4], "last": q[5], "agent": q[6] or ""}
                            for q in sorted((q for q in queries if q[4]), key=lambda q: -q[5])[:15]]

    lat: Dict[str, List[float]] = {}
    for day, ms in con.execute("SELECT day, latency_ms FROM kb_usage WHERE base = ? AND ts >= ? "
                               "AND kind IN ('context','tool') AND latency_ms IS NOT NULL", (base, since)):
        lat.setdefault(day, []).append(ms)
    every = [v for vals in lat.values() for v in vals]
    out["latency"] = {"avg": round(sum(every) / len(every), 2) if every else None,
                      "p95": _p95(every), "n": len(every)}
    out["latency_per_day"] = [
        {"day": d, "avg": round(sum(lat[d]) / len(lat[d]), 2) if d in lat else None,
         "p95": _p95(lat.get(d, [])), "n": len(lat.get(d, []))} for d in day_list]

    notes = con.execute("SELECT permalink, SUM(ts >= ?), MAX(ts) FROM kb_usage_notes "
                        "WHERE base = ? AND kind != 'write' GROUP BY permalink", (since, base)).fetchall()
    used = [n for n in notes if n[1]]
    out["notes_used"] = len(used)
    top = []
    for permalink, count, last in sorted(used, key=lambda n: (-n[1], -n[2]))[:15]:
        who = con.execute("SELECT agent FROM kb_usage_notes WHERE base = ? AND permalink = ? AND kind != 'write' "
                          "ORDER BY ts DESC LIMIT 1", (base, permalink)).fetchone()
        top.append({"permalink": permalink, "count": count, "last": last, "last_agent": who[0] if who else "",
                    "title": (titles or {}).get(permalink) or permalink,
                    "exists": True if titles is None else permalink in titles})
    out["top_notes"] = top
    if titles is not None:
        ever = {n[0] for n in notes}
        out["never_used"] = sum(1 for p in titles if p not in ever)
        out["never_used_sample"] = _sample(titles, ever)


def _sample(titles: Dict[str, str], used: set, n: int = 20) -> List[Dict[str, str]]:
    never = sorted((t or p, p) for p, t in titles.items() if p not in used)
    return [{"permalink": p, "title": t} for t, p in never[:n]]


def note_usage(base: str, permalink: str) -> Dict[str, Any]:
    """How often agents used one note (reads; writes counted apart), the last time and agent."""
    out: Dict[str, Any] = {"permalink": permalink, "count": 0, "writes": 0, "last": None,
                           "last_agent": None, "last_action": None, "enabled": enabled()}
    con = _open_ro()
    if con is None:
        return out
    try:
        r = con.execute("SELECT SUM(CASE WHEN kind != 'write' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN kind = 'write' THEN 1 ELSE 0 END) FROM kb_usage_notes "
                        "WHERE base = ? AND permalink = ?", (base, permalink)).fetchone()
        out["count"], out["writes"] = r[0] or 0, r[1] or 0
        last = con.execute("SELECT n.ts, n.agent, u.action FROM kb_usage_notes n LEFT JOIN kb_usage u "
                           "ON u.id = n.usage_id WHERE n.base = ? AND n.permalink = ? AND n.kind != 'write' "
                           "ORDER BY n.ts DESC LIMIT 1", (base, permalink)).fetchone()
        if last:
            out["last"], out["last_agent"], out["last_action"] = last[0], last[1], last[2]
        return out
    finally:
        con.close()


def agents(base: Optional[str] = None) -> List[str]:
    """Agent names in the log (for the filter)."""
    con = _open_ro()
    if con is None:
        return []
    try:
        sql = "SELECT DISTINCT agent FROM kb_usage" + (" WHERE base = ?" if base else "") + " ORDER BY agent"
        return [r[0] for r in con.execute(sql, (base,) if base else ())]
    finally:
        con.close()


# ── daily snapshots of the main counts (growth chart) ──────────────────────

_snapshot_done: set = set()


def snapshot(base: str, counts: Dict[str, Any]) -> bool:
    """Store today's notes/facts/links/tags of ``base`` (at most once a day). True when stored."""
    if not enabled():
        return False
    day = time.strftime("%Y-%m-%d")
    key = (str(db_path()), base, day)
    if key in _snapshot_done:
        return False
    try:
        con = connect(timeout=1.0)
        try:
            with con:
                cur = con.execute(
                    "INSERT OR IGNORE INTO kb_snapshots (base, day, notes, facts, links, tags) "
                    "VALUES (?,?,?,?,?,?)",
                    (base, day, int(counts.get("notes") or 0), int(counts.get("facts") or 0),
                     int(counts.get("links") or 0), int(counts.get("tags") or 0)))
            _snapshot_done.add(key)
            return bool(cur.rowcount)
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError) as e:
        _warn_once("snapshot", "Knowledge usage log: snapshot not stored (%s)", e)
        return False


def snapshots(base: str, limit: int = 400) -> List[Dict[str, Any]]:
    """Daily snapshots of one base, oldest first."""
    try:
        con = _open_ro()
    except (sqlite3.Error, OSError):
        return []
    if con is None:
        return []
    try:
        rows = con.execute("SELECT day, notes, facts, links, tags FROM kb_snapshots WHERE base = ? "
                           "ORDER BY day DESC LIMIT ?", (base, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]
    except sqlite3.Error:
        return []
    finally:
        con.close()

