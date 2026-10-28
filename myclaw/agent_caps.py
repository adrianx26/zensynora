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
  (``myclaw.knowledge.researcher.GAP_FILE``). In a chat where the asking
  agent is a member, the user's next message without ``@mentions`` goes to
  it (:func:`open_questions`, used by the chat engine) until the question is
  answered or 24 h old. At most :data:`MAX_QUESTIONS_PER_TURN` questions per
  user message (all agents together) or per job run.

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
# A question stays open (the user's next message goes to the asker) at most this long.
QUESTION_TTL_S = 24 * 3600
# Without a turn (CLI, Telegram): at most this many questions per agent in this window.
_WINDOW_S = 600
# One dict per user message in a chat (chat engine) or per job run (job runner),
# shared by every agent that answers it and by delegated agents: the limit is
# MAX_QUESTIONS_PER_TURN questions in total per user message (2026-10-28: it was
# per agent answer). A job run also collects its questions here ("questions").
QUESTION_BUDGET: ContextVar[dict[str, Any] | None] = ContextVar("question_budget", default=None)
JOB_QUESTIONS_MARK = "\n\n---\nQuestions from the agent (request_information):\n"

_lock = threading.Lock()
_recent: dict[str, list[float]] = {}


def new_turn_budget(job_run: str = "") -> dict[str, Any]:
    """A fresh budget for one user message (chat) or one job run (``job_run`` = "<job>-<run>")."""
    return {"n": 0, "job_run": job_run, "questions": []} if job_run else {"n": 0}


def questions_file() -> Path:
    return Path.home() / ".myclaw" / "agent_questions.jsonl"


def _caller() -> str:
    from .events import CURRENT_AGENT

    return CURRENT_AGENT.get() or "default"


# ── Skills ───────────────────────────────────────────────────────────────────
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

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
    if key in _WINDOWS_RESERVED:
        return f"Error: '{key}' is a reserved file name on Windows; pick another name."
    # 2026-10-28: skills are plain files other agents load into their prompt — no secrets in them.
    try:
        from .knowledge.ingest.safety import redact

        if redact(instructions) != instructions or redact(description) != description:
            return ("Error: the skill text contains what looks like a password, token or API key. Skills are "
                    "plain files shown to every agent — remove the secret (refer to the setting instead) and "
                    "try again.")
    except ImportError:  # pragma: no cover - the safety module is part of ZenSynora
        pass
    existing = agent_skills.get_skill(key)
    if existing is not None:
        where = "a built-in skill" if existing.get("source") == "builtin" else "one of the user's skills"
        return (f"Error: '{key}' is already {where}; skills are never overwritten by agents. Load it with "
                f"use_skill('{key}'), or create yours under another name (e.g. {key}-2). The user can change "
                "it on the Skills page.")
    if len(instructions) > agent_skills.MAX_BODY:
        return f"Error: a skill can be at most {agent_skills.MAX_BODY // 1000} kB; shorten the instructions."
    # 2026-10-28: atomic, exclusive create — two agents creating the same name at the same
    # time must not overwrite each other (save_skill, used by the Skills page, overwrites).
    base = agent_skills.user_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        if (base / f"{key}.md").exists():
            raise FileExistsError(key)
        folder = base / key
        folder.mkdir()  # fails if another agent (or the user) made it first
        try:
            with open(folder / "SKILL.md", "x", encoding="utf-8") as f:
                f.write(agent_skills.render({"name": key, "description": description, "body": instructions}))
        except BaseException:
            try:
                (folder / "SKILL.md").unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass
            raise
    except FileExistsError:
        return (f"Error: a skill called '{key}' already exists; skills are never overwritten by agents. "
                f"Pick another name (e.g. {key}-2).")
    except OSError as e:
        logger.warning("Could not write skill %s: %s", key, e)
        return f"Error: the skill could not be saved ({e.__class__.__name__}: {e}). Check the disk and try again."
    sk = agent_skills.get_skill(key) or {"name": key, "description": description}
    logger.info("Skill %s created by @%s", key, _caller())
    return (f"Created skill '{sk['name']}': {sk['description']}. It is on the Skills page; load it with "
            f"use_skill('{sk['name']}'). To list it in your own prompt call assign_skill('{sk['name']}').")


def assign_skill(skill: str, agent: str = "") -> str:
    """Give a skill to yourself, so it is listed in your prompt (you cannot change other agents' skills).

    skill: the skill name (see list_skills)
    agent: leave empty (only your own name is accepted)
    """
    from . import agent_skills
    from .config import AppConfig, load_config, raw_for_update, save_config

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
    raw = raw_for_update(cfg)  # 2026-10-28: real secrets, never "**********"
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


