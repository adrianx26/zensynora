"""Knowledge base health (KB phase 6, 2026-10-25): index drift, waiting links,
conflicts, embeddings, distillation, the file watcher, import errors, trash.

Used by ``GET /api/knowledge/health`` (the status line on the Knowledge page)
and by the doctor checks (``myclaw/doctor/checks_knowledge.py``). Everything is
read-only (the index database is opened with ``mode=ro``), fast (well under a
second on 5000 notes: one directory walk with ``stat``, one query per table,
checksums only for a bounded sample) and never raises — a part that cannot be
read reports ``error`` instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SAMPLE_CHECKSUMS = 200        # files whose checksum is compared at most (changed time/size first)
SAMPLE_UNCHANGED = 25         # …plus this many files that look unchanged (edits that kept the time)
PENDING_WARN = 25             # waiting links from this many on are a warning …
PENDING_RATIO = 0.2           # … and only when they are more than this share of all links
                              # (2026-10-27, review: wiki-style forward links are normal)
TRASH_WARN_BYTES = 500 * 1024 * 1024
TRASH_WARN_FILES = 10_000
TRASH_MAX_WALK = 50_000
INGEST_HOURS = 24
MAX_BASES = 50


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def db_path(base: str) -> Path:
    from .budget import key_for

    return Path(key_for(base))


def base_dir(base: str) -> Path:
    from .storage import knowledge_root

    return knowledge_root() / base


def bases() -> List[str]:
    """Knowledge bases with an index database or a folder (``default`` first)."""
    from .storage import knowledge_root

    names = set()
    try:
        for p in (Path.home() / ".myclaw").glob("knowledge_*.db"):
            names.add(p.name[len("knowledge_"):-len(".db")])
    except OSError:
        pass
    try:
        root = knowledge_root()
        if root.is_dir():
            for e in os.scandir(root):
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    if (db_path(e.name).is_file() or os.path.isdir(os.path.join(e.path, ".trash"))
                            or any(True for _ in _md_files_quick(Path(e.path), 1))):
                        names.add(e.name)
    except OSError:
        pass
    out = sorted(names, key=lambda n: (n != "default", n))
    return out[:MAX_BASES]


def _md_files_quick(folder: Path, limit: int):
    n = 0
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith(".md") and not f.startswith("."):
                yield os.path.join(dirpath, f)
                n += 1
                if n >= limit:
                    return


def _md5(path: str) -> Optional[str]:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ── Index drift ─────────────────────────────────────────────────────────────

def index_drift(base: str = "default", sample: int = SAMPLE_CHECKSUMS) -> Dict[str, Any]:
    """Files vs. index: notes not indexed, index entries without a file, and
    notes changed since they were indexed (checksums of a bounded sample)."""
    t0 = time.monotonic()
    from .sync import _list_markdown_files, _load_bmignore_patterns

    kdir = base_dir(base)
    out: Dict[str, Any] = {"base": base, "files": 0, "indexed": 0, "missing": 0, "extra": 0,
                           "changed": 0, "checked": 0, "unchecked": 0, "db": False, "fts": None,
                           "examples": {"missing": [], "extra": [], "changed": []}, "error": None}
    try:
        files = _list_markdown_files(kdir, _load_bmignore_patterns(kdir)) if kdir.is_dir() else set()
    except OSError as e:
        out["error"] = f"cannot list {kdir}: {e}"
        files = set()
    out["files"] = len(files)
    path = db_path(base)
    rows: List[Tuple[str, str, Optional[str], Optional[float], Optional[int]]] = []
    if path.is_file():
        out["db"] = True
        try:
            con = _ro(path)
            try:
                tables = _tables(con)
                if "entities" in tables:
                    cols = {r[1] for r in con.execute("PRAGMA table_info(entities)")}
                    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in ("checksum", "mtime", "size"))
                    rows = [(r[0], r[1], r[2], r[3], r[4]) for r in
                            con.execute(f"SELECT permalink, file_path, {sel} FROM entities")]
                out["fts"] = "notes_text_fts" in tables
            finally:
                con.close()
        except sqlite3.Error as e:
            out["error"] = f"cannot read the index: {e}"
    out["indexed"] = len(rows)
    on_disk = {str(f): f for f in files}
    known: Dict[str, Tuple[str, Optional[str], Optional[float], Optional[int]]] = {}
    for permalink, fp, cs, mt, sz in rows:
        p = Path(fp or "")
        key = str(p if p.is_absolute() else kdir / p)
        known[key] = (permalink, cs, mt, sz)
    missing = sorted(p for p in on_disk if p not in known)
    extra = sorted(k for k in known if k not in on_disk)
    out["missing"], out["extra"] = len(missing), len(extra)
    out["examples"]["missing"] = [os.path.relpath(p, kdir) for p in missing[:5]]
    out["examples"]["extra"] = [known[k][0] for k in extra[:5]]

    suspects: List[str] = []
    calm: List[str] = []
    for p, (_perm, cs, mt, sz) in known.items():
        if p not in on_disk:
            continue
        if cs is None:
            suspects.append(p)
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if sz is None or mt is None or st.st_size != sz or abs(st.st_mtime - float(mt)) > 1e-3:
            suspects.append(p)
        else:
            calm.append(p)
    rng = random.Random(len(files))
    todo = sorted(suspects)[:max(0, sample)]
    out["unchecked"] = max(0, len(suspects) - len(todo))
    if calm:
        todo += rng.sample(calm, min(len(calm), SAMPLE_UNCHANGED))
    changed: List[str] = []
    for p in todo:
        cs = _md5(p)
        if cs is not None and cs != known[p][1]:
            changed.append(p)
    out["checked"] = len(todo)
    out["changed"] = len(changed)
    out["examples"]["changed"] = [known[p][0] for p in changed[:5]]
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


def drift_total(d: Dict[str, Any]) -> int:
    return int(d.get("missing", 0)) + int(d.get("extra", 0)) + int(d.get("changed", 0))


def drift_text(d: Dict[str, Any]) -> str:
    parts = [f"{d['files']} note file(s), {d['indexed']} indexed"]
    if d["missing"]:
        parts.append(f"{d['missing']} not indexed")
    if d["extra"]:
        parts.append(f"{d['extra']} index entr{'y' if d['extra'] == 1 else 'ies'} without a file")
    if d["changed"]:
        more = f" (+{d['unchecked']} not checked)" if d.get("unchecked") else ""
        parts.append(f"{d['changed']} changed since indexing{more}")
    return ", ".join(parts)


# ── Links, conflicts ────────────────────────────────────────────────────────

def links(base: str = "default") -> Dict[str, Any]:
    """Waiting links (to notes that do not exist) and the most wanted targets."""
    out: Dict[str, Any] = {"pending": 0, "relations": 0, "top": [], "error": None}
    path = db_path(base)
    if not path.is_file():
        return out
    try:
        con = _ro(path)
        try:
            tables = _tables(con)
            if "relations" in tables:
                out["relations"] = con.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            if "pending_relations" in tables:
                out["pending"] = con.execute("SELECT COUNT(*) FROM pending_relations").fetchone()[0]
                out["top"] = [{"target": r[0], "links": r[1]} for r in con.execute(
                    "SELECT MIN(to_name), COUNT(*) AS n FROM pending_relations GROUP BY to_key "
                    "ORDER BY n DESC, MIN(to_name) LIMIT 5")]
        finally:
            con.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    return out


def conflicts(base: str = "default") -> Dict[str, Any]:
    out: Dict[str, Any] = {"open": 0, "notes": 0, "error": None}
    path = db_path(base)
    if not path.is_file():
        return out
    try:
        con = _ro(path)
        try:
            if "conflicts" in _tables(con):
                r = con.execute("SELECT COUNT(*), COUNT(DISTINCT permalink) FROM conflicts "
                                "WHERE status = 'open'").fetchone()
                out["open"], out["notes"] = r[0], r[1]
        finally:
            con.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    return out


# ── Embeddings, distillation, watcher ───────────────────────────────────────

def embeddings(base: str = "default") -> Dict[str, Any]:
    """Coverage of semantic search (only meaningful while it is on)."""
    from . import semantic

    out: Dict[str, Any] = {"enabled": semantic.enabled(), "installed": semantic.library_installed(),
                           "notes": 0, "embedded": 0, "waiting": 0, "stale": 0, "queued": 0,
                           "model": semantic.model_name(), "embedder": None, "last_error": None,
                           "retry_in": None, "error": None}
    path = db_path(base)
    if not out["enabled"] or not path.is_file():
        return out
    try:
        con = _ro(path)
        try:
            out["notes"] = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            if "note_embed_state" in _tables(con):
                out["stale"] = len(semantic._stale_ids(con, out["model"]))
            else:
                out["stale"] = out["notes"]
        finally:
            con.close()
        out["waiting"] = out["stale"]
        out["embedded"] = max(0, out["notes"] - out["stale"])
        out["queued"] = semantic._WORKER.queued(str(path))
        r = semantic._WORKER.retry_in(str(path))
        out["retry_in"] = None if r is None else round(r)
        state, err = semantic.embedder_state()
        out["embedder"] = state
        last = semantic._LAST.get(str(path), {})
        out["last_error"] = last.get("last_error") or err
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def distillation(base: str = "default") -> Dict[str, Any]:
    """Distillation status and its database lease (only while it is on)."""
    from . import distill

    out: Dict[str, Any] = {"enabled": distill.enabled(), "pending": 0, "last_run": None,
                           "last_error": None, "running": False, "lease": None, "error": None}
    path = db_path(base)
    if not out["enabled"] or not path.is_file():
        return out
    try:
        st = distill.status(base, db_path=path)
        for k in ("pending", "last_run", "last_error", "running", "facts", "notes"):
            out[k] = st.get(k)
        con = _ro(path)
        try:
            if "distill_state" in _tables(con):
                row = con.execute("SELECT value FROM distill_state WHERE key = 'lease'").fetchone()
                out["lease"] = _lease_info(row[0]) if row else None
        finally:
            con.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _lease_info(value: str) -> Dict[str, Any]:
    from . import distill

    try:
        host, pid, ts = value.split("|")[0], int(value.split("|")[1]), float(value.split("|")[2])
    except (ValueError, IndexError):
        return {"raw": value, "state": "unreadable"}
    age = time.time() - ts
    if host == distill._HOST and not distill._pid_alive(pid):
        state = "stale (the process is gone; the next run takes it over)"
    elif age > distill.LEASE_SECONDS:
        state = "expired (the next run takes it over)"
    else:
        state = "held"
    return {"host": host, "pid": pid, "age_seconds": round(age), "state": state}


def watcher() -> Dict[str, Any]:
    try:
        from . import watch

        return watch.status()
    except Exception as e:
        return {"enabled": False, "running": False, "error": f"{type(e).__name__}: {e}"}


# ── Imports, trash ──────────────────────────────────────────────────────────

def ingest_errors(hours: float = INGEST_HOURS, base: Optional[str] = None) -> Dict[str, Any]:
    """Import runs with errors in the last ``hours`` (``knowledge_ingest.jsonl``)."""
    out: Dict[str, Any] = {"runs": 0, "failed": 0, "recent": [], "log": None, "error": None}
    try:
        from .ingest import log as ingest_log

        out["log"] = str(ingest_log.log_path())
        cutoff = time.time() - hours * 3600
        for e in ingest_log.read_recent(500):
            if base is not None and e.get("base") not in (None, base):
                continue
            try:
                ts = time.mktime(time.strptime(str(e.get("time", "")), "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, OverflowError):
                continue
            if ts < cutoff:
                continue
            out["runs"] += 1
            errs = [str(x) for x in (e.get("errors") or []) if x]
            if errs:
                out["failed"] += 1
                out["recent"].append({"time": e.get("time"), "source": e.get("source"),
                                      "base": e.get("base"), "error": errs[0][:200], "errors": len(errs)})
        out["recent"] = out["recent"][-3:]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def trash(base: str = "default") -> Dict[str, Any]:
    out: Dict[str, Any] = {"files": 0, "bytes": 0, "path": str(base_dir(base) / ".trash"),
                           "truncated": False, "error": None}
    folder = base_dir(base) / ".trash"
    if not folder.is_dir():
        return out
    try:
        for dirpath, _dirs, files in os.walk(folder):
            for f in files:
                try:
                    out["bytes"] += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    continue
                out["files"] += 1
                if out["files"] >= TRASH_MAX_WALK:
                    out["truncated"] = True
                    return out
    except OSError as e:
        out["error"] = str(e)
    return out


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


# ── All together ────────────────────────────────────────────────────────────

def _warn(out: List[Dict[str, str]], id: str, text: str, fix: str) -> None:
    out.append({"id": id, "text": text, "fix": fix})


def warnings_for(h: Dict[str, Any]) -> List[Dict[str, str]]:
    """The actionable warnings of a health dict (the doctor uses the same rules)."""
    w: List[Dict[str, str]] = []
    d = h.get("index") or {}
    if d.get("error"):
        _warn(w, "index", f"Index could not be checked: {d['error']}", "zensynora knowledge sync --force")
    elif drift_total(d) or (d.get("fts") is False and d.get("indexed")):
        _warn(w, "index", "Index out of date: " + drift_text(d),
              "Run zensynora knowledge sync (or ↻ Re-index on this page); "
              "zensynora knowledge sync --force rebuilds the whole index.")
    ln = h.get("links") or {}
    pend = ln.get("pending", 0)
    if pend >= PENDING_WARN and pend > PENDING_RATIO * (pend + ln.get("relations", 0)):
        top = ", ".join(f"{t['target']} ({t['links']})" for t in ln.get("top", [])[:3])
        _warn(w, "links", f"{pend} of {pend + ln.get('relations', 0)} links point to notes that "
              f"do not exist (most: {top})",
              "Create those notes or correct the link text — they show as dashed dots in the graph.")
    c = h.get("conflicts") or {}
    if c.get("open"):
        _warn(w, "conflicts", f"{c['open']} open conflict(s) in {c.get('notes', 0)} note(s)",
              "Knowledge page → open a note marked ⚠ and keep one of the two facts.")
    e = h.get("embeddings") or {}
    if e.get("enabled"):
        if not e.get("installed"):
            _warn(w, "embeddings", "Semantic search is on, but fastembed is not installed",
                  "Knowledge page → Tags & types → Semantic search → Install.")
        elif e.get("embedder") == "error" or (e.get("last_error") and e.get("waiting")):
            _warn(w, "embeddings", f"The embedding model could not be used: {e.get('last_error')}",
                  "Check the network / disk space, then press Backfill (retried automatically).")
        elif e.get("waiting"):
            _warn(w, "embeddings", f"{e['waiting']} of {e['notes']} note(s) waiting to be embedded",
                  "Knowledge page → Semantic search → Backfill, or zensynora knowledge embed.")
    ds = h.get("distill") or {}
    if ds.get("enabled"):
        if ds.get("last_error"):
            _warn(w, "distill", f"The last distillation failed: {ds['last_error']}",
                  "Knowledge page → Distilled → Distil now, or zensynora knowledge distill.")
        elif ds.get("pending") and not ds.get("running") and _older_than(ds.get("last_run"), 86400):
            _warn(w, "distill", f"{ds['pending']} note(s) not distilled for more than a day",
                  "Keep the Web UI running, or run zensynora knowledge distill.")
    wt = h.get("watcher") or {}
    if wt.get("enabled") and wt.get("wanted") and not wt.get("running"):
        _warn(w, "watcher", "The file watcher is on but not running",
              "Turn Settings → Features → Watch note files off and on again, or restart the Web UI.")
    elif wt.get("enabled") and wt.get("last_error"):
        _warn(w, "watcher", f"The file watcher reported: {wt['last_error']}",
              "Run zensynora knowledge sync; see the log for details.")
    ig = h.get("ingest") or {}
    if ig.get("failed"):
        last = ig["recent"][-1] if ig.get("recent") else {}
        _warn(w, "ingest", f"{ig['failed']} import(s) with errors in the last {INGEST_HOURS} h"
              + (f" (last: {last.get('source')}: {last.get('error')})" if last else ""),
              f"See {ig.get('log')}; fix the source and import again.")
    t = h.get("trash") or {}
    if t.get("bytes", 0) >= TRASH_WARN_BYTES or t.get("files", 0) >= TRASH_WARN_FILES:
        _warn(w, "trash", f".trash holds {t['files']} file(s), {human_bytes(t['bytes'])}",
              f"Check {t.get('path')} and delete old versions you do not need "
              "(ZenSynora never empties it).")
    return w


def _older_than(iso: Optional[str], seconds: float) -> bool:
    if not iso:
        return True
    try:
        from datetime import datetime

        return (datetime.now() - datetime.fromisoformat(str(iso))).total_seconds() > seconds
    except ValueError:
        return True


def health(base: str = "default") -> Dict[str, Any]:
    """Everything above for one base, plus the warnings. Never raises."""
    t0 = time.monotonic()
    out: Dict[str, Any] = {"base": base, "generated_at": time.time()}
    parts = (("index", lambda: index_drift(base)), ("links", lambda: links(base)),
             ("conflicts", lambda: conflicts(base)), ("embeddings", lambda: embeddings(base)),
             ("distill", lambda: distillation(base)), ("watcher", watcher),
             ("ingest", lambda: ingest_errors(base=base)), ("trash", lambda: trash(base)))
    for name, fn in parts:
        try:
            out[name] = fn()
        except Exception as e:  # never raise
            logger.debug("knowledge health %s: %s", name, e, exc_info=True)
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from . import budget

        out["writers"] = budget.status(db_path(base))
    except Exception:
        out["writers"] = None
    out["warnings"] = warnings_for(out)
    out["ok"] = not out["warnings"]
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


def to_json(h: Dict[str, Any]) -> str:
    return json.dumps(h, default=str)
