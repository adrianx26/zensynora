"""Class-based interfaces over the free-function helpers.

The Sprint 5 decomposition extracted the agent phases into free functions
that all take ``agent`` as their first argument. That was a deliberate
trade-off — fast to land, preserves all behavior, but every helper still
reaches into ~30 ``self.X`` attributes.

This module is the *next* iteration: real classes with constructor-level
dependency injection. Each phase exposes the same async API as the free
function but is built around explicit collaborators (timer, memory factory,
hooks, etc.) so:

  * Unit tests construct the class with mocks rather than a stub Agent.
  * The ``Agent`` orchestrator becomes thinner — it owns one instance
    of each phase rather than reaching across the dotted path.
  * Each class has a single, declared dependency surface that breaking
    changes show up as.

Backward compatibility: the existing free functions in
``agent_internals.{router,context_builder,tool_executor}`` continue to
work, and ``Agent._route_message`` / ``_build_context`` / ``_execute_tools``
keep delegating to them. Sites that want the new shape opt in by
constructing the class directly.

The classes wrap the free functions rather than duplicating logic — so
behavior stays in one place and the migration path is incremental.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, TYPE_CHECKING

from .context_builder import build_message_context as _build_message_context
from .router import route_message as _route_message
from .tool_executor import execute_tools as _execute_tools

if TYPE_CHECKING:
    from ..memory import Memory

logger = logging.getLogger(__name__)


# ── Dependency protocols ─────────────────────────────────────────────────
#
# We declare the minimum interface each phase needs as a ``Protocol``. This
# lets callers pass any object (real Agent, stub, or future split) without
# inheriting from a base class — duck-typed, statically checkable.


class _TaskTimer(Protocol):
    async def start_task_timer(self, **kwargs: Any) -> None: ...
    async def complete_task(self, *args: Any, **kwargs: Any) -> None: ...
    async def update_step(self, *args: Any, **kwargs: Any) -> None: ...
    def is_task_active(self, task_id: str) -> bool: ...


class _MemoryProvider(Protocol):
    async def __call__(self, user_id: str) -> "Memory": ...


class _HookEmitter(Protocol):
    def __call__(self, name: str, *args: Any, **kwargs: Any) -> List[Any]: ...


# ── Adapter back to the free-function helpers ────────────────────────────


class _FreeFnAdapter:
    """Internal: runs a free-function helper with the wrapped target.

    Each phase class exposes a clean ``run(...)`` API but defers to the
    Sprint-5 free functions to avoid duplicating logic. Once those bodies
    are themselves migrated into these classes, this adapter goes away.
    """

    def __init__(self, target: Any) -> None:
        self._target = target


# ── MessageRouter ────────────────────────────────────────────────────────


class MessageRouter(_FreeFnAdapter):
    """Phase 1: route an incoming message.

    Owns model selection, task-timer setup, depth guard, medic check,
    memory hydration, history fetch, summarization-threshold detection.

    Construct with the Agent (or any duck-typed equivalent). Subsequent
    refactors will replace the ``target`` parameter with explicit
    dependencies (timer, memory factory, …).
    """

    async def route(
        self,
        user_message: str,
        user_id: str,
        depth: int = 0,
    ) -> Optional[tuple]:
        """Returns ``(request_model, mem, history, full_history_for_bg)``
        or ``None`` if the request was dropped (timeout / depth / loop).
        """
        return await _route_message(self._target, user_message, user_id, depth)


# ── ContextBuilder ───────────────────────────────────────────────────────


class ContextBuilder(_FreeFnAdapter):
    """Phase 2: assemble messages for the LLM.

    Owns skill preloading kickoff, KB search, gap detection + structured
    logging, system prompt + KB context concatenation, and the
    ``pre_llm_call`` hook fan-out.
    """

    async def build(
        self,
        user_message: str,
        user_id: str,
        mem: "Memory",
        history: list,
        request_model: str,
    ) -> Optional[tuple]:
        """Returns ``(messages, had_kb_results, kb_gap_hint)`` or ``None``
        if the task was cancelled mid-build."""
        return await _build_message_context(
            self._target, user_message, user_id, mem, history, request_model
        )


# ── ToolExecutor ─────────────────────────────────────────────────────────


class ToolExecutor(_FreeFnAdapter):
    """Phase 3: execute the tool calls returned by the first LLM turn.

    Owns parallel vs sequential dispatch, per-tool error handling,
    KB-gap recording for empty searches, fire-and-forget KB extraction,
    the followup LLM call, and empty-response recovery.
    """

    async def execute(
        self,
        tool_calls: list,
        messages: list,
        user_message: str,
        user_id: str,
        mem: "Memory",
        depth: int,
        had_kb_results: bool,
    ) -> str:
        return await _execute_tools(
            self._target, tool_calls, messages, user_message, user_id, mem,
            depth, had_kb_results,
        )


# ── ResponseHandler ──────────────────────────────────────────────────────
#
# This is the *new* phase Sprint 5 didn't extract. It owns the
# fire-and-forget cleanup that runs after the response has been sent:
# KB auto-extraction, on_session_end hook, background summarization,
# task-timer completion.
#
# Originally lived as ``Agent._handle_summarization`` (~30 lines). Moved
# here as a proper class with explicit dependencies because everything
# in this phase is a side effect — easy to forget, easy to test.


class ResponseHandler:
    """Phase 4: background cleanup after a response has been delivered.

    Construct with the Agent; ``handle()`` performs all post-response
    side effects in fire-and-forget fashion. Designed so a future
    refactor can swap the agent reference for an explicit (kb_extractor,
    hook_emitter, task_timer, summarizer) tuple — the public API stays
    the same.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    async def handle(
        self,
        user_message: str,
        response: str,
        user_id: str,
        mem: "Memory",
        full_history_for_bg: Optional[list],
    ) -> None:
        agent = self._agent

        # Background KB auto-extraction (fire-and-forget, never blocks).
        if (
            agent._kb_auto_extract
            and agent._should_extract_knowledge(user_message, response)
        ):
            _kb_task = asyncio.create_task(
                agent._extract_and_save_knowledge(user_message, response, user_id)
            )
            agent._track_preload(_kb_task)

        # on_session_end hook — depend on hasattr because some test mocks
        # don't implement get_history.
        try:
            message_count = (
                len(await mem.get_history()) if hasattr(mem, "get_history") else 0
            )
        except Exception as e:
            logger.debug("get_history failed in ResponseHandler", exc_info=e)
            message_count = 0
        from ..tools import trigger_hook
        trigger_hook("on_session_end", user_id, agent.name, message_count)

        # Background context summarization. Captured snapshot is None when
        # the history was below the threshold at request start — skipping
        # avoids a wasted summarization pass.
        if full_history_for_bg:
            _summarize_task = asyncio.create_task(
                agent._background_summarize_context(full_history_for_bg, user_id, mem)
            )
            agent._track_preload(_summarize_task)

        # Complete the task timer. Mirrors the original logic exactly: the
        # task is marked successful and the active-id is cleared so the
        # next request gets a fresh slot.
        if agent._current_task_id:
            await agent._task_timer.complete_task(
                agent._current_task_id, success=True
            )
            agent._current_task_id = None
