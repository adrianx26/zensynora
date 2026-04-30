"""Tests for the class-based agent_internals layer (Sprint 9 phase 2).

Locks in the new ``MessageRouter`` / ``ContextBuilder`` / ``ToolExecutor``
/ ``ResponseHandler`` shapes and proves they delegate to the same
behavior as the free-function helpers from Sprint 5.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import List

import pytest

from myclaw.agent_internals import (
    ContextBuilder,
    MessageRouter,
    ResponseHandler,
    ToolExecutor,
    build_message_context,
    execute_tools,
    route_message,
)


# ── Construction & signature shape ────────────────────────────────────────


def test_classes_constructible_with_only_a_target():
    """Each class accepts a single positional ``target`` (the Agent or any
    duck-typed equivalent). No other constructor args are required so a
    test stub can be a SimpleNamespace."""
    target = SimpleNamespace()
    MessageRouter(target)
    ContextBuilder(target)
    ToolExecutor(target)
    ResponseHandler(target)


@pytest.mark.parametrize("cls,method", [
    (MessageRouter, "route"),
    (ContextBuilder, "build"),
    (ToolExecutor, "execute"),
    (ResponseHandler, "handle"),
])
def test_class_methods_are_async(cls, method):
    """The contract of every phase is async; sync methods would silently
    swallow awaits when chained."""
    target = SimpleNamespace()
    inst = cls(target)
    assert inspect.iscoroutinefunction(getattr(inst, method))


def test_message_router_route_signature():
    sig = inspect.signature(MessageRouter.route)
    # Skip ``self``.
    params = list(sig.parameters)[1:]
    assert params == ["user_message", "user_id", "depth"]


def test_context_builder_build_signature():
    sig = inspect.signature(ContextBuilder.build)
    params = list(sig.parameters)[1:]
    assert params == ["user_message", "user_id", "mem", "history", "request_model"]


def test_tool_executor_execute_signature():
    sig = inspect.signature(ToolExecutor.execute)
    params = list(sig.parameters)[1:]
    assert params == [
        "tool_calls", "messages", "user_message", "user_id",
        "mem", "depth", "had_kb_results",
    ]


def test_response_handler_handle_signature():
    sig = inspect.signature(ResponseHandler.handle)
    params = list(sig.parameters)[1:]
    assert params == [
        "user_message", "response", "user_id", "mem", "full_history_for_bg",
    ]


# ── Delegation: classes call the same free function ───────────────────────
#
# Each class is currently a thin wrapper over the Sprint-5 free function.
# Verifying delegation now means a future refactor that inlines the body
# can swap implementations without breaking callers.


@pytest.mark.asyncio
async def test_message_router_delegates_to_route_message(monkeypatch):
    captured = {}

    async def fake(target, user_message, user_id, depth):
        captured["args"] = (target, user_message, user_id, depth)
        return ("modelX", "memY", [], None)

    monkeypatch.setattr("myclaw.agent_internals.classes._route_message", fake)
    target = SimpleNamespace()
    out = await MessageRouter(target).route("hi", "u1", depth=3)
    assert out == ("modelX", "memY", [], None)
    assert captured["args"] == (target, "hi", "u1", 3)


@pytest.mark.asyncio
async def test_context_builder_delegates(monkeypatch):
    captured = {}

    async def fake(target, user_message, user_id, mem, history, request_model):
        captured["args"] = (target, user_message, user_id, mem, history, request_model)
        return (["msg"], True, "")

    monkeypatch.setattr("myclaw.agent_internals.classes._build_message_context", fake)
    target = SimpleNamespace()
    out = await ContextBuilder(target).build("q", "u1", mem={}, history=[], request_model="m")
    assert out == (["msg"], True, "")
    assert captured["args"][0] is target


@pytest.mark.asyncio
async def test_tool_executor_delegates(monkeypatch):
    captured = {}

    async def fake(target, tool_calls, messages, user_message, user_id, mem, depth, had_kb_results):
        captured["args"] = (
            target, tool_calls, messages, user_message, user_id, mem, depth, had_kb_results,
        )
        return "final-response"

    monkeypatch.setattr("myclaw.agent_internals.classes._execute_tools", fake)
    target = SimpleNamespace()
    out = await ToolExecutor(target).execute(
        tool_calls=[], messages=[], user_message="q",
        user_id="u1", mem={}, depth=0, had_kb_results=False,
    )
    assert out == "final-response"
    assert captured["args"][0] is target


# ── ResponseHandler — real (not delegated) behavior tests ─────────────────


class _StubMemory:
    """Memory stub: just enough for ResponseHandler's get_history call."""
    def __init__(self, n: int = 3):
        self._messages = list(range(n))
    async def get_history(self):
        return self._messages


class _StubTaskTimer:
    def __init__(self):
        self.completed_with: List = []
    async def complete_task(self, task_id, **kwargs):
        self.completed_with.append((task_id, kwargs.get("success")))


