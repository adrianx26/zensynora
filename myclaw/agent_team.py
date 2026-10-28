"""Agents working as a team (v0.7.5): who else is there, delegating, creating agents.

* :func:`team_section` — part of every agent's system prompt: its level (1-9,
  a label only — 2026-10-27), what every agent can do itself (skills, tools,
  jobs, delegation, knowledge, asking the user — see ``myclaw.agent_caps``),
  the other agents and what each does, and how to hand them work
  (``delegate``) or create a new specialist (``create_agent``).
* :func:`create_agent` — tool: an agent adds a new named agent to the settings
  (name, what it does, prompt, model, skills). It is available at once — in the
  sidebar, in chat (``@name``) and for delegation — and can be changed or removed
  in Settings → Agents. Turn it off with ``agents.allow_agent_creation``.
* :data:`DELEGATION_DEPTH` — counts nested hand-overs so two agents cannot pass
  a task back and forth forever.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any, Callable

logger = logging.getLogger(__name__)

DELEGATION_DEPTH: ContextVar[int] = ContextVar("delegation_depth", default=0)
MAX_DELEGATION_DEPTH = 2
MAX_AGENTS = 30
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")

# Called with (name, agent) after create_agent; the Web UI adds it to its registry.
_on_created: list[Callable[[str, Any], None]] = []


def on_agent_created(fn: Callable[[str, Any], None]) -> None:
    if fn not in _on_created:
        _on_created.append(fn)


def _describe(config: Any, name: str) -> str:
    d = config.agents.defaults
    if name == "default":
        return f"the main agent, general tasks ({d.provider}/{d.model}, level {agent_level(config, name)})"
    for a in config.agents.named:
        if a.name == name:
            what = (a.description or "").strip() or (a.system_prompt or "").strip().split("\n")[0][:120]
            return (f"{what or 'no description'} ({a.provider or d.provider}/{a.model or d.model}, "
                    f"level {agent_level(config, name)})")
    return ""


def agent_level(config: Any, name: str) -> int:
    """The agent's level (1-9; 1 when unknown). A label only — see AgentDefaults.level."""
    try:
        if name == "default":
            return int(getattr(config.agents.defaults, "level", 1) or 1)
        for a in config.agents.named:
            if a.name == name:
                return int(getattr(a, "level", 1) or 1)
    except (AttributeError, TypeError, ValueError):
        pass
    return 1


def capabilities_lines(config: Any, has_others: bool) -> list[str]:
    """2026-10-27: what EVERY agent can do (level 1 included), told in its prompt."""
    jobs_on = bool(getattr(config.agents, "allow_job_creation", True))
    return [
        "What you can do yourself (every agent can, whatever its level):",
        "- create a skill (reusable instructions, shown on the Skills page): create_skill(name, description, "
        "instructions); give it to yourself with assign_skill(skill). Existing and built-in skills are never "
        "overwritten — pick another name.",
        "- create a tool: register_tool(name, code, documentation) — call list_toolbox() first and reuse a "
        "similar tool.",
        ("- schedule work: job_create(name, prompt, agent, schedule, …) makes a job on the Jobs page."
         if jobs_on else "- scheduling jobs is turned off by the user (agents.allow_job_creation)."),
        ("- delegate: delegate(agent_name, task) hands a task to another agent." if has_others
         else "- delegate: delegate(agent_name, task) once there are other agents."),
        "- add to the knowledge base: write_to_knowledge(title, content, tags, …) for facts worth keeping.",
        "- ask for more information: request_information(topic, question, why) posts your question to the "
        "user in this chat (or in the job's result) and records the topic for research. Use it when facts "
        "about a task, job, subject or domain are missing and your tools cannot find them; at most 3 "
        "questions per turn, then say what you are waiting for. The user's reply comes back to you.",
    ]


def team_section(config: Any, agent_name: str) -> str:
    """'Your team' part of the system prompt: level, capabilities, other agents."""
    names = ["default"] + [a.name for a in config.agents.named]
    others = [n for n in names if n != agent_name]
    can_create = bool(getattr(config.agents, "allow_agent_creation", True))
    jobs_on = bool(getattr(config.agents, "allow_job_creation", True))
    lines = ["## Your team"]
    # 2026-10-27: agent levels 1-9 — a label only for now (no permissions).
    lines.append(f"You are @{agent_name}, level {agent_level(config, agent_name)} of 9. The level is only a "
                 "label for now; it does not change what you may do.")
    lines += capabilities_lines(config, bool(others))
    if others:
        lines.append("Other agents you can hand work to:")
        lines += [f"- @{n}: {_describe(config, n)}" for n in others]
        lines.append(
            "To give one of them a task call delegate(agent_name, task). Write the task so it stands on its "
            "own (they do not see this conversation), wait for the answer, check it, then reply. Delegate "
            "when another agent fits the task better or the work splits into parts; do simple things yourself.")
    tg = getattr(getattr(config, "channels", None), "telegram", None)
    if tg is not None and getattr(tg, "enabled", False):
        # 2026-10-18: agents built their own Telegram tools with placeholder
        # tokens and asked the user for the bot token.
        lines.append(
            "To message the user on Telegram call send_telegram_message(text). ZenSynora's bot already "
            "has the token and the user's id — never ask for them and never build your own Telegram tool.")
    if agent_name.lower() == "medic":
        # 2026-10-28: only the medic proposes fusions (other agents get an error from the tool).
        lines.append(
            "As the medic you may propose fusing two agents that do the same work with "
            "propose_agent_fusion(agent_a, agent_b, new_name, reason); the user approves or rejects it.")
    if can_create:
        lines.append(
            "If a large or recurring kind of task needs a specialist nobody here is, you may call "
            "create_agent(name, description, system_prompt) — say in your reply that you did.")
    if jobs_on:
        # 2026-10-21: agents can use the Jobs page (before: only Telegram reminders).
        lines.append(
            "For work that should run later, repeatedly or in the background, create a job with "
            "job_create(name, prompt, agent, schedule, …) — it appears on the Jobs page, every run is "
            "recorded and the result is posted in this chat. job_list, job_run, job_status and "
            "job_update show, start, check and pause jobs. Tell the user which job you created.")
    return "\n".join(lines)


