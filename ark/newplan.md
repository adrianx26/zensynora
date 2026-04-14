# Implementation Plan: Integrating MemoPad Storage into ZenSynora

## Overview

> **Repo**: [github.com/adrianx26/memopad](https://github.com/adrianx26/memopad)  
> **Status**: ✅ **Fully Implemented** — see `myclaw/knowledge/`

This document outlined the plan to integrate the memory/storage solution from MemoPad (Markdown files indexed with SQLite) into ZenSynora (MyClaw). The integration focuses exclusively on SQLite for indexing and search, eliminating any Postgres references or support.

This enhances ZenSynora's current SQLite-based conversation memory with structured knowledge storage, enabling persistent, editable notes and a knowledge graph while maintaining local-first principles.

---

## Compatibility Assessment

- **MemoPad's storage** (Markdown + SQLite) is highly compatible with ZenSynora, as both are Python-based, use SQLite, and focus on local AI agents.
- **Benefits**: Adds semantic structure, manual editing (e.g., via Obsidian), and graph traversal to ZenSynora's simple conversation history.
- **Challenges**: Adapt MemoPad's MCP (Model Context Protocol) to ZenSynora's direct Ollama calls; extended for multi-user support.
- **Focus**: SQLite-only for all database operations (no Postgres).

## Assumptions

- Development in Python 3.10+.
- Reuse MemoPad code modules where possible (e.g., Markdown parsing, SQLite indexing).
- Total estimated time: 7-10 days for a single developer.

---

## Phase 1: Preparation and Dependency Integration ✅ Done

**Objectives**: Set up the foundation by integrating MemoPad's core storage components.

### Steps Completed

- **Cloned and Integrated MemoPad Code** — Relevant modules copied into `myclaw/knowledge/` (parser, SQLite indexing, no Postgres).
- **Updated Dependencies** — `requirements.txt` includes `pydantic`, `pyyaml`, `watchdog`, etc.
- **Configured Storage Directory** — `~/.myclaw/config.json` includes `knowledge_dir`. Per-user subdirs: `~/.myclaw/knowledge/{user_id}/`.
- **Initialized SQLite Schema** — `entities`, `observations`, `relations`, `FTS5` tables initialized on startup via `myclaw/knowledge/db.py`.

---

## Phase 2: Storage and Synchronization ✅ Done

**Objectives**: Enable writing/reading structured Markdown notes with SQLite indexing and sync.

### Steps Completed

- **Storage Functions** (`myclaw/knowledge/storage.py`):
  - `write_note(entity, observations, relations)` — generates Markdown with frontmatter.
  - `read_note(permalink)` — parses Markdown and returns structured data.
- **SQLite Indexing and Sync** — `sync_knowledge()` scans Markdown files, updates SQLite indexes.
- **Background extraction** — configurable background sync via `config.knowledge.auto_extract`.
- **Graph Support** (`myclaw/knowledge/graph.py`) — `get_related_entities(permalink)` traverses relations via SQLite queries.

---

## Phase 3: LLM and Tool Integration ✅ Done

**Objectives**: Make the knowledge base accessible via the agent and channels.

### Tools Added (`myclaw/tools.py`)

- `write_to_knowledge(title, content, tags, observations, relations)` — creates structured notes.
- `search_knowledge(query)` — SQLite FTS5 full-text search with BM25 ranking.
- `read_knowledge(permalink)` — reads a note by permalink.
- `get_knowledge_context(permalink, depth)` — graph-traversal context building.
- `list_knowledge()`, `list_knowledge_tags()`, `sync_knowledge_base()`.

### Agent Integration (`myclaw/agent.py`)

- Auto-searches knowledge base before generating responses.
- Injects relevant knowledge into system prompt context.

### Multi-User Isolation

- Per-user SQLite databases: `~/.myclaw/knowledge_{user_id}.db`.
- Per-user Markdown directories: `~/.myclaw/knowledge/{user_id}/`.

### Channel Commands

**Telegram & WhatsApp** — all `/knowledge_*` commands:
`/knowledge_search`, `/knowledge_list`, `/knowledge_read`, `/knowledge_write`, `/knowledge_sync`, `/knowledge_tags`

**CLI**:
```bash
python cli.py knowledge search "query"
python cli.py knowledge write
python cli.py knowledge read <permalink>
python cli.py knowledge list
python cli.py knowledge sync
python cli.py knowledge tags
```

---

## Phase 4: Optimizations, Security, and Documentation ✅ Done

- **Caching** — Note parse cache with mtime invalidation (`myclaw/knowledge/sync.py`).
- **FTS5 BM25 ranking** — more relevant search results (`myclaw/knowledge/db.py`).
- **Composite indexes** — faster graph queries.
- **Security** — path validation in all tools, parameterized SQL queries, per-user isolation.
- **Documentation** — `README.md` updated with full Knowledge Base section and CLI commands.

---

## Resources and Risks

- **Resources**: Git for version control; existing dependencies.
- **Risks**: Schema conflicts resolved with migrations; Ollama prompt adaptations tuned.

---

*Original plan created: ~2026-03-10 | Fully implemented: 2026-03-18*