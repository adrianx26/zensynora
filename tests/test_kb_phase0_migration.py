"""Migration 0003 rebuilds the knowledge index so links by title are stored (2026-10-23, KB phase 0)."""
import importlib
import logging


def test_migration_0003_stores_title_links_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".myclaw" / "knowledge" / "default"
    d.mkdir(parents=True)
    (d / "ollama.md").write_text('---\ntitle: "Ollama"\npermalink: ollama\n---\n\n# Ollama\n\n'
                                 "## Relations\n\n- runs_on [[Home Server]]\n", encoding="utf-8")
    (d / "home-server.md").write_text('---\ntitle: "Home Server"\npermalink: home-server\n---\n\n'
                                      "# Home Server\n\nRuns Ubuntu.\n", encoding="utf-8")
    trash = d / ".trash"
    trash.mkdir()
    (trash / "old-20261001-000000.md").write_text("# Old\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in d.glob("*.md")}

    from updater.migrations import MigrationContext

    m = importlib.import_module("updater.migrations.m_0003_knowledge_reindex_links")
    ctx = MigrationContext(data_dir=tmp_path / ".myclaw", app_version="0.7.5", log=logging.getLogger("t"))
    m.apply(ctx)
    m.apply(ctx)

    from myclaw.knowledge.db import KnowledgeDB

    with KnowledgeDB("default") as db:
        stats = db.get_stats()
        assert stats["entities"] == 2 and stats["relations"] == 1  # .trash not indexed
        src = db.get_entity_by_permalink("ollama")
        assert [r[1] for r in db.get_relations_from(src.id)] == ["home-server"]
    assert {p.name: p.read_bytes() for p in d.glob("*.md")} == before  # notes untouched


def test_migration_0003_without_knowledge_folder_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from updater.migrations import MigrationContext

    m = importlib.import_module("updater.migrations.m_0003_knowledge_reindex_links")
    m.apply(MigrationContext(data_dir=tmp_path / ".myclaw", app_version="0.7.5", log=logging.getLogger("t")))
