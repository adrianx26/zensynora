"""Jobs and their run history, stored in ``~/.myclaw/jobs.db`` (v0.7.1).

A *job* is a prompt that an agent runs on a schedule (or on demand). Every
execution is a *run* with its output, duration, the model that answered,
and optionally an evaluation score. Benchmark runs (the same prompt sent to
several models) are runs too, with ``kind = "bench"``.

Schedules
---------
``manual``    only when you press Run
``once``      at ``run_at`` (unix time)
``interval``  every ``every_minutes``
``daily``     every day at ``at_time`` ("HH:MM", local time)
``weekly``    on ``days`` ("mon,wed,fri") at ``at_time``
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEDULES = ("manual", "once", "interval", "daily", "weekly")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    agent TEXT NOT NULL DEFAULT 'default',
    schedule TEXT NOT NULL DEFAULT 'manual',
    every_minutes INTEGER NOT NULL DEFAULT 60,
    at_time TEXT NOT NULL DEFAULT '09:00',
    days TEXT NOT NULL DEFAULT '',
    run_at REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    channel_id TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    auto_evaluate INTEGER NOT NULL DEFAULT 0,
    fresh_context INTEGER NOT NULL DEFAULT 1,
    timeout_s INTEGER NOT NULL DEFAULT 300,
    retries INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'you',
    created REAL NOT NULL,
    updated REAL NOT NULL,
    next_run REAL,
    last_run REAL,
    last_status TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'run',
    trigger TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'running',
    started REAL NOT NULL,
    finished REAL,
    ms INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    score REAL,
    evaluation TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_runs_job ON runs(job_id, id);
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    job_id TEXT NOT NULL DEFAULT '',
    context_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    ok INTEGER NOT NULL DEFAULT 1,
    result_json TEXT NOT NULL DEFAULT 'null',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tool_events_run ON tool_events(run_id, id);
CREATE INDEX IF NOT EXISTS ix_tool_events_job_created ON tool_events(job_id, created);
CREATE INDEX IF NOT EXISTS ix_tool_events_created ON tool_events(created);
CREATE TABLE IF NOT EXISTS medic_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    job_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT '',
    review_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_medic_reviews_run ON medic_reviews(run_id, id);
CREATE INDEX IF NOT EXISTS ix_medic_reviews_job_created ON medic_reviews(job_id, created);
CREATE INDEX IF NOT EXISTS ix_medic_reviews_status_created ON medic_reviews(status, created);
"""

EDITABLE = ("name", "prompt", "agent", "schedule", "every_minutes", "at_time", "days", "run_at",
            "enabled", "channel_id", "success_criteria", "auto_evaluate", "fresh_context",
            "timeout_s", "retries", "on_failure", "max_steps", "context_runs",
            "insight_notes", "insight_profile")
ON_FAILURE = ("", "diagnose", "fix")  # v0.7.5: what the medic does when a run fails

# Set by the runner/tool layer while a job is active.  The values deliberately
# live in ContextVars so concurrent jobs cannot leak their identifiers into one
# another's tool-event records.
CURRENT_RUN_ID: ContextVar[int | None] = ContextVar("jobs_current_run_id", default=None)
CURRENT_JOB_ID: ContextVar[str | None] = ContextVar("jobs_current_job_id", default=None)
CURRENT_RUN = CURRENT_RUN_ID
CURRENT_JOB = CURRENT_JOB_ID
current_run_id = CURRENT_RUN_ID
current_job_id = CURRENT_JOB_ID

TOOL_EVENT_RETENTION_DAYS = 30
TOOL_EVENT_MAX_ROWS = 20_000
MEDIC_REVIEW_RETENTION_DAYS = 90
MEDIC_REVIEW_MAX_ROWS = 10_000
_JSON_MAX_CHARS = 16_000
_TEXT_MAX_CHARS = 8_000
_DB_LOCK = threading.RLock()
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?key|authorization|credential|cookie|"
    r"passwd|password|private[_-]?key|secret|token)",
    re.I,
)

