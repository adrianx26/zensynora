"""Tests for the shared cache primitives in ``myclaw.caching``."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from myclaw.caching import BaseTTLCache, PersistentCacheMixin, TTLEntry


# ── BaseTTLCache ──────────────────────────────────────────────────────────


def test_get_returns_none_on_miss():
    cache: BaseTTLCache[str] = BaseTTLCache()
    assert cache.get("nope") is None
    assert cache.misses == 1


def test_set_then_get_returns_value():
    cache: BaseTTLCache[str] = BaseTTLCache()
    cache.set("k", "v")
    assert cache.get("k") == "v"
    assert cache.hits == 1


def test_lru_eviction_when_at_capacity():
    cache: BaseTTLCache[int] = BaseTTLCache(max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # Evicts "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    assert cache.evictions >= 1


def test_set_replaces_existing_and_marks_mru():
    """Re-setting an existing key shouldn't evict another entry, AND it
    should mark the touched key as most-recently-used."""
    cache: BaseTTLCache[int] = BaseTTLCache(max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 99)  # Re-set; "a" becomes MRU. "b" is now LRU.
    cache.set("c", 3)   # Evicts "b" (LRU), keeps "a".
    assert cache.get("a") == 99
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_ttl_expiry_returns_none():
    cache: BaseTTLCache[str] = BaseTTLCache(ttl_seconds=0.05)
    cache.set("k", "v")
    time.sleep(0.06)
    assert cache.get("k") is None


def test_ttl_zero_means_no_expiry():
    cache: BaseTTLCache[str] = BaseTTLCache(ttl_seconds=0)
    cache.set("k", "v")
    time.sleep(0.05)
    assert cache.get("k") == "v"


def test_contains_respects_expiry():
    cache: BaseTTLCache[str] = BaseTTLCache(ttl_seconds=0.05)
    cache.set("k", "v")
    assert "k" in cache
    time.sleep(0.06)
    assert "k" not in cache


def test_pop_removes_and_returns():
    cache: BaseTTLCache[str] = BaseTTLCache()
    cache.set("k", "v")
    assert cache.pop("k") == "v"
    assert cache.get("k") is None


def test_clear_resets_stats():
    cache: BaseTTLCache[str] = BaseTTLCache()
    cache.set("a", "1")
    cache.get("a")
    cache.clear()
    assert len(cache) == 0
    assert cache.hits == 0
    assert cache.misses == 0


def test_lru_tail_yields_newest_first():
    cache: BaseTTLCache[int] = BaseTTLCache(max_size=10)
    for i in range(5):
        cache.set(f"k{i}", i)
    tail = list(cache.lru_tail(3))
    # Newest-first: k4, k3, k2.
    assert [k for k, _ in tail] == ["k4", "k3", "k2"]


def test_items_skips_expired(monkeypatch):
    cache: BaseTTLCache[str] = BaseTTLCache(ttl_seconds=0.05)
    cache.set("k", "v")
    time.sleep(0.06)
    assert list(cache.items()) == []


def test_constructor_rejects_bad_max_size():
    with pytest.raises(ValueError):
        BaseTTLCache(max_size=0)


def test_subclass_hooks_fire():
    """``_on_set`` / ``_on_evict`` let subclasses observe lifecycle events."""
    events = []

    class LoudCache(BaseTTLCache[int]):
        def _on_set(self, key, value):
            events.append(("set", key, value))
        def _on_evict(self, key, entry):
            events.append(("evict", key, entry.value))

    cache = LoudCache(max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # evicts "a"

    set_events = [e for e in events if e[0] == "set"]
    evict_events = [e for e in events if e[0] == "evict"]
    assert len(set_events) == 3
    assert ("evict", "a", 1) in evict_events


# ── PersistentCacheMixin ──────────────────────────────────────────────────


class _PersistedCache(PersistentCacheMixin, BaseTTLCache[str]):
    """Concrete persistent cache used by the tests below."""


def test_save_writes_json(tmp_path: Path):
    path = tmp_path / "cache.json"
    cache = _PersistedCache(persist_path=path)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    keys = {row["k"] for row in data}
    assert keys == {"a", "b"}


def test_save_skips_when_no_path():
    """No path configured ⇒ save() is a no-op, not a crash."""
    cache = _PersistedCache(persist_path=None)
    cache.set("a", "1")
    cache.save()  # must not raise


def test_load_if_present_restores(tmp_path: Path):
    path = tmp_path / "c.json"
    a = _PersistedCache(persist_path=path)
    a.set("a", "1")
    a.save()

    b = _PersistedCache(persist_path=path)
    n = b.load_if_present()
    assert n == 1
    assert b.get("a") == "1"


def test_load_if_present_idempotent(tmp_path: Path):
    path = tmp_path / "c.json"
    cache = _PersistedCache(persist_path=path)
    cache.set("a", "1")
    cache.save()
    cache2 = _PersistedCache(persist_path=path)
    cache2.load_if_present()
    second = cache2.load_if_present()  # no-op
    assert second == 0  # idempotent


def test_load_skips_expired_entries(tmp_path: Path):
    path = tmp_path / "c.json"
    cache = _PersistedCache(ttl_seconds=0.05, persist_path=path)
    cache.set("a", "1")
    cache.save()
    time.sleep(0.06)

    fresh = _PersistedCache(ttl_seconds=10.0, persist_path=path)
    n = fresh.load_if_present()
    # Expired entries from the file are dropped on load.
    assert n == 0
    assert fresh.get("a") is None


def test_load_handles_corrupt_file(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("{ not json", encoding="utf-8")
    cache = _PersistedCache(persist_path=path)
    n = cache.load_if_present()
    assert n == 0  # logged warning, no crash


def test_serialize_deserialize_hooks(tmp_path: Path):
    """Subclasses with non-JSON-native values use the hooks."""
    path = tmp_path / "c.json"

    class TupleCache(PersistentCacheMixin, BaseTTLCache[tuple]):
        def _serialize_value(self, value):
            return list(value)
        def _deserialize_value(self, raw):
            return tuple(raw)

    a = TupleCache(persist_path=path)
    a.set("k", (1, 2, 3))
    a.save()

    b = TupleCache(persist_path=path)
    b.load_if_present()
    assert b.get("k") == (1, 2, 3)
