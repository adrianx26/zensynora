"""In-memory backend — brute force cosine, zero dependencies.

For tests and tiny corpora (~5K records). All state lives in a single
dict, so it disappears when the process exits. Production callers should
use ``SQLiteBackend`` for persistence or ``QdrantBackend`` for scale.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

from .base import SearchHit, VectorBackend, VectorRecord, cosine_similarity


class InMemoryBackend(VectorBackend):
    name = "memory"

    def __init__(self) -> None:
        self._store: Dict[str, VectorRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        async with self._lock:
            for r in records:
                self._store[r.id] = r
            return len(records)

    async def search(
        self,
        query_vector: Sequence[float],
        limit: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        async with self._lock:
            candidates = list(self._store.values())

        # Optional metadata filter.
        if filter_metadata:
            candidates = [
                r for r in candidates
                if all(r.metadata.get(k) == v for k, v in filter_metadata.items())
            ]

        scored = [
            SearchHit(id=r.id, score=cosine_similarity(query_vector, r.vector), metadata=r.metadata)
            for r in candidates
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]

    async def delete(self, ids: Sequence[str]) -> int:
        async with self._lock:
            removed = 0
            for i in ids:
                if i in self._store:
                    del self._store[i]
                    removed += 1
            return removed

    async def count(self) -> int:
        async with self._lock:
            return len(self._store)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
