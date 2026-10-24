"""KB phase 3 (2026-10-24): semantic search of the knowledge base.

Chunking, the vector store in the index database (composite key, cascade,
stale on checksum / model change, re-use after a full rebuild), embed on
write through the background worker, backfill + status, hybrid search (RRF),
the automatic agent context, the tool, the API routes and old databases.
Uses the deterministic HashEmbedder — no fastembed, no download.
"""
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sem(home, monkeypatch):
    """Semantic search on, with the fake embedder ("car" means "automobile")."""
    from myclaw.knowledge import semantic
    from myclaw.knowledge.semantic import HashEmbedder

    made = []

    def factory(model):
        e = HashEmbedder(model=model, synonyms={"car": "automobile", "cars": "automobile"})
        made.append(e)
        return e

    monkeypatch.setattr(semantic, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(semantic, "_OPTIONS", dict(semantic._OPTIONS, semantic_search=True,
                                                   embedding_model="test/hash", kb_enabled=True,
                                                   min_score=0.2))
    semantic.set_embedder_factory(factory)
    semantic.made = made
    yield semantic
    semantic.wait_idle(20)
    semantic.set_embedder_factory(None)


def _db(home):
    return home / ".myclaw" / "knowledge_default.db"


def _rows(home, sql, args=()):
    conn = sqlite3.connect(_db(home))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _eid(home, permalink):
    return _rows(home, "SELECT id FROM entities WHERE permalink = ?", (permalink,))[0][0]


def _calls(sem):
    return sum(e.calls for e in sem.made)


def _three_notes():
    from myclaw.knowledge import write_note

    write_note("Garage", content="The car is parked in the garage next to the bikes.")
    write_note("Vehicle", content="My automobile needs new tyres before winter.")
    write_note("Bread", content="Recipes for sourdough bread and baking at home.")


# ── Step 1: chunking ───────────────────────────────────────────────────────

def test_short_note_is_one_chunk_with_title_tags_facts_and_links():
    from myclaw.knowledge.semantic import chunk_note

    chunks = chunk_note("Home server", "Runs Ubuntu.", ["homelab"],
                        [("spec", "32 GB RAM")], [("runs", "ZenSynora")])
    assert len(chunks) == 1
    c = chunks[0]
    assert c.startswith("Home server\nTags: homelab")
    assert "Runs Ubuntu." in c and "- spec: 32 GB RAM" in c and "- runs ZenSynora" in c


def test_long_note_is_split_at_headings_and_paragraphs_with_overlap():
    from myclaw.knowledge.semantic import chunk_note

    paras = [f"Paragraph {i} " + ("word%d " % i) * 60 for i in range(8)]
    body = "## First part\n\n" + "\n\n".join(paras[:4]) + "\n## Second part\n" + "\n\n".join(paras[4:])
    chunks = chunk_note("Long", body, max_chars=1000, overlap=150)
    assert len(chunks) > 2
    assert all(c.startswith("Long\n\n") for c in chunks)
    assert all(len(c) <= 1000 + 20 for c in chunks)
    # consecutive chunks share text (overlap)
    for a, b in zip(chunks, chunks[1:]):
        tail_words = a.split()[-3:]
        assert " ".join(tail_words) in b
    joined = "\n".join(chunks)
    for i in range(8):
        assert f"Paragraph {i}" in joined
    # a paragraph longer than a chunk is cut at spaces
    huge = chunk_note("Huge", "x " * 3000, max_chars=500)
    assert len(huge) > 5 and all(len(c) <= 520 for c in huge)


def test_chunks_are_capped_and_empty_note_gives_title():
    from myclaw.knowledge.semantic import chunk_note

    assert chunk_note("Only title", "") == ["Only title"]
    many = chunk_note("Big", "\n\n".join("p%d " % i + "y " * 400 for i in range(200)),
                      max_chars=1000, max_chunks=5)
    assert len(many) == 5


# ── Step 1: store ──────────────────────────────────────────────────────────

def test_schema_composite_key_and_vectors(home, sem):
    from myclaw.knowledge import write_note

    write_note("Box", content="A box of screws.", tags=["hw"])
    assert sem.wait_idle(10)
    pk = [r[1] for r in sorted(_rows(home, "PRAGMA table_info(note_vectors)"), key=lambda r: r[5])
          if r[5]]
    assert pk == ["entity_id", "chunk_no"]
    rows = _rows(home, "SELECT entity_id, chunk_no, model, dim, checksum, length(vector) "
                       "FROM note_vectors")
    assert rows == [(_eid(home, "box"), 0, "test/hash", 64, rows[0][4], 64 * 4)]
    assert len(rows[0][4]) == 64  # sha256 hex
    fk = _rows(home, "PRAGMA foreign_key_list(note_vectors)")
    assert fk and fk[0][2] == "entities" and fk[0][6].upper() == "CASCADE"


def test_update_replaces_rows_and_delete_cascades(home, sem):
    from myclaw.knowledge import delete_note, update_note, write_note

    long_text = "\n\n".join(f"Part {i} " + "lorem ipsum " * 60 for i in range(6))
    write_note("Doc", content=long_text)
    assert sem.wait_idle(10)
    eid = _eid(home, "doc")
    before = _rows(home, "SELECT chunk_no, checksum FROM note_vectors WHERE entity_id = ? "
                         "ORDER BY chunk_no", (eid,))
    assert len(before) > 2
    update_note("doc", content="Now a short note.")
    assert sem.wait_idle(10)
    after = _rows(home, "SELECT chunk_no, checksum FROM note_vectors WHERE entity_id = ?", (eid,))
    assert len(after) == 1 and after[0][1] != before[0][1]   # extra chunks removed, text replaced
    assert _rows(home, "SELECT chunks FROM note_embed_state WHERE entity_id = ?", (eid,)) == [(1,)]
    assert delete_note("doc")
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors") == [(0,)]
    assert _rows(home, "SELECT COUNT(*) FROM note_embed_state") == [(0,)]
    # kept for re-use by checksum (restore from .trash / full rebuild)
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors_carry") == [(1,)]


def test_delete_without_foreign_keys_still_removes_vectors(home, sem):
    from myclaw.knowledge import write_note

    write_note("Box", content="A box.")
    assert sem.wait_idle(10)
    conn = sqlite3.connect(_db(home))       # another tool, PRAGMA foreign_keys off
    conn.execute("DELETE FROM entities WHERE permalink = 'box'")
    conn.commit()
    conn.close()
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors") == [(0,)]


def test_unchanged_text_is_not_embedded_again(home, sem):
    from myclaw.knowledge import update_note, write_note

    write_note("Box", content="A box of screws.", tags=["hw"])
    assert sem.wait_idle(10)
    n = _calls(sem)
    update_note("box", tags=["hw"])          # file rewritten (new 'updated'), same text
    assert sem.wait_idle(10)
    assert _calls(sem) == n
    st = sem.embedding_status()
    assert st["stale"] == 0 and st["embedded"] == 1


def test_full_rebuild_reuses_vectors_without_model_calls(home, sem):
    from myclaw.knowledge.sync import sync_knowledge

    _three_notes()
    assert sem.wait_idle(10)
    n = _calls(sem)
    sync_knowledge(force=True)               # deletes and re-creates every entry
    assert sem.wait_idle(10)
    assert _calls(sem) == n
    st = sem.embedding_status()
    assert st["embedded"] == 3 and st["chunks"] == 3 and st["stale"] == 0
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors_carry") == [(0,)]


def test_stale_on_file_change_and_on_model_change(home, sem, monkeypatch):
    from myclaw.knowledge import update_note

    _three_notes()
    assert sem.wait_idle(10)
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)   # no embed on write now
    update_note("bread", content="Rye bread and a little salt.")
    st = sem.embedding_status()
    assert st["stale"] == 1 and st["embedded"] == 2
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    sem.set_options(embedding_model="test/other")                  # model change → all stale
    st = sem.embedding_status()
    assert st["stale"] == 3 and st["chunks"] == 0 and st["other_model_chunks"] == 3
    info = {}
    from myclaw.knowledge import search_notes
    search_notes("car", mode="auto", info=info)          # vectors of another model are never used
    assert info["mode"] == "fts" and "Backfill" in info["fallback"]
    r = sem.backfill_embeddings("default")
    assert r["ok"] and r["notes"] == 3
    assert sem.embedding_status()["stale"] == 0


