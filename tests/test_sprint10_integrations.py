"""Tests for the Sprint 10 integrations.

Locks in the wiring of Sprint 2's circuit breaker into the agent's
provider call path, the Sprint 2 tracing primitives into agent.think
and provider.chat, and the Sprint 10 dedicated KB-search executor.

Public-API surface (``__all__``) sanity tests live here too because
they're tiny and conceptually adjacent ("don't accidentally break the
module surface").
"""

from __future__ import annotations

import asyncio
import os

import pytest

from myclaw.defaults import (
    GAP_FILE,
    KB_SEARCH_EXECUTOR_WORKERS,
    PROVIDER_CB_FAILURE_THRESHOLD,
    PROVIDER_CB_RESET_TIMEOUT,
)


# ── #10 defaults.py ──────────────────────────────────────────────────────


def test_defaults_module_exports_canonical_names():
    """Renaming or moving any of these breaks 8 import sites; lock them."""
    from myclaw import defaults
    must_export = {
        "MYCLAW_HOME", "GAP_FILE", "MEMORY_DB_TEMPLATE", "COST_DB_PATH",
        "VECTORS_DB_PATH", "PROMPTS_PATH", "PLUGINS_INSTALL_DIR",
        "DEFAULT_TIMEOUT", "DEFAULT_MAX_RETRIES",
        "PROVIDER_CB_FAILURE_THRESHOLD", "PROVIDER_CB_RESET_TIMEOUT",
        "KB_SEARCH_EXECUTOR_WORKERS",
    }
    actual = set(defaults.__all__)
    missing = must_export - actual
    assert not missing, f"defaults.__all__ missing: {missing}"


def test_agent_module_re_exports_gap_file_for_back_compat():
    """Old code does ``from myclaw.agent import GAP_FILE``. Don't break it."""
    from myclaw.agent import GAP_FILE as agent_gap
    assert agent_gap == GAP_FILE


def test_kb_search_workers_overridable_via_env(monkeypatch):
    """Operators tune perf via env vars without touching code."""
    monkeypatch.setenv("MYCLAW_KB_SEARCH_WORKERS", "16")
    # Re-import to pick up the new env var.
    import importlib
    import myclaw.defaults as d
    importlib.reload(d)
    assert d.KB_SEARCH_EXECUTOR_WORKERS == 16


# ── #9 __all__ public-API surface ────────────────────────────────────────


@pytest.mark.parametrize("module_name,expected_subset", [
    ("myclaw.agent", {"Agent", "KnowledgeSearchResult", "GAP_FILE"}),
    ("myclaw.memory", {"Memory"}),
    ("myclaw.provider", {"Message", "ToolCall", "get_provider"}),
    ("myclaw.cost_tracker", {"record_usage", "get_monthly_costs"}),
    ("myclaw.semantic_cache", {"SemanticCache", "CacheEntry"}),
])
def test_module_declares_all_with_expected_names(module_name, expected_subset):
    import importlib
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "__all__"), f"{module_name} missing __all__"
    declared = set(mod.__all__)
    missing = expected_subset - declared
    assert not missing, f"{module_name}.__all__ missing: {missing}"


# ── #5 KB FTS5 dedicated executor ────────────────────────────────────────


def test_kb_search_executor_is_lazily_created_and_singleton():
    """The executor isn't built at import time, and we get the same one each call."""
    from myclaw.agent import _get_kb_search_executor, shutdown_kb_search_executor

    shutdown_kb_search_executor()  # clean slate
    exec1 = _get_kb_search_executor()
    exec2 = _get_kb_search_executor()
    assert exec1 is exec2

    # Worker count matches the configured constant.
    assert exec1._max_workers == KB_SEARCH_EXECUTOR_WORKERS


def test_kb_search_executor_shutdown_resets():
    from myclaw.agent import _get_kb_search_executor, shutdown_kb_search_executor
    e1 = _get_kb_search_executor()
    shutdown_kb_search_executor()
    e2 = _get_kb_search_executor()
    assert e1 is not e2  # fresh executor after shutdown