def _make_response_handler_stub(
    *,
    kb_auto_extract: bool = False,
    should_extract_return: bool = False,
    has_task_id: bool = True,
):
    """Build a SimpleNamespace mimicking the Agent surface ResponseHandler uses."""
    extracted: List = []
    summarized: List = []
    tracked_tasks: List = []

    async def _extract(user_message, response, user_id):
        extracted.append((user_message, response, user_id))

    async def _summarize(history, user_id, mem):
        summarized.append(len(history))

    timer = _StubTaskTimer()
    agent = SimpleNamespace(
        name="test-agent",
        _kb_auto_extract=kb_auto_extract,
        _should_extract_knowledge=lambda u, r: should_extract_return,
        _extract_and_save_knowledge=_extract,
        _track_preload=lambda t: tracked_tasks.append(t),
        _background_summarize_context=_summarize,
        _task_timer=timer,
        _current_task_id="task-123" if has_task_id else None,
    )
    return agent, extracted, summarized, tracked_tasks, timer


@pytest.mark.asyncio
async def test_response_handler_skips_kb_extraction_when_disabled(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, extracted, _, tracked, _ = _make_response_handler_stub(
        kb_auto_extract=False,
    )
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(), full_history_for_bg=None,
    )
    assert extracted == []  # no extraction triggered
    assert tracked == []     # no fire-and-forget task scheduled


@pytest.mark.asyncio
async def test_response_handler_extracts_when_enabled_and_should(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, extracted, _, tracked, _ = _make_response_handler_stub(
        kb_auto_extract=True, should_extract_return=True,
    )
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(), full_history_for_bg=None,
    )
    # Give the spawned task a turn to run.
    await asyncio.sleep(0)
    assert extracted == [("q", "r", "u1")]
    assert len(tracked) == 1


@pytest.mark.asyncio
async def test_response_handler_skips_summarization_when_no_snapshot(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, _, summarized, _, _ = _make_response_handler_stub()
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(), full_history_for_bg=None,
    )
    await asyncio.sleep(0)
    assert summarized == []


@pytest.mark.asyncio
async def test_response_handler_runs_summarization_when_snapshot_present(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, _, summarized, tracked, _ = _make_response_handler_stub()
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(),
        full_history_for_bg=[{"role": "user", "content": str(i)} for i in range(20)],
    )
    await asyncio.sleep(0)
    assert summarized == [20]
    assert tracked  # at least one preload task tracked


@pytest.mark.asyncio
async def test_response_handler_completes_task_timer(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, _, _, _, timer = _make_response_handler_stub(has_task_id=True)
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(), full_history_for_bg=None,
    )
    assert timer.completed_with == [("task-123", True)]
    # The current_task_id must be cleared so the next request gets a fresh slot.
    assert agent._current_task_id is None


@pytest.mark.asyncio
async def test_response_handler_skips_timer_completion_when_no_task(monkeypatch):
    monkeypatch.setattr("myclaw.tools.trigger_hook", lambda *a, **k: [])
    agent, _, _, _, timer = _make_response_handler_stub(has_task_id=False)
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(), full_history_for_bg=None,
    )
    assert timer.completed_with == []  # never called


@pytest.mark.asyncio
async def test_response_handler_emits_session_end_hook(monkeypatch):
    captured: List = []

    def fake_trigger(name, *args, **kwargs):
        captured.append((name, args, kwargs))
        return []

    monkeypatch.setattr("myclaw.tools.trigger_hook", fake_trigger)
    agent, _, _, _, _ = _make_response_handler_stub()
    await ResponseHandler(agent).handle(
        "q", "r", "u1", _StubMemory(n=5), full_history_for_bg=None,
    )
    end_calls = [c for c in captured if c[0] == "on_session_end"]
    assert len(end_calls) == 1
    assert end_calls[0][1] == ("u1", "test-agent", 5)


@pytest.mark.asyncio
async def test_response_handler_handles_get_history_failure(monkeypatch):
    """If get_history raises, message_count falls back to 0 — but we
    still emit the hook and complete the timer rather than silently
    aborting cleanup."""
    captured: List = []
    monkeypatch.setattr(
        "myclaw.tools.trigger_hook",
        lambda *a, **k: captured.append(a) or [],
    )

    class BoomMemory:
        async def get_history(self):
            raise RuntimeError("db down")

    agent, _, _, _, timer = _make_response_handler_stub()
    await ResponseHandler(agent).handle(
        "q", "r", "u1", BoomMemory(), full_history_for_bg=None,
    )
    # Hook still fired with message_count=0.
    end_calls = [c for c in captured if c[0] == "on_session_end"]
    assert len(end_calls) == 1
    # Tuple shape: (hook_name, user_id, agent_name, message_count)
    assert end_calls[0][3] == 0
    # Timer still completed.
    assert timer.completed_with == [("task-123", True)]
