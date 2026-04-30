"""SQLite-backed vector store — persistent, no new deps.

Stores vectors as JSON blobs in a single table; similarity is computed in
Python. Good up to ~10-50K vectors with default embedding sizes (768/1536
dims). Beyond that, switch to ``QdrantBackend``.

We keep the file-IO sync (sqlite3 stdlib) and yield to the event loop via
``asyncio.to_thread`` so multi-coroutine workloads don't block.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .base import SearchHit, VectorBackend, VectorRecord, cosine_similarity

logger = logging.getLogger(__name__)


class SQLiteBackend(VectorBackend):
    name = "sqlite"

    def __init__(self, db_path: Path, table: str = "vectors") -> None:
        # Reject odd table names defensively — we interpolate it into DDL.
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Invalid table name: {table!r}")
        self._db_path = Path(db_path)
        self._table = table
        self._lock = asyncio.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    id TEXT PRIMARY KEY,
                    vector_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # ── Sync helpers (run via to_thread) ──────────────────────────────

    def _upsert_sync(self, records: Sequence[VectorRecord]) -> int:
        conn = self._connect()
        try:
            for r in records:
                conn.execute(
                    f"""
                    INSERT INTO {self._table} (id, vector_json, metadata_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        vector_json=excluded.vector_json,
                        metadata_json=excluded.metadata_json
                    """,
                    (r.id, json.dumps(r.vector), json.dumps(r.metadata)),
                )
            conn.commit()
            return len(records)
        finally:
            conn.close()

    def _scan_sync(self) -> List[VectorRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT id, vector_json, metadata_json FROM {self._table}"
            ).fetchall()
            out: List[VectorRecord] = []
            for row in rows:
                try:
                    vec = json.loads(row["vector_json"])
                    meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                    out.append(VectorRecord(id=row["id"], vector=vec, metadata=meta))
                except Exception as e:
                    logger.warning("Skipping malformed vector row %s: %s", row["id"], e)
            return out
        finally:
            conn.close()

    def _delete_sync(self, ids: Sequence[str]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        conn = self._connect()
        try:
            cur = conn.execute(
                f"DELETE FROM {self._table} WHERE id IN ({placeholders})", tuple(ids)
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()

    # ── Async API ──────────────────────────────────────────────────────

    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._upsert_sync, list(records))

    async def search(
        self,
        query_vector: Sequence[float],
        limit: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        async with self._lock:
            records = await asyncio.to_thread(self._scan_sync)

        if filter_metadata:
            records = [
                r for r in records
                if all(r.metadata.get(k) == v for k, v in filter_metadata.items())
            ]

        # Brute-force cosine. Acceptable for the SQLite backend's target size.
        hits = [
            SearchHit(id=r.id, score=cosine_similarity(query_vector, r.vector), metadata=r.metadata)
            for r in records
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def delete(self, ids: Sequence[str]) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, list(ids))

    async def count(self) -> int:
        async with self._lock:
            def _count():
                conn = self._connect()
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {self._table}").fetchone()
                    return int(n[0]) if n else 0
                finally:
                    conn.close()
            return await asyncio.to_thread(_count)

    async def clear(self) -> None:
        async with self._lock:
            def _clear():
                conn = self._connect()
                try:
                    conn.execute(f"DELETE FROM {self._table}")
                    conn.commit()
                finally:
                    conn.close()
            await asyncio.to_thread(_clear)