# ── Step 2: embed on write ─────────────────────────────────────────────────

def test_embed_on_write_through_the_worker_for_every_write_path(home, sem):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from myclaw.knowledge import write_note
    from myclaw.knowledge.sync import sync_knowledge
    from myclaw.web import knowledge_api

    write_note("Tool note", content="Written by an agent.")
    (home / ".myclaw" / "knowledge" / "default" / "hand.md").write_text(
        "---\ntitle: Hand\n---\n\n# Hand\n\nWritten by hand in an editor.\n")
    sync_knowledge()
    app = FastAPI()
    app.include_router(knowledge_api.router)
    TestClient(app).post("/api/knowledge/note", json={"name": "Web", "content": "From the Web UI."})
    assert sem.wait_idle(10)
    got = {r[0] for r in _rows(home, "SELECT e.permalink FROM note_vectors v "
                                     "JOIN entities e ON e.id = v.entity_id")}
    assert got == {"tool-note", "hand", "web"}


def test_no_queue_when_off_or_not_installed(home, sem, monkeypatch):
    from myclaw.knowledge import write_note

    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    write_note("A", content="one")
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    monkeypatch.setitem(sem._OPTIONS, "kb_enabled", False)     # knowledge.enabled = false
    write_note("B", content="two")
    monkeypatch.setitem(sem._OPTIONS, "kb_enabled", True)
    monkeypatch.setattr(sem, "library_installed", lambda: False)
    write_note("C", content="three")
    assert sem.wait_idle(10)
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors") == [(0,)]
    assert sem.embedding_status()["stale"] == 3


