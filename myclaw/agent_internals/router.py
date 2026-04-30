"""Message-routing phase of Agent.think.

Owns: model selection (intelligent router), task-timer setup, depth
guard, medic loop-prevention check, memory hydration, history fetch,
summarization-threshold detection.

Returns a 4-tuple consumed by ``Agent.think`` or ``None`` on early-exit
(timeout, depth limit, medic block).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import Memory

logger = logging.getLogger(__name__)


async def route_message(
    agent: "Agent",
    user_message: str,
    user_id: str,
    _depth: int,
) -> Optional[Tuple[str, "Memory", list, Optional[list]]]:
    """Set up request routing, task timer, and guardrails.

    Returns:
        ``(request_model, mem, history, _full_history_for_bg)`` or
        ``None`` if the request should be dropped (depth, timeout, loop).
    """
    # Update the module-level "last active" timestamp on the agent module.
    # We mutate via the module rather than a parameter so the existing
    # `get_last_active_time` global still reads the same value.
    from .. import agent as _agent_module
    _agent_module._LAST_ACTIVE_TIME = time.time()

    # Intelligent Routing: Determine model for THIS request only.
    request_model = agent.model
    if agent._router:
        routed_model = agent._router.get_routing_decision(user_message, agent.model)
        if routed_model:
            request_model = routed_model

    # Generate a unique task id for the timer.
    agent._current_task_id = (
        f"task_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    )

    await agent._task_timer.start_task_timer(
        task_id=agent._current_task_id,
        user_question=user_message,
        on_status_update=agent._handle_task_status_update,
        steps_total=5,  # memory, knowledge, LLM, tools, response
    )

    # Loop-prevention: hard depth cap.
    if _depth > 10:
        logger.warning(
            f"Max delegation depth reached ({_depth}). "
            "Preventing potential infinite loop."
        )
        await agent._task_timer.complete_task(
            agent._current_task_id,
            success=False,
            error_message="Max delegation depth reached",
        )
        return None

    # Medic agent: secondary loop-prevention based on global execution count.
    try:
        from .medic_proxy import medic_loop_check
        blocked = medic_loop_check()
        if blocked:
            logger.warning("Execution limit reached by loop prevention")
            await agent._task_timer.complete_task(
                agent._current_task_id,
                success=False,
                error_message="Loop prevention limit reached",
            )
            return None
    except Exception:
        # Medic agent is optional infrastructure — never fail-open or
        # fail-closed loudly here; just continue.
        pass

    # Timer may have already fired between scheduling and now.
    if agent._current_task_id and not agent._task_timer.is_task_active(
        agent._current_task_id
    ):
        logger.warning(f"Task {agent._current_task_id} was cancelled or timed out")
        return None

    mem = await agent._get_memory(user_id)
    await mem.add("user", user_message)

    await agent._task_timer.update_step(agent._current_task_id, "memory_loading", 1, 5)

    # on_session_start hook (registered via myclaw.tools.trigger_hook).
    from ..tools import trigger_hook
    trigger_hook("on_session_start", user_id, agent.name)

    history = await mem.get_history()

    # Background summarization decision: capture history snapshot only if
    # we'll actually use it (avoids a pointless copy every request).
    threshold = getattr(agent.config.agents, "summarization_threshold", 10)
    should_summarize_after = len(history) > threshold
    full_history_for_bg = history.copy() if should_summarize_after else None

    return request_model, mem, history, full_history_for_bg
