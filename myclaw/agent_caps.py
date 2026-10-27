"""Things every agent can do itself (2026-10-27): write skills, ask the user.

Every agent — whatever its level (levels are only a label for now) — can
create skills, tools (``register_tool``), jobs (``job_create``), delegate,
add to the knowledge base and ask for more information. This module adds
the tools that were missing:

* :func:`create_skill` — a new skill (``~/.myclaw/skills/<name>/SKILL.md``),
  written by the same code as the Skills page. An existing skill — yours or
  built-in — is never overwritten.
* :func:`assign_skill` — give a skill to *yourself* (the calling agent only;
  other agents' skills are changed by the user on the Skills page).
* :func:`request_information` — post a question to the user, marked as a
  question from the agent, in the chat where the agent works (or in the job
  run's result) and record the topic as a knowledge gap for the researcher
  (``myclaw.knowledge.researcher.GAP_FILE``). In a channel the user's next
  message without ``@mentions`` goes to the agent that asked
  (:func:`pending_question`, used by the chat engine). At most
  :data:`MAX_QUESTIONS_PER_TURN` questions per turn.

Import stays cheap: everything heavy is imported inside the functions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_QUESTIONS_PER_TURN = 3
# Without a turn (CLI, Telegram): at most this many questions per agent in this window.
_WINDOW_S = 600
# Set to {"n": 0} at the start of each chat answer / job run (chat engine, job runner);
# delegated agents inherit it, so a whole turn shares one budget.
QUESTION_BUDGET: ContextVar[dict[str, int] | None] = ContextVar("question_budget", default=None)

_lock = threading.Lock()
_recent: dict[str, list[float]] = {}
# Questions asked during a job run, keyed by memory scope ("job-<id>"); the runner adds them to the result.
_job_questions: dict[str, list[str]] = {}


def questions_file() -> Path:
    return Path.home() / ".myclaw" / "agent_questions.jsonl"


def _caller() -> str:
    from .events import CURRENT_AGENT

    return CURRENT_AGENT.get() or "default"


# ── Skills ───────────────────────────────────────────────────────────────────
def create_skill(name: str, description: str, instructions: str) -> str:
    """Create a new skill (reusable instructions shown on the Skills page) that agents can load with use_skill.

    name: short name, letters/digits/dashes (e.g. invoice-check)
    description: one line saying when to use the skill
    instructions: the full instructions in Markdown (steps, rules, examples)
    """
    from . import agent_skills

    key = agent_skills.slug(name or "")
    if not key or not agent_skills.NAME_RE.match(key):
        return "Error: give the skill a name of letters, digits and dashes (e.g. invoice-check)."
    description = " ".join((description or "").split())[:300]
    if not description:
        return "Error: give a one-line description saying when the skill should be used."
    if not (instructions or "").strip():
        return "Error: write the skill's instructions (what to do, step by step)."
    existing = agent_skills.get_skill(key)
    if existing is not None:
        where = "a built-in skill" if existing.get("source") == "builtin" else "one of the user's skills"
        return (f"Error: '{key}' is already {where}; skills are never overwritten by agents. Load it with "
                f"use_skill('{key}'), or create yours under another name (e.g. {key}-2). The user can change "
                "it on the Skills page.")
    # A skill of the user's with a built-in name would replace the built-in one: the check above covers it.
    try:
        sk = agent_skills.save_skill(key, description, instructions)
    except ValueError as e:
        return f"Error: {e}"
    except OSError as e:
        logger.warning("Could not write skill %s: %s", key, e)
        return f"Error: the skill could not be saved ({e.__class__.__name__}: {e}). Check the disk and try again."
    logger.info("Skill %s created by @%s", key, _caller())
    return (f"Created skill '{sk['name']}': {sk['description']}. It is on the Skills page; load it with "
            f"use_skill('{sk['name']}'). To list it in your own prompt call assign_skill('{sk['name']}').")


def assign_skill(skill: str, agent: str = "") -> str:
    """Give a skill to yourself, so it is listed in your prompt (you cannot change other agents' skills).

    skill: the skill name (see list_skills)
    agent: leave empty (only your own name is accepted)
    """
    from . import agent_skills
    from .config import CONFIG_FILE, AppConfig, load_config, load_encrypted_or_plain, save_config

    me = _caller()
    target = (agent or "").strip().lstrip("@") or me
    if target.lower() != me.lower():
        return (f"Error: you can only give skills to yourself (@{me}). Ask the user to give '{skill}' to "
                f"@{target} on the Skills page, or delegate the task to @{target} and mention the skill.")
    sk = agent_skills.get_skill(skill or "")
    if sk is None:
        names = ", ".join(s["name"] for s in agent_skills.list_skills()) or "none"
        return f"Error: no skill named '{skill}'. Available skills: {names}. Create one with create_skill."
    key = sk["name"]
    cfg = load_config()
    if key in agent_skills.skills_for(cfg, me):
        return f"You already have the skill '{key}'."
    raw = (load_encrypted_or_plain(CONFIG_FILE) or {}) if CONFIG_FILE.exists() else {}
    if not raw:
        raw = cfg.model_dump(mode="json")
    agents = raw.setdefault("agents", {})
    if me == "default":
        d = agents.setdefault("defaults", {})
        d["skills"] = list(d.get("skills") or []) + [key]
    else:
        entry = next((a for a in agents.get("named") or [] if a.get("name") == me), None)
        if entry is None:
            return f"Error: @{me} is not in the settings (was it renamed or removed?). Ask the user to add the skill."
        entry["skills"] = list(entry.get("skills") or []) + [key]
    try:
        AppConfig(**raw)
    except Exception as e:
        return f"Error: the settings would not be valid: {str(e)[:300]}"
    save_config(raw)
    new_cfg = load_config(force_reload=True)
    # The running agent keeps its config object; give it the new skill list too.
    try:
        from .agent_team import _reload_prompts
        from .tools.core import _agent_registry

        running = _agent_registry.get(me)
        if running is not None and getattr(running, "config", None) is not None:
            target_cfg = running.config.agents
            if me == "default":
                target_cfg.defaults.skills = list(new_cfg.agents.defaults.skills)
            else:
                for a in target_cfg.named:
                    if a.name == me:
                        a.skills = list(agent_skills.skills_for(new_cfg, me))
        _reload_prompts()
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("could not refresh @%s after assign_skill: %s", me, e)
    return (f"Gave the skill '{key}' to @{me}. It is listed in your prompt from your next message; "
            f"use_skill('{key}') loads it now.")


# ── Asking for information ───────────────────────────────────────────────────
def _take_budget(agent: str) -> bool:
    budget = QUESTION_BUDGET.get()
    with _lock:
        if budget is not None:
            if budget.get("n", 0) >= MAX_QUESTIONS_PER_TURN:
                return False
            budget["n"] = budget.get("n", 0) + 1
            return True
        now = time.time()
        times = [t for t in _recent.get(agent, []) if now - t < _WINDOW_S]
        if len(times) >= MAX_QUESTIONS_PER_TURN:
            _recent[agent] = times
            return False
        times.append(now)
        _recent[agent] = times
        return True


def _gap_file() -> Path:
    try:
        from .knowledge import researcher

        return Path(researcher.GAP_FILE)
    except Exception:  # the researcher module could not be imported: same file
        from .defaults import GAP_FILE

        return Path(GAP_FILE)


def record_gap(topic: str, question: str, agent: str) -> None:
    """One knowledge-gap line for the researcher (same format as Agent._log_kb_gap)."""
    path = _gap_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "agent_information_request",
                "query": topic,
                "description": question,
                "agent": agent,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "recommendation": "Research the topic and write a knowledge note about it",
            }) + "\n")
    except OSError as e:
        logger.warning("Could not record the knowledge gap '%s': %s", topic[:60], e)


def _log_question(rec: dict[str, Any]) -> None:
    path = questions_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        logger.warning("Could not record the agent question: %s", e)


def question_text(agent: str, topic: str, question: str, why: str = "") -> str:
    text = f"**Question from @{agent}** — {topic}\n\n{question}"
    if why:
        text += f"\n\n_Why I ask: {why}_"
    return text + f"\n\n(Reply here; your answer goes to @{agent}.)"


def take_job_questions(scope: str) -> list[str]:
    """Questions asked during a job run (memory scope ``job-<id>``); called by the job runner."""
    with _lock:
        return _job_questions.pop(scope, [])


async def request_information(topic: str, question: str, why: str = "") -> str:
    """Ask the user for missing information about a task, job, subject or domain; the question is shown to the user and the topic is recorded for research.

    topic: the subject in a few words (e.g. 'customer invoice format')
    question: the exact question for the user
    why: optional, what you need the answer for
    """
    from .agent import MEMORY_SCOPE
    from .events import CURRENT_CHANNEL

    topic = " ".join((topic or "").split())[:200]
    question = (question or "").strip()[:2000]
    why = " ".join((why or "").split())[:1000]
    if not topic:
        return "Error: give the topic in a few words (e.g. 'invoice format of customer X')."
    if not question:
        return "Error: write the question you want the user to answer."
    agent = _caller()
    if not _take_budget(agent):
        return (f"Not sent: you already asked {MAX_QUESTIONS_PER_TURN} questions in this turn. Put the rest in "
                "one question or in your reply, and wait for the user's answer.")
    channel = CURRENT_CHANNEL.get()
    scope = MEMORY_SCOPE.get() or ""
    await asyncio.to_thread(record_gap, topic, question, agent)
    rec = {"ts": time.time(), "agent": agent, "topic": topic, "question": question, "why": why,
           "channel": channel or "", "scope": scope}
    await asyncio.to_thread(_log_question, rec)
    text = question_text(agent, topic, question, why)
    where = []
    if scope.startswith("job-"):
        with _lock:
            _job_questions.setdefault(scope, []).append(text)
        where.append("added to this job run's result")
    if channel:
        try:
            from . import events
            from .chat import store

            msg = await asyncio.to_thread(store.add_message, channel, "agent", agent, text, {
                "question": {"topic": topic, "question": question, "why": why, "asked_by": agent}})
            events.publish(channel, {"type": "message", "message": msg})
            where.append("posted in this chat")
        except Exception as e:
            logger.warning("Could not post @%s's question in %s: %s", agent, channel, e)
    if not where:
        return ("There is no chat to post the question in here. Ask it in your reply instead; the topic was "
                "recorded as a knowledge gap for research.")
    logger.info("@%s asked the user about %s (%s)", agent, topic, ", ".join(where))
    return ("Question sent to the user (" + ", ".join(where) + "; the topic was recorded for research). "
            "Finish your reply by saying what you are waiting for; the answer will come in a later message.")


def pending_question(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The agent question the newest user message answers, or None.

    ``messages`` are a channel's latest messages, oldest first, ending with the
    user's new message. A question counts when no other user message came
    between it and the new one.
    """
    seen_new = False
    for m in reversed(messages):
        if m.get("role") == "user":
            if seen_new:
                return None
            seen_new = True
            continue
        if seen_new and m.get("role") == "agent" and (m.get("meta") or {}).get("question"):
            return m
    return None


__all__ = ["create_skill", "assign_skill", "request_information", "pending_question",
           "take_job_questions", "QUESTION_BUDGET", "MAX_QUESTIONS_PER_TURN", "record_gap"]