def test_worker_failure_is_logged_once_and_writes_keep_working(home, sem, caplog):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.semantic import HashEmbedder

    class Broken(HashEmbedder):
        def embed_passages(self, texts):
            raise RuntimeError("onnx exploded")

    sem.set_embedder_factory(lambda m: Broken(model=m))
    with caplog.at_level("WARNING", logger="myclaw.knowledge.semantic"):
        for i in range(4):
            write_note(f"N{i}", content=f"note {i}")
            assert sem.wait_idle(10)
    assert read_note("n3") is not None
    msgs = [r.getMessage() for r in caplog.records if "onnx exploded" in r.getMessage()]
    assert len(msgs) == 1
    assert "onnx exploded" in sem.embedding_status()["last_error"]


def test_write_inside_a_failed_batch_is_not_queued(home, sem):
    from myclaw.knowledge.db import KnowledgeDB
    from myclaw.knowledge.parser import Note

    with pytest.raises(RuntimeError):
        with KnowledgeDB("default") as db, db.batch():
            db.sync_entity_from_note(Note(name="x", permalink="x", title="X", content="x"))
            raise RuntimeError("abort")
    assert sem.wait_idle(10)
    assert _rows(home, "SELECT COUNT(*) FROM entities") == [(0,)]
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors") == [(0,)]


# ── Step 3: backfill + status ──────────────────────────────────────────────

def test_backfill_and_status(home, sem, monkeypatch):
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    st = sem.embedding_status()
    assert (st["notes"], st["embedded"], st["stale"], st["enabled"], st["installed"]) == (3, 0, 3, False, True)
    assert st["ready"] is False and "off" in st["reason"]
    seen = []
    r = sem.backfill_embeddings("default", limit=2, progress=lambda d, t: seen.append((d, t)))
    assert r["ok"] and r["notes"] == 2 and seen[-1] == (2, 2)
    assert sem.embedding_status()["stale"] == 1
    r = sem.backfill_embeddings("default")
    assert r["ok"] and r["notes"] == 1
    n = _calls(sem)
    r = sem.backfill_embeddings("default", all_notes=True)       # checks all, embeds nothing new
    assert r["ok"] and r["checked"] == 3 and r["embedded"] == 0 and _calls(sem) == n
    st = sem.embedding_status()
    assert (st["embedded"], st["stale"], st["chunks"]) == (3, 0, 3)


