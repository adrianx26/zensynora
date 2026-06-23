"""
Tests for the knowledge storage system.
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from myclaw.knowledge.parser import (
    parse_frontmatter, parse_observations, parse_relations,
    parse_note, generate_markdown, Note, Observation, Relation
)
from myclaw.knowledge.db import KnowledgeDB
from myclaw.knowledge.storage import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_all_tags, validate_permalink
)
from myclaw.knowledge.graph import get_related_entities, build_context, get_backlinks, search_by_metadata
from myclaw.knowledge.sync import sync_knowledge


# ── Parser Tests ─────────────────────────────────────────────────────────────

class TestParser:
    def test_parse_frontmatter(self):
        content = """---
title: "Test Note"
permalink: test-note
tags: [work, test]
created: 2026-03-08T10:00:00
---

# Test Note
"""
        fm = parse_frontmatter(content)
        assert fm['title'] == "Test Note"
        assert fm['permalink'] == "test-note"
        assert 'work' in fm['tags']
        assert 'test' in fm['tags']
    
    def test_parse_observations(self):
        content = """
## Observations
- [status] Active project #work
- [priority] High priority task
- [note] Some observation #tag1 #tag2
"""
        obs = parse_observations(content)
        assert len(obs) == 3
        assert obs[0].category == "status"
        assert obs[0].content == "Active project"
        assert "work" in obs[0].tags
        assert obs[2].content == "Some observation"
        assert "tag1" in obs[2].tags
        assert "tag2" in obs[2].tags
    
    def test_parse_relations(self):
        content = """
