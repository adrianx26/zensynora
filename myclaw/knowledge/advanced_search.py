"""Advanced knowledge base search with semantic (embedding) support.

Provides search capabilities beyond FTS5:
    - Semantic search using sentence-transformers embeddings
    - Date-range filtering
    - Tag filtering
    - Combined FTS + semantic hybrid search

Dependencies:
    - sentence-transformers (optional, for semantic search)
    - numpy (for embedding math)

When sentence-transformers is not installed, semantic search falls back
to FTS5 with a warning.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Lazy-loaded embedding model (shared instance)
_embedding_model = None
_model_loaded = False


def _get_embedding_model():
    """Lazy-load the sentence-transformers embedding model."""
    global _embedding_model, _model_loaded
    if _model_loaded:
        return _embedding_model

    try:
        from sentence_transformers import SentenceTransformer
        try:
            import torch
            torch.set_num_threads(4)
            device = "cpu"
        except ImportError:
            device = "cpu"

        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device=device)
        _model_loaded = True
        logger.info("Advanced search: embedding model loaded")
        return _embedding_model
    except ImportError:
        _model_loaded = True
        logger.warning(
            "sentence-transformers not installed. Semantic search disabled. "
            "Install: pip install sentence-transformers"
        )
        return None


def compute_embedding(text: str) -> Optional[np.ndarray]:
    """Compute embedding vector for text.

    Returns None if sentence-transformers is not available.
    """
    model = _get_embedding_model()
    if model is None:
        return None
    try:
        return model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    except Exception as e:
        logger.error(f"Embedding computation failed: {e}")
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two normalized vectors."""
    return float(np.dot(a, b))


@dataclass
class SearchFilters:
    """Filters for advanced knowledge search.

    All filters are optional — omitting a filter means "no restriction".
    """
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    tags: Optional[List[str]] = None
    categories: Optional[List[str]] = None
    # Hybrid search weight: 0.0 = pure FTS, 1.0 = pure semantic, 0.5 = balanced
    semantic_weight: float = 0.3


@dataclass
class SearchResult:
    """Single search result with relevance score."""
    permalink: str
    name: str
    content_preview: str
    fts_score: float
    semantic_score: float
    combined_score: float
    created_at: datetime
    updated_at: datetime
    tags: List[str]