def question_text(agent: str, topic: str, question: str, why: str = "", reply_here: bool = True) -> str:
    """Plain text: the chat shows a "question" badge and renders no Markdown emphasis (2026-10-28)."""
    lines = [f"Question from @{agent} about {topic}:", question]
    if why:
        lines.append(f"Why I ask: {why}")
    lines.append(f"Reply here; your answer goes to @{agent}." if reply_here
                 else f"@{agent} is not in this chat: answer it in a conversation with @{agent}.")
    return "\n\n".join(lines)


def job_questions_text(questions: list[str]) -> str:
    return JOB_QUESTIONS_MARK + "\n\n".join(questions) if questions else ""


def strip_job_questions(output: str) -> str:
    """The run's answer without the questions added to it (the judge scores only the answer)."""
    return (output or "").split(JOB_QUESTIONS_MARK, 1)[0]


async def request_information(topic: str, question: str, why: str = "") -> str:
    """Ask the user for missing information about a task, job, subject or domain; the question is shown to the user and the topic is recorded for research.

    topic: the subject in a few words (e.g. 'customer invoice format')
    question: the exact question for the user
    why: optional, what you need the answer for
    """
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
        return (f"Not sent: {MAX_QUESTIONS_PER_TURN} questions were already asked for this message. Put the "
                "rest in one question or in your reply, and wait for the user's answer.")
    channel = CURRENT_CHANNEL.get()
    budget = QUESTION_BUDGET.get()
    job_run = str((budget or {}).get("job_run") or "")
    await asyncio.to_thread(record_gap, topic, question, agent)
    rec = {"ts": time.time(), "agent": agent, "topic": topic, "question": question, "why": why,
           "channel": channel or "", "job_run": job_run}
    await asyncio.to_thread(_log_question, rec)
    where = []
    if job_run and budget is not None:  # per run (job-<id>-<run>), never mixed with another run
        with _lock:
            budget.setdefault("questions", []).append(question_text(agent, topic, question, why, reply_here=False))
        where.append("added to this job run's result")
    if channel:
        try:
            from . import events
            from .chat import store

            ch = await asyncio.to_thread(store.get_channel, channel)
            member = bool(ch) and agent in ((ch or {}).get("members") or [])
            msg = await asyncio.to_thread(
                store.add_message, channel, "agent", agent,
                question_text(agent, topic, question, why, reply_here=member),
                {"question": {"topic": topic, "question": question, "why": why, "asked_by": agent,
                              "asked_at": time.time(), "open": member}})
            events.publish(channel, {"type": "message", "message": msg})
            where.append("posted in this chat" if member else
                         "posted in this chat, but you are not a member of it, so the user's reply will not "
                         "come to you here")
        except Exception as e:
            logger.warning("Could not post @%s's question in %s: %s", agent, channel, e)
    if not where:
        return ("There is no chat to post the question in here. Ask it in your reply instead; the topic was "
                "recorded as a knowledge gap for research.")
    logger.info("@%s asked the user about %s (%s)", agent, topic, ", ".join(where))
    return ("Question sent to the user (" + ", ".join(where) + "; the topic was recorded for research). "
            "Finish your reply by saying what you are waiting for; the answer will come in a later message.")


def open_questions(messages: list[dict[str, Any]], members: list[str], now: float | None = None,
                   ttl: float = QUESTION_TTL_S) -> list[dict[str, Any]]:
    """Unanswered agent questions in a channel's messages, newest first.

    Only questions of agents that are members of the channel count (a job of
    another agent posting into someone's DM never takes that DM over), at most
    ``ttl`` seconds old and not answered yet (``meta.question.answered``)."""
    now = now or time.time()
    out = []
    for m in reversed(messages):
        q = (m.get("meta") or {}).get("question") or {}
        if m.get("role") != "agent" or not q or q.get("answered") or q.get("open") is False:
            continue
        if m.get("sender") not in members:
            continue
        if now - float(q.get("asked_at") or m.get("created") or 0) > ttl:
            continue
        out.append(m)
    return out


def mark_answered(messages: list[dict[str, Any]], agent: str) -> list[int]:
    """Close the open questions of ``agent`` among ``messages`` (the user answered). Returns their ids."""
    from .chat import store

    done = []
    for m in messages:
        q = (m.get("meta") or {}).get("question") or {}
        if m.get("sender") == agent and q and not q.get("answered"):
            meta = dict(m.get("meta") or {})
            meta["question"] = {**q, "answered": True, "answered_at": time.time()}
            store.update_message(m["id"], None, meta)
            done.append(m["id"])
    return done


__all__ = ["create_skill", "assign_skill", "request_information", "open_questions", "mark_answered",
           "new_turn_budget", "strip_job_questions", "job_questions_text", "QUESTION_BUDGET",
           "MAX_QUESTIONS_PER_TURN", "QUESTION_TTL_S", "record_gap"]