def _reload_prompts() -> None:
    """Agents cache their system prompt; make them rebuild it (the team changed)."""
    try:
        from .tools.core import _agent_registry

        for agent in list(_agent_registry.values()):
            if hasattr(agent, "_system_prompt_loaded"):
                agent._system_prompt_loaded = False
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("could not refresh agent prompts: %s", e)


def create_agent(name: str, description: str, system_prompt: str = "", model: str = "",
                 provider: str = "", skills: str = "", key: str = "") -> str:
    """Create a new named agent (a specialist) that you and the user can then talk to and delegate to.

    name: short unique name, letters/digits/_/- (e.g. Weather_Bot)
    description: one line saying what the agent does
    system_prompt: its instructions (role, how to work, limits); a sensible one is written if empty
    model: model to use (empty = the default model)
    provider: provider of that model (empty = the default provider)
    skills: comma-separated skill names to give it (see list_skills)
    key: id of the provider's API key to use (empty = the default key)
    """
    from .config import AppConfig, load_config, save_config
    from .events import CURRENT_AGENT, emit

    cfg = load_config()
    if not getattr(cfg.agents, "allow_agent_creation", True):
        return "Error: creating agents is turned off (Settings → Features → agents.allow_agent_creation)."
    name = (name or "").strip().lstrip("@").replace(" ", "_")
    if not NAME_RE.match(name):
        return "Error: the name must start with a letter and use 2-32 letters, digits, _ or - (e.g. Weather_Bot)."
    existing = ["default"] + [a.name for a in cfg.agents.named]
    if name.lower() in (n.lower() for n in existing):
        return f"Error: an agent called '{name}' already exists. Delegate to it instead, or pick another name."
    if len(cfg.agents.named) >= MAX_AGENTS:
        return f"Error: there are already {MAX_AGENTS} agents; ask the user to remove some first."
    description = (description or "").strip()[:300]
    if not description:
        return "Error: give a one-line description of what the agent does."
    from .provider import provider_names

    if provider and provider not in provider_names(cfg):
        return f"Error: unknown provider '{provider}'. Known: {', '.join(provider_names(cfg))}."
    from .provider_keys import key_ids, normalize_key_id

    key_id = normalize_key_id(key) or ""
    if key_id:
        prov_for_key = (provider or "").strip() or cfg.agents.defaults.provider
        known_keys = key_ids(cfg, prov_for_key)
        if key_id not in known_keys:
            others = [k for k in known_keys if k != "default"]
            return (f"Error: {prov_for_key} has no key '{key_id}'. "
                    + (f"Its keys: default, {', '.join(others)}." if others else "It only has its default key."))
    from .agent_skills import list_skills

    known_skills = {s["name"] for s in list_skills()}
    wanted = [s.strip() for s in (skills or "").split(",") if s.strip()]
    unknown = [s for s in wanted if s not in known_skills]
    if unknown:
        return f"Error: unknown skill(s) {', '.join(unknown)}. Use list_skills to see them."
    prompt = (system_prompt or "").strip() or (
        f"You are {name}. {description}\n"
        "Work step by step, use your tools when they help, say clearly when you are unsure, "
        "and keep answers short and concrete.")
    entry = {"name": name, "description": description, "system_prompt": prompt[:8000],
             "model": (model or "").strip() or cfg.agents.defaults.model, "provider": (provider or "").strip(),
             "skills": wanted, "key": key_id}

    from .config import raw_for_update

    raw = raw_for_update(cfg)  # 2026-10-28: never masked secrets on a fresh install
    raw.setdefault("agents", {}).setdefault("named", []).append(entry)
    try:
        AppConfig(**raw)
    except Exception as e:
        return f"Error: the new agent's settings are not valid: {str(e)[:300]}"
    save_config(raw)
    cfg = load_config(force_reload=True)

    creator = CURRENT_AGENT.get() or "default"
    try:
        from .agent import Agent
        from .tools.core import _agent_registry

        agent = Agent(cfg, name=name, model=entry["model"], system_prompt=prompt,
                      provider_name=entry["provider"] or None, key=key_id or None)
        _agent_registry[name] = agent
        for fn in _on_created:
            fn(name, agent)
    except Exception as e:  # saved; it appears after the next reload
        logger.warning("Agent %s saved but not started yet: %s", name, e)
    _reload_prompts()
    emit({"type": "agent_created", "from": creator, "to": name, "task": description})
    logger.info("Agent %s created by @%s", name, creator)
    return (f"Created agent @{name} ({entry['provider'] or cfg.agents.defaults.provider}/{entry['model']}): "
            f"{description}. It can be addressed as @{name} and given tasks with delegate('{name}', task). "
            "The user can change or remove it in Settings → Agents.")