def test_backfill_without_the_library_says_how_to_install(home, monkeypatch):
    from myclaw.knowledge import semantic

    monkeypatch.setattr(importlib_util(), "find_spec", lambda name, *a: None)
    r = semantic.backfill_embeddings("default")
    assert r["ok"] is False and "pip install" in r["message"] and "fastembed" in r["message"]
    st = semantic.embedding_status("default")
    assert st["installed"] is False


def importlib_util():
    import importlib.util

    return importlib.util


def test_cli_embed_command(home, sem, monkeypatch):
    from click.testing import CliRunner
    from myclaw.cli import cli

    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    monkeypatch.setattr("myclaw.cli.load_config", lambda *a, **k: None)
    r = CliRunner().invoke(cli, ["knowledge", "embed"])
    assert r.exit_code == 0, r.output
    assert "0/3 notes embedded" in r.output and "Embedded 3 note(s)" in r.output
    r = CliRunner().invoke(cli, ["knowledge", "embed"])
    assert "Every note is embedded" in r.output


# ── Step 4: hybrid search ──────────────────────────────────────────────────

def test_rrf_fuses_and_deduplicates():
    from myclaw.knowledge.semantic import rrf

    fused = rrf([[1, 2, 3], [4, 2, 2]])
    ids = [k for k, _ in fused]
    assert ids[0] == 2                    # in both lists
    assert set(ids) == {1, 2, 3, 4} and len(ids) == 4
    assert abs(dict(fused)[1] - 1 / 61) < 1e-9


def test_hybrid_finds_semantic_only_notes_and_keeps_fts_ranking(home, sem):
    from myclaw.knowledge import search_notes

    _three_notes()
    assert sem.wait_idle(10)
    fts = [n.permalink for n in search_notes("car")]
    assert fts == ["garage"]
    info = {}
    hyb = [n.permalink for n in search_notes("car", mode="hybrid", info=info)]
    assert hyb[0] == "garage"             # found by words and by meaning: first
    assert "vehicle" in hyb               # found only by meaning
    assert "bread" not in hyb             # below the similarity floor
    assert info["mode"] == "hybrid"
    sem_only = [n.permalink for n in search_notes("car", mode="semantic")]
    assert set(sem_only) == {"garage", "vehicle"}
    auto = [n.permalink for n in search_notes("car", mode="auto")]
    assert auto == hyb


def test_boolean_queries_use_fts_only(home, sem):
    from myclaw.knowledge import search_notes

    _three_notes()
    assert sem.wait_idle(10)
    for q in ("car OR bread", '"the car"', "garage NOT bikes", "sourd*"):
        info = {}
        got = [n.permalink for n in search_notes(q, boolean=True, mode="hybrid", info=info)]
        assert info["mode"] == "fts", q
        assert got == [n.permalink for n in search_notes(q, boolean=True)], q
        assert "vehicle" not in got


