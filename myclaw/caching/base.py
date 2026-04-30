"""Abstract caching primitives shared by ``semantic_cache`` and
``semantic_memory``.

Design notes:

* ``BaseTTLCache`` is **not** a subclass of dict. We expose ``get``, ``set``,
  ``pop``, ``__len__`` so the public surface is small and explicit; the
  underlying ``OrderedDict`` is private. This avoids the trap where
  ``cache["k"] = v`` skips TTL bookkeeping.

* TTL is per-cache, not per-entry. Per-entry TTLs are easy to add later
  by overriding ``_compute_expiry``; we don't ship them yet because no
  current caller needs them.

* Eviction order is LRU (newest at the right end of the OrderedDict).
  This matches what ``SemanticCache`` did before the extraction; the
  Sprint 8 vectorized scan iterates the LRU tail, which depends on this
  ordering invariant.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Iterator, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)


V = TypeVar("V")


@dataclass
class TTLEntry(Generic[V]):
    """Single cache entry. ``expires_at == 0`` ⇒ never expires."""
    value: V
    expires_at: float
    access_count: int = 0


# ── BaseTTLCache ─────────────────────────────────────────────────────────


class BaseTTLCache(Generic[V]):
    """OrderedDict-backed TTL cache with LRU eviction.

    Args:
        max_size: Hard upper bound on the number of entries.
        ttl_seconds: Entry lifetime. ``0`` ⇒ infinite (use sparingly).

    Subclasses customize behavior by overriding:

    * ``_on_set`` / ``_on_evict`` for telemetry.
    * ``_compute_expiry`` for per-entry TTL.
    """

    def __init__(self, max_size: int = 256, ttl_seconds: float = 3600.0) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._ttl = float(ttl_seconds)
        self._store: "OrderedDict[str, TTLEntry[V]]" = OrderedDict()
        self._lock = threading.RLock()

        # Public stats — useful enough that hand-rolling them in every
        # caller was tedious. Subclasses can extend.
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0

    # ── Hooks subclasses may override ─────────────────────────────────

    def _compute_expiry(self) -> float:
        """Return the absolute time after which a fresh entry expires."""
        return 0.0 if self._ttl == 0 else (time.time() + self._ttl)

    def _on_set(self, key: str, value: V) -> None:
        """Hook fired after a successful set. Default no-op."""

    def _on_evict(self, key: str, entry: TTLEntry[V]) -> None:
        """Hook fired when an entry is evicted (TTL expiry or LRU). Default no-op."""

    # ── Core operations ───────────────────────────────────────────────

    def get(self, key: str) -> Optional[V]:
        """Return the value for ``key`` or ``None`` if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            if self._is_expired(entry):
                self._store.pop(key, None)
                self._on_evict(key, entry)
                self.evictions += 1
                self.misses += 1
                return None
            entry.access_count += 1
            self._store.move_to_end(key)  # mark MRU
            self.hits += 1
            return entry.value

    def set(self, key: str, value: V) -> None:
        """Insert or replace ``key``. Evicts the LRU entry when at capacity."""
        with self._lock:
            if key in self._store:
                # Replace in place; move to end to mark MRU.
                self._store[key] = TTLEntry(value, self._compute_expiry())
                self._store.move_to_end(key)
            else:
                if len(self._store) >= self._max_size:
                    # Evict expired first; if still at capacity, drop oldest.
                    self._sweep_expired()
                    while len(self._store) >= self._max_size:
                        old_key, old_entry = self._store.popitem(last=False)
                        self._on_evict(old_key, old_entry)
                        self.evictions += 1
                self._store[key] = TTLEntry(value, self._compute_expiry())
            self._on_set(key, value)

    def pop(self, key: str) -> Optional[V]:
        with self._lock:
            entry = self._store.pop(key, None)
            return entry.value if entry is not None else None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0
            self.evictions = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if self._is_expired(entry):
                return False
            return True

    # ── Iteration helpers ─────────────────────────────────────────────

    def items(self) -> Iterator[Tuple[str, V]]:
        """Yield non-expired (key, value) pairs in LRU order (oldest first)."""
        with self._lock:
            snapshot = list(self._store.items())
        for k, e in snapshot:
            if not self._is_expired(e):
                yield k, e.value

    def lru_tail(self, n: int) -> Iterator[Tuple[str, TTLEntry[V]]]:
        """Yield the ``n`` most recently used entries (newest first).

        Used by the semantic cache's vectorized scan.
        """
        with self._lock:
            snapshot = list(self._store.items())
        for k, e in reversed(snapshot[-n:]):
            if not self._is_expired(e):
                yield k, e

    # ── Internals ─────────────────────────────────────────────────────

    def _is_expired(self, entry: TTLEntry[V]) -> bool:
        return entry.expires_at != 0 and time.time() > entry.expires_at

    def _sweep_expired(self) -> None:
        """Remove every expired entry. Called inside the lock."""
        expired = [k for k, e in self._store.items() if self._is_expired(e)]
        for k in expired:
            entry = self._store.pop(k)
            self._on_evict(k, entry)
            self.evictions += 1


