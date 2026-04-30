"""Qdrant-backed vector store — production scale.

This backend talks to a running Qdrant instance. Local development:

    docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant

Then:

    backend = QdrantBackend(url="http://localhost:6333", collection="zen")

Optional dependency: ``qdrant-client``. When unavailable, importing this
module is still safe — but constructing the backend raises a clear
``ImportError`` so callers can fall back to ``SQLiteBackend`` at config
time rather than hitting a runtime crash inside a request.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from .base import SearchHit, VectorBackend, VectorRecord

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as qmodels

    _QDRANT_AVAILABLE = True
except Exception:
    AsyncQdrantClient = None  # type: ignore[assignment]
    qmodels = None  # type: ignore[assignment]
    _QDRANT_AVAILABLE = False


def is_qdrant_available() -> bool:
    """True when ``qdrant-client`` is installed."""
    return _QDRANT_AVAILABLE


class QdrantBackend(VectorBackend):
    name = "qdrant"

    def __init__(
        self,
        collection: str,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        vector_size: int = 1536,
        distance: str = "Cosine",
    ) -> None:
        if not _QDRANT_AVAILABLE:
            raise ImportError(
                "qdrant-client is not installed. Install with `pip install qdrant-client` "
                "or fall back to SQLiteBackend."
            )
        self._collection = collection
        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        self._vector_size = vector_size
        # Qdrant accepts "Cosine", "Dot", "Euclid"
        self._distance = qmodels.Distance[distance.upper()] if hasattr(qmodels.Distance, distance.upper()) else qmodels.Distance.COSINE
        self._initialized = False

    async def _ensure_collection(self) -> None:
        if self._initialized:
            return
        existing = await self._client.get_collections()
        names = {c.name for c in existing.collections}
        if self._collection not in names:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qmodels.VectorParams(
                    size=self._vector_size, distance=self._distance
                ),
            )
        self._initialized = True

    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        await self._ensure_collection()
        if not records:
            return 0
        points = [
            qmodels.PointStruct(id=r.id, vector=r.vector, payload=r.metadata)
            for r in records
        ]
        await self._client.upsert(collection_name=self._collection, points=points)
        return len(records)

    async def search(
        self,
        query_vector: Sequence[float],
        limit: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        await self._ensure_collection()
        qfilter = None
        if filter_metadata:
            qfilter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(key=k, match=qmodels.MatchValue(value=v))
                    for k, v in filter_metadata.items()
                ]
            )
        results = await self._client.search(
            collection_name=self._collection,
            query_vector=list(query_vector),
            limit=limit,
            query_filter=qfilter,
        )
        return [
            SearchHit(id=str(r.id), score=float(r.score), metadata=dict(r.payload or {}))
            for r in results
        ]

    async def delete(self, ids: Sequence[str]) -> int:
        await self._ensure_collection()
        if not ids:
            return 0
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.PointIdsList(points=list(ids)),
        )
        return len(ids)

    async def count(self) -> int:
        await self._ensure_collection()
        result = await self._client.count(collection_name=self._collection, exact=True)
        return int(result.count)

    async def clear(self) -> None:
        # Drop and recreate is the cleanest way to "clear" in Qdrant.
        try:
            await self._client.delete_collection(collection_name=self._collection)
        except Exception as e:
            logger.debug("delete_collection failed (may not exist yet)", exc_info=e)
        self._initialized = False
        await self._ensure_collection()

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception as e:
            logger.debug("Qdrant client close failed", exc_info=e)