def test_auto_falls_back_to_fts_when_off_not_installed_or_slow(home, sem, monkeypatch):
    from myclaw.knowledge import search_notes
    from myclaw.knowledge.semantic import HashEmbedder

    _three_notes()
    assert sem.wait_idle(10)
    # off
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    info = {}
    assert [n.permalink for n in search_notes("car", mode="auto", info=info)] == ["garage"]
    assert info["mode"] == "fts" and "fallback" not in info
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    # not installed
    with patch.object(sem, "library_installed", lambda: False):
        info = {}
        assert [n.permalink for n in search_notes("car", mode="auto", info=info)] == ["garage"]
        assert "not installed" in info["fallback"]
    # slow query embedding
    sem.set_embedder_factory(lambda m: HashEmbedder(model=m, synonyms={"car": "automobile"},
                                                    delay=1.0))
    info = {}
    t0 = time.time()
    got = [n.permalink for n in search_notes("car", mode="auto", semantic_timeout=0.2, info=info)]
    assert time.time() - t0 < 0.9 + 0.5     # model load waits too, but each stays bounded
    assert got == ["garage"] and info["mode"] == "fts"
    time.sleep(1.2)                          # the embedding finished in the background…
    info = {}
    got = [n.permalink for n in search_notes("car", mode="auto", semantic_timeout=0.2, info=info)]
    assert info["mode"] == "hybrid" and "vehicle" in got   # …and is cached


def test_search_with_no_vectors_yet_uses_fts(home, sem, monkeypatch):
    from myclaw.knowledge import search_notes

    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    info = {}
    assert [n.permalink for n in search_notes("car", mode="auto", info=info)] == ["garage"]
    assert "Backfill" in info["fallback"]


def test_default_search_notes_is_unchanged(home, sem):
    """mode defaults to "fts": internal callers (NOT exclusion, tags…) stay lexical."""
    from myclaw.knowledge import search_notes

    _three_notes()
    assert sem.wait_idle(10)
    assert [n.permalink for n in search_notes("car")] == ["garage"]
    assert [n.permalink for n in search_notes("car", "default", 10, None, True)] == ["garage"]


def test_search_knowledge_tool_mode(home, sem, monkeypatch):
    from myclaw.tools.kb import search_knowledge

    _three_notes()
    assert sem.wait_idle(10)
    out = search_knowledge("car")
    assert "Vehicle" in out and "Garage" in out and "words + meaning" in out
    out = search_knowledge("car", mode="fts")
    assert "Vehicle" not in out and "Garage" in out
    out = search_knowledge("car", mode="sideways")
    assert "not known" in out and "Vehicle" in out
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    out = search_knowledge("car")
    assert "Vehicle" not in out and "meaning" not in out
    out = search_knowledge("car", mode="semantic")
    assert "semantic search not used" in out and "off" in out and "Garage" in out
    out = search_knowledge("tag:nothing car", mode="hybrid")
    assert "No results found" in out


def test_tool_schema_has_mode():
    import myclaw.tools  # noqa: F401
    from myclaw.tools.core import _generate_schemas

    schema = {s["function"]["name"]: s for s in _generate_schemas()}["search_knowledge"]
    props = schema["function"]["parameters"]["properties"]
    assert "mode" in props and "mode" not in schema["function"]["parameters"]["required"]


def test_advanced_search_semantic_list_is_a_real_ranking(home, sem):
    from myclaw.knowledge import SearchFilters, search_advanced

    _three_notes()
    assert sem.wait_idle(10)
    res = search_advanced("car", filters=SearchFilters())
    by = {r.permalink: r for r in res}
    assert "vehicle" in by and by["vehicle"].fts_score == 0.0 and by["vehicle"].semantic_score > 0.2
    assert res[0].permalink == "garage"
    # off: the FTS result only
    sem._OPTIONS["semantic_search"] = False
    try:
        assert [r.permalink for r in search_advanced("car")] == ["garage"]
    finally:
        sem._OPTIONS["semantic_search"] = True


# ── agent context ──────────────────────────────────────────────────────────

@pytest.fixture
def agent(home):
    from myclaw.agent import Agent
    from myclaw.config import load_config

    a = Agent(load_config(), name="default")
    a._provider = object()
    a.set_gap_cache_enabled(False)
    return a


def test_agent_context_uses_hybrid_when_on(home, sem, agent):
    _three_notes()
    assert sem.wait_idle(10)
    sem.get_embedder(wait=5)                 # loaded (the first turn only warms it up)
    res = agent._search_knowledge_context_sync("where is my car today", "default",
                                               return_structured=True)
    assert "**Vehicle**" in res.context and "**Garage**" in res.context
    assert res.metadata.get("search_mode") == "hybrid"


