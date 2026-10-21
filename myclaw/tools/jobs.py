"""Tools — jobs on the Jobs page (2026-10-21).

Agents can create jobs, list them, start one, read how its runs went, and
pause / change jobs. The jobs are the same as on the Web UI Jobs page: kept in
``~/.myclaw/jobs.db``, run by the job runner of the Web UI (with the job's
agent, timeout, retries, tool-step limit and medic on failure), and each run
is recorded (status, answer, model, duration). A job created from a chat
channel posts its results back to that channel.

Before this, the only scheduling tools (``schedule`` / ``nlp_schedule``) used
Telegram's in-memory queue: not on the Jobs page, lost on restart, always the
default agent, results only to Telegram.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIN_AGENT_INTERVAL = 5  # minutes — agent-made repeating jobs cannot run more often
MAX_AGENT_JOBS = 50


def _runner() -> Any:
    """The Web UI's job runner, or None (CLI / gateway: jobs wait for the Web UI)."""
    try:
        from ..web import jobs_api

        return jobs_api._runner
    except Exception:
        return None


def _agents() -> list[str]:
    r = _runner()
    if r is not None:
        try:
            return list(r._registry().keys())
        except Exception:
            pass
    try:
        from ..config import load_config

        return ["default"] + [a.name for a in load_config().agents.named]
    except Exception:
        return ["default"]


def _allowed() -> Optional[str]:
    try:
        from ..config import load_config

        if not getattr(load_config().agents, "allow_job_creation", True):
            return "Error: agents may not create or change jobs (Settings → Features → agents.allow_job_creation)."
    except Exception:
        pass
    return None


