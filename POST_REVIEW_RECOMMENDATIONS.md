# ZenSynora — Post-Review Recommendations

**Generated:** 2026-06-23  
**Review basis:** commit `63e14cc` (latest on main)  
**Scope:** Code fixes, test gaps, diagram sync, knowledge-graph hygiene

---

## 1. Critical Fixes (do first)

### 1.1 Fix `get_backlinks()` scope bug in `myclaw/knowledge/graph.py`

**File:** `myclaw/knowledge/graph.py` (lines 357–385)

**Problem:** `db` is used outside its `with` block → `NameError` at runtime when backlinks exist.

**Fix:** Move the results-building loop inside the `with` block, and add the missing `db_path` parameter.

```python
def get_backlinks(
    permalink: str,
    user_id: str = "default",
    db_path: Optional[Path] = None,
) -> List[Dict]:
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entity = db.get_entity_by_permalink(permalink)
        if not entity:
            return []
        backlink_entities = db.get_backlinks(entity.id)

        results = []
        for bl_entity in backlink_entities:
            relations = db.get_relations_from(bl_entity.id)
            for rel_type, target_permalink, _ in relations:
                if target_permalink == permalink:
                    results.append({
                        "permalink": bl_entity.permalink,
                        "name": bl_entity.name,
                        "relation_type": rel_type,
                    })
                    break
    return results
```

### 1.2 Pass `db_path` through in `advanced_search.py`

**File:** `myclaw/knowledge/advanced_search.py` (line 165)

**Problem:** `db_path` parameter is accepted but ignored — custom DB paths silently fall back to the default.

**Fix:**
```python
with KnowledgeDB(user_id, db_path=db_path) as db:
```

### 1.3 Pass `db_path` through in `path_reasoning.py`

**File:** `myclaw/knowledge/path_reasoning.py` (line 79)

**Fix:**
```python
with KnowledgeDB(user_id, db_path=db_path) as db:
```

---

## 2. API Consistency (add `db_path` to new functions)

### 2.1 Add `db_path` to `search_by_metadata()` in `graph.py`

**File:** `myclaw/knowledge/graph.py` (lines 388–401)

**Fix:**
```python
def search_by_metadata(
    filters: Dict[str, Any],
    user_id: str = "default",
    db_path: Optional[Path] = None,
) -> List[Note]:
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entities = db.search_by_metadata(filters)
    return _batch_read_notes([e.permalink for e in entities], user_id)
```

### 2.2 Audit all knowledge modules for missing `db_path`

Files to check:
- `myclaw/knowledge/graph.py` — lines 36, 99, 204, 252, 368 (fix 368 as part of 1.1)
- `myclaw/knowledge/advanced_search.py` — line 165 (fix as part of 1.2)
- `myclaw/knowledge/path_reasoning.py` — line 79 (fix as part of 1.3)

**Goal:** Every `KnowledgeDB(user_id)` call should become `KnowledgeDB(user_id, db_path=db_path)` if the enclosing function accepts `db_path`.

---

## 3. Test Coverage Gaps (add before next merge)

### 3.1 Add `test_get_backlinks`

**File:** `tests/test_knowledge.py`

```python
def test_get_backlinks(self):
    write_note(
        name="Target Note",
        title="Target Note",
        user_id="test",
        db_path=self.db_path,
    )
    write_note(
        name="Source Note",
        title="Source Note",
        relations=[Relation("depends_on", "target-note")],
        user_id="test",
        db_path=self.db_path,
    )
    sync_knowledge(user_id="test", force=True, db_path=self.db_path)

    backlinks = get_backlinks("target-note", user_id="test", db_path=self.db_path)
    assert len(backlinks) == 1
    assert backlinks[0]["permalink"] == "source-note"
    assert backlinks[0]["relation_type"] == "depends_on"
```

### 3.2 Add `test_search_by_metadata`

```python
def test_search_by_metadata(self):
    write_note(
        name="Project Alpha",
        title="Project Alpha",
        user_id="test",
        db_path=self.db_path,
    )
    sync_knowledge(user_id="test", force=True, db_path=self.db_path)

    results = search_by_metadata(
        {"status": "active"},
        user_id="test",
        db_path=self.db_path,
    )
    # Assertions depend on frontmatter parsing behavior
```

### 3.3 Add `test_sync_circuit_breaker`

```python
def test_sync_circuit_breaker(self):
    bad_file = Path(self.tmpdir) / "test" / "bad-note.md"
    bad_file.write_text("---\ninvalid: [yaml\n---\n# Title\n", encoding="utf-8")

    stats = sync_knowledge(user_id="test", db_path=self.db_path)
    assert stats["errors"] >= 1

    sync_knowledge(user_id="test", db_path=self.db_path)
    sync_knowledge(user_id="test", db_path=self.db_path)

    stats = sync_knowledge(user_id="test", db_path=self.db_path)
    # errors should not increase for the same file
```

### 3.4 Add `test_detect_moves`

```python
def test_detect_moves(self):
    write_note(
        name="Original",
        title="Original",
        user_id="test",
        db_path=self.db_path,
    )
    sync_knowledge(user_id="test", force=True, db_path=self.db_path)

    knowledge_dir = Path(self.tmpdir) / "test"
    old_path = knowledge_dir / "original.md"
    new_path = knowledge_dir / "renamed.md"
    old_path.rename(new_path)

    stats = sync_knowledge(user_id="test", db_path=self.db_path)
    assert stats["updated"] >= 1
```

---

## 4. Diagram Updates

### 4.1 Expand `readme-architecture.mmd` to show new knowledge subsystems

**File:** `diagrams/assets/readme-architecture.mmd`