def test_agent_context_first_turn_does_not_wait_for_the_model(home, sem, agent):
    _three_notes()
    assert sem.wait_idle(10)
    sem._reset_caches()                      # model not loaded
    from myclaw.knowledge.semantic import HashEmbedder

    def slow_load(m):
        time.sleep(1.0)
        return HashEmbedder(model=m, synonyms={"car": "automobile"})

    sem.set_embedder_factory(slow_load)
    t0 = time.time()
    res = agent._search_knowledge_context_sync("where is my car today", "default",
                                               return_structured=True)
    assert time.time() - t0 < 0.8
    assert "**Garage**" in res.context and res.metadata.get("search_mode") == "fts"
    assert sem.get_embedder(wait=5) is not None    # warmed up in the background


def test_agent_context_unchanged_when_off(home, sem, agent, monkeypatch):
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    calls = []

    def fake_search(query, user_id, limit=10, boolean=False):   # the pre-phase-3 signature
        calls.append((query, boolean))
        return []

    with patch("myclaw.agent.search_notes", side_effect=fake_search):
        agent._search_knowledge_context_sync("quantum computing basics", "default")
    assert calls and calls[0] == ("quantum computing basics", False)


# ── API ────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from myclaw.web import knowledge_api, knowledge_semantic_api

    app = FastAPI()
    app.include_router(knowledge_api.router)
    app.include_router(knowledge_semantic_api.router)
    return TestClient(app)


