# MemoPad → Zensynora Import Plan

Goal: Port high-value patterns and capabilities from `github.com/adrianx26/memopad` into zensynora's `myclaw/knowledge/`, respecting zensynora's existing architecture (raw SQLite, no ORM, per-user tenancy, async-first).

---

## 1. Change Detection & Sync Improvements

**Source:** `src/memopad/sync/sync_service.py`

| Feature | Current Zensynora | Proposed |
|---------|-------------------|----------|
| **Change detection** | `mtime` only | Add `checksum` (md5/sha256) + `size` as additional signals. Keep `mtime` as fast pre-filter; verify with checksum on mismatch. |
| **Move/rename detection** | None | Detect moves by matching checksums between "new" files and DB entries with different paths. |
| **Circuit breaker** | None | Skip files that fail sync > N times consecutively. Reset on checksum change. |
| **`.bmignore` support** | None | Load ignore patterns from `.bmignore` in knowledge root; skip ignored files during scan. |
| **Frontmatter auto-update** | None | When a file is renamed/moved, auto-update its `permalink` in frontmatter to match new path. |
| **Forward reference resolution** | Warn-and-skip | On sync, attempt to resolve `[[Target]]` relations whose target entity doesn't exist yet. Create placeholder or retry after all files are processed. |

**Implementation notes:**
- Store `checksum`, `mtime`, `size` on `Entity` in `db.py`.
- Update `detect_changes()` in `sync.py` to use checksum + mtime + size.
- Add `handle_move()`, `resolve_forward_references()` to `sync.py`.
- Add `.bmignore` loading utility.

---

## 2. Data Model Enrichment

**Source:** `src/memopad/models/knowledge.py`

| Feature | Current Zensynora | Proposed |
|---------|-------------------|----------|
| **Entity aliases** | None | Add `EntityAlias` table: `entity_id`, `alias`, `created_at`. Allows alternative names for search. |
| **Observation context** | None | Add `context` field to `Observation` — free-text provenance note about where this observation came from. |
| **Observation conflict tracking** | None | Add `conflict_score`, `conflicting_obs_id`, `conflict_resolved` — enables surfacing contradictory facts. |
| **Provenance path** | None | Add `provenance_path` to `Observation` — source file + location for audit trail. |
| **Entity checksum/mtime/size** | None | Add to `Entity` table for robust change detection. |

**Implementation notes:**
- New columns are additive; existing DBs can be migrated with `ALTER TABLE` where SQLite permits, or via schema version bump.
- Conflict fields are opt-in; existing observations default to `conflict_resolved=True`, `conflict_score=NULL`.

---

## 3. Search & Indexing

**Source:** `src/memopad/services/search_service.py`, `repository/search_repository.py`

| Feature | Current Zensynora | Proposed |
|---------|-------------------|----------|
| **Backlinks** | None | `backlinks(permalink)` — find all notes linking TO a given entity. |
| **Structured metadata search** | `search_by_tag` only | `search_by_metadata(filters)` — filter by arbitrary frontmatter fields (type, status, dates). |
| **Recent activity** | None | `recent_activity(type, timeframe)` — find recently updated/changed entities. |
| **Search index cleanup on delete** | Cascade-only | Explicitly remove FTS entries for observations + relations when entity is deleted. |
| **Hybrid search RRF** | Weighted sum | Replace weighted-sum fusion with Reciprocal Rank Fusion (RRF) for more robust hybrid FTS+semantic ranking. |

**Implementation notes:**
- Zensynora already has `search_advanced` with FTS+semantic. Just swap the fusion algorithm.
- Backlinks: `SELECT e.* FROM entities e JOIN relations r ON e.id = r.from_id WHERE r.to_id = ?`.
- `search_by_metadata`: parse frontmatter fields into a JSON blob column on `Entity`, then query with `json_extract`.

---

## 4. Sync Architecture

**Source:** `src/memopad/sync/sync_service.py`

| Feature | Current Zensynora | Proposed |
|---------|-------------------|----------|
| **Watermark-based scanning** | Full scan every time | Store `last_scan_timestamp` + `last_file_count` on a `sync_state` table. Use `find -newermt` (or Python `os.scandir` with mtime filter) for incremental scans. |
| **Parallel file processing** | Thread pool for reads only | Add `asyncio.Semaphore`-controlled parallel sync for write operations (add/update/delete). |
| **Move detection** | None | See §1. |
| **Checksum caching** | None | Cache checksums during scan to avoid recomputing. |

