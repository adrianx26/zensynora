"""Advanced knowledge base search: FTS + semantic vectors + filters.

Provides search capabilities beyond plain FTS5:
    - Semantic ranking from the knowledge base's vector store
      (``knowledge.semantic_search``, KB phase 3 — see ``semantic.py``)
    - Date-range filtering
    - Tag filtering
    - Category filtering
    - Combined FTS + semantic hybrid ranking (RRF or weighted)

2026-10-24 (KB phase 3): the "semantic" list used to copy the FTS rank (so
RRF only re-sorted the FTS result) and every candidate was embedded again
with sentence-transformers on every search. Now the semantic list is a real
ranking from the stored vectors, notes found only by meaning are included
(after the same filters), and nothing is embedded per candidate. When
semantic search is off or not ready the result is the FTS one.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def compute_embedding(text: str) -> Optional[np.ndarray]:
    """Query embedding of ``text`` with the knowledge base's model (unit length).

    Returns None when semantic search is not available (fastembed not
    installed, the model cannot be loaded, or it takes too long).
    """
    from . import semantic

    try:
        return semantic.query_vector(text, timeout=semantic.SEARCH_TIMEOUT,
                                     wait_load=semantic.SEARCH_TIMEOUT)
    except Exception as e:  # pragma: no cover - defensive
        logger.info("Embedding computation failed: %s", e)
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
    # Hybrid search fusion: "weighted" (legacy) or "rrf" (Reciprocal Rank Fusion)
    fusion_method: str = "rrf"
    # Weighted sum parameters (used only when fusion_method="weighted")
    semantic_weight: float = 0.3
    # RRF parameter: number of top results to consider for rank fusion
    rrf_k: int = 60


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
    """Advanced hybrid search: FTS5 + semantic vectors + filters.

    Algorithm:
        1. FTS ranking (title, text, tags, facts) and — when semantic search
           is on and notes are embedded — a vector ranking of the notes
        2. Date/tag/category filters applied to both lists in SQL
        3. Fusion: Reciprocal Rank Fusion of the two rankings (default) or a
           weighted sum of the FTS score and the cosine similarity
        4. Return top-k results

    Args:
        query: Search query
        user_id: User ID for isolation
        filters: Optional SearchFilters
        limit: Max results
        db_path: Optional custom DB path. If None, uses the default per-user path.

    Returns:
        List of SearchResult sorted by combined_score descending
    """
    from . import semantic
    from .db import KnowledgeDB, sanitize_fts_query

    filters = filters or SearchFilters()
    results: List[SearchResult] = []
    fetch = max(1, int(limit)) * 3

    with KnowledgeDB(user_id, db_path=db_path) as db:
        conn = db._get_connection()
        has_query = sanitize_fts_query(query or "") != "*"

        # ── Filters (SQL on entities e) ────────────────────────────────────
        where_clauses = ["1=1"]
        params: List = []
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
        if filters.categories:
            cat_placeholders = ",".join("?" * len(filters.categories))
            where_clauses.append(
                f"EXISTS (SELECT 1 FROM observations o3 WHERE o3.entity_id = e.id AND o3.category IN ({cat_placeholders}))"
            )
            params.extend(filters.categories)
        where = " AND ".join(where_clauses)

        def _filtered(ids: List[int]) -> Dict[int, object]:
            if not ids:
                return {}
            ph = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT e.* FROM entities e WHERE {where} AND e.id IN ({ph})",
                                (*params, *ids)).fetchall()
            return {r["id"]: r for r in rows}

        # ── Rankings ───────────────────────────────────────────────────────
        fts_order: List[int] = []
        sem_order: List[int] = []
        sem_scores: Dict[int, float] = {}
        if has_query:
            fts_order = [e.id for e in db.search_fts(query, fetch * 2)]
            use_semantic = filters.fusion_method == "rrf" or filters.semantic_weight > 0
            if use_semantic and semantic.enabled():
                vec, reason = semantic.vector_rank(conn, db.db_path, query, fetch * 2)
                if vec is not None:
                    sem_order = [eid for eid, _ in vec]
                    sem_scores = dict(vec)
                elif reason:
                    logger.debug("advanced search without vectors: %s", reason)
            rows_by_id = _filtered(list(dict.fromkeys(fts_order + sem_order)))
            fts_order = [i for i in fts_order if i in rows_by_id][:fetch]
            sem_order = [i for i in sem_order if i in rows_by_id][:fetch]
            candidates = list(dict.fromkeys(fts_order + sem_order))
        else:
            # No FTS query — just filter + sort by updated_at
            rows = conn.execute(f"SELECT e.* FROM entities e WHERE {where} "
                                f"ORDER BY e.updated_at DESC LIMIT ?", (*params, fetch)).fetchall()
            rows_by_id = {r["id"]: r for r in rows}
            candidates = [r["id"] for r in rows]

        if not candidates:
            return []

        fts_ranks = {eid: i + 1 for i, eid in enumerate(fts_order)}
        sem_ranks = {eid: i + 1 for i, eid in enumerate(sem_order)}
        n_fts = max(1, len(fts_order))

        for eid in candidates:
            row = rows_by_id[eid]
            # FTS score in [0, 1]: 1 for the best FTS match, 0 when not found by words
            fts_score = (1.0 - (fts_ranks[eid] - 1) / n_fts) if eid in fts_ranks else 0.0
            semantic_score = float(sem_scores.get(eid, 0.0))

            if filters.fusion_method == "rrf":
                combined = sum(1.0 / (filters.rrf_k + r[eid]) for r in (fts_ranks, sem_ranks)
                               if eid in r)
            else:
                combined = ((1 - filters.semantic_weight) * fts_score
                            + filters.semantic_weight * semantic_score)

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

    # Sort by combined score descending (stable: FTS order breaks ties)
    results.sort(key=lambda r: r.combined_score, reverse=True)
    return results[:limit]
