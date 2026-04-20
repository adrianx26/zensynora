"""
Tools — Task Scheduling
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,
    ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    _rate_limiter, _tool_audit_logger,
    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,
    _runtime_config,
    TOOLS, TOOL_SCHEMAS,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import json
import time
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Feature 5: Agent-Initiated Scheduling ────────────────────────────────────

def _create_job_internal(task: str, delay: int, every: int, user_id: str, job_id: str) -> str:
    chat_id = _user_chat_ids.get(user_id)
    job_data = {
        "task": task,
        "user_id": user_id,
        "chat_id": chat_id,
        "delay": delay,
        "every": every
    }

    async def _job_fn(context):
        jd = context.job.data
        agent = _agent_registry.get("default")
        if not agent:
            return
        result = await agent.think(jd["task"], user_id=jd["user_id"])
        msg = f"⏰ Scheduled task '{jd['task']}' result:\n{result}"
        # Try channel-agnostic callback first (WhatsApp, future channels)
        if _notification_callback:
            try:
                import asyncio
                asyncio.ensure_future(_notification_callback(jd["user_id"], msg))
            except Exception as e:
                logger.error(f"Notification callback error: {e}")
        # Fall back to Telegram bot.send_message
        elif jd["chat_id"] and hasattr(context, 'bot'):
            await context.bot.send_message(
                chat_id=jd["chat_id"],
                text=msg
            )

    if every > 0:
        _job_queue.run_repeating(_job_fn, interval=every, first=every, name=job_id, data=job_data)
        return f"Recurring job '{job_id}' scheduled — runs every {every}s."
    else:
        _job_queue.run_once(_job_fn, when=delay, name=job_id, data=job_data)
        return f"One-shot job '{job_id}' scheduled — fires in {delay}s."


def schedule(task: str, delay: int = 0, every: int = 0, user_id: str = "default") -> str:
    """Schedule a task to run in the future, executed by the default agent."""
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."
    if delay <= 0 and every <= 0:
        return "Error: Specify 'delay' (one-shot) or 'every' (recurring) in seconds."

    job_id  = f"agent_{user_id}_{int(time.time())}"
    return _create_job_internal(task, delay, every, user_id, job_id)


def edit_schedule(job_id: str, new_task: str = "", delay: int = -1, every: int = -1) -> str:
    """Edit an active scheduled job.
    new_task specifies the new action. delay/every > 0 reschedules it.
    """
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"

    job = jobs[0]
    data = job.data or {}

    final_task = new_task if new_task else data.get("task", "")
    final_delay = delay if delay > 0 else data.get("delay", 0)
    final_every = every if every > 0 else data.get("every", 0)
    user_id = data.get("user_id", "default")

    if delay > 0 or every > 0:
        job.schedule_removal()
        return _create_job_internal(final_task, final_delay, final_every, user_id, job_id)
    else:
        data["task"] = final_task
        return f"Job '{job_id}' updated with new task: {final_task}"


def split_schedule(job_id: str, sub_tasks_json: str) -> str:
    """Split an existing job into multiple sub-jobs.
    sub_tasks_json: A JSON array of strings, each being a new task.
    They will inherit the delay/every settings of the original job.
    """
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"

    import json
    try:
        tasks = json.loads(sub_tasks_json)
        if not isinstance(tasks, list):
            raise ValueError()
    except:
        return "Error: sub_tasks_json must be a valid JSON array of strings."

    job = jobs[0]
    data = job.data or {}
    delay = data.get("delay", 0)
    every = data.get("every", 0)
    user_id = data.get("user_id", "default")

    job.schedule_removal()

    results = [f"Original job '{job_id}' removed and split into {len(tasks)} tasks:"]
    for i, t in enumerate(tasks):
        new_id = f"{job_id}_sub{i}"
        res = _create_job_internal(str(t), delay, every, user_id, new_id)
        results.append(res)

    return "\n".join(results)


def suspend_schedule(job_id: str) -> str:
    """Suspend (pause) an active scheduled job without cancelling it."""
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    for job in jobs:
        job.enabled = False
    return f"Job '{job_id}' suspended."


def resume_schedule(job_id: str) -> str:
    """Resume a suspended scheduled job."""
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    for job in jobs:
        job.enabled = True
    return f"Job '{job_id}' resumed."


def cancel_schedule(job_id: str) -> str:
    """Cancel an active scheduled job by its ID."""
    if _job_queue is None:
        return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs:
        return f"No job found with ID: {job_id}"
    for job in jobs:
        job.schedule_removal()
    return f"Job '{job_id}' cancelled."


def list_schedules() -> str:
    """List all currently active scheduled jobs."""
    if _job_queue is None:
        return "Error: Scheduler not available."
    jobs = _job_queue.jobs()
    if not jobs:
        return "No scheduled jobs active."
    lines = []
    for j in jobs:
        status = "🟢 Active" if j.enabled else "⏸️ Suspended"
        task_name = j.data.get("task", "Unknown Task") if j.data else "Unknown Task"
        lines.append(f"- {j.name} ({status}) | task: {task_name} | next: {j.next_t}")
    return "Active jobs:\n" + "\n".join(lines)


# ── Natural Language Scheduling Parser ──────────────────────────────────────

def _parse_natural_schedule(natural_time: str) -> dict:
    """Parse natural language scheduling expressions into delay/every values.

    Supports patterns like:
    - "at 8 AM" -> one-shot at 8 AM today/tomorrow
    - "at 8 AM daily" or "every day at 8 AM" -> daily recurring
    - "every Monday at 9pm" -> weekly recurring
    - "every 2 hours" -> hourly recurring
    - "in 5 minutes" -> one-shot in 5 minutes
    - "every 30 minutes" -> recurring every 30 minutes

    Returns:
        dict with 'delay' (seconds for one-shot) or 'every' (seconds for recurring),
        and 'parsed' description of what was parsed.
    """
    import re
    from datetime import datetime, timedelta

    text = natural_time.lower().strip()
    result = {"delay": 0, "every": 0, "parsed": text}

    # "in X minutes/hours/days"
    in_match = re.match(r'in (\d+) (minute|minutes|min|mins|hour|hours|hr|hrs|day|days|d)', text)
    if in_match:
        value = int(in_match.group(1))
        unit = in_match.group(2)
        if unit.startswith('min'):
            result["delay"] = value * 60
        elif unit.startswith('hour') or unit == 'hr':
            result["delay"] = value * 3600
        elif unit == 'd' or unit == 'day' or unit.startswith('day'):
            result["delay"] = value * 86400
        result["parsed"] = f"in {value} {'minutes' if value > 1 else 'minute'}"
        return result

    # "every X minutes/hours/days"
    every_match = re.match(r'every (\d+) (minute|minutes|min|mins|hour|hours|hr|hrs|day|days|d|weeks?|week)', text)
    if every_match:
        value = int(every_match.group(1))
        unit = every_match.group(2)
        if unit.startswith('min'):
            result["every"] = value * 60
        elif unit.startswith('hour') or unit == 'hr':
            result["every"] = value * 3600
        elif unit == 'd' or unit == 'day' or unit.startswith('day'):
            result["every"] = value * 86400
        elif unit.startswith('week'):
            result["every"] = value * 604800
        result["parsed"] = f"every {value} {'minutes' if unit.startswith('min') else 'hours' if unit.startswith('hour') else 'days' if value > 1 else 'day'}"
        return result

    # "at HH:MM AM/PM" or "at HH AM"
    time_match = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "0")
        period = time_match.group(3).lower()

        if period == 'pm' and hour != 12:
            hour += 12
        elif period == 'am' and hour == 12:
            hour = 0

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If target time has passed today, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)

        delay = int((target - now).total_seconds())

        # Check for daily/recurring pattern
        if 'daily' in text or 'every day' in text or 'everyday' in text:
            result["every"] = 86400
            result["parsed"] = f"daily at {hour%12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
        else:
            result["delay"] = delay
            result["parsed"] = f"at {hour%12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
        return result

    # "every Monday at 9pm" etc.
    day_match = re.search(r'every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', text)
    time_of_day = re.search(r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)

    if day_match:
        day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        target_day_idx = day_names.index(day_match.group(1))

        now = datetime.now()
        days_ahead = target_day_idx - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7  # Next occurrence of this day

        hour = 0
        minute = 0
        if time_of_day:
            hour = int(time_of_day.group(1))
            minute = int(time_of_day.group(2) or "0")
            period = time_of_day.group(3)
            if period:
                period = period.lower()
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0

        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
        delay = int((target - now).total_seconds())

        result["delay"] = delay
        result["every"] = 604800  # Weekly
        result["parsed"] = f"every {day_match.group(1).capitalize()} at {hour%12 or 12}:{minute:02d}"
        return result

    # "daily" or "every day" shorthand
    if 'daily' in text or text == 'every day' or text == 'everyday':
        result["every"] = 86400
        result["parsed"] = "daily"
        return result

    # "hourly" shorthand
    if text == 'hourly':
        result["every"] = 3600
        result["parsed"] = "hourly"
        return result

    return result  # Return unchanged if couldn't parse


def nlp_schedule(task: str, natural_time: str, user_id: str = "default") -> str:
    """Schedule a task using natural language time expressions.

    Supports patterns like:
    - "at 8 AM" - one-shot at 8 AM today/tomorrow
    - "in 5 minutes" - one-shot in 5 minutes
    - "every 2 hours" - recurring every 2 hours
    - "daily at 9pm" - daily recurring at 9 PM
    - "every Monday at 9pm" - weekly recurring

    task: The task description to execute
    natural_time: Natural language time expression
    user_id: User ID for notification routing

    Returns:
        Success or error message with parsed schedule info.
    """
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."

    parsed = _parse_natural_schedule(natural_time)

    if parsed["delay"] == 0 and parsed["every"] == 0:
        return f"Error: Could not parse time expression '{natural_time}'. Try patterns like 'in 5 minutes', 'at 8 AM daily', 'every 2 hours', etc."

    job_id = f"nlp_{user_id}_{int(time.time())}"

    if parsed["every"] > 0:
        return _create_job_internal(task, 0, parsed["every"], user_id, job_id) + f" (parsed: {parsed['parsed']})"
    else:
        return _create_job_internal(task, parsed["delay"], 0, user_id, job_id) + f" (parsed: {parsed['parsed']})"