**Implementation notes:**
- Zensynora already has `AsyncSQLitePool`; reuse the semaphore pattern for file I/O concurrency.
- Watermark: add `sync_meta` table with `key`, `value` (JSON). Keys: `last_scan_ts`, `last_file_count`.
- On Windows, fall back to Python-based mtime filtering (MemoPad already handles this).

---

## 5. File Watching

**Source:** `src/memopad/sync/sync_service.py` + `watchdog`

| Feature | Current Zensynora | Proposed |
|---------|-------------------|----------|
| **Watch mode** | Disabled | Add optional `watchdog`-based `sync --watch`. Auto-sync on file change. Opt-in via dependency. |

**Implementation notes:**
- Make `watchdog` an optional extra: `pip install myclaw[watch]`.
- Use `watchdog.observers.Observer` + `PatternMatchingEventHandler`.
- Debounce events (e.g., 500ms) to avoid thrashing on bulk saves.

---

## 6. NOT Worth Importing

These are product-level or architecture-mismatched features:

| Feature | Reason to skip |
|---------|----------------|
| **MCP server** | Zensynora already has its own agent/tool system. MemoPad's MCP tools (`write_note`, `read_note`, etc.) map 1:1 to existing `myclaw/knowledge/` APIs — no need to adopt MCP protocol. |
| **REST API** | Zensynora is not an API server. |
| **Cloud sync** | Local-only architecture. |
| **Project management** | Zensynora uses `user_id` tenancy, not projects. |
| **Dual SQLite/Postgres** | Zensynora is SQLite-only. |
| **SQLAlchemy ORM** | Zensynora uses raw `sqlite3`/`aiosqlite`. No ORM migration planned. |
| **Canvas visualization** | Too specific to MemoPad's UX. |
| **Daily journal notes** | Specific to MemoPad's workflow. |
| **Duplicate optimization** (`optimize_storage`) | Nice-to-have but low priority; Zensynora doesn't have duplicate accumulation problem yet. |
| **Entity type/content_type system** | Overkill for zensynora's current scope (markdown-only knowledge). |

---

## 7. Implementation Priority

### Phase 1 — Robustness (high value, low risk)
1. **Checksum + mtime + size change detection** in `db.py` + `sync.py`
2. **Circuit breaker** for sync failures in `sync.py`
3. **Move/rename detection** in `sync.py`
4. **`.bmignore` support** in `sync.py`

### Phase 2 — Data Model (medium value, moderate risk)
5. **Entity aliases** table + `get_entity_by_alias()` query
6. **Observation `context` + `provenance_path`** fields
7. **Forward reference resolution** during sync

### Phase 3 — Search (medium value, low risk)
8. **Backlinks** query in `graph.py`
9. **Structured metadata search** (`search_by_metadata`)
10. **RRF fusion** in `advanced_search.py`

### Phase 4 — Performance (medium value, moderate risk)
11. **Watermark-based incremental scanning**
12. **Parallel sync with semaphore** for write ops
13. **Watch mode** (optional extra)

---

## 8. Compatibility Guarantees

- All changes are **additive** — no breaking changes to existing `Note`, `Observation`, `Relation` dataclasses.
- Existing markdown files parse identically (same `parser.py` format).
- New DB columns are nullable; existing `KnowledgeDB` instances migrate cleanly.
- `myclaw/knowledge/__init__.py` exports remain stable; new functions appended to `__all__`.

---

## 9. Estimated Effort

| Phase | Files touched | New tests needed | Risk |
|-------|--------------|------------------|------|
| 1 | `db.py`, `sync.py`, `storage.py` | `tests/test_sync.py` | Low |
| 2 | `db.py`, `parser.py`, `storage.py` | `tests/test_knowledge.py` | Medium |
| 3 | `graph.py`, `advanced_search.py` | `tests/test_search.py` | Low |
| 4 | `sync.py`, `storage.py` | `tests/test_sync.py` | Medium |
