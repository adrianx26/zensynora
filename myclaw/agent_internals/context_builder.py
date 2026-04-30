"""Context-assembly phase of Agent.think.

Owns: skill preloading kickoff, knowledge-base search, KB-gap detection
and structured logging, system prompt + KB context concatenation,
``pre_llm_call`` hook fan-out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import Memory

logger = logging.getLogger(__name__)
kb_gap_logger = logging.getLogger("myclaw.knowledge.gaps")


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
    kb_gap_hint = ""
    if not had_kb_results and agent._kb_gaps.get(user_id):
        last_gap = next(iter(agent._kb_gaps[user_id]))
        kb_gap_hint = (
            f"\n\n[Note: The knowledge base has no entries related to '{last_gap[:60]}'. "
            "Consider using write_to_knowledge() to store useful information for future queries.]"
        )

        # Emit a structured log event for the gap (deduplicated within a
        # short window via the agent's gap cache).
        if not agent._gap_cache.is_duplicate(last_gap, user_id):
            gap_data = {
                "event": "knowledge_gap_detected",
                "query": last_gap,
                "description": "No knowledge base entries found for query",
                "user_id": user_id,
                "session_context": (
                    "System will preserve context to avoid redundant empty "
                    "searches in this session"
                ),
                "timestamp": datetime.utcnow().isoformat(),
                "recommendation": (
                    "Use write_to_knowledge() to create a new entry for "
                    "future queries"
                ),
            }
            kb_gap_logger.info(gap_data)

            # Append to the researchers JSONL for later review.
            from ..agent import GAP_FILE  # module-level constant
            try:
                GAP_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(GAP_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(gap_data) + "\n")
            except Exception as e:
                logger.error(f"Failed to record gap to file: {e}")

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