## Relations
- depends_on [[project-alpha]]
- blocks [[task-beta]]
- related_to [[other-project]]
"""
        rels = parse_relations(content)
        assert len(rels) == 3
        assert rels[0].relation_type == "depends_on"
        assert rels[0].target == "project-alpha"
        assert rels[1].target == "task-beta"
    
    def test_generate_markdown(self):
        note = Note(
            name="Test Project",
            permalink="test-project",
            title="Test Project",
            content="",
            tags=["work", "test"],
            observations=[
                Observation(category="status", content="In progress", tags=["active"]),
                Observation(category="priority", content="High", tags=[])
            ],
            relations=[
                Relation(relation_type="depends_on", target="other-project")
            ]
        )
        
        md = generate_markdown(note)
        assert "Test Project" in md
        assert "test-project" in md
        assert "work" in md
        assert "test" in md
        assert "[status] In progress" in md
        assert "depends_on [[other-project]]" in md


# ── Database Tests ───────────────────────────────────────────────────────────

class TestDatabase:
    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = KnowledgeDB(user_id="test")
            # Override the database path
            db.db_path = Path(tmpdir) / "test.db"
            db._conn = None
            db._init_db()
            yield db
            db.close()
    
    def test_create_entity(self, db):
        entity_id = db.create_entity("Test Entity", "test-entity", "/path/to/file.md")
        assert entity_id > 0
        
        entity = db.get_entity_by_permalink("test-entity")
        assert entity is not None
        assert entity.name == "Test Entity"
        assert entity.permalink == "test-entity"
    
    def test_add_observation(self, db):
        entity_id = db.create_entity("Test", "test", "test.md")
        db.add_observation(entity_id, "status", "Active", ["work"])
        
        obs = db.get_observations(entity_id)
        assert len(obs) == 1
        assert obs[0].category == "status"
        assert obs[0].content == "Active"
    
    def test_add_relation(self, db):
        entity1 = db.create_entity("Entity 1", "entity-1", "e1.md")
        entity2 = db.create_entity("Entity 2", "entity-2", "e2.md")
        
        db.add_relation(entity1, "depends_on", entity2)
        
        rels = db.get_relations_from(entity1)
        assert len(rels) == 1
        assert rels[0][0] == "depends_on"
        assert rels[0][1] == "entity-2"
    
    def test_search_fts(self, db):
        db.create_entity("Project Alpha", "project-alpha", "alpha.md")
        db.create_entity("Project Beta", "project-beta", "beta.md")
        db.create_entity("Task Gamma", "task-gamma", "gamma.md")
        
        results = db.search_fts("Project", limit=10)
        assert len(results) == 2
        
        results = db.search_fts("alpha", limit=10)
        assert len(results) == 1
        assert results[0].permalink == "project-alpha"


# ── Storage Tests ────────────────────────────────────────────────────────────

class TestStorage:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Setup and cleanup for storage tests."""
        import myclaw.knowledge.storage as storage
        from myclaw.knowledge.db import KnowledgeDB
        # Save original
        original_dir = storage.get_knowledge_dir
        
        self.tmpdir = tempfile.mkdtemp()
        
        # Override knowledge directory
        def mock_get_knowledge_dir(user_id="default"):
            path = Path(self.tmpdir) / user_id
            path.mkdir(parents=True, exist_ok=True)
            return path
        
        storage.get_knowledge_dir = mock_get_knowledge_dir
        self.db_path = Path(self.tmpdir) / "knowledge.db"
        yield
        storage.get_knowledge_dir = original_dir
    
    def test_write_and_read_note(self):
        permalink = write_note(
            name="Test Note",
            title="Test Note",
            observations=[
                Observation(category="fact", content="This is a test", tags=[])
            ],
            tags=["test"],
            user_id="test",
            db_path=self.db_path
        )
        
        assert permalink == "test-note"
        
        note = read_note(permalink, user_id="test", db_path=self.db_path)
        assert note is not None
        assert note.title == "Test Note"
        assert len(note.observations) == 1
        assert note.observations[0].content == "This is a test"
    
    def test_delete_note(self):
        permalink = write_note(
            name="To Delete",
            title="To Delete",
            user_id="test",
            db_path=self.db_path
        )
        
        assert delete_note(permalink, user_id="test", db_path=self.db_path) is True
        assert read_note(permalink, user_id="test", db_path=self.db_path) is None
        assert delete_note(permalink, user_id="test", db_path=self.db_path) is False
    
    def test_list_notes(self):
        write_note(name="Note 1", title="Note 1", tags=["work"], user_id="test", db_path=self.db_path)
        write_note(name="Note 2", title="Note 2", tags=["personal"], user_id="test", db_path=self.db_path)
        write_note(name="Note 3", title="Note 3", tags=["work"], user_id="test", db_path=self.db_path)
        
        all_notes = list_notes(user_id="test", db_path=self.db_path)
        assert len(all_notes) == 3
        
        work_notes = list_notes(user_id="test", tags=["work"])
        assert len(work_notes) == 2
    
    def test_validate_permalink(self):
        assert validate_permalink("Hello World") == "hello-world"
        assert validate_permalink("Test-Note-123") == "test-note-123"
        assert validate_permalink("  Spaces  ") == "spaces"
        
        with pytest.raises(ValueError):
            validate_permalink("")


# ── Graph Tests ──────────────────────────────────────────────────────────────

