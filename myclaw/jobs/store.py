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
import uuid
import hashlib
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
CREATE TABLE IF NOT EXISTS job_audit (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    run_id INTEGER,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created REAL NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS ix_job_audit_job ON job_audit(job_id, created);
"""

EDITABLE = ("name", "prompt", "agent", "schedule", "every_minutes", "at_time", "days", "run_at",
            "enabled", "channel_id", "success_criteria", "auto_evaluate", "fresh_context",
            "timeout_s", "retries", "on_failure", "max_steps", "context_runs",
            "insight_notes", "insight_profile", "doctor_managed", "doctor_recovery",
            "doctor_auto_workflow", "recovery_attempts", "recovery_timeout_s", "team_agents")
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
    "job_audit": [("actor", "TEXT NOT NULL DEFAULT 'system'")],
}
_ADDED_COLUMNS["jobs"] += [
    ("uid", "TEXT NOT NULL DEFAULT ''"), ("doctor_managed", "INTEGER NOT NULL DEFAULT 0"),
    ("doctor_recovery", "INTEGER NOT NULL DEFAULT 0"), ("doctor_auto_workflow", "INTEGER NOT NULL DEFAULT 0"),
    ("recovery_attempts", "INTEGER NOT NULL DEFAULT 3"), ("recovery_timeout_s", "INTEGER NOT NULL DEFAULT 1800"),
    ("team_agents", "TEXT NOT NULL DEFAULT '[]'"), ("planning_state", "TEXT NOT NULL DEFAULT 'ready'"),
    ("doctor_plan", "TEXT NOT NULL DEFAULT '{}'"), ("parent_job_id", "TEXT NOT NULL DEFAULT ''"),
    ("plan_revision", "INTEGER NOT NULL DEFAULT 0"), ("recovery_state", "TEXT NOT NULL DEFAULT 'idle'"),
    ("plan_run_now", "INTEGER NOT NULL DEFAULT 0"),
    ("step_order", "INTEGER NOT NULL DEFAULT 0"),
]
_ADDED_COLUMNS["runs"] += [
    ("uid", "TEXT NOT NULL DEFAULT ''"), ("agent", "TEXT NOT NULL DEFAULT ''"),
    ("parent_run_id", "INTEGER"),
    ("task_hash", "TEXT NOT NULL DEFAULT ''"),
    ("execution_prompt", "TEXT NOT NULL DEFAULT ''"),
    ("input_hash", "TEXT NOT NULL DEFAULT ''"),
]
_migrated: set[str] = set()


class JobBusyError(ValueError):
    pass


def task_hash(job: dict[str, Any], context: str = "") -> str:
    return hashlib.sha256(json.dumps([job["prompt"], job.get("success_criteria", ""), context]).encode("utf-8")).hexdigest()


def _workflow_rows(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "WITH RECURSIVE descendants(id) AS ("
        "SELECT id FROM jobs WHERE parent_job_id = ? AND planning_state != 'superseded' "
        "UNION SELECT jobs.id FROM jobs JOIN descendants ON jobs.parent_job_id = descendants.id "
        "WHERE jobs.planning_state != 'superseded') "
        "SELECT jobs.* FROM jobs JOIN descendants ON jobs.id = descendants.id "
        "ORDER BY parent_job_id, step_order, created, jobs.id", (job_id,)).fetchall()


def _execution_hash(conn: sqlite3.Connection, job: dict[str, Any], prompt: str | None = None) -> str:
    team = job["team_agents"]
    if isinstance(team, str):
        team = json.loads(team)
    fields = ("id", "parent_job_id", "name", "prompt", "success_criteria", "agent", "team_agents", "enabled", "planning_state")
    steps = [{key: row[key] for key in fields} for row in _workflow_rows(conn, job["id"])]
    context = json.dumps({"team_agents": team, "subtasks": steps}) if team or steps else ""
    return task_hash({**job, "prompt": job["prompt"] if prompt is None else prompt}, context)


def execution_hash(job: dict[str, Any]) -> str:
    with _conn() as c:
        return _execution_hash(c, job)


def _approval_hash(conn: sqlite3.Connection, job: dict[str, Any]) -> str:
    fields = sorted({"id", "uid", "parent_job_id", "planning_state", "step_order", *EDITABLE})
    steps = [{key: row[key] for key in fields} for row in _workflow_rows(conn, job["id"])]
    return hashlib.sha256(json.dumps([_execution_hash(conn, job), job["agent"], steps]).encode("utf-8")).hexdigest()


def approval_hash(job: dict[str, Any]) -> str:
    with _conn() as c:
        return _approval_hash(c, job)


def _touch_ancestors(conn: sqlite3.Connection, job: dict[str, Any], actor: str) -> None:
    parent_id = job["parent_job_id"]
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (parent_id,)).fetchone()
        if row is None:
            break
        parent = _job(row)
        conn.execute("UPDATE jobs SET updated = ? WHERE id = ?", (job["updated"], parent_id))
        _audit(conn, parent_id, "subtask-updated", parent, {**parent, "updated": job["updated"]}, actor=actor)
        parent_id = parent["parent_job_id"]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    for table in ("jobs", "runs"):
        for row in conn.execute(f"SELECT id FROM {table} WHERE uid = ''").fetchall():
            conn.execute(f"UPDATE {table} SET uid = ? WHERE id = ?", (str(uuid.uuid4()), row["id"]))
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ix_{table}_uid ON {table}(uid)")


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
    for k in ("enabled", "auto_evaluate", "fresh_context", "insight_notes", "insight_profile",
              "doctor_managed", "doctor_recovery", "doctor_auto_workflow", "plan_run_now"):
        d[k] = bool(d[k])
    for key in ("team_agents", "doctor_plan"):
        d[key] = json.loads(d[key])
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


def _safe_value(value: Any, *, depth: int = 0, seen: set[int] | None = None,
                preserve_ids: bool = False) -> Any:
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
            if preserve_ids and key in {"id", "uid", "job_id", "job_uid", "parent_job_id"} \
                    and isinstance(item, str) and re.fullmatch(
                        r"(?:job-[0-9a-f]{8}|job-[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                        item, re.I):
                out[key] = item
                continue
            out[key] = "[redacted]" if _SECRET_KEY.search(key) else _safe_value(
                item, depth=depth + 1, seen=seen, preserve_ids=preserve_ids)
        seen.discard(identity)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(identity)
        values = list(value)
        out = [_safe_value(item, depth=depth + 1, seen=seen, preserve_ids=preserve_ids) for item in values[:200]]
        if len(values) > 200:
            out.append(f"[{len(values) - 200} items truncated]")
        seen.discard(identity)
        return out
    if hasattr(value, "__dict__"):
        return _safe_value(vars(value), depth=depth + 1, seen=seen, preserve_ids=preserve_ids)
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
    if int(job.get("recovery_attempts", 3)) < 1 or int(job.get("recovery_timeout_s", 1800)) < 10:
        raise ValueError("Doctor recovery needs at least one attempt and a time limit of at least 10 seconds.")
    if not isinstance(job.get("team_agents", []), list) or any(
            not isinstance(name, str) or not name.strip() for name in job.get("team_agents", [])):
        raise ValueError("Team agents must be a list of non-empty agent names.")
    members = [str(job.get("agent") or "").casefold(), *[name.casefold() for name in job.get("team_agents", [])]]
    if len(members) != len(set(members)):
        raise ValueError("Each agent may appear only once in a job team; remove the duplicate assignment.")
    if not job.get("doctor_managed") and not job.get("agent"):
        raise ValueError("Choose an agent or enable Doctor assignment before saving this job.")


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


def _new_job(data: dict[str, Any]) -> dict[str, Any]:
    job = {"agent": "default", "schedule": "manual", "every_minutes": 60, "at_time": "09:00", "days": "",
           "run_at": None, "enabled": True, "channel_id": "", "success_criteria": "",
           "auto_evaluate": False, "fresh_context": True, "timeout_s": 300, "retries": 0,
           "on_failure": "", "max_steps": 8, "context_runs": 0, "created_by": "you",
           "insight_notes": False, "insight_profile": False, "doctor_managed": False,
           "doctor_recovery": False, "doctor_auto_workflow": False, "recovery_attempts": 3,
           "recovery_timeout_s": 1800, "team_agents": [], "planning_state": "ready",
           "doctor_plan": {}, "parent_job_id": "", "plan_revision": 0,
           "recovery_state": "idle", "plan_run_now": False, "step_order": 0}
    job.update({k: v for k, v in data.items() if k in EDITABLE or k == "created_by"})
    validate(job)
    now = time.time()
    uid = uuid.uuid4()
    job_id = data.get("id") or f"job-{uid.hex}"
    job.update(id=job_id, uid=str(uid), created=now, updated=now, last_run=None, last_status="")
    if job["doctor_managed"]:
        job.update(agent="", team_agents=[], planning_state="pending")
    job["next_run"] = compute_next_run(job)
    return job


def _values(job: dict[str, Any], cols: list[str]) -> list[Any]:
    return [int(job[k]) if isinstance(job[k], bool) else
            json.dumps(job[k]) if isinstance(job[k], (list, dict)) else job[k] for k in cols]


def _insert_job(c: sqlite3.Connection, job: dict[str, Any]) -> None:
    cols = list(job.keys())
    c.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", _values(job, cols))


def _audit(c: sqlite3.Connection, job_id: str, action: str, before: Any, after: Any,
           run_id: int | None = None, actor: str = "") -> None:
    if not actor:
        actor = "doctor" if action.startswith(("plan", "subtask", "recovery")) else "user"
    c.execute("INSERT INTO job_audit (id, job_id, run_id, action, before_json, after_json, created, actor) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), job_id, run_id, action, json.dumps(before, ensure_ascii=False),
               json.dumps(after, ensure_ascii=False), time.time(), actor))


def create_job(data: dict[str, Any]) -> dict[str, Any]:
    job = _new_job(data)
    with _conn() as c:
        _insert_job(c, job)
        _audit(c, job["id"], "created", None, job, actor=job["created_by"] or "user")
    return get_job(job["id"])  # type: ignore[return-value]


def job_audit(job_id: str) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM job_audit WHERE job_id = ? ORDER BY created, id", (job_id,)).fetchall()
    return [{**{key: row[key] for key in ("id", "job_id", "run_id", "action", "created", "actor")},
             "before": _safe_value(json.loads(row["before_json"]), preserve_ids=True),
             "after": _safe_value(json.loads(row["after_json"]), preserve_ids=True)}
            for row in rows]


def children(job_id: str) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE parent_job_id = ? AND planning_state != 'superseded' "
                         "ORDER BY step_order, created, id", (job_id,)).fetchall()
    return [_job(row) for row in rows]


def pending_jobs() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE enabled = 1 AND planning_state = 'pending' "
                         "ORDER BY created, id").fetchall()
    return [_job(row) for row in rows]


def request_planning(job_id: str, reason: str = "", run_now: bool = False,
                     expected_updated: float | None = None) -> bool:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        before = _job(row)
        if expected_updated is not None and before["updated"] != expected_updated:
            _audit(c, job_id, "planning-discarded", before,
                   {**before, "planning_reason": "Job changed before automatic reassignment."})
            return False
        c.execute("UPDATE jobs SET agent = '', team_agents = '[]', doctor_managed = 1, "
                  "planning_state = 'pending', plan_run_now = ?, updated = ? WHERE id = ?",
                  (int(run_now or before["plan_run_now"]), time.time(), job_id))
        after = _job(c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        _audit(c, job_id, "planning-requested", before, {**after, "planning_reason": reason})
        _touch_ancestors(c, after, "doctor")
        return True


def reassign_agent(old: str, destination: str) -> int:
    """Queue affected jobs individually, retaining previous assignments in the audit."""
    count = 0
    for job in list_jobs():
        if job["agent"].casefold() == old.casefold() or old.casefold() in {
                name.casefold() for name in job["team_agents"]}:
            if request_planning(job["id"], f"Agent {old} became inactive; consider {destination} and other active agents.",
                                expected_updated=job["updated"]):
                count += 1
    return count


def recovery_state(job_id: str, state: str, detail: str = "", run_id: int | None = None) -> None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        before = _job(row)
        c.execute("UPDATE jobs SET recovery_state = ? WHERE id = ?", (state, job_id))
        _audit(c, job_id, f"recovery-{state}", before, {**before, "recovery_state": state, "detail": detail}, run_id)


def planning_failed(job_id: str, detail: str, expected_updated: float) -> None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        before = _job(row)
        if before["updated"] != expected_updated or not before["doctor_managed"]:
            _audit(c, job_id, "planning-discarded", before, {**before, "detail": detail})
            return
        c.execute("UPDATE jobs SET planning_state = 'blocked', doctor_plan = ? WHERE id = ?",
                  (json.dumps({"reason": detail}), job_id))
        _audit(c, job_id, "planning-blocked", before,
               {**before, "planning_state": "blocked", "doctor_plan": {"reason": detail}})


def linked_runs(run_id: int, recursive: bool = False) -> list[dict[str, Any]]:
    with _conn() as c:
        if recursive:
            rows = c.execute(
                "WITH RECURSIVE related(id) AS (SELECT id FROM runs WHERE parent_run_id = ? "
                "UNION SELECT runs.id FROM runs JOIN related ON runs.parent_run_id = related.id) "
                "SELECT runs.* FROM runs JOIN related ON runs.id = related.id ORDER BY started, runs.id",
                (run_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM runs WHERE parent_run_id = ? ORDER BY started, id", (run_id,)).fetchall()
    return [_run(row) for row in rows]


def recovery_root(run_id: int) -> dict[str, Any] | None:
    run = get_run(run_id)
    seen: set[int] = set()
    while run and run["kind"] == "step" and run["parent_run_id"] and run["id"] not in seen:
        seen.add(run["id"])
        parent = get_run(run["parent_run_id"])
        if parent is None or parent["job_id"] != run["job_id"]:
            break
        run = parent
    return run


def completed_recovery(run: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    current_hash = execution_hash(job)
    return next((item for item in reversed(linked_runs(run["id"], recursive=True))
                 if item["job_id"] == job["id"] and item["kind"] == "run" and item["status"] == "ok"
                 and item["task_hash"] == current_hash), None)


def save_plan(job_id: str, plan: dict[str, Any], expected_updated: float, approved: bool = False) -> dict[str, Any]:
    """Commit a plan and its child jobs atomically, preserving previous workflow revisions."""
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        before = _job(row)
        if before["updated"] != expected_updated:
            raise ValueError("The job changed during Doctor analysis. Analyze it again before applying this plan.")
        waiting = bool(plan.get("requires_approval") and not approved)
        plan = dict(plan)
        if approved and plan.get("approval_hash") and plan["approval_hash"] != _approval_hash(c, before):
            raise ValueError("The job or its subtasks changed after this proposal. Ask Doctor to analyze it again.")
        if waiting:
            plan["approval_hash"] = _approval_hash(c, before)
        state = "needs_approval" if waiting else "ready"
        revision = before["plan_revision"] + (0 if waiting else 1)
        c.execute("UPDATE jobs SET doctor_managed = 1, doctor_plan = ?, planning_state = ?, plan_revision = ?, "
                  "updated = ? WHERE id = ?",
                  (json.dumps(plan), state, revision, time.time(), job_id))
        if not waiting:
            c.execute("UPDATE jobs SET agent = ?, team_agents = ? WHERE id = ?",
                      (plan["agent"], json.dumps(plan["team_agents"]), job_id))
            old_children = [_job(row) for row in c.execute(
                "SELECT * FROM jobs WHERE parent_job_id = ? AND planning_state != 'superseded'", (job_id,)).fetchall()]
            retained: set[str] = set()
            for position, item in enumerate(plan.get("subtasks", [])):
                existing = next((child for child in old_children
                                 if child["id"] not in retained and child["name"] == item["name"]
                                 and child["prompt"] == item["prompt"]), None)
                if existing is not None:
                    retained.add(existing["id"])
                    c.execute("UPDATE jobs SET agent = ?, team_agents = ?, planning_state = 'ready', "
                              "plan_revision = ?, step_order = ?, updated = ? WHERE id = ?",
                              (item["agents"][0], json.dumps(item["agents"][1:]),
                               revision, position, time.time(), existing["id"]))
                    after = _job(c.execute("SELECT * FROM jobs WHERE id = ?", (existing["id"],)).fetchone())
                    _audit(c, existing["id"], "subtask-reassigned", existing, after)
                    continue
                child = _new_job({
                    "name": item["name"], "prompt": item["prompt"], "agent": item["agents"][0],
                    "team_agents": item["agents"][1:], "schedule": "manual",
                    "created_by": "doctor", "timeout_s": before["timeout_s"],
                    "max_steps": before["max_steps"], "insight_notes": before["insight_notes"],
                })
                child.update(parent_job_id=job_id, plan_revision=revision, step_order=position)
                _insert_job(c, child)
                _audit(c, child["id"], "subtask-created", None, child)
            for child in old_children:
                if child["id"] not in retained:
                    c.execute("UPDATE jobs SET planning_state = 'superseded', enabled = 0 WHERE id = ?", (child["id"],))
                    _audit(c, child["id"], "subtask-superseded", child,
                           {**child, "planning_state": "superseded", "enabled": False})
        after = _job(c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        actor = "user" if approved else "doctor"
        _audit(c, job_id, "plan-proposed" if waiting else "plan-applied", before, after, actor=actor)
        _touch_ancestors(c, after, actor)
    return get_job(job_id)  # type: ignore[return-value]


def update_job(job_id: str, data: dict[str, Any], *, actor: str = "user",
               require_idle: bool = False) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        if require_idle and _has_running(c, job_id):
            raise JobBusyError("Stop the active job before editing it.")
        job = _job(row)
        before = dict(job)
        changes = {k: v for k, v in data.items() if k in EDITABLE and v is not None and v != job[k]}
        job.update(changes)
        cols = [*EDITABLE, "next_run", "updated"]
        changed_intent = bool({"prompt", "agent", "team_agents", "success_criteria"} & changes.keys())
        if "doctor_managed" in changes or (job["doctor_managed"] and changed_intent) \
                or (job["planning_state"] == "needs_approval" and changes):
            job.update(planning_state="pending" if job["doctor_managed"] else "ready", doctor_plan={})
            if job["doctor_managed"]:
                job.update(agent="", team_agents=[])
            cols.extend(["planning_state", "doctor_plan"])
            if not job["doctor_managed"]:
                for child in c.execute("SELECT * FROM jobs WHERE parent_job_id = ? AND planning_state != 'superseded'",
                                       (job_id,)).fetchall():
                    previous = _job(child)
                    c.execute("UPDATE jobs SET planning_state = 'superseded', enabled = 0 WHERE id = ?", (child["id"],))
                    _audit(c, child["id"], "subtask-superseded", previous,
                           {**previous, "planning_state": "superseded", "enabled": False}, actor=actor)
        validate(job)
        job["next_run"] = compute_next_run(job)
        job["updated"] = time.time()
        c.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                  [*_values(job, cols), job_id])
        _audit(c, job_id, "updated", before, job, actor=actor)
        if changes:
            _touch_ancestors(c, job, actor)
    return get_job(job_id)


def rename_agent(old: str, new: str) -> int:
    """Jobs follow an agent that was renamed in Settings (v0.7.5)."""
    with _conn() as c:
        count = 0
        for row in c.execute("SELECT * FROM jobs").fetchall():
            before = _job(row)
            if before["agent"] != old and old not in before["team_agents"]:
                continue
            agent = new if before["agent"] == old else before["agent"]
            team = list(dict.fromkeys(new if name == old else name for name in before["team_agents"]))
            team = [name for name in team if name != agent]
            c.execute("UPDATE jobs SET agent = ?, team_agents = ?, updated = ? WHERE id = ?",
                      (agent, json.dumps(team), time.time(), before["id"]))
            after = _job(c.execute("SELECT * FROM jobs WHERE id = ?", (before["id"],)).fetchone())
            _audit(c, before["id"], "agent-renamed", before, after, actor="settings")
            _touch_ancestors(c, after, "settings")
            count += 1
        return count


def _delete_job(c: sqlite3.Connection, job_id: str) -> bool:
    row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return False
    for child in c.execute("SELECT * FROM jobs WHERE parent_job_id = ?", (job_id,)).fetchall():
        before = _job(child)
        c.execute("UPDATE jobs SET parent_job_id = '', enabled = 0, plan_run_now = 0 WHERE id = ?", (child["id"],))
        after = _job(c.execute("SELECT * FROM jobs WHERE id = ?", (child["id"],)).fetchone())
        _audit(c, child["id"], "parent-deleted-child-retained", before, after)
    _audit(c, job_id, "deleted", _job(row), None)
    _touch_ancestors(c, {**_job(row), "updated": time.time()}, "user")
    c.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
    c.execute("DELETE FROM tool_events WHERE job_id = ?", (job_id,))
    c.execute("DELETE FROM medic_reviews WHERE job_id = ?", (job_id,))
    return c.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


def delete_job(job_id: str) -> bool:
    with _conn() as c:
        return _delete_job(c, job_id)


def _has_running(c: sqlite3.Connection, job_id: str) -> bool:
    return c.execute(
        "WITH RECURSIVE related(id) AS (SELECT ? UNION SELECT jobs.id FROM jobs JOIN related "
        "ON jobs.parent_job_id = related.id) SELECT 1 FROM runs JOIN related ON runs.job_id = related.id "
        "WHERE runs.status = 'running' LIMIT 1", (job_id,)).fetchone() is not None


def delete_job_if_idle(job_id: str) -> str:
    """Delete atomically unless at least one persisted run is still active."""
    with _conn() as c:
        if c.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
            return "missing"
        if _has_running(c, job_id):
            return "running"
        _delete_job(c, job_id)
    return "deleted"


def due_jobs(now: float | None = None) -> list[dict[str, Any]]:
    now = now or time.time()
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE enabled = 1 AND planning_state = 'ready' "
                         "AND parent_job_id = '' AND (plan_run_now = 1 OR (next_run IS NOT NULL AND next_run <= ?))",
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
        c.execute("UPDATE jobs SET plan_run_now = 0 WHERE id = ?", (job_id,))
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
              model: str = "", attempt: int = 1, parent_run_id: int | None = None, agent: str = "",
              execution_prompt: str | None = None) -> int:
    with _conn() as c:
        owner = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        identity = ""
        input_hash = ""
        actual_prompt = ""
        if owner:
            snapshot = dict(owner)
            actual_prompt = snapshot["prompt"] if execution_prompt is None else execution_prompt
            if kind == "step":
                identity = task_hash(snapshot)
                input_hash = task_hash({**snapshot, "prompt": actual_prompt})
            else:
                identity = _execution_hash(c, snapshot)
                input_hash = _execution_hash(c, snapshot, execution_prompt)
        uid = str(uuid.uuid4())
        cur = c.execute(
            "INSERT INTO runs (job_id, kind, trigger, status, started, provider, model, attempt, uid, agent, parent_run_id, task_hash, execution_prompt, input_hash) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, kind, trigger, time.time(), provider, model, attempt, uid,
             agent or (owner["agent"] if owner else ""), parent_run_id, identity, actual_prompt, input_hash))
        run_id = int(cur.lastrowid)
        _audit(c, job_id, "run-started", None, {
            **_run(c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()),
            "job_uid": owner["uid"] if owner else None, "job_updated": owner["updated"] if owner else None,
        }, run_id, actor="runner")
        return run_id


def finish_run(run_id: int, status: str, output: str = "", error: str = "",
               provider: str = "", model: str = "") -> None:
    now = time.time()
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        started = row["started"] if row else now
        c.execute("UPDATE runs SET status = ?, finished = ?, ms = ?, output = ?, error = ?, "
                  "provider = ?, model = ? WHERE id = ?",
                  (status, now, int((now - started) * 1000), output, error,
                   provider or (row["provider"] if row else ""), model or (row["model"] if row else ""), run_id))
        if row:
            after = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            _audit(c, row["job_id"], "run-finished", _run(row), _run(after), run_id, actor="runner")


def progress_run(run_id: int, output: str) -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET output = ? WHERE id = ? AND status = 'running'", (output, run_id))


def set_run_input(run_id: int, job: dict[str, Any]) -> None:
    with _conn() as c:
        before = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if before is None or before["job_id"] != job["id"] or before["status"] != "running":
            raise ValueError("Only the active run of this job can record execution inputs.")
        identity = task_hash(job) if before["kind"] == "step" else _execution_hash(c, job)
        c.execute("UPDATE runs SET execution_prompt = ?, input_hash = ? WHERE id = ?",
                  (job["prompt"], identity, run_id))
        after = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        _audit(c, job["id"], "run-input-prepared", _run(before), _run(after), run_id, actor="runner")


def set_evaluation(run_id: int, score: float | None, evaluation: dict[str, Any]) -> None:
    with _conn() as c:
        before = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        c.execute("UPDATE runs SET score = ?, evaluation = ? WHERE id = ?",
                  (score, json.dumps(evaluation), run_id))
        if before:
            after = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            _audit(c, before["job_id"], "run-evaluated", _run(before), _run(after), run_id, actor="runner")


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
        before = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        c.execute("UPDATE runs SET diagnosis = ? WHERE id = ?", (json.dumps(diagnosis), run_id))
        if before:
            after = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            _audit(c, before["job_id"], "run-diagnosed", _run(before), _run(after), run_id, actor="medic")


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
        rows = c.execute("SELECT * FROM jobs WHERE recovery_state = 'running'").fetchall()
        for row in rows:
            before = _job(row)
            c.execute("UPDATE jobs SET recovery_state = 'interrupted' WHERE id = ?", (row["id"],))
            _audit(c, row["id"], "recovery-interrupted", before, {**before, "recovery_state": "interrupted"})
        runs = c.execute("SELECT * FROM runs WHERE status = 'running'").fetchall()
        finished = time.time()
        count = c.execute("UPDATE runs SET status = 'interrupted', finished = ? WHERE status = 'running'",
                          (finished,)).rowcount
        for row in runs:
            before = _run(row)
            _audit(c, row["job_id"], "run-interrupted", before,
                   {**before, "status": "interrupted", "finished": finished}, row["id"], actor="runner")
        return count


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