def _build_entity_text(db, entity_id: int) -> str:
    """Build a searchable text representation of an entity (name + observations)."""
    conn = db._get_connection()
    name_row = conn.execute(
        "SELECT name FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if not name_row:
        return ""
    text_parts = [name_row["name"]]

    obs_rows = conn.execute(
        "SELECT content FROM observations WHERE entity_id = ?", (entity_id,)
    ).fetchall()
    for row in obs_rows:
        text_parts.append(row["content"])

    return "\n".join(text_parts)


def search_advanced(
    query: str,
    user_id: str = "default",
    filters: Optional[SearchFilters] = None,
    limit: int = 10,
    db_path: Optional[Path] = None,
) -> List[SearchResult]:
    """Advanced hybrid search: FTS5 + semantic + filters.

    Algorithm:
        1. Run FTS5 to get candidate entities
        2. Apply date/tag/category filters in SQL
        3. Compute semantic similarity for remaining candidates (if available)
        4. Combine FTS score and semantic score with configurable weight
        5. Return top-k results

    Args:
        query: Search query
        user_id: User ID for isolation
        filters: Optional SearchFilters
        limit: Max results
        db_path: Optional custom DB path

    Returns:
        List of SearchResult sorted by combined_score descending
    """
    from .db import KnowledgeDB, sanitize_fts_query

    filters = filters or SearchFilters()
    results: List[SearchResult] = []

    with KnowledgeDB(user_id) as db:
        conn = db._get_connection()
        safe_query = sanitize_fts_query(query)

        # ── Build the base query with optional filters ──────────────────────
        where_clauses = ["1=1"]
        params: List = []

        # FTS match
        if safe_query != "*":
            where_clauses.append("e.id IN (SELECT rowid FROM entities_fts WHERE entities_fts MATCH ?)")
            params.append(safe_query)

        # Date range filter
        if filters.date_from:
            where_clauses.append("e.created_at >= ?")
            params.append(filters.date_from.isoformat())
        if filters.date_to:
            where_clauses.append("e.created_at <= ?")
            params.append(filters.date_to.isoformat())

        # Tag filter — note must have at least one matching tag in observations
        if filters.tags:
            tag_conditions = []
            for tag in filters.tags:
                tag_conditions.append("EXISTS (SELECT 1 FROM observations o2 WHERE o2.entity_id = e.id AND json_extract(o2.tags, '$') LIKE ?)")
                params.append(f'%"{tag}"%')
            where_clauses.append(f"({' OR '.join(tag_conditions)})")

        # Category filter
        if filters.categories:
            cat_placeholders = ",".join("?" * len(filters.categories))
            where_clauses.append(
                f"EXISTS (SELECT 1 FROM observations o3 WHERE o3.entity_id = e.id AND o3.category IN ({cat_placeholders}))"
            )
            params.extend(filters.categories)

        # Run the filtered query
        # Use FTS rank when FTS is active, otherwise order by updated_at
        if safe_query != "*":
            sql = f"""
                SELECT e.*, fts.rank as fts_rank
                FROM entities e
                JOIN entities_fts fts ON e.id = fts.rowid
                WHERE {' AND '.join(where_clauses)}
                ORDER BY fts.rank
                LIMIT ?
            """
        else:
            # No FTS query — just filter + sort by updated_at
            sql = f"""
                SELECT e.*, 0.0 as fts_rank
                FROM entities e
                WHERE {' AND '.join(where_clauses)}
                ORDER BY e.updated_at DESC
                LIMIT ?
            """
        params.append(limit * 3)  # Fetch more for semantic re-ranking

        rows = conn.execute(sql, tuple(params)).fetchall()

        if not rows:
            return []

        # ── Compute semantic similarity if enabled ──────────────────────────
        query_embedding = None
        if filters.semantic_weight > 0:
            query_embedding = compute_embedding(query)

        entity_embeddings: Dict[int, np.ndarray] = {}
        if query_embedding is not None:
            for row in rows:
                eid = row["id"]
                text = _build_entity_text(db, eid)
                emb = compute_embedding(text)
                if emb is not None:
                    entity_embeddings[eid] = emb

        # ── Build results with combined scoring ─────────────────────────────
        for row in rows:
            eid = row["id"]
            fts_rank = row["fts_rank"]
            # FTS rank from SQLite FTS5: lower is better, so invert
            # Typical range: -10 to 0 for BM25
            fts_score = max(0.0, 1.0 + float(fts_rank))  # Normalize to 0..1

            semantic_score = 0.0
            if eid in entity_embeddings and query_embedding is not None:
                semantic_score = cosine_similarity(query_embedding, entity_embeddings[eid])

            combined = (
                (1 - filters.semantic_weight) * fts_score +
                filters.semantic_weight * semantic_score
            )

            # Gather tags
            tags: List[str] = []
            tag_rows = conn.execute(
                "SELECT DISTINCT tags FROM observations WHERE entity_id = ?", (eid,)
            ).fetchall()
            for tr in tag_rows:
                try:
                    t = json.loads(tr["tags"] or "[]")
                    tags.extend(t)
                except Exception:
                    pass
            tags = list(dict.fromkeys(tags))  # Deduplicate, preserve order

            results.append(SearchResult(
                permalink=row["permalink"],
                name=row["name"],
                content_preview=row["name"][:200],
                fts_score=fts_score,
                semantic_score=semantic_score,
                combined_score=combined,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                tags=tags,
            ))

    # Sort by combined score descending
    results.sort(key=lambda r: r.combined_score, reverse=True)
    return results[:limit]
