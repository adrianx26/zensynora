"""Abstract base for pluggable vector-storage backends.

The interface is intentionally narrow — `upsert`, `search`, `delete`,
`count`, `clear`. Anything richer (filtering, hybrid scoring, grouping)
should be handled by callers using the data they get back, so the
contract stays uniform across very different backends (SQLite vs Qdrant
vs Pinecone).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class VectorRecord:
    """A single row in a vector index.

    ``id`` is the caller-supplied stable identifier (typically the note
    permalink). ``vector`` is the embedding. ``metadata`` is opaque JSON
    the backend stores verbatim and returns alongside hits.
    """
    id: str
    vector: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    """One result from a similarity search."""
    id: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in pure Python.

    Returns 0.0 if either vector is zero-length or zero-magnitude — caller
    can treat that as "no match" without special-casing NaN.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


class VectorBackend(ABC):
    """Async storage interface implemented by every concrete backend."""

    #: User-friendly name for logs/metrics (e.g. ``"qdrant"``, ``"sqlite"``).
    name: str = "vector-backend"

    @abstractmethod
    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        """Insert or update ``records``. Returns the number written."""

    @abstractmethod
    async def search(
        self,
        query_vector: Sequence[float],
        limit: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        """Return the top ``limit`` hits by similarity, descending.

        ``filter_metadata`` is an optional equality filter applied to
        record metadata; backends without server-side filtering should
        apply it post-search.
        """

    @abstractmethod
    async def delete(self, ids: Sequence[str]) -> int:
        """Remove ``ids`` from the index. Returns the count actually deleted."""

    @abstractmethod
    async def count(self) -> int:
        """Total number of records in the index."""

    @abstractmethod
    async def clear(self) -> None:
        """Drop every record. Mostly used by tests; admins should be cautious."""

    async def close(self) -> None:
        """Optional resource cleanup. Default is a no-op."""
        return None
