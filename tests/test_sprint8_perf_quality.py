"""Tests for Sprint 8 cleanup items.

These are point-tests for the specific changes that closed out the
original 16-point app review. Larger surfaces (semantic cache full
behavior, memory schema migrations) are covered by their own files
elsewhere; here we just lock in the diffs.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import pytest


# ── semantic_cache vectorized scan ────────────────────────────────────────


def test_semantic_cache_default_max_scan_raised_to_256():
    """Sprint 8 raised the scan cap from 64 → 256. Hardcoded so a
    regression that quietly drops it back is caught."""
    from myclaw.semantic_cache import SemanticCache
    cache = SemanticCache()
    assert cache.max_scan_entries == 256


def test_semantic_cache_vectorized_scan_finds_best_match(monkeypatch, tmp_path):
    """Build a cache with a few vectors, query, verify the closest one
    wins. Bypass the embedding model by directly constructing entries."""
    from myclaw.semantic_cache import SemanticCache, CacheEntry

    cache = SemanticCache(cache_dir=tmp_path, similarity_threshold=0.5)

    # Stub _get_embedding so we control the query vector exactly.
    target_vec = np.array([1.0, 0.0, 0.0])
    near_vec = np.array([0.99, 0.0, 0.1])
    far_vec = np.array([0.0, 1.0, 0.0])

    def fake_embed(_text):
        return target_vec
    monkeypatch.setattr(cache, "_get_embedding", fake_embed)
    cache._embedding_model = object()  # any truthy non-None — the code only checks identity

    # Inject three entries directly. Hash keys don't matter for similarity scan.
    import time
    for key, vec, response in [
        ("k_far", far_vec, "wrong"),
        ("k_near", near_vec, "right"),
    ]:
        cache._cache[key] = CacheEntry(
            query_embedding=vec,
            response=response,
            tool_calls=None,
            timestamp=time.time(),
            access_count=0,
            query_hash=key,
        )

    # Query with a message list — content goes through the same path
    # as a real lookup. The hash won't exact-match, so we exercise the
    # vectorized similarity branch.
    out = cache.get([{"role": "user", "content": "anything"}], model="m")
    assert out is not None
    response, _tools = out
    assert response == "right"


# ── memory.py composite index ─────────────────────────────────────────────


def test_memory_schema_includes_composite_index_ddl():
    """The Memory class must emit a (role, timestamp) composite index in
    its schema-init code path. We assert against the source rather than
    spinning up a real Memory instance because the AsyncSQLitePool has
    test-environment-sensitive locking; the goal here is to lock in the
    DDL string so a future refactor can't quietly drop it."""
    import inspect

    from myclaw.memory import Memory

    src = inspect.getsource(Memory.initialize)
    assert "idx_role_timestamp" in src, (
        "Memory.initialize is missing the (role, timestamp) composite index"
    )
    assert "messages(role, timestamp)" in src, (
        "Composite index must reference (role, timestamp) in that order"
    )


# ── graph.py build_context batch reads ────────────────────────────────────


def test_graph_build_context_batches_depth1_reads(monkeypatch):
    """Smoke-test that build_context routes its depth-1 note loads
    through ``_batch_read_notes``. We don't simulate a full DB — just
    monkey-patch the helper and assert it's called once with all the
    permalinks rather than N times one-by-one."""
    from myclaw.knowledge import graph as graph_mod

    # Stub the underlying read_note + related-entity walker.
    fake_main = type("N", (), {
        "title": "Main", "observations": [],
    })()
    monkeypatch.setattr(graph_mod, "read_note", lambda perm, uid: fake_main)
    monkeypatch.setattr(
        graph_mod,
        "get_related_entities",
        lambda perm, uid, depth: [
            {"depth": 1, "permalink": "x", "name": "X", "relation_type": "links"},
            {"depth": 1, "permalink": "y", "name": "Y", "relation_type": "links"},
            {"depth": 2, "permalink": "z", "name": "Z", "relation_type": "links"},
        ],
    )

    calls = {"count": 0, "args": None}

    def fake_batch(perms, uid):
        calls["count"] += 1
        calls["args"] = list(perms)
        return []

    # storage._batch_read_notes is imported lazily inside build_context.
    monkeypatch.setattr(
        "myclaw.knowledge.storage._batch_read_notes",
        fake_batch,
    )

    out = graph_mod.build_context("main", user_id="u1", depth=2)
    assert "Main" in out
    # Exactly one batched call for the two depth-1 neighbors.
    assert calls["count"] == 1
    assert sorted(calls["args"]) == ["x", "y"]


# ── agent.py broad-except logging ─────────────────────────────────────────


def test_kb_auto_extract_logs_once_on_failure(caplog):
    """The `_kb_auto_extract` property must log on the first config-read
    failure but not spam the logs on repeated reads."""
    from myclaw.agent import Agent

    # Build a minimal Agent shell that will trip the except branch.
    # We avoid the real __init__ since it loads providers etc.
    a = Agent.__new__(Agent)

    # Config that raises on attribute access — simulates a misconfigured deploy.
    class BoomConfig:
        @property
        def knowledge(self):
            raise RuntimeError("config not loaded")

    a.config = BoomConfig()  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger="myclaw.agent"):
        # First read: should log.
        assert a._kb_auto_extract is False
        # Second read: should NOT add another warning.
        assert a._kb_auto_extract is False

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "auto_extract" in warnings[0].message


# ── provider.py Message TypedDict export ─────────────────────────────────


def test_message_typeddict_is_exported():
    """Static-analysis tools and downstream modules can import the new
    types — verifies they're at module level, not nested."""
    from myclaw.provider import Message, ToolCall, ToolCallFunction
    # TypedDict instances behave like dicts at runtime; constructing one
    # with the expected keys must not raise.
    m: Message = {"role": "user", "content": "hi"}
    tc: ToolCall = {"id": "abc", "type": "function", "function": {"name": "x", "arguments": "{}"}}
    assert m["role"] == "user"
    assert tc["function"]["name"] == "x"