def _when(text: str) -> Optional[float]:
    """'2026-10-22 08:30', '08:30' (next time), 'in 20 minutes', 'in 2 hours', 'tomorrow 09:00'."""
    t = (text or "").strip().lower()
    if not t:
        return None
    now = datetime.now()
    m = re.fullmatch(r"in\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return (now + delta).timestamp()
    m = re.fullmatch(r"(tomorrow\s+)?(\d{1,2}):(\d{2})", t)
    if m:
        cand = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        if m.group(1) or cand <= now:
            cand += timedelta(days=1)
        return cand.timestamp()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    return None


def _fmt_time(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


def _describe_schedule(j: dict[str, Any]) -> str:
    s = j.get("schedule", "manual")
    if s == "interval":
        return f"every {j.get('every_minutes')} min"
    if s == "daily":
        return f"daily at {j.get('at_time')}"
    if s == "weekly":
        return f"{j.get('days')} at {j.get('at_time')}"
    if s == "once":
        return f"once at {_fmt_time(j.get('run_at'))}"
    return "manual (run with job_run)"


def job_create(name: str, prompt: str, agent: str = "", schedule: str = "manual", when: str = "",
               every_minutes: int = 0, at_time: str = "", days: str = "", run_now: bool = False,
               report_here: bool = True, success_criteria: str = "", max_steps: int = 0,
               timeout_s: int = 0) -> str:
    """Create a job on the Jobs page: a task an agent runs now, later or on a schedule.

    name: short name shown on the Jobs page
    prompt: the task, written so it stands on its own (the agent does not see this chat)
    agent: which agent runs it (empty = you)
    schedule: manual | once | interval | daily | weekly
    when: for "once" — "in 20 minutes", "in 2 hours", "18:30", "tomorrow 09:00" or "2026-10-22 08:30"
    every_minutes: for "interval" (at least 5)
    at_time: for daily / weekly, "HH:MM"
    days: for weekly, e.g. "mon,wed,fri"
    run_now: also start it right away
    report_here: post each result in this chat channel (default yes)
    success_criteria: what a good result looks like (used when runs are evaluated)
    max_steps: tool rounds per run (1-30, default 8); timeout_s: seconds per run (default 300)
    """
    from ..events import CURRENT_AGENT, CURRENT_CHANNEL
    from ..jobs import store

    if (msg := _allowed()):
        return msg
    me = CURRENT_AGENT.get() or "default"
    agent = (agent or "").strip().lstrip("@") or me
    if agent not in _agents():
        return f"Error: no agent called '{agent}'. Agents: {', '.join(_agents())}."
    schedule = (schedule or "manual").strip().lower()
    data: dict[str, Any] = {
        "name": (name or "").strip()[:80], "prompt": (prompt or "").strip(), "agent": agent,
        "schedule": schedule, "success_criteria": (success_criteria or "").strip(),
        "created_by": f"agent:{me}",
        "channel_id": (CURRENT_CHANNEL.get() or "") if report_here else "",
    }
    if schedule == "once":
        ts = _when(when)
        if ts is None:
            return ("Error: say when, e.g. when=\"in 30 minutes\", \"18:30\", \"tomorrow 09:00\" "
                    "or \"2026-10-22 08:30\".")
        if ts <= time.time():
            return "Error: that time has already passed."
        data["run_at"] = ts
    elif schedule == "interval":
        if int(every_minutes or 0) < MIN_AGENT_INTERVAL:
            return f"Error: every_minutes must be at least {MIN_AGENT_INTERVAL} for jobs created by agents."
        data["every_minutes"] = int(every_minutes)
    elif schedule in ("daily", "weekly"):
        data["at_time"] = (at_time or "").strip()
        data["days"] = (days or "").replace(" ", "").lower()
    if max_steps:
        data["max_steps"] = int(max_steps)
    if timeout_s:
        data["timeout_s"] = int(timeout_s)
    try:
        if sum(1 for j in store.list_jobs() if str(j.get("created_by", "")).startswith("agent:")) >= MAX_AGENT_JOBS:
            return f"Error: agents already created {MAX_AGENT_JOBS} jobs; ask the user to remove some on the Jobs page."
        job = store.create_job(data)
    except ValueError as e:
        return f"Error: {e}"
    started = ""
    if run_now:
        started = " " + job_run(job["id"])
    where = f" Results are posted in #{data['channel_id']}." if data["channel_id"] else ""
    nxt = f" Next run: {_fmt_time(job.get('next_run'))}." if job.get("next_run") else ""
    runner_note = "" if _runner() is not None else " (Jobs run while the Web UI is running.)"
    logger.info("Job %s (%s) created by @%s", job["id"], job["name"], me)
    return (f"Created job {job['id']} \"{job['name']}\" for @{agent}, {_describe_schedule(job)}.{nxt}{where}"
            f"{runner_note}{started} The user can see and change it on the Jobs page; "
            f"check results with job_status('{job['id']}').")


def job_list() -> str:
    """List the jobs on the Jobs page: id, name, agent, schedule, last result, next run."""
    from ..jobs import store

    jobs = store.list_jobs()
    if not jobs:
        return "There are no jobs yet. Create one with job_create."
    r = _runner()
    running = set(r.running_job_ids()) if r is not None else set()
    lines = []
    for j in jobs:
        st = j.get("stats") or {}
        rate = f", {int(st['success_rate'] * 100)}% ok of {st['runs']}" if st.get("success_rate") is not None else ""
        state = "RUNNING" if j["id"] in running else (j.get("last_status") or "never run")
        lines.append(f"- {j['id']} \"{j['name']}\" @{j['agent']} · {_describe_schedule(j)}"
                     f"{'' if j.get('enabled') else ' · PAUSED'} · last: {state}{rate}"
                     f" · next: {_fmt_time(j.get('next_run'))} · by {j.get('created_by') or 'you'}")
    return f"{len(jobs)} job(s):\n" + "\n".join(lines)


def _find(job_id: str) -> Optional[dict[str, Any]]:
    from ..jobs import store

    job = store.get_job((job_id or "").strip())
    if job is None:
        low = (job_id or "").strip().lower()
        for j in store.list_jobs():
            if j["name"].lower() == low:
                return j
    return job


def job_run(job_id: str) -> str:
    """Start a job now (in the background). job_id: the job's id or exact name. Check with job_status."""
    from ..agent import MEMORY_SCOPE

    job = _find(job_id)
    if job is None:
        return f"Error: no job '{job_id}'. Use job_list to see them."
    r = _runner()
    if r is None:
        return "Error: jobs are run by the Web UI, which is not running in this process."
    if job["id"] in r.running_job_ids():
        return f"Job {job['id']} is already running — check it later with job_status('{job['id']}')."
    if MEMORY_SCOPE.get() == f"job-{job['id']}":
        return "Error: a job cannot start itself."
    r.run_in_background(job["id"], trigger="agent")
    return f"Started job {job['id']} \"{job['name']}\" (@{job['agent']}). Check the result with job_status('{job['id']}')."


def job_status(job_id: str, runs: int = 3) -> str:
    """How a job's recent runs went: status, duration, model, answer or error, score.

    job_id: the job's id or exact name; runs: how many recent runs (1-10)
    """
    from ..jobs import store

    job = _find(job_id)
    if job is None:
        return f"Error: no job '{job_id}'. Use job_list to see them."
    r = _runner()
    running = r is not None and job["id"] in r.running_job_ids()
    out = [f"Job {job['id']} \"{job['name']}\" @{job['agent']} · {_describe_schedule(job)}"
           f"{'' if job.get('enabled') else ' · PAUSED'}{' · RUNNING NOW' if running else ''}"
           f" · next: {_fmt_time(job.get('next_run'))}"]
    history = store.list_runs(job["id"], limit=max(1, min(int(runs or 3), 10)), kind="run")
    if not history:
        out.append("No runs yet.")
    for run in history:
        secs = f"{(run.get('ms') or 0) / 1000:.1f}s" if run.get("ms") else "…"
        score = f" · score {run['score']}/10" if run.get("score") is not None else ""
        model = f" · {run.get('provider')}/{run.get('model')}" if run.get("model") else ""
        body = (run.get("output") or run.get("error") or "").strip().replace("\n", " ")
        out.append(f"- run {run['id']} {_fmt_time(run.get('started'))} · {run.get('status')} · {secs}{model}{score}"
                   f" · trigger {run.get('trigger')}\n  {body[:600] or '(no answer)'}")
    return "\n".join(out)


def job_update(job_id: str, state: str = "", prompt: str = "", agent: str = "",
               schedule: str = "", when: str = "", every_minutes: int = 0, at_time: str = "",
               days: str = "") -> str:
    """Pause / resume a job, or change one that an agent created.

    job_id: the job's id or exact name
    state: "pause" or "resume" (any job)
    prompt, agent, schedule, when, every_minutes, at_time, days: as in job_create
      (only for jobs created by agents — the user's own jobs are changed on the Jobs page)
    """
    from ..jobs import store

    if (msg := _allowed()):
        return msg
    job = _find(job_id)
    if job is None:
        return f"Error: no job '{job_id}'. Use job_list to see them."
    changes: dict[str, Any] = {}
    st = (state or "").strip().lower()
    if st in ("pause", "paused", "disable", "off", "false", "stop"):
        changes["enabled"] = False
    elif st in ("resume", "enable", "on", "true", "start"):
        changes["enabled"] = True
    elif st:
        return "Error: state must be \"pause\" or \"resume\"."
    edits = any([prompt, agent, schedule, when, every_minutes, at_time, days])
    if edits and not str(job.get("created_by", "")).startswith("agent:"):
        return ("Error: this job was made by the user — you may only pause or resume it. "
                "Ask the user to change it on the Jobs page.")
    if prompt:
        changes["prompt"] = prompt.strip()
    if agent:
        if agent.lstrip("@") not in _agents():
            return f"Error: no agent called '{agent}'."
        changes["agent"] = agent.lstrip("@")
    if schedule:
        changes["schedule"] = schedule.strip().lower()
    if when:
        ts = _when(when)
        if ts is None:
            return "Error: could not read the time in 'when'."
        changes["run_at"] = ts
    if every_minutes:
        if int(every_minutes) < MIN_AGENT_INTERVAL:
            return f"Error: every_minutes must be at least {MIN_AGENT_INTERVAL}."
        changes["every_minutes"] = int(every_minutes)
    if at_time:
        changes["at_time"] = at_time.strip()
    if days:
        changes["days"] = days.replace(" ", "").lower()
    if not changes:
        return "Nothing to change."
    try:
        new = store.update_job(job["id"], changes)
    except ValueError as e:
        return f"Error: {e}"
    if new is None:
        return f"Error: job {job['id']} disappeared."
    return (f"Updated job {new['id']} \"{new['name']}\": {', '.join(changes)} — now {_describe_schedule(new)}"
            f"{'' if new.get('enabled') else ', PAUSED'}, next run {_fmt_time(new.get('next_run'))}.")