# Columns added after 0.7.1 (older jobs.db files get them on first use).
_ADDED_COLUMNS = {
    "jobs": [("on_failure", "TEXT NOT NULL DEFAULT ''"), ("max_steps", "INTEGER NOT NULL DEFAULT 8"),
             ("context_runs", "INTEGER NOT NULL DEFAULT 0"),
             # v0.8.5: insight notes and profile proposals are opt-in.
             ("insight_notes", "INTEGER NOT NULL DEFAULT 0"),
             ("insight_profile", "INTEGER NOT NULL DEFAULT 0")],
    "runs": [("diagnosis", "TEXT NOT NULL DEFAULT ''")],
    "tool_events": [
        ("run_id", "INTEGER"), ("job_id", "TEXT NOT NULL DEFAULT ''"),
        ("context_id", "TEXT NOT NULL DEFAULT ''"),
        ("agent", "TEXT NOT NULL DEFAULT ''"), ("tool", "TEXT NOT NULL DEFAULT ''"),
        ("args_json", "TEXT NOT NULL DEFAULT '{}'"), ("ok", "INTEGER NOT NULL DEFAULT 1"),
        ("result_json", "TEXT NOT NULL DEFAULT 'null'"),
        ("duration_ms", "INTEGER NOT NULL DEFAULT 0"), ("created", "REAL NOT NULL DEFAULT 0"),
    ],
    "medic_reviews": [
        ("run_id", "INTEGER"), ("job_id", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"), ("source", "TEXT NOT NULL DEFAULT ''"),
        ("review_json", "TEXT NOT NULL DEFAULT '{}'"), ("note", "TEXT NOT NULL DEFAULT ''"),
        ("created", "REAL NOT NULL DEFAULT 0"), ("updated", "REAL NOT NULL DEFAULT 0"),
    ],
}
_migrated: set[str] = set()


def _migrate(conn: sqlite3.Connection) -> None:
    key = str(db_path())
    if key in _migrated:
        return
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _migrated.add(key)


def db_path() -> Path:
    return Path.home() / ".myclaw" / "jobs.db"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    with _DB_LOCK:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _job(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("enabled", "auto_evaluate", "fresh_context", "insight_notes", "insight_profile"):
        d[k] = bool(d[k])
    return d


def _bounded_text(value: Any, limit: int = _TEXT_MAX_CHARS) -> str:
    from ..doctor.logscan import redact
    from ..logging_config import scrub_pii

    if not isinstance(value, str):
        value = repr(value)
    clean = scrub_pii(redact(value))
    if len(clean) <= limit:
        return clean
    return clean[:limit] + f"…[truncated {len(clean) - limit} chars]"


def _safe_value(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    """Return JSON-compatible data with secrets/PII hidden and nesting bounded."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, bytes):
        return _bounded_text(value.decode("utf-8", errors="replace"))
    if depth >= 6:
        return "[nested data truncated]"
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return "[circular reference omitted]"
    if isinstance(value, dict):
        seen.add(identity)
        out: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= 200:
                out["[truncated]"] = f"{len(value) - index} entries omitted"
                break
            key = str(raw_key)[:200]
            out[key] = "[redacted]" if _SECRET_KEY.search(key) else _safe_value(
                item, depth=depth + 1, seen=seen)
        seen.discard(identity)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(identity)
        values = list(value)
        out = [_safe_value(item, depth=depth + 1, seen=seen) for item in values[:200]]
        if len(values) > 200:
            out.append(f"[{len(values) - 200} items truncated]")
        seen.discard(identity)
        return out
    if hasattr(value, "__dict__"):
        return _safe_value(vars(value), depth=depth + 1, seen=seen)
    return _bounded_text(value)


def _json_payload(value: Any) -> str:
    encoded = json.dumps(_safe_value(value), ensure_ascii=False, default=str)
    if len(encoded) <= _JSON_MAX_CHARS:
        return encoded
    return json.dumps({
        "truncated": True,
        "length": len(encoded),
        "preview": _bounded_text(encoded, _JSON_MAX_CHARS - 100),
    }, ensure_ascii=False)


def _retain(conn: sqlite3.Connection, table: str, days: int, max_rows: int) -> None:
    cutoff = time.time() - max(1, days) * 86_400
    conn.execute(f"DELETE FROM {table} WHERE created < ?", (cutoff,))
    conn.execute(
        f"DELETE FROM {table} WHERE id NOT IN "
        f"(SELECT id FROM {table} ORDER BY created DESC, id DESC LIMIT ?)",
        (max(1, max_rows),),
    )


def record_tool_event(
    run_id: int | None = None,
    job_id: str | None = None,
    agent: str = "",
    tool: str = "",
    args: Any = None,
    ok: bool = True,
    result: Any = None,
    duration_ms: int | float = 0,
    context_id: str = "",
) -> int:
    """Persist one bounded, secret/PII-scrubbed tool event."""
    if run_id is None:
        run_id = CURRENT_RUN_ID.get()
    if job_id is None:
        job_id = CURRENT_JOB_ID.get()
    try:
        duration = max(0, int(duration_ms or 0))
    except (TypeError, ValueError):
        duration = 0
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tool_events "
            "(run_id, job_id, context_id, agent, tool, args_json, ok, result_json, duration_ms, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, str(job_id or ""), _bounded_text(context_id, 500), _bounded_text(agent, 200),
                _bounded_text(tool, 200), _json_payload(args), int(bool(ok)),
                _json_payload(result), duration, time.time(),
            ),
        )
        event_id = int(cur.lastrowid)
        _retain(c, "tool_events", TOOL_EVENT_RETENTION_DAYS, TOOL_EVENT_MAX_ROWS)
        return event_id


def _tool_event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    for source, target in (("args_json", "args"), ("result_json", "result")):
        try:
            event[target] = json.loads(event.pop(source))
        except (TypeError, ValueError):
            event[target] = None
    event["ok"] = bool(event["ok"])
    event["status"] = "ok" if event["ok"] else "error"
    return event


def get_tool_event(event_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM tool_events WHERE id = ?", (int(event_id),)
        ).fetchone()
    return _tool_event(row) if row else None


def list_tool_events(
    run_id: int | None = None,
    job_id: str | None = None,
    tool: str | None = None,
    agent: str | None = None,
    context_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List events, newest first, with optional run/job/tool/agent filters."""
    clauses: list[str] = []
    args: list[Any] = []
    for column, value in (("run_id", run_id), ("job_id", job_id),
                          ("context_id", context_id), ("tool", tool), ("agent", agent)):
        if value is not None:
            clauses.append(f"{column} = ?")
            args.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    args.append(max(1, min(int(limit), 1000)))
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM tool_events{where} ORDER BY id DESC LIMIT ?", args
        ).fetchall()
    return [_tool_event(row) for row in rows]


def save_medic_review(
    run_id: int | None,
    job_id: str | None,
    review: dict[str, Any],
    status: str = "pending",
    source: str = "",
) -> dict[str, Any]:
    """Create a medic review and return its decoded database representation."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO medic_reviews "
            "(run_id, job_id, status, source, review_json, note, created, updated) "
            "VALUES (?, ?, ?, ?, ?, '', ?, ?)",
            (
                run_id if run_id is not None else CURRENT_RUN_ID.get(),
                str(job_id or CURRENT_JOB_ID.get() or ""),
                str(status or "pending")[:50],
                str(source or "")[:200],
                _json_payload(review or {}),
                now,
                now,
            ),
        )
        review_id = int(cur.lastrowid)
        _retain(c, "medic_reviews", MEDIC_REVIEW_RETENTION_DAYS, MEDIC_REVIEW_MAX_ROWS)
        row = c.execute("SELECT * FROM medic_reviews WHERE id = ?", (review_id,)).fetchone()
    return _medic_review(row)  # type: ignore[arg-type]


def _medic_review(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["review"] = json.loads(item.pop("review_json"))
    except (TypeError, ValueError):
        item["review"] = {}
    return item


def get_medic_review(review_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM medic_reviews WHERE id = ?", (int(review_id),)
        ).fetchone()
    return _medic_review(row) if row else None


def list_medic_reviews(
    run_id: int | None = None,
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    for column, value in (("run_id", run_id), ("job_id", job_id), ("status", status)):
        if value is not None:
            clauses.append(f"{column} = ?")
            args.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    args.append(max(1, min(int(limit), 1000)))
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM medic_reviews{where} ORDER BY id DESC LIMIT ?", args
        ).fetchall()
    return [_medic_review(row) for row in rows]


def set_medic_review_status(
    review_id: int,
    status: str,
    note: str = "",
) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "UPDATE medic_reviews SET status = ?, note = ?, updated = ? WHERE id = ?",
            (str(status)[:50], _bounded_text(note, 2000), time.time(), int(review_id)),
        )
        if not row.rowcount:
            return None
        saved = c.execute("SELECT * FROM medic_reviews WHERE id = ?", (review_id,)).fetchone()
    return _medic_review(saved)  # type: ignore[arg-type]


def delete_medic_review(review_id: int) -> bool:
    with _conn() as c:
        return c.execute(
            "DELETE FROM medic_reviews WHERE id = ?", (int(review_id),)
        ).rowcount > 0


# ── Scheduling ───────────────────────────────────────────────────────────────
def _parse_hhmm(value: str) -> tuple:
    try:
        h, m = (int(x) for x in (value or "09:00").split(":")[:2])
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    raise ValueError(f"Time must be HH:MM, got '{value}'")


def validate(job: dict[str, Any]) -> None:
    if not str(job.get("name", "")).strip():
        raise ValueError("Give the job a name.")
    if not str(job.get("prompt", "")).strip():
        raise ValueError("The job needs a prompt (what the agent should do).")
    sched = job.get("schedule", "manual")
    if sched not in SCHEDULES:
        raise ValueError(f"Schedule must be one of {', '.join(SCHEDULES)}")
    if sched == "interval" and int(job.get("every_minutes") or 0) < 1:
        raise ValueError("Interval must be at least 1 minute.")
    if sched in ("daily", "weekly"):
        _parse_hhmm(job.get("at_time", ""))
    if sched == "weekly":
        days = [d for d in str(job.get("days", "")).split(",") if d]
        if not days or any(d not in DAYS for d in days):
            raise ValueError("Pick the weekdays (mon…sun) for a weekly job.")
    if sched == "once" and not job.get("run_at"):
        raise ValueError("Pick the date and time for a one-time job.")
    if int(job.get("timeout_s") or 0) < 10:
        raise ValueError("Timeout must be at least 10 seconds.")
    if (job.get("on_failure") or "") not in ON_FAILURE:
        raise ValueError("When a run fails: choose nothing, diagnose or fix.")
    if not 1 <= int(job.get("max_steps") or 8) <= 30:
        raise ValueError("Tool steps must be between 1 and 30.")
    context_runs = job.get("context_runs")
    if not 0 <= int(0 if context_runs is None else context_runs) <= 20:
        raise ValueError("Prior-run context must be between 0 and 20 runs.")


def compute_next_run(job: dict[str, Any], after: float | None = None) -> float | None:
    """Next due time (unix) for an enabled job, or None (manual / finished / disabled)."""
    if not job.get("enabled"):
        return None
    now = after if after is not None else time.time()
    sched = job.get("schedule", "manual")
    if sched == "manual":
        return None
    if sched == "once":
        run_at = job.get("run_at")
        return float(run_at) if run_at and (job.get("last_run") or 0) < float(run_at) else None
    if sched == "interval":
        last = job.get("last_run") or 0
        step = max(1, int(job.get("every_minutes") or 60)) * 60
        return max(now, last + step) if last else now + step
    h, m = _parse_hhmm(job.get("at_time", "09:00"))
    base = datetime.fromtimestamp(now)
    days = [DAYS.index(d) for d in str(job.get("days", "")).split(",") if d in DAYS] if sched == "weekly" else list(range(7))
    for offset in range(0, 8):
        cand = (base + timedelta(days=offset)).replace(hour=h, minute=m, second=0, microsecond=0)
        if cand.timestamp() > now and cand.weekday() in days:
            return cand.timestamp()
    return None


# ── Jobs CRUD ────────────────────────────────────────────────────────────────
def list_jobs() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY name").fetchall()
        stats = {r["job_id"]: dict(r) for r in c.execute(
            "SELECT job_id, COUNT(*) AS runs, SUM(status = 'ok') AS ok, AVG(ms) AS avg_ms, "
            "AVG(score) AS avg_score FROM runs WHERE kind = 'run' GROUP BY job_id").fetchall()}
    out = []
    for r in rows:
        j = _job(r)
        st = stats.get(j["id"], {})
        runs = st.get("runs") or 0
        j["stats"] = {
            "runs": runs,
            "success_rate": round((st.get("ok") or 0) / runs, 3) if runs else None,
            "avg_ms": int(st["avg_ms"]) if st.get("avg_ms") else None,
            "avg_score": round(st["avg_score"], 1) if st.get("avg_score") is not None else None,
        }
        out.append(j)
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job(row) if row else None


def create_job(data: dict[str, Any]) -> dict[str, Any]:
    job = {"agent": "default", "schedule": "manual", "every_minutes": 60, "at_time": "09:00", "days": "",
           "run_at": None, "enabled": True, "channel_id": "", "success_criteria": "",
           "auto_evaluate": False, "fresh_context": True, "timeout_s": 300, "retries": 0,
           "on_failure": "", "max_steps": 8, "context_runs": 0, "created_by": "you",
           "insight_notes": False, "insight_profile": False}
    job.update({k: v for k, v in data.items() if k in EDITABLE or k == "created_by"})
    validate(job)
    now = time.time()
    job_id = data.get("id") or f"job-{secrets.token_hex(4)}"
    job.update(id=job_id, created=now, updated=now, last_run=None, last_status="")
    job["next_run"] = compute_next_run(job)
    cols = list(job.keys())
    with _conn() as c:
        c.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                  [int(v) if isinstance(v, bool) else v for v in job.values()])
    return get_job(job_id)  # type: ignore[return-value]


def update_job(job_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    job = get_job(job_id)
    if job is None:
        return None
    job.update({k: v for k, v in data.items() if k in EDITABLE and v is not None})
    validate(job)
    job["next_run"] = compute_next_run(job)
    job["updated"] = time.time()
    cols = [*EDITABLE, "next_run", "updated"]
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                  [*(int(job[k]) if isinstance(job[k], bool) else job[k] for k in cols), job_id])
    return get_job(job_id)


def rename_agent(old: str, new: str) -> int:
    """Jobs follow an agent that was renamed in Settings (v0.7.5)."""
    with _conn() as c:
        return c.execute("UPDATE jobs SET agent = ? WHERE agent = ?", (new, old)).rowcount


def delete_job(job_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM tool_events WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM medic_reviews WHERE job_id = ?", (job_id,))
        return c.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


def delete_job_if_idle(job_id: str) -> str:
    """Delete atomically unless at least one persisted run is still active."""
    with _conn() as c:
        if c.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
            return "missing"
        if c.execute(
            "SELECT 1 FROM runs WHERE job_id = ? AND status = 'running' LIMIT 1",
            (job_id,),
        ).fetchone() is not None:
            return "running"
        c.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM tool_events WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM medic_reviews WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return "deleted"


def due_jobs(now: float | None = None) -> list[dict[str, Any]]:
    now = now or time.time()
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                         (now,)).fetchall()
    return [_job(r) for r in rows]


def mark_ran(job_id: str, status: str, when: float) -> None:
    job = get_job(job_id)
    if job is None:
        return
    job["last_run"] = when
    job["last_status"] = status
    if job["schedule"] == "once":
        job["enabled"] = False
    nxt = compute_next_run(job, after=time.time())
    with _conn() as c:
        c.execute("UPDATE jobs SET last_run = ?, last_status = ?, next_run = ?, enabled = ? WHERE id = ?",
                  (when, status, nxt, int(job["enabled"]), job_id))


def mark_blocked(job_id: str, when: float, scheduled: bool) -> None:
    """Keep blocked jobs enabled; defer scheduler retries without consuming a once job."""
    with _conn() as c:
        if scheduled:
            c.execute("UPDATE jobs SET last_run = ?, last_status = 'blocked', next_run = ? WHERE id = ?",
                      (when, when + 60, job_id))
        else:
            c.execute("UPDATE jobs SET last_run = ?, last_status = 'blocked' WHERE id = ?", (when, job_id))


# ── Runs ─────────────────────────────────────────────────────────────────────
def start_run(job_id: str, kind: str = "run", trigger: str = "manual", provider: str = "",
              model: str = "", attempt: int = 1) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (job_id, kind, trigger, status, started, provider, model, attempt) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
            (job_id, kind, trigger, time.time(), provider, model, attempt))
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, output: str = "", error: str = "",
               provider: str = "", model: str = "") -> None:
    now = time.time()
    with _conn() as c:
        row = c.execute("SELECT started, provider, model FROM runs WHERE id = ?", (run_id,)).fetchone()
        started = row["started"] if row else now
        c.execute("UPDATE runs SET status = ?, finished = ?, ms = ?, output = ?, error = ?, "
                  "provider = ?, model = ? WHERE id = ?",
                  (status, now, int((now - started) * 1000), output, error,
                   provider or (row["provider"] if row else ""), model or (row["model"] if row else ""), run_id))


def set_evaluation(run_id: int, score: float | None, evaluation: dict[str, Any]) -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET score = ?, evaluation = ? WHERE id = ?",
                  (score, json.dumps(evaluation), run_id))


def get_run(run_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def _run(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("evaluation", "diagnosis"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except ValueError:
            d[k] = {"text": d[k]}
    return d


def set_diagnosis(run_id: int, diagnosis: dict[str, Any]) -> None:
    """The medic's diagnosis of a failed run (v0.7.5)."""
    with _conn() as c:
        c.execute("UPDATE runs SET diagnosis = ? WHERE id = ?", (json.dumps(diagnosis), run_id))


def list_runs(job_id: str, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM runs WHERE job_id = ?"
    args: list = [job_id]
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with _conn() as c:
        return [_run(r) for r in c.execute(q, args).fetchall()]


def recover_interrupted() -> int:
    """Runs left 'running' by a crash/restart are marked as interrupted."""
    with _conn() as c:
        return c.execute("UPDATE runs SET status = 'interrupted', finished = ? WHERE status = 'running'",
                         (time.time(),)).rowcount


def prior_runs(job_id: str, exclude_run_id: int, limit: int = 3) -> list[dict[str, Any]]:
    """The last ``limit`` finished runs of a job (newest first), excluding one run.

    Used to give a job run bounded context about its own history.  Each run
    gets its latest Medic review summary attached as ``medic_summary`` /
    ``medic_severity`` when a review exists.  Text fields are returned raw
    from the database; the caller sanitizes before they reach a prompt.
    """
    limit = max(0, min(int(limit), 20))
    if limit == 0:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE job_id = ? AND kind = 'run' AND id != ? "
            "AND status != 'running' ORDER BY id DESC LIMIT ?",
            (job_id, int(exclude_run_id), limit)).fetchall()
        runs = [_run(r) for r in rows]
        for run in runs:
            row = c.execute(
                "SELECT review_json FROM medic_reviews WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run["id"],)).fetchone()
            try:
                review = json.loads(row["review_json"]) if row else {}
            except (TypeError, ValueError):
                review = {}
            if review.get("summary"):
                run["medic_summary"] = str(review["summary"])[:500]
                run["medic_severity"] = str(review.get("severity") or "info")
    return runs