# ── PersistentCacheMixin ─────────────────────────────────────────────────


class PersistentCacheMixin:
    """Mix into a :class:`BaseTTLCache` subclass to add JSON file persistence.

    Subclasses provide ``_serialize_value`` / ``_deserialize_value`` so the
    cache can store types JSON can't natively round-trip (numpy arrays,
    dataclasses…). Default implementations are pass-through.

    Usage::

        class MyCache(PersistentCacheMixin, BaseTTLCache[MyValue]):
            def _serialize_value(self, v): return v.to_dict()
            def _deserialize_value(self, raw): return MyValue.from_dict(raw)

    Save is fire-and-forget by default — call :meth:`save` explicitly to
    persist. Loading happens once on first ``load_if_present`` call so
    constructors stay cheap.
    """

    persist_path: Optional[Path]

    def __init__(self, *args: Any, persist_path: Optional[Path] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[misc]
        self.persist_path = Path(persist_path) if persist_path else None
        self._loaded = False

    # Override these in subclasses for non-trivial value types.

    def _serialize_value(self, value: Any) -> Any:
        return value

    def _deserialize_value(self, raw: Any) -> Any:
        return raw

    # ── Persistence operations ────────────────────────────────────────

    def save(self) -> None:
        """Write the cache to disk. Silent no-op when no path is configured."""
        if self.persist_path is None:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = []
            # Snapshot under the lock to avoid mutation during iteration.
            with self._lock:  # type: ignore[attr-defined]
                for k, entry in self._store.items():  # type: ignore[attr-defined]
                    if self._is_expired(entry):  # type: ignore[attr-defined]
                        continue
                    payload.append({
                        "k": k,
                        "v": self._serialize_value(entry.value),
                        "expires_at": entry.expires_at,
                        "access_count": entry.access_count,
                    })
            self.persist_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Cache save failed: %s", e, exc_info=e)

    def load_if_present(self) -> int:
        """Load entries from disk. Returns the number loaded.

        Idempotent — first call loads, subsequent calls are no-ops.
        Caller is responsible for invoking once, typically right after
        construction. We don't auto-load on construction so test setups
        and reload flows can delay it.
        """
        if self._loaded or self.persist_path is None:
            return 0
        self._loaded = True
        if not self.persist_path.exists():
            return 0
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Cache load failed (corrupt file?): %s", e, exc_info=e)
            return 0
        loaded = 0
        now = time.time()
        with self._lock:  # type: ignore[attr-defined]
            for record in data:
                expires_at = record.get("expires_at", 0)
                if expires_at and now > expires_at:
                    continue
                self._store[record["k"]] = TTLEntry(  # type: ignore[attr-defined]
                    value=self._deserialize_value(record["v"]),
                    expires_at=expires_at,
                    access_count=record.get("access_count", 0),
                )
                loaded += 1
        return loaded
