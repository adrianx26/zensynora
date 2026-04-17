"""
Knowledge storage system for MyClaw.

Inspired by MemoPad's Markdown + SQLite approach:
- Markdown files are the source of truth
- SQLite provides fast search and graph traversal
- Supports observations, relations, and full-text search

Async wrappers: All core operations have async variants (prefixed with 'a_')
that run sync operations in thread pools to avoid blocking the event loop.
"""

import asyncio
from typing import List, Optional

from .db import KnowledgeDB
from .parser import (
    parse_note, parse_frontmatter, parse_observations, parse_relations,
    Observation, Relation, Note
)
from .storage import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_all_tags, update_note, get_note_by_tag
)
from .graph import (
    get_related_entities, get_entity_network, find_path,
    get_central_entities, build_context
)
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
    include_observations: bool = True
) -> str:
    """Async wrapper for build_context. Runs graph traversal in a thread pool."""
    return await asyncio.to_thread(
        build_context, permalink, user_id, depth, include_observations
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
    # Graph
    "get_related_entities",
    "get_entity_network",
    "find_path",
    "get_central_entities",
    "build_context",
    # Sync
    "sync_knowledge",
    "sync_and_report",
    "verify_sync",
    # Async wrappers
    "a_search_notes",
    "a_build_context",
    "a_read_note",
    "a_write_note",
]
