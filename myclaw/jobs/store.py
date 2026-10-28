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
import secrets
import sqlite3
import time
from collections.abc import Iterator
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
"""

EDITABLE = ("name", "prompt", "agent", "schedule", "every_minutes", "at_time", "days", "run_at",
            "enabled", "channel_id", "success_criteria", "auto_evaluate", "fresh_context",
            "timeout_s", "retries", "on_failure", "max_steps")
ON_FAILURE = ("", "diagnose", "fix")  # v0.7.5: what the medic does when a run fails

# Columns added after 0.7.1 (older jobs.db files get them on first use).
_ADDED_COLUMNS = {
    "jobs": [("on_failure", "TEXT NOT NULL DEFAULT ''"), ("max_steps", "INTEGER NOT NULL DEFAULT 8")],
    "runs": [("diagnosis", "TEXT NOT NULL DEFAULT ''")],
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
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _job(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("enabled", "auto_evaluate", "fresh_context"):
        d[k] = bool(d[k])
    return d


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
           "on_failure": "", "max_steps": 8, "created_by": "you"}
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


def move_agent_jobs(old: str, new: str, channel_moves: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Jobs of ``old`` run with ``new`` (agent fusion, 2026-10-27); jobs reporting to a
    channel in ``channel_moves`` report to its new id. Returns what changed, for undo."""
    channel_moves = channel_moves or {}
    changed: list[dict[str, Any]] = []
    with _conn() as c:
        for r in c.execute("SELECT id, agent, channel_id FROM jobs").fetchall():
            sets: dict[str, Any] = {}
            if r["agent"] == old:
                sets["agent"] = new
            if r["channel_id"] in channel_moves:
                sets["channel_id"] = channel_moves[r["channel_id"]]
            if not sets:
                continue
            c.execute(f"UPDATE jobs SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?",
                      (*sets.values(), r["id"]))
            changed.append({"id": r["id"], "agent": r["agent"], "channel_id": r["channel_id"], **{
                f"new_{k}": v for k, v in sets.items()}})
    return changed


def plan_agent_jobs(old: str, new: str, channel_moves: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """What :func:`move_agent_jobs` would change (nothing is changed; 2026-10-28)."""
    channel_moves = channel_moves or {}
    out: list[dict[str, Any]] = []
    with _conn() as c:
        for r in c.execute("SELECT id, agent, channel_id FROM jobs").fetchall():
            sets: dict[str, Any] = {}
            if r["agent"] == old:
                sets["agent"] = new
            if r["channel_id"] in channel_moves:
                sets["channel_id"] = channel_moves[r["channel_id"]]
            if sets:
                out.append({"id": r["id"], "agent": r["agent"], "channel_id": r["channel_id"],
                            **{f"new_{k}": v for k, v in sets.items()}})
    return out


def restore_agent_jobs(changes: list[dict[str, Any]]) -> int:
    """Undo :func:`move_agent_jobs` for jobs that still have the values it set."""
    n = 0
    with _conn() as c:
        for ch in changes:
            row = c.execute("SELECT agent, channel_id FROM jobs WHERE id = ?", (ch["id"],)).fetchone()
            if row is None:
                continue
            sets: dict[str, Any] = {}
            if "new_agent" in ch and row["agent"] == ch["new_agent"]:
                sets["agent"] = ch["agent"]
            if "new_channel_id" in ch and row["channel_id"] == ch["new_channel_id"]:
                sets["channel_id"] = ch["channel_id"]
            if sets:
                c.execute(f"UPDATE jobs SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?",
                          (*sets.values(), ch["id"]))
                n += 1
    return n


def delete_job(job_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
        return c.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


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