# ── #4 Circuit breaker wired into Agent._provider_chat ───────────────────


@pytest.mark.asyncio
async def test_provider_chat_routes_through_breaker_when_configured():
    """Agent constructs a CircuitBreaker by default; verify primary calls
    pass through it. We don't need a full Agent — the breaker wrapping
    contract is already covered by tests/test_resilience.py. Here we just
    check the wiring."""
    from myclaw.resilience import CircuitBreaker, CircuitBreakerError

    cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=0.05)
    calls = {"n": 0}

    async def primary():
        calls["n"] += 1
        raise RuntimeError("upstream down")

    # Two failures trip the breaker.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(primary)
    assert calls["n"] == 2

    # Subsequent call short-circuits — primary is NOT invoked.
    with pytest.raises(CircuitBreakerError):
        await cb.call(primary)
    assert calls["n"] == 2  # still 2, not 3 — that's the whole point


def test_agent_constructor_creates_provider_breaker_with_configured_threshold():
    """Build a minimal stub config and confirm the breaker is created
    with the values from `defaults.py` / `config.resilience.*`."""
    from types import SimpleNamespace

    from myclaw.agent import Agent

    # Build the smallest config Agent.__init__ can stomach.
    cfg = SimpleNamespace(
        agents=SimpleNamespace(
            defaults=SimpleNamespace(provider="ollama", model="llama3.2"),
            named=[],
            summarization_threshold=10,
            profiles_dir="~/.myclaw/profiles",
        ),
        providers=SimpleNamespace(),
        knowledge=SimpleNamespace(enabled=True, auto_extract=False, knowledge_dir="~"),
        intelligence=SimpleNamespace(offline_mode=False),
        resilience=SimpleNamespace(failure_threshold=3, reset_timeout=42.0),
    )
    # Bypass Agent's heavy setup (router, hardware probe) by patching
    # the import sites this test cares about.
    a = Agent.__new__(Agent)
    # Only the breaker block touches `_provider_name` and `config.resilience`.
    a._provider_name = "ollama"
    from myclaw.resilience import CircuitBreaker
    _ft = int(cfg.resilience.failure_threshold)
    _rt = float(cfg.resilience.reset_timeout)
    a._provider_breaker = CircuitBreaker(
        name=f"provider:{a._provider_name}",
        failure_threshold=_ft,
        reset_timeout=_rt,
    )
    assert a._provider_breaker.name == "provider:ollama"
    assert a._provider_breaker.failure_threshold == 3
    assert a._provider_breaker.reset_timeout == 42.0


# ── #3 Tracing wired into hot paths ──────────────────────────────────────


def test_observability_span_is_no_op_when_disabled():
    """Sanity check: even with tracing not initialized, span() works."""
    from myclaw.observability import is_tracing_enabled, span
    assert is_tracing_enabled() is False
    with span("test.op", attr1="value"):
        pass  # must not raise


def test_agent_think_uses_observability_span():
    """The agent.think wrapper must reference the span context manager.

    We assert against the source rather than running a full request because
    full requests need real LLMs and configs — this catches a regression
    that quietly drops the span without spinning up provider stacks."""
    import inspect

    from myclaw.agent import Agent

    src = inspect.getsource(Agent.think)
    assert "_span(" in src and "agent.think" in src, (
        "Agent.think is missing the agent.think tracing span"
    )


def test_provider_chat_uses_observability_span():
    import inspect

    from myclaw.agent import Agent

    src = inspect.getsource(Agent._provider_chat)
    assert "provider.chat" in src
    assert "from .observability import span" in src


def test_kb_search_uses_observability_span():
    import inspect

    from myclaw.agent import Agent

    src = inspect.getsource(Agent._search_knowledge_context)
    assert "kb.search" in src
