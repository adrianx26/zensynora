"""Pluggable vector-storage backends.

Why: SQLite + FTS5 is great up to ~100K notes, but it's lexical (BM25),
not semantic. For larger corpora or semantic recall users want a real
vector database. Rather than rewrite knowledge/ to talk to one specific
service, we put a thin interface in front and let users pick a backend
via config.

Backends shipped here:
  * ``InMemoryBackend`` — zero-dep, brute-force cosine. Good for tests and
    small (~5K vector) workloads.
  * ``SQLiteBackend`` — JSON-blob storage with brute-force cosine in
    Python. No new deps; persists across restarts.
  * ``QdrantBackend`` — production-grade ANN (HNSW). Optional dep.

All backends share the same async API; swapping one for another is a
config change.
"""

from .base import (
    VectorBackend,
    VectorRecord,
    SearchHit,
    cosine_similarity,
)
from .memory import InMemoryBackend
from .sqlite_backend import SQLiteBackend
from .qdrant_backend import QdrantBackend
from .factory import make_backend

__all__ = [
    "VectorBackend",
    "VectorRecord",
    "SearchHit",
    "cosine_similarity",
    "InMemoryBackend",
    "SQLiteBackend",
    "QdrantBackend",
    "make_backend",
]