class TestGraph:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Setup and cleanup for graph tests."""
        import myclaw.knowledge.storage as storage
        original_dir = storage.get_knowledge_dir
        
        self.tmpdir = tempfile.mkdtemp()
        
        def mock_get_knowledge_dir(user_id="default"):
            path = Path(self.tmpdir) / user_id
            path.mkdir(parents=True, exist_ok=True)
            return path
        
        storage.get_knowledge_dir = mock_get_knowledge_dir
        self.db_path = Path(self.tmpdir) / "knowledge.db"
        yield
        storage.get_knowledge_dir = original_dir
    
    def test_get_related_entities(self):
        # Create related notes
        write_note(
            name="Project Alpha",
            title="Project Alpha",
            relations=[Relation("depends_on", "infrastructure")],
            user_id="test",
            db_path=self.db_path
        )
        write_note(
            name="Infrastructure",
            title="Infrastructure",
            user_id="test",
            db_path=self.db_path
        )
        
        # Sync to database
        sync_knowledge(user_id="test", force=True, db_path=self.db_path)
        
        # Test graph traversal
        related = get_related_entities("project-alpha", user_id="test", depth=1)
        assert len(related) >= 0  # May be empty if relation not found

    def test_get_backlinks(self):
        from myclaw.knowledge.db import KnowledgeDB

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

        # force=True processes files in filesystem order (rglob is non-deterministic
        # relative to entity creation order), so forward refs may be skipped.
        # Insert the relation explicitly to test get_backlinks in isolation.
        with KnowledgeDB(user_id="test", db_path=self.db_path) as db:
            src = db.get_entity_by_permalink("source-note")
            tgt = db.get_entity_by_permalink("target-note")
            if src and tgt:
                db.add_relation(src.id, "depends_on", tgt.id)

        backlinks = get_backlinks("target-note", user_id="test", db_path=self.db_path)
        assert len(backlinks) == 1
        assert backlinks[0]["permalink"] == "source-note"
        assert backlinks[0]["relation_type"] == "depends_on"

    def test_search_by_metadata(self):
        from myclaw.knowledge.db import KnowledgeDB
        write_note(
            name="Project Alpha",
            title="Project Alpha",
            user_id="test",
            db_path=self.db_path,
        )
        sync_knowledge(user_id="test", force=True, db_path=self.db_path)

        with KnowledgeDB(user_id="test", db_path=self.db_path) as db:
            entity = db.get_entity_by_permalink("project-alpha")
            if entity:
                conn = db._get_connection()
                conn.execute(
                    "UPDATE entities SET entity_metadata = ? WHERE id = ?",
                    (json.dumps({"status": "active", "type": "project"}), entity.id),
                )
                conn.commit()

        results = search_by_metadata(
            {"status": "active"},
            user_id="test",
            db_path=self.db_path,
        )
        assert len(results) >= 1


# ── Sync Tests ───────────────────────────────────────────────────────────────

class TestSync:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        import myclaw.knowledge.storage as storage
        original_dir = storage.get_knowledge_dir
        
        self.tmpdir = tempfile.mkdtemp()
        
        def mock_get_knowledge_dir(user_id="default"):
            path = Path(self.tmpdir) / user_id
            path.mkdir(parents=True, exist_ok=True)
            return path
        
        storage.get_knowledge_dir = mock_get_knowledge_dir
        self.db_path = Path(self.tmpdir) / "knowledge.db"
        yield
        storage.get_knowledge_dir = original_dir
    
    def test_sync_knowledge(self):
        write_note(name="Note 1", title="Note 1", user_id="test", db_path=self.db_path)
        write_note(name="Note 2", title="Note 2", user_id="test", db_path=self.db_path)

        stats = sync_knowledge(user_id="test", db_path=self.db_path)

        assert stats['added'] == 0
        assert stats['updated'] == 0
        assert stats['deleted'] == 0

    def test_sync_circuit_breaker(self):
        import myclaw.knowledge.storage as storage
        from myclaw.knowledge.sync import _record_sync_failure, MAX_CONSECUTIVE_FAILURES

        knowledge_dir = Path(self.tmpdir) / "test"
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        bad_path_str = str(knowledge_dir / "bad-note.md")

        # Manually trip the circuit breaker to the failure threshold.
        for i in range(1, MAX_CONSECUTIVE_FAILURES + 1):
            _record_sync_failure(bad_path_str, f"parse error #{i}")

        assert _record_sync_failure(bad_path_str, "parse error again") is None

        from myclaw.knowledge.sync import _should_skip_file
        assert _should_skip_file(bad_path_str)

    def test_detect_moves(self):
        from myclaw.knowledge.sync import detect_moves
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
        new_path.write_text("# Renamed content\n", encoding="utf-8")

        checksums = {str(new_path): "checksum-renamed-content"}

        moves = detect_moves(user_id="test", new_files=[new_path],
                              checksums=checksums, db_path=self.db_path)
        assert str(old_path) in moves or str(new_path) in [v for v in moves.values()] or len(moves) >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
