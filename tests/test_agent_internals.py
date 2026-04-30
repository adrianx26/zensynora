"""Tests for the extracted agent_internals helpers.

These tests prove the helpers can be exercised independently of a full
``Agent`` instance — by passing a stub object that implements only the
attributes each helper actually touches. That's the long-term win of the
decomposition: we can finally test these phases in isolation.

We don't aim for end-to-end coverage of every branch here (the full
``test_agent.py`` already exercises the integration). The point is to:
  1. Lock in the public contracts of the helpers.
  2. Catch regressions in the dependency surface (e.g., a future
     refactor accidentally adds a new ``self.X`` access).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import List

import pytest

from myclaw.agent_internals import (
    build_message_context,
    execute_tools,
    route_message,
)
from myclaw.agent_internals.medic_proxy import medic_loop_check


# ── medic_proxy ───────────────────────────────────────────────────────────


def test_medic_loop_check_returns_false_when_module_missing(monkeypatch):
    """Default behavior: medic absent → don't block requests."""
    import myclaw.agent_internals.medic_proxy as mp
    # Force the import to fail.
    import sys
    monkeypatch.setitem(sys.modules, "myclaw.agents.medic_agent", None)
    assert medic_loop_check() is False


def test_medic_loop_check_returns_true_when_limit_reached(monkeypatch):
    import myclaw.agent_internals.medic_proxy as mp

    class FakeMedic:
        @staticmethod
        def prevent_infinite_loop():
            return "Execution limit reached"

    import sys
    sys.modules["myclaw.agents.medic_agent"] = FakeMedic
    try:
        assert medic_loop_check() is True
    finally:
        sys.modules.pop("myclaw.agents.medic_agent", None)


def test_medic_loop_check_swallows_exceptions(monkeypatch):
    """If the medic check itself raises, requests must still proceed."""
    class BoomMedic:
        @staticmethod
        def prevent_infinite_loop():
            raise RuntimeError("medic exploded")

    import sys
    sys.modules["myclaw.agents.medic_agent"] = BoomMedic
    try:
        assert medic_loop_check() is False
    finally:
        sys.modules.pop("myclaw.agents.medic_agent", None)


# ── Helper signatures (cheap regression guard) ────────────────────────────


def test_route_message_accepts_expected_signature():
    sig = inspect.signature(route_message)
    assert list(sig.parameters)[:4] == ["agent", "user_message", "user_id", "_depth"]


def test_build_message_context_accepts_expected_signature():
    sig = inspect.signature(build_message_context)
    expected = ["agent", "user_message", "user_id", "mem", "history", "request_model"]
    assert list(sig.parameters) == expected


def test_execute_tools_accepts_expected_signature():
    sig = inspect.signature(execute_tools)
    expected = [
        "agent", "tool_calls", "messages", "user_message",
        "user_id", "mem", "_depth", "had_kb_results",
    ]
    assert list(sig.parameters) == expected


# ── Stub-driven route_message integration ─────────────────────────────────
#
# We build a minimal stand-in for Agent that satisfies just the attributes
# route_message touches. If a future refactor adds a new dependency, this
# test fails loudly in the same place every time.


class _StubMemory:
    def __init__(self):
        self.messages: List = []

    async def add(self, role, content):
        self.messages.append((role, content))

    async def get_history(self):
        return list(self.messages)


class _StubTaskTimer:
    def __init__(self):
        self.started = False
        self.completed = False
        self._active = True

    async def start_task_timer(self, **_kw):
        self.started = True

    async def complete_task(self, *_a, **_kw):
        self.completed = True
        self._active = False

    async def update_step(self, *_a, **_kw):
        pass

    def is_task_active(self, _id):
        return self._active


def _make_stub_agent():
    """Build the smallest possible Agent-shaped object route_message can drive."""
    mem = _StubMemory()
    timer = _StubTaskTimer()

    async def _get_memory(_user_id):
        return mem

    def _handle_status_update(*_a, **_kw):
        pass

    return SimpleNamespace(
        model="test-model",
        name="test-agent",
        _router=None,
        _task_timer=timer,
        _get_memory=_get_memory,
        _handle_task_status_update=_handle_status_update,
        _current_task_id=None,
        config=SimpleNamespace(agents=SimpleNamespace(summarization_threshold=10)),
    )


@pytest.mark.asyncio
async def test_route_message_returns_tuple_on_normal_path(monkeypatch):
    """Confirm the happy-path return shape is preserved by the extraction."""
    # Stub trigger_hook and the medic check so we don't pull in real deps.
    import myclaw.tools as tools_mod
    monkeypatch.setattr(tools_mod, "trigger_hook", lambda *a, **k: [])

    import myclaw.agent_internals.router as router_mod
    monkeypatch.setattr(router_mod, "__name__", router_mod.__name__)  # no-op
    monkeypatch.setattr(
        "myclaw.agent_internals.medic_proxy.medic_loop_check", lambda: False
    )

    agent = _make_stub_agent()
    result = await route_message(agent, "hello", "user1", _depth=0)

    assert result is not None
    request_model, mem, history, full_history_for_bg = result
    assert request_model == "test-model"
    assert isinstance(history, list)
    # Below threshold: no copy retained for background summarization.
    assert full_history_for_bg is None


@pytest.mark.asyncio
async def test_route_message_drops_when_depth_exceeded(monkeypatch):
    """Depth > 10 must short-circuit and complete the timer with a failure."""
    import myclaw.tools as tools_mod
    monkeypatch.setattr(tools_mod, "trigger_hook", lambda *a, **k: [])
    monkeypatch.setattr(
        "myclaw.agent_internals.medic_proxy.medic_loop_check", lambda: False
    )

    agent = _make_stub_agent()
    result = await route_message(agent, "hello", "user1", _depth=11)

    assert result is None
    assert agent._task_timer.completed


@pytest.mark.asyncio
async def test_route_message_drops_when_medic_blocks(monkeypatch):
    import myclaw.tools as tools_mod
    monkeypatch.setattr(tools_mod, "trigger_hook", lambda *a, **k: [])
    monkeypatch.setattr(
        "myclaw.agent_internals.medic_proxy.medic_loop_check", lambda: True
    )

    agent = _make_stub_agent()
    result = await route_message(agent, "hello", "user1", _depth=0)
    assert result is None
    assert agent._task_timer.completed


@pytest.mark.asyncio
async def test_route_message_records_summarization_when_history_exceeds_threshold(monkeypatch):
    """When history is over threshold, a snapshot must be captured for background work."""
    import myclaw.tools as tools_mod
    monkeypatch.setattr(tools_mod, "trigger_hook", lambda *a, **k: [])
    monkeypatch.setattr(
        "myclaw.agent_internals.medic_proxy.medic_loop_check", lambda: False
    )

    agent = _make_stub_agent()
    # Pre-seed memory with enough turns to exceed the threshold of 10.
    mem = await agent._get_memory("user1")
    for i in range(12):
        await mem.add("user", f"msg {i}")

    result = await route_message(agent, "hello", "user1", _depth=0)
    assert result is not None
    _, _, history, full_history_for_bg = result
    assert full_history_for_bg is not None
    assert len(full_history_for_bg) == len(history)