def _poll(api, until, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = api.get("/api/knowledge/semantic/status").json()
        if until(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"timed out: {st}")


def test_api_status_backfill_and_search_mode(api, home, sem, monkeypatch):
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    st = api.get("/api/knowledge/semantic/status").json()
    assert (st["notes"], st["embedded"], st["installed"], st["job"]) == (3, 0, True, None)
    r = api.post("/api/knowledge/semantic/backfill")
    assert r.status_code == 200 and r.json()["job"]["status"] == "running"
    st = _poll(api, lambda s: s["job"]["status"] != "running")
    assert st["job"]["status"] == "done" and st["embedded"] == 3 and st["ready"]
    assert st["job"]["done"] == st["job"]["total"] == 3
    assert api.delete("/api/knowledge/semantic/backfill").status_code == 404
    r = api.get("/api/knowledge/search", params={"q": "car", "mode": "hybrid"}).json()
    assert set(r["results"]) >= {"garage", "vehicle"} and r["mode"] == "hybrid"
    r = api.get("/api/knowledge/search", params={"q": "car", "mode": "fts"}).json()
    assert r["results"] == ["garage"] and r["mode"] == "fts"
    r = api.get("/api/knowledge/search", params={"q": "car"}).json()   # default auto
    assert "vehicle" in r["results"]
    assert api.get("/api/knowledge/search", params={"q": "car", "mode": "x"}).status_code == 400
    assert api.get("/api/knowledge/semantic/status", params={"base": "../x"}).status_code == 400


def test_api_backfill_refused_without_library(api, home, monkeypatch):
    from myclaw.knowledge import semantic

    monkeypatch.setattr(semantic, "library_installed", lambda: False)
    r = api.post("/api/knowledge/semantic/backfill")
    assert r.status_code == 400 and "fastembed" in r.json()["detail"]


def test_api_install_runs_pip_and_refuses_when_installed(api, home, monkeypatch):
    import sys

    from myclaw.knowledge import semantic
    from myclaw.web import knowledge_semantic_api as mod

    installed = {"v": False}
    monkeypatch.setattr(semantic, "library_installed", lambda: installed["v"])
    monkeypatch.setattr(sys, "base_prefix", "/not/the/venv")
    seen = {}

    class FakeProc:
        def __init__(self, cmd, stdout=None, **kw):
            seen["cmd"] = cmd
            stdout.write("Successfully installed fastembed-0.9.0\n")
            installed["v"] = True

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return 0

    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(semantic, "get_embedder", lambda *a, **k: None)
    mod._install.clear()
    mod._install["running"] = False
    r = api.post("/api/knowledge/semantic/install")
    assert r.status_code == 200
    st = _poll(api, lambda s: s["install"].get("running") is False)
    assert st["install"]["ok"] is True and "Successfully installed" in st["install"]["log"]
    assert seen["cmd"][:4] == [sys.executable, "-m", "pip", "install"]
    assert seen["cmd"][-1].startswith("fastembed") and seen["timeout"] == mod.INSTALL_TIMEOUT
    assert api.post("/api/knowledge/semantic/install").status_code == 409


def test_api_install_refused_outside_a_venv(api, home, monkeypatch):
    import sys

    from myclaw.knowledge import semantic

    monkeypatch.setattr(semantic, "library_installed", lambda: False)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    r = api.post("/api/knowledge/semantic/install")
    assert r.status_code == 400 and "pip install" in r.json()["detail"]


# ── old databases, config ──────────────────────────────────────────────────

def test_old_database_without_vector_tables_opens_and_works(home, sem):
    from myclaw.knowledge import search_notes, write_note
    from myclaw.knowledge.db import KnowledgeDB

    write_note("Garage", content="The car is parked in the garage.")
    assert sem.wait_idle(10)
    conn = sqlite3.connect(_db(home))           # make it look like a pre-phase-3 index
    for t in ("entities_vec_bd", "entities_vec_ad"):
        conn.execute(f"DROP TRIGGER {t}")
    for t in ("note_vectors", "note_embed_state", "note_vectors_carry"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    conn.close()
    sem._MATS.clear()
    st = sem.embedding_status()                  # opens it: tables come back, nothing lost
    assert st["notes"] == 1 and st["stale"] == 1
    assert [n.permalink for n in search_notes("car", mode="auto")] == ["garage"]
    with KnowledgeDB("default"):
        pass
    assert _rows(home, "SELECT COUNT(*) FROM entities") == [(1,)]


def test_schema_init_never_drops_vectors(home, sem):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.db import KnowledgeDB

    write_note("Box", content="A box.")
    assert sem.wait_idle(10)
    for _ in range(2):
        with KnowledgeDB("default"):
            pass
    assert _rows(home, "SELECT COUNT(*) FROM note_vectors") == [(1,)]


def test_config_defaults_and_apply(home):
    from myclaw.config import AppConfig, _apply_knowledge_semantic
    from myclaw.knowledge import semantic

    cfg = AppConfig()
    assert cfg.knowledge.semantic_search is False
    assert cfg.knowledge.embedding_model == "BAAI/bge-small-en-v1.5"
    saved = dict(semantic._OPTIONS)
    try:
        cfg.knowledge.semantic_search = True
        cfg.knowledge.embedding_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        _apply_knowledge_semantic(cfg)
        assert semantic.enabled() and semantic.model_info().chunk_chars == 450
    finally:
        semantic._OPTIONS.clear()
        semantic._OPTIONS.update(saved)
        semantic._reset_caches()


def test_fastembed_embedder_is_imported_lazily(home, monkeypatch):
    """Without fastembed the module imports fine and loading reports how to install."""
    import builtins

    from myclaw.knowledge import semantic

    real = builtins.__import__

    def no_fastembed(name, *a, **k):
        if name == "fastembed":
            raise ImportError("No module named 'fastembed'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_fastembed)
    with pytest.raises(ImportError):
        semantic.FastEmbedEmbedder("BAAI/bge-small-en-v1.5", cache_dir=Path(home) / "m")


def test_fastembed_real_model_if_available(home):
    pytest.importorskip("fastembed")
    from myclaw.knowledge import semantic

    try:
        emb = semantic.FastEmbedEmbedder(semantic.DEFAULT_MODEL)
    except Exception as e:  # no network to Hugging Face here
        pytest.skip(f"model not available: {e}")
    v = emb.embed_query("hello")
    assert v.shape == (emb.dim,) and abs(float((v * v).sum()) - 1.0) < 1e-3
