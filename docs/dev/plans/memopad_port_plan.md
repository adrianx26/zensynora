# Plan: memopad features for the ZenSynora knowledge base

> Written 2026-10-23 from a review of [memopad](https://github.com/adrianx26/memopad)
> v0.21.6 (HEAD `e31a76f`, 219 Python files and its full git history) and of
> ZenSynora's `myclaw/knowledge/`. ZenSynora's knowledge base is a port of
> memopad's Markdown + SQLite design.

**Status:** Phase 0 done on branch `kb-phase0-fixes` (2026-10-23). Phases 1–6
not started; each needs approval.

Two findings shaped the plan:

* Part of what ZenSynora had already copied from memopad never worked (links
  by title were dropped, editing a note deleted the links to it, `.trash` was
  re-indexed, agents never saw note text). Phase 0 fixes this first, because
  every later phase relies on the links being stored correctly.
* memopad's release of 2026-08-06 (commit `5c3b6c4`) was built from an older
  tree and silently removed persistent embeddings (sqlite-vec),
  `reindex_state` and `doctor --health`. Those must be ported from memopad's
  git history, not from its current version.

## Phase 0 — fix what exists (done)

Contracts and division of work: [kb_phase0_contracts.md](kb_phase0_contracts.md).
User documentation: [knowledge_base_guide.md](../../knowledge_base_guide.md).

| # | Fixed |
|---|---|
| B1 | Re-indexing a note replaced *all* its relations (both directions): backlinks vanished |
| B2 | Links resolved only by exact permalink: links by title/alias were dropped; no forward links |
| B3 | `.trash` (and other dot-folders) re-indexed by `rglob` |
| B4/B5 | Agents never got note text (automatic context, `read_knowledge`, `build_context`) |
| B6 | Checksum / mtime / path / name not updated for existing entries |
| B7 | `sync(since=…)` deleted every note not modified recently |
| B8 | Circuit breaker reset on every call |
| B9 | Detected moves were deleted afterwards |
| B11 | Knowledge gaps from the Web UI never logged; greetings logged as gaps |
| B12 | Scalar `tags:` split into characters; task checkboxes parsed as facts |
| B13 | Tag search missed frontmatter tags |
| B14 | `__all__` listed names that were not imported |
| — | Aliases and metadata never stored; `knowledge.enabled` and `knowledge.knowledge_dir` ignored; a session-summary note per turn; MCP tool parameters all typed as string; CLI fixed to the `default` base; system prompt said "save credentials"; dead `Memory.extract_knowledge_candidates` |

Not fixed in Phase 0 (follow-ups): the knowledge researcher (only scheduled
by the gateway/CLI, not the Web UI; re-researches the first 5 gaps forever;
ignores `user_id` and `knowledge.enabled`; scrapes Google); `Agent._kb_gaps`
is a class-level dict that never shrinks; `myclaw/vector/` and
`knowledge_spaces` are still unused; `search_advanced` still ranks the
"semantic" list with the FTS rank (Phase 3); `write_note` with the same
title replaces facts and links (Phase 1 adds `edit_note`).

## Phase 1 — note tools agents are missing

* `edit_note`: append, prepend, find-and-replace, replace one section.
* `move_note`, `delete_note` (to trash), `backlinks`, `recent_activity`,
  `daily_note` (journal note per day, linked to the previous/next day).
* `search_by_metadata` with operators (`$in`, `$gt`, `$between`, dotted
  paths); `build_context("memory://…", depth)` with timeframes.
* Inline `[[links]]` in the text become `links_to` relations; `(…)` at the
  end of a fact line is kept as its context.
* Web UI: per-section edit, *Recent*, daily-note button.

## Phase 2 — quality layer (memopad "MemGraphRAG")

* Observation-category registry with suggestions (`Fact` / `facts` → `fact`).
* Conflict detection between facts of one category on one note: flagged ⚠
  with *Keep A / Keep B / Both are fine* (detection only; memopad's own review
  called its check too aggressive — use a stricter threshold).
* Hub penalty in context building; provenance of every fact (conversation,
  tool, URL, file); `optimize_storage` (merge duplicates with redirects,
  preview first).

## Phase 3 — semantic search

* `fastembed` `bge-small-en-v1.5` (local, ~30 MB), `threads=1`, inference off
  the event loop; vectors in SQLite (reuse `myclaw/vector/`).
* Embed on write (memopad only has a manual backfill), plus a backfill
  command and a Knowledge-page button. Hybrid search (FTS + vectors, RRF) for
  `search_knowledge` and the automatic context. Off by default.

## Phase 4 — L0–L3 distillation

* L1 facts (deduplicated, confidence), L2 scenarios (clusters), L3 persona;
  rule-based like memopad, optional LLM extractor later. Higher levels rank
  first in the context; `drill_down` to the source note. Background scheduler
  under the shared write limit. Web UI: level filter, Persona panel. Opt-in.

## Phase 5 — ingestion

* `assimilate`: URL, GitHub repo, local folder, PDF/DOCX/XLSX/image → linked
  notes plus a Mermaid diagram, as one tool call.
* Importers: ChatGPT and Claude.ai exports, MCP memory JSON, a Markdown
  folder (Obsidian vault). Upload on the Knowledge page.

## Phase 6 — robustness and live sync

* File watcher in the Web UI process (ignores its own logs/status files —
  memopad's self-loop); one shared write limit for all background writers;
  retry on "database is locked"; doctor checks for index drift, broken links,
  conflicts, embedding coverage, distillation status.

## Optional

Short-term session layering (G6), retrieval caps/timeouts (G4), CodeGraph
(G2), skills as notes (G1 — probably covered by the Skills page), Obsidian
`.canvas` export.

## Not porting

Postgres and Stoolap backends, cloud sync, ChatGPT connector tools,
Handlebars prompts, memopad's FastAPI v2 / FastMCP architecture and its
multi-project configuration (ZenSynora's knowledge bases cover projects).

## memopad pitfalls to avoid (from its history)

* Unify background writers under one semaphore; `busy_timeout` 30 s and
  retry only on "database is locked" (done in Phase 0: `busy_timeout`).
* Replace observations and relations of a note in one transaction (done in
  Phase 0).
* `strict` link resolution: never fuzzy-match on create (done in Phase 0).
* Watcher must ignore its own writes; embeddings need a composite primary key
  and must be updated on write; onnx `threads=1`, inference off the event loop.
* Type hints importable on Python 3.11; map `IntegrityError` to HTTP 409.
* Conflict detection must not flag ordinary updates.
* A release must not revert features — compare the feature list before
  tagging (memopad `5c3b6c4`).
