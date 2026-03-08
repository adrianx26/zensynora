"""
Knowledge storage system for MyClaw.

Inspired by MemoPad's Markdown + SQLite approach:
- Markdown files are the source of truth
- SQLite provides fast search and graph traversal
- Supports observations, relations, and full-text search
"""

from .db import KnowledgeDB
from .parser import parse_note, parse_frontmatter, parse_observations, parse_relations
from .storage import write_note, read_note, delete_note, list_notes
from .graph import get_related_entities, get_entity_network
from .sync import sync_knowledge

__all__ = [
    # Database
    "KnowledgeDB",
    # Parser
    "parse_note",
    "parse_frontmatter",
    "parse_observations",
    "parse_relations",
    # Storage
    "write_note",
    "read_note",
    "delete_note",
    "list_notes",
    # Graph
    "get_related_entities",
    "get_entity_network",
    # Sync
    "sync_knowledge",
]
