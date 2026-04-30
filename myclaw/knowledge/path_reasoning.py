"""Path-finding over the knowledge graph.

Sits next to ``graph.py``'s ``get_related_entities`` (BFS expansion from
one node). What's missing is the answer to *"how is X connected to Y?"*
— a path query rather than a neighborhood query. This module owns it.

Implementation notes:

* In-memory BFS over the entity graph, using the existing ``KnowledgeDB``
  for relation lookups. Edges are pulled lazily as we expand the frontier.
* Bounded by ``max_hops`` (default 3) and ``max_paths`` (default 10) so
  the cost is predictable on dense graphs.
* Edges are typed (``relation_type``); callers can filter to specific
  relation kinds (e.g. only "depends_on" edges).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .db import KnowledgeDB

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathStep:
    """One edge on a path: ``A -[relation]-> B`` (B's permalink)."""
    relation_type: str
    target_permalink: str
    target_name: Optional[str] = None


@dataclass
class GraphPath:
    """An ordered sequence of edges from ``start`` to ``end``."""
    start_permalink: str
    end_permalink: str
    steps: List[PathStep] = field(default_factory=list)

    @property
    def length(self) -> int:
        """Number of hops (= number of edges = ``len(steps)``)."""
        return len(self.steps)


def find_paths(
    start_permalink: str,
    end_permalink: str,
    user_id: str = "default",
    max_hops: int = 3,
    max_paths: int = 10,
    relation_filter: Optional[Set[str]] = None,
) -> List[GraphPath]:
    """All simple paths from ``start_permalink`` to ``end_permalink``.

    Args:
        start_permalink: Source entity. Must exist; otherwise ``[]``.
        end_permalink: Target entity.
        user_id: User scope for the underlying KnowledgeDB.
        max_hops: Edge budget. Paths longer than this are not explored.
        max_paths: Stop once we've collected this many paths.
        relation_filter: If set, only edges of these types are followed.

    Returns:
        Up to ``max_paths`` distinct simple paths, sorted shortest-first.
    """
    if start_permalink == end_permalink:
        # Trivial self-path is intentionally excluded — callers asking
        # for paths from X to X almost always mean "is X reachable" which
        # is a different question.
        return []
    if max_hops < 1 or max_paths < 1:
        return []

    with KnowledgeDB(user_id) as db:
        start = db.get_entity_by_permalink(start_permalink)
        end = db.get_entity_by_permalink(end_permalink)
        if start is None or end is None:
            return []

        # BFS with explicit path tracking. Each frontier item is
        # (current_permalink, current_entity_id, path_steps, visited_perms).
        # `visited_perms` lives per-path so different paths can share a node
        # but no path revisits a node.
        results: List[GraphPath] = []
        frontier: deque = deque()
        frontier.append((
            start_permalink,
            start.id,
            [],            # no steps yet
            {start_permalink},
        ))

        # Cache outgoing-relation lookups so we don't hit the DB twice for
        # the same node when many partial paths converge.
        rels_cache: Dict[int, list] = {}

        def _outgoing(entity_id: int):
            cached = rels_cache.get(entity_id)
            if cached is not None:
                return cached
            rows = db.get_relations_from(entity_id)
            rels_cache[entity_id] = rows
            return rows

        while frontier and len(results) < max_paths:
            current_perm, current_id, steps, visited = frontier.popleft()

            if len(steps) >= max_hops:
                continue

            relations = _outgoing(current_id)
            # Batch fetch the next-hop entities to avoid N+1.
            target_permalinks = [r[1] for r in relations]
            target_entities = (
                db.get_entities_by_permalinks(target_permalinks)
                if target_permalinks else {}
            )

            for rel_type, tgt_perm, tgt_name in relations:
                if relation_filter and rel_type not in relation_filter:
                    continue
                if tgt_perm in visited:
                    # No revisits — keeps paths simple (no cycles).
                    continue
                step = PathStep(relation_type=rel_type, target_permalink=tgt_perm, target_name=tgt_name)
                new_steps = steps + [step]

                if tgt_perm == end_permalink:
                    results.append(GraphPath(
                        start_permalink=start_permalink,
                        end_permalink=end_permalink,
                        steps=new_steps,
                    ))
                    if len(results) >= max_paths:
                        break
                    continue

                tgt_entity = target_entities.get(tgt_perm)
                if tgt_entity is None:
                    continue

                frontier.append((
                    tgt_perm,
                    tgt_entity.id,
                    new_steps,
                    visited | {tgt_perm},
                ))

        results.sort(key=lambda p: p.length)
        return results


def shortest_path(
    start_permalink: str,
    end_permalink: str,
    user_id: str = "default",
    max_hops: int = 3,
    relation_filter: Optional[Set[str]] = None,
) -> Optional[GraphPath]:
    """Convenience: return the single shortest path or None."""
    paths = find_paths(
        start_permalink=start_permalink,
        end_permalink=end_permalink,
        user_id=user_id,
        max_hops=max_hops,
        max_paths=1,
        relation_filter=relation_filter,
    )
    return paths[0] if paths else None
