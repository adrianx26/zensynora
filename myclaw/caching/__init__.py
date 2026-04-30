"""Shared cache primitives — eliminates the drift between
``semantic_cache.py`` and ``semantic_memory.py``.

Both modules previously implemented their own ``CacheEntry`` dataclass,
TTL bookkeeping, and JSON-file persistence. They've been kept independent
because their *content* differs (LLM responses vs. user preferences),
but the eviction/TTL/persist plumbing was identical and drifting apart
sprint by sprint.

This module owns the plumbing. Subclasses provide value (de)serialization
and any extra metadata. The classes below cover the two patterns those
files actually need:

* :class:`BaseTTLCache` — OrderedDict + TTL + LRU eviction. Use when
  every entry has the same lifetime.
* :class:`PersistentCacheMixin` — adds JSON file persistence on a path.
  Mix into ``BaseTTLCache`` (or any cache type) without inheriting twice.

The Sprint 3 ``SemanticCache`` and the Sprint 8 vectorized scan still
own their numpy similarity logic — those are content-specific and don't
belong here.
"""

from .base import (
    BaseTTLCache,
    PersistentCacheMixin,
    TTLEntry,
)

__all__ = [
    "BaseTTLCache",
    "PersistentCacheMixin",
    "TTLEntry",
]
