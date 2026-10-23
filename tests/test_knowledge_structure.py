"""Knowledge notes keep their text AND their tags, facts and links (2026-10-23).

On mindy01 the Knowledge page showed 266 notes with 0 tags, 0 facts, 0 links:
write_note(content=…) stored only the raw text, and generate_markdown() never
wrote the text — so callers had to choose, and every caller lost the structure.
"""
import asyncio
import importlib
import logging
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    return tmp_path


def _kdir(home):
    return home / ".myclaw" / "knowledge" / "default"


def test_write_to_knowledge_keeps_text_tags_facts_links(home):
    from myclaw.knowledge import read_note
    from myclaw.tools.kb import write_to_knowledge

    write_to_knowledge("Home server", "Runs Ubuntu 24.04.\nHosts ZenSynora.", tags="homelab, linux",
                       observations="spec | 32 GB RAM", relations="runs | ZenSynora")
    raw = (_kdir(home) / "home-server.md").read_text()
    assert raw.startswith("---\ntitle: \"Home server\"") and "Runs Ubuntu 24.04." in raw
    n = read_note("home-server")
    assert n.tags == ["homelab", "linux"]
    assert [(o.category, o.content) for o in n.observations] == [("spec", "32 GB RAM")]
    assert [(r.relation_type, r.target) for r in n.relations] == [("runs", "ZenSynora")]


def test_facts_and_links_written_in_the_text_are_kept_once(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Pi", content="A small computer.\n- [spec] 8 GB RAM\n- part_of [[Homelab]]",
               observations=[Observation("spec", "8 GB RAM", [])])
    n = read_note("pi")
    assert [(o.category, o.content) for o in n.observations] == [("spec", "8 GB RAM")]
    assert [r.target for r in n.relations] == ["Homelab"]
    assert (_kdir(home) / "pi.md").read_text().count("8 GB RAM") == 1


def test_escaped_newlines_become_real_lines(home):
    from myclaw.knowledge import write_note

    write_note("Weather", content="Line one\\nLine two")
    assert "Line one\nLine two" in (_kdir(home) / "weather.md").read_text()


def test_update_note_keeps_the_text(home):
    from myclaw.knowledge import read_note, update_note, write_note

    write_note("Server", content="Important text.", tags=["a"])
    created = read_note("server").created_at
    update_note("server", tags=["a", "b"])
    raw = (_kdir(home) / "server.md").read_text()
    assert "Important text." in raw and read_note("server").tags == ["a", "b"]
    assert read_note("server").created_at == created


def test_a_full_note_file_is_kept_as_given(home):
    from myclaw.knowledge import write_note

    doc = '---\ntitle: "X"\npermalink: x\ntags: ["t"]\n---\n\n# X\n\nbody\n'
    write_note("x", content=doc)
    assert (_kdir(home) / "x.md").read_text() == doc


def test_extracted_items_are_saved_with_structure(home):
    from myclaw.knowledge import read_note
    from myclaw.knowledge.extract import parse_json_array, save_items

    raw = ('```json\n[{"title": "Ollama setup", "content": "Ollama runs on mindy01.", "tags": "llm, Local AI",'
           ' "facts": [{"category": "setting", "text": "port 11434"}, "plain fact"],'
           ' "links": [{"type": "runs on", "target": "[[Mindy01]]"}, {"type": "x", "target": "Ollama setup"}]}]\n```')
    items = parse_json_array(raw)
    assert save_items(items, "default", ["auto-extracted"], 5, "[T]") == 1
    n = read_note("ollama-setup")
    assert n.tags == ["llm", "local-ai", "auto-extracted"]
    assert [(o.category, o.content) for o in n.observations] == [("setting", "port 11434"), ("fact", "plain fact")]
    assert [(r.relation_type, r.target) for r in n.relations] == [("runs_on", "Mindy01")]  # no self-link
    assert parse_json_array("not json") == []


def test_migration_adds_structure_to_plain_notes_once(home):
    from updater.migrations import MigrationContext

    m = importlib.import_module("updater.migrations.m_0002_knowledge_note_structure")
    d = _kdir(home)
    (d / "agent-delegation-protocol.md").write_text("Use the delegate tool.\\n- uses [[Delegate tool]]")
    (d / "already.md").write_text('---\ntitle: "Already"\n---\n\n# Already\n')
    ctx = MigrationContext(data_dir=home / ".myclaw", app_version="0.7.5", log=logging.getLogger("t"))
    m.apply(ctx)
    first = (d / "agent-delegation-protocol.md").read_text()
    m.apply(ctx)
    assert (d / "agent-delegation-protocol.md").read_text() == first  # idempotent
    assert first.startswith('---\ntitle: "Agent delegation protocol"') and "Use the delegate tool." in first
    assert "- uses [[Delegate tool]]" in first
    assert (d / "already.md").read_text() == '---\ntitle: "Already"\n---\n\n# Already\n'


def test_enrich_adds_tags_facts_links_without_changing_text(home, monkeypatch):
    from myclaw.config import AppConfig
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.extract import enrich_note, needs_structure

    write_note("Mindy01", content="Linux PC with 32 GB RAM that runs ZenSynora.")
    write_note("ZenSynora", content="Multi-agent assistant.", tags=["app"])
    assert needs_structure(read_note("mindy01"))

    async def fake(config, messages, provider=None, model=None):
        assert "ZenSynora" in messages[1]["content"]  # existing notes offered as link targets
        return ('{"tags": "homelab, hardware", "facts": [{"category": "spec", "text": "32 GB RAM"}],'
                ' "links": [{"type": "runs", "target": "ZenSynora"}]}', "p", "m")

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)
    r = asyncio.run(enrich_note(AppConfig(), "default", "mindy01"))
    assert r == {"permalink": "mindy01", "ok": True, "tags": 2, "facts": 1, "links": 1}
    n = read_note("mindy01")
    assert n.tags == ["homelab", "hardware"] and n.relations[0].target == "ZenSynora"
    assert "Linux PC with 32 GB RAM that runs ZenSynora." in Path(n.file_path).read_text()
    assert not needs_structure(n)


def test_enrich_api_batches_and_stops_on_model_errors(home, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.knowledge import write_note
    from myclaw.web import knowledge_api

    for i in range(3):
        write_note(f"Note {i}", content=f"text {i}")
    calls = []

    async def fake(config, messages, provider=None, model=None):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Error code: 429 - temporarily rate-limited upstream")
        return '{"tags": "x", "facts": [], "links": []}', "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)
    app = FastAPI()
    app.include_router(knowledge_api.router)
    r = TestClient(app).post("/api/knowledge/enrich", json={"limit": 10}).json()
    assert len(r["enriched"]) == 1 and r["remaining"] == 2 and "rate-limited" in r["stopped"]
