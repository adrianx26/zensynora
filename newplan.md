Implementation Plan: Integrating MemoPad Storage into ZenSynora
Overview

github.com/adrianx26/memopad is the repo for memopad



This document outlines the plan to integrate the memory/storage solution from MemoPad (Markdown files indexed with SQLite) into ZenSynora (MyClaw). The integration focuses exclusively on SQLite for indexing and search, eliminating any Postgres references or support, as it is optional in MemoPad and not used in ZenSynora. This enhances ZenSynora's current SQLite-based conversation memory with structured knowledge storage, enabling persistent, editable notes and a knowledge graph while maintaining local-first principles.
Compatibility Assessment:

MemoPad's storage (Markdown + SQLite) is highly compatible with ZenSynora, as both are Python-based, use SQLite, and focus on local AI agents. ZenSynora's Ollama integration can be adapted to use MemoPad-like tools for reading/writing knowledge.
Benefits: Adds semantic structure, manual editing (e.g., via Obsidian), and graph traversal to ZenSynora's simple conversation history.
Challenges: Adapt MemoPad's MCP (Model Context Protocol) to ZenSynora's direct Ollama calls; extend for multi-user support.
Focus: SQLite-only for all database operations (no Postgres migration or testing).

Assumptions:

Development in Python 3.10+.
Reuse MemoPad code modules where possible (e.g., Markdown parsing, SQLite indexing).
Total estimated time: 7-10 days for a single developer.

Phase 1: Preparation and Dependency Integration (1-2 days)
Objectives
Set up the foundation by integrating MemoPad's core storage components into ZenSynora, focusing on SQLite.
Steps

Clone and Integrate MemoPad Code:
Copy relevant modules from MemoPad's src/memopad/ (e.g., Markdown parsing for frontmatter/observations/relations, SQLite indexing) into a new subdirectory myclaw/knowledge/ in ZenSynora. Avoid any Postgres-related code (e.g., testcontainers or Docker configs).
Update Dependencies:
Modify ZenSynora's requirements.txt to include MemoPad dependencies like loguru for logging. Ensure compatibility with existing libs (e.g., pydantic, python-telegram-bot). No new DB drivers needed since SQLite is built-in.
Configure Storage Directory:
Extend ~/.myclaw/config.json with "knowledge_dir": "~/.myclaw/knowledge". For multi-user support, create subdirs like ~/.myclaw/knowledge/user_id/. Use SQLite DB at ~/.myclaw/knowledge.db (per-user tables if needed).
Initialize SQLite Schema:
In myclaw/memory.py, add new tables from MemoPad (e.g., entities, observations, relations, FTS5 for full-text search). Use MemoPad's schema but ensure it's SQLite-only. Run initialization on startup if DB doesn't exist.

Testing

Create a sample Markdown note and verify SQLite indexing via a test script (inspired by MemoPad's test_fts5.py).

Phase 2: Implement Storage and Synchronization (2-3 days)
Objectives
Enable writing/reading structured Markdown notes with SQLite indexing and sync.
Steps

Storage Functions:
Create myclaw/knowledge/storage.py:
write_note(entity, observations, relations): Generate Markdown files with frontmatter (title, permalink, tags) and semantic content. Save to knowledge_dir.
read_note(permalink): Parse Markdown file and return structured data.
Use MemoPad's parsing logic for observations (- [category] content #tag) and relations (- relation_type [[WikiLink]]).

SQLite Indexing and Sync:
Implement sync_knowledge(watch=False) in myclaw/tools.py: Scan Markdown files, update SQLite indexes (entities, relations). For real-time, add --watch using file watchers (reuse MemoPad's sync logic).
Ensure bidirectional sync: Changes in SQLite reflect in files, and vice versa.
Knowledge Extraction from Conversations:
Modify myclaw/memory.py: After saving a conversation message to SQLite, use Ollama to extract entities/observations (prompt: "Extract structured knowledge from this message"). Store as new Markdown notes if relevant.
Graph Support:
Add myclaw/knowledge/graph.py: Functions like get_related_entities(permalink) for traversing relations, using SQLite queries (no graph libs needed initially).

Testing

Write a note via code, modify the file manually, run sync, and verify SQLite updates. Test search with sample queries.

Phase 3: LLM and Tool Integration (2-3 days)
Objectives
Make the knowledge base accessible via ZenSynora's agent and channels.
Steps

New Tools:
In myclaw/tools.py, add:
write_to_knowledge(note_content): Allow Ollama/LLM to create structured notes.
search_knowledge(query): Use SQLite FTS5 for full-text/metadata search; return results with memory:// permalinks.
build_context(permalink, depth=2): Traverse graph to build context for prompts (reuse MemoPad logic).

Agent Enhancements:
Update myclaw/agent.py: Before generating responses, auto-search knowledge base and inject relevant context into Ollama prompts.
Multi-User Isolation:
Prefix SQLite tables with user_{id}_ and use subdirs for Markdown files. Pass user_id from Telegram/CLI to tools.
Channel Integration:
Add Telegram commands like /knowledge search <query> or /knowledge write <content> in myclaw/channels/telegram.py. Extend CLI similarly.

Testing

Run a conversation via Telegram/CLI; verify that knowledge from notes influences responses (e.g., recall stored facts).

Phase 4: Optimizations, Security, and Documentation (1-2 days)
Objectives
Polish the integration for production use.
Steps

Optimizations:
Add caching for frequent searches (inspired by MemoPad's cache optimizations). Implement duplicate cleanup in SQLite.
Security:
Validate paths in tools to prevent directory traversal. Restrict knowledge access to authenticated users.
Optional Cloud Sync:
If desired, add basic file sync (e.g., via rsync), but keep it local-first and optional. No DB sync needed since SQLite is file-based.
Documentation:
Update ZenSynora's README.md with a new section: "Enhanced Knowledge Storage". Include Markdown examples, setup instructions, and tool usage.
Modify onboard.py to include knowledge_dir setup in the wizard.
Visualization:
Add a simple tool for generating knowledge canvases (text-based graphs) from SQLite relations.

Testing

End-to-end: Long conversations with knowledge persistence. Multi-user scenarios. Performance checks on large note sets.

Resources and Risks

Resources: Git for version control; existing dependencies.
Risks: Schema conflicts in SQLite – resolve with migrations. Ollama prompt adaptations may need tuning.
Next Steps: Start with Phase 1; iterate based on tests. This integration will make ZenSynora a more powerful knowledge agent.