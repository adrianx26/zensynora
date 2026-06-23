"""
Knowledge storage system for MyClaw.

Inspired by MemoPad's Markdown + SQLite approach:
- Markdown files are the source of truth
- SQLite provides fast search and graph traversal
- Supports observations, relations, and full-text search

db_path convention
------------------
* ``db_path=None`` (default)  → per-user path under ``~/.myclaw/``.
* ``db_path=Path(...)``       → used in tests and multi-tenant scenarios
  to isolate each user/test in a dedicated SQLite file.

Every public function that accepts ``db_path`` threads it through to
``KnowledgeDB(..., db_path=db_path)`` so callers can fully control
the backing store without relying on ``user_id`` path magic.

Async wrappers: All core operations have async variants (prefixed with 'a_')
that run sync operations in thread pools to avoid blocking the event loop.
"""

import asyncio
from typing import List, Optional, Set

from .db import KnowledgeDB
from .parser import (
    parse_note, parse_frontmatter, parse_observations, parse_relations,
    Observation, Relation, Note
)
from .storage import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_all_tags, update_note, get_note_by_tag
)
from .advanced_search import (
    search_advanced, SearchFilters, SearchResult, compute_embedding
)
from .graph import (
    get_related_entities, get_entity_network, find_path,
    get_central_entities, build_context
)
from .db import EntityAlias
from .sync import sync_knowledge, sync_and_report, verify_sync


# ── Async wrappers for knowledge operations (Phase 2.2) ──────────────────────

async def a_search_notes(
    query: str,
    user_id: str = "default",
    limit: int = 10
) -> List[Note]:
    """Async wrapper for search_notes. Runs FTS5 search in a thread pool."""
    return await asyncio.to_thread(search_notes, query, user_id, limit)


async def a_build_context(
    permalink: str,
    user_id: str = "default",
    depth: int = 2,
    include_observations: bool = True,
    db_path=None,
) -> str:
    """Async wrapper for build_context. Runs graph traversal in a thread pool."""
    return await asyncio.to_thread(
        build_context, permalink, user_id, depth, include_observations, db_path
    )


async def a_read_note(permalink: str, user_id: str = "default") -> Optional[Note]:
    """Async wrapper for read_note."""
    return await asyncio.to_thread(read_note, permalink, user_id)


async def a_write_note(
    name: str,
    title: Optional[str] = None,
    observations: Optional[List[Observation]] = None,
    relations: Optional[List[Relation]] = None,
    tags: Optional[List[str]] = None,
    user_id: str = "default",
    content: Optional[str] = None
) -> str:
    """Async wrapper for write_note."""
    return await asyncio.to_thread(
        write_note, name, title, observations, relations, tags, user_id, content
    )


async def a_search_advanced(
    query: str,
    user_id: str = "default",
    filters=None,
    limit: int = 10,
    db_path=None,
):
    """Async wrapper for search_advanced."""
    from .advanced_search import search_advanced
    return await asyncio.to_thread(
        search_advanced, query, user_id, filters, limit, db_path
    )


async def a_get_backlinks(permalink: str, user_id: str = "default", db_path=None):
    """Async wrapper for get_backlinks."""
    from .graph import get_backlinks
    return await asyncio.to_thread(get_backlinks, permalink, user_id, db_path)


async def a_search_by_metadata(filters: dict, user_id: str = "default", db_path=None):
    """Async wrapper for search_by_metadata."""
    from .graph import search_by_metadata
    return await asyncio.to_thread(search_by_metadata, filters, user_id, db_path)


async def a_find_paths(start_permalink: str, end_permalink: str, user_id: str = "default",
                       max_hops: int = 3, max_paths: int = 10,
                       relation_filter: Optional[Set[str]] = None, db_path=None):
    """Async wrapper for find_paths."""
    from .path_reasoning import find_paths
    return await asyncio.to_thread(
        find_paths, start_permalink, end_permalink, user_id,
        max_hops, max_paths, relation_filter, db_path
    )

__all__ = [
    # Database
    "KnowledgeDB",
    # Parser
    "parse_note",
    "parse_frontmatter",
    "parse_observations",
    "parse_relations",
    "Observation",
    "Relation",
    "Note",
    # Storage
    "write_note",
    "read_note",
    "delete_note",
    "list_notes",
    "search_notes",
    "get_all_tags",
    "update_note",
    "get_note_by_tag",
    # Advanced Search
    "search_advanced",
    "SearchFilters",
    "SearchResult",
    "compute_embedding",
    # Graph
    "get_related_entities",
    "get_entity_network",
    "find_path",
    "get_central_entities",
    "build_context",
    # Phase 3 (MemoPad import)
    "get_backlinks",
    "search_by_metadata",
    # Sync
    "sync_knowledge",
    "sync_and_report",
    "verify_sync",
    # Async wrappers
    "a_search_notes",
    "a_build_context",
    "a_read_note",
    "a_write_note",
    "a_search_advanced",
    "a_get_backlinks",
    "a_search_by_metadata",
    "a_find_paths",
]