The `Storage` subgraph currently shows `KnowledgeDB` as a single box. Expand to reflect new capabilities.

```mermaid
    subgraph Storage [Storage]
        Mem[Memory<br/>per-tenant]
        KB[KnowledgeDB<br/>FTS5 + executor]
        Vec[Vector store<br/>memory/sqlite/qdrant]
        Cache[BaseTTLCache]
        KB_Sub --> Aliases[EntityAliases]
        KB_Sub --> Backlinks[Backlinks]
        KB_Sub --> MetaSearch[Metadata Search]
        KB_Sub --> Sync[Sync Engine<br/>circuit breaker + .bmignore]
    end
```

### 4.2 Regenerate HTML diagram viewer

```bash
python diagrams/generate.py
```

---

## 5. Knowledge Graph (`graphify-out/`) — Regenerate

### 5.1 Why regenerate now

- Current graph is from **2026-04-26** (commit `63e14cc` is newer).
- New code adds: `EntityAlias`, `get_entity_by_alias()`, `get_backlinks()`, `search_by_metadata()`, `resolve_forward_references()`, `detect_moves()`, circuit breaker state, `.bmignore` support.
- 931 isolated nodes in the current graph — many may now have connections.

### 5.2 How to regenerate

```bash
graphify update .
```

### 5.3 Post-regeneration checklist

- [ ] Verify `EntityAlias` appears in community detection
- [ ] Check that `get_backlinks` and `get_entity_by_alias` have inferred edges
- [ ] Confirm isolated-node count drops
- [ ] Update `graphify-out/GRAPH_REPORT.md` date header

---

## 6. Code Quality Improvements

### 6.1 Extract `SyncEngine` class from `sync.py`

**Why:** ~400 lines with global mutable state. A class would encapsulate circuit-breaker state and make testing easier.

```python
class SyncEngine:
    def __init__(self, user_id="default", db_path=None, max_failures=3):
        self.user_id = user_id
        self.db_path = db_path
        self.max_failures = max_failures
        self._failures: Dict[str, Dict] = {}
        self._cache: Dict[str, tuple] = {}
```

### 6.2 Add `__all__` to `myclaw/knowledge/__init__.py`

New public functions should be explicitly exported for IDE autocomplete.

### 6.3 Document the `db_path` convention

- `db_path=None` → default per-user path
- `db_path=Path(...)` → used in tests and multi-tenant scenarios

### 6.4 Fix `detect_changes()` return-type fragility

Return a `@dataclass` instead of a growing tuple:

```python
@dataclass
class ChangeSet:
    to_add: List[Path]
    to_update: List[str]
    to_delete: List[str]
    checksums: Dict[str, str]
```

### 6.5 Fix `_get_cached_note` vs `parse_note` inconsistency

In `sync_knowledge()`, the `force=True` branch calls `parse_note()` directly while the incremental branch uses `_get_cached_note()`. Use `_get_cached_note()` in both branches, clearing the cache at the start of a forced re-sync if needed.

### 6.6 Add generated-column index for `entity_metadata`

For large entity sets, add a generated column + index on frequently queried metadata keys:

```sql
ALTER TABLE entities ADD COLUMN meta_type TEXT
    GENERATED ALWAYS AS (json_extract(entity_metadata, '$.type')) VIRTUAL;
CREATE INDEX idx_entities_meta_type ON entities(meta_type);
```

---

## 7. Priority Order (suggested)

| Priority | Item | Effort | Risk |
|---|---|---|---|
| P0 | 1.1 Fix `get_backlinks` scope bug | 5 min | Low |
| P0 | 1.2 Pass `db_path` in `advanced_search.py` | 2 min | Low |
| P0 | 1.3 Pass `db_path` in `path_reasoning.py` | 2 min | Low |
| P0 | 2.1 Add `db_path` to `search_by_metadata()` | 2 min | Low |
| P0 | 2.2 Audit all `KnowledgeDB()` calls for `db_path` | 30 min | Low |
| P1 | 3.1–3.4 Add missing tests | 2–3 hrs | Low |
| P1 | 5 Regenerate `graphify-out/` | 15 min | Low |
| P2 | 4.1 Expand architecture diagram | 1 hr | Low |
| P2 | 6.3 Document `db_path` convention | 30 min | Low |
| P2 | 6.5 Fix cache inconsistency | 15 min | Low |
| P2 | 6.4 `detect_changes` return-type refactor | 1 hr | Low |
| P3 | 6.1 Extract `SyncEngine` class | 3–4 hrs | Medium |
| P3 | 6.6 Add `__all__` + generated-column index | 1 hr | Low |

---

## 8. Files to Touch (summary)

| File | Changes |
|---|---|
| `myclaw/knowledge/graph.py` | Fix scope bug (1.1), add `db_path` to `search_by_metadata` (2.1), audit all `KnowledgeDB()` calls (2.2) |
| `myclaw/knowledge/advanced_search.py` | Pass `db_path` (1.2) |
| `myclaw/knowledge/path_reasoning.py` | Pass `db_path` (1.3) |
| `myclaw/knowledge/sync.py` | Fix cache inconsistency (6.5), optional `detect_changes` refactor (6.4) |
| `myclaw/knowledge/__init__.py` | Add `__all__` (6.2) |
| `myclaw/knowledge/db.py` | Optional generated-column index (6.6) |
| `tests/test_knowledge.py` | Add 4 new test methods (3.1–3.4) |
| `diagrams/assets/readme-architecture.mmd` | Expand Storage subgraph (4.1) |
| `diagrams/generate.py` | Re-run after `.mmd` changes (4.2) |
| `graphify-out/` | Regenerate (5.2) |
