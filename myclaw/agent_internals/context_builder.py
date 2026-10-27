"""Context-assembly phase of Agent.think.

Owns: skill preloading kickoff, knowledge-base search, KB-gap detection
and structured logging, system prompt + KB context concatenation,
``pre_llm_call`` hook fan-out.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import Memory

logger = logging.getLogger(__name__)
kb_gap_logger = logging.getLogger("myclaw.knowledge.gaps")  # kept for importers


async def build_message_context(
    agent: "Agent",
    user_message: str,
    user_id: str,
    mem: "Memory",
    history: list,
    request_model: str,
) -> Optional[Tuple[list, bool, str]]:
    """Build the message context: knowledge search + system prompt + hooks.

    Returns:
        ``(messages, had_kb_results, kb_gap_hint)`` or ``None`` if the
        task was cancelled mid-build.
    """
    # Optimization #4: kick off proactive skill pre-loading in parallel.
    task = asyncio.create_task(
        agent._skill_preloader.predict_and_preload(history, user_message)
    )
    agent._track_preload(task)

    # Knowledge-base search (semantic + BM25 depending on backend).
    knowledge_context = await agent._search_knowledge_context(user_message, user_id)
    had_kb_results = bool(knowledge_context)

    # If KB was empty AND we already noted a gap for this user, include a
    # one-line hint so the model knows to call write_to_knowledge().
    # 2026-10-23 (KB phase 0): Agent._kb_gap_hint (shared with stream_think)
    # uses the newest gap and really writes it to GAP_FILE — the old code here
    # de-duplicated with the cache _record_kb_gap had just filled, so the gap
    # file stayed empty; off when knowledge.enabled is False.
    kb_gap_hint = agent._kb_gap_hint(user_id, had_kb_results)

    # Bail if the task timer fired between scheduling and here.
    if agent._current_task_id and not agent._task_timer.is_task_active(
        agent._current_task_id
    ):
        logger.warning(f"Task {agent._current_task_id} was cancelled or timed out")
        return None

    if agent._current_task_id:
        await agent._task_timer.update_step(
            agent._current_task_id, "building_prompt", 2, 5
        )

    # System prompt = profile + (optional) knowledge context + (optional) gap hint.
    system_prompt = await agent._load_system_prompt()
    system_content = system_prompt
    if knowledge_context:
        system_content = f"{system_prompt}\n\n{knowledge_context}"
    if kb_gap_hint:
        system_content = f"{system_content}{kb_gap_hint}"

    messages = [{"role": "system", "content": system_content}] + history

    # Context length of this agent (Settings → Agents): leave out the oldest
    # messages when the turn would not fit.
    from ..context_budget import agent_context_length, fit_messages
    from ..config import load_config

    messages, _ = fit_messages(messages, agent_context_length(load_config(), agent.name))

    # ``pre_llm_call`` hooks may rewrite the message list (used by, e.g.,
    # PII filters). Last-write-wins is intentional and matches the
    # original behavior.
    from ..tools import trigger_hook
    hook_results = trigger_hook("pre_llm_call", messages, request_model)
    for result in hook_results:
        if result and isinstance(result, list):
            messages = result
            logger.debug("pre_llm_call hook modified messages")

    return messages, had_kb_results, kb_gap_hint
