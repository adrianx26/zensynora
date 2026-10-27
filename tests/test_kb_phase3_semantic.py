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
    semantic.wait_idle(20)
    w = semantic._WORKER
    with w.cv:      # a clean worker per test (no bases / backoff of earlier tests)
        for part in (w.known, w.first_seen, w.deferred, w.backoff, w.retry_load, w.last_prune):
            part.clear()
        w.periodic = False
    monkeypatch.setattr(semantic, "_APPLIED", False)
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
    """Counts only (no settings save in this process: _APPLIED is False)."""
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


def test_api_install_runs_pip_with_pins_and_refuses_when_installed(api, home, monkeypatch):
    """2026-10-24 (review): deterministic — the install runs inline (no thread)
    and only the module's own process hooks are replaced, never the global
    subprocess.Popen (the cause of the 1-in-4 flake: other threads of the test
    process got the fake)."""
    import subprocess
    import sys

    from myclaw.knowledge import semantic
    from myclaw.web import knowledge_semantic_api as mod

    installed = {"v": False}
    monkeypatch.setattr(semantic, "library_installed", lambda: installed["v"])
    monkeypatch.setattr(semantic, "get_embedder", lambda *a, **k: None)
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

    def fake_run(cmd, **kw):
        seen["freeze"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=(
            "numpy==2.2.6\n-e git+https://x/zensynora#egg=zensynora\nzensynora==0.7.5\n"
            "pkg @ file:///tmp/pkg.whl\nrequests==2.32.3\n"), stderr="")

    monkeypatch.setattr(mod, "_popen", FakeProc)
    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.setattr(mod, "_spawn", lambda target, *args: target(*args))
    monkeypatch.setattr(mod, "_install", {"running": False})
    r = api.post("/api/knowledge/semantic/install")
    assert r.status_code == 200
    st = api.get("/api/knowledge/semantic/status").json()
    assert st["install"]["running"] is False and st["install"]["ok"] is True
    assert "Successfully installed" in st["install"]["log"]
    cmd = seen["cmd"]
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"] and seen["timeout"] == mod.INSTALL_TIMEOUT
    assert any(c.startswith("fastembed") for c in cmd)
    pins = Path(cmd[cmd.index("-c") + 1]).read_text().split()
    assert pins == ["numpy==2.2.6", "requests==2.32.3"]       # no editable / URL / zensynora
    assert api.post("/api/knowledge/semantic/install").status_code == 409


def test_api_install_without_freeze_still_runs_unpinned(api, home, monkeypatch):
    import sys

    from myclaw.knowledge import semantic
    from myclaw.web import knowledge_semantic_api as mod

    monkeypatch.setattr(semantic, "library_installed", lambda: False)
    monkeypatch.setattr(sys, "base_prefix", "/not/the/venv")
    seen = {}

    class FailProc:
        def __init__(self, cmd, stdout=None, **kw):
            seen["cmd"] = cmd

        def wait(self, timeout=None):
            return 1

    def broken_run(cmd, **kw):
        raise OSError("no pip")

    monkeypatch.setattr(mod, "_popen", FailProc)
    monkeypatch.setattr(mod, "_run", broken_run)
    monkeypatch.setattr(mod, "_spawn", lambda target, *args: target(*args))
    monkeypatch.setattr(mod, "_install", {"running": False})
    assert api.post("/api/knowledge/semantic/install").status_code == 200
    st = api.get("/api/knowledge/semantic/status").json()["install"]
    assert st["ok"] is False and "exit code 1" in st["error"] and "-c" not in seen["cmd"]
    assert "not pinned" in st["log"]


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


# ── 2026-10-24 review fixes ────────────────────────────────────────────────

def test_review1_exact_keyword_hit_stays_in_hybrid_top(home, sem):
    """rrf2.py: 30 similar notes must not push the one note with the code out."""
    from myclaw.knowledge import search_notes, write_note

    sem._OPTIONS["min_score"] = 0.05
    write_note("Kx", content="ZX4521 was resolved by swapping the cable.")
    for i in range(30):
        write_note(f"Printer fix {i}", content=f"How the printer error got fixed {i}: what fixed "
                                               "it was a driver update. fixed printer error")
    assert sem.wait_idle(30)
    q = "what fixed ZX4521 printer error"
    fts = [n.permalink for n in search_notes(q, limit=5)]
    hyb = [n.permalink for n in search_notes(q, limit=5, mode="hybrid")]
    assert fts[0] == "kx" and hyb[:2] == fts[:2]


def test_review1_fuse_weights_words_and_pins_top_word_hits():
    from myclaw.knowledge.semantic import fuse

    # meaning-only notes 7, 8 rank high by meaning; word hits keep their places
    assert fuse([1, 2, 3], [7, 8, 3, 2])[:2] == [1, 2]
    order = fuse([1, 2, 3], [7, 8, 3, 2])
    assert order.index(3) < order.index(7)            # in both lists: above meaning-only
    assert set(order) == {1, 2, 3, 7, 8}
    assert fuse([], [5, 6]) == [5, 6]
    assert fuse([4], []) == [4]


def test_review2_switch_on_queues_waiting_notes(home, sem, monkeypatch):
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    assert sem.embedding_status()["waiting"] == 3
    monkeypatch.setattr(sem, "_APPLIED", True)        # a settings save, not the start
    sem.set_options(semantic_search=True)
    assert sem.wait_idle(10)
    st = sem.embedding_status()
    assert st["waiting"] == 0 and st["embedded"] == 3


def test_review2_model_change_requeues(home, sem, monkeypatch):
    _three_notes()
    assert sem.wait_idle(10)
    monkeypatch.setattr(sem, "_APPLIED", True)
    sem.set_options(embedding_model="test/other")
    assert sem.wait_idle(10)
    st = sem.embedding_status()
    assert st["model"] == "test/other" and st["waiting"] == 0 and st["chunks"] == 3


def test_review2_first_write_in_a_process_picks_up_waiting_notes(home, sem, monkeypatch):
    from myclaw.knowledge import write_note

    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()                                    # e.g. written by a CLI command
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    write_note("Fourth", content="another note")
    assert sem.wait_idle(10)
    assert sem.embedding_status()["waiting"] == 0


def test_review2_load_error_keeps_notes_and_retries_with_backoff(home, sem, monkeypatch):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.semantic import HashEmbedder

    monkeypatch.setattr(sem, "RETRY_MIN", 0.3)
    state = {"fail": True, "loads": 0}

    def flaky(m):
        state["loads"] += 1
        if state["fail"]:
            raise OSError("no network")
        return HashEmbedder(model=m)

    sem.set_embedder_factory(flaky)
    write_note("A", content="one")
    write_note("B", content="two")
    assert sem.wait_idle(10)
    st = sem.embedding_status()
    assert st["waiting"] == 2 and "next try" in st["waiting_text"] and st["retry_in"] is not None
    assert "no network" in (st["last_error"] or "")
    state["fail"] = False
    end = time.time() + 10
    while time.time() < end and sem.embedding_status()["waiting"]:
        time.sleep(0.1)
    assert sem.embedding_status()["waiting"] == 0 and state["loads"] >= 2


def test_review2_periodic_scan_in_long_running_process(home, sem, monkeypatch):
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", False)
    _three_notes()
    monkeypatch.setitem(sem._OPTIONS, "semantic_search", True)
    monkeypatch.setattr(sem, "SCAN_INTERVAL", 0.2)
    sem.start_background()
    try:
        end = time.time() + 10
        while time.time() < end and sem.embedding_status()["waiting"]:
            time.sleep(0.1)
        assert sem.embedding_status()["waiting"] == 0
    finally:
        with sem._WORKER.cv:
            sem._WORKER.periodic = False


def test_review3_carry_pruned_by_the_worker_at_most_daily(home, sem, monkeypatch):
    from myclaw.knowledge import write_note

    calls = []
    monkeypatch.setattr(sem, "prune_carry", lambda path: calls.append(path))
    for i in range(3):
        write_note(f"P{i}", content=f"note {i}")
        assert sem.wait_idle(10)
    assert len(calls) == 1


def test_review4_timed_out_queries_do_not_pile_up(home, sem):
    from myclaw.knowledge.semantic import HashEmbedder

    sem.set_embedder_factory(lambda m: HashEmbedder(model=m, delay=0.5))
    assert sem.get_embedder(wait=5) is not None
    for i in range(6):
        assert sem.query_vector(f"query {i}", timeout=0.05) is None
    assert sem._ENGINE.pending() <= 1                  # the rest were cancelled
    assert sem._ENGINE.thread.daemon


def test_review5_search_budget_includes_model_loading(home, sem):
    from myclaw.knowledge import search_notes
    from myclaw.knowledge.semantic import HashEmbedder

    _three_notes()
    assert sem.wait_idle(10)
    sem._reset_caches()

    def slow_load(m):
        time.sleep(3)
        return HashEmbedder(model=m)

    sem.set_embedder_factory(slow_load)
    info = {}
    t0 = time.time()
    search_notes("car", mode="hybrid", semantic_timeout=0.5, info=info)
    assert time.time() - t0 < 1.5 and info["mode"] == "fts"


def test_review7_advanced_filters_apply_to_all_matches(home):
    from myclaw.knowledge import SearchFilters, search_advanced, write_note
    from myclaw.knowledge.parser import Observation

    for i in range(60):
        write_note(f"Server {i}", content="server server server config " * 3)
    write_note("Special", content="Once mentions server. " + "filler words here " * 200,
               observations=[Observation(category="special", content="a special fact")])
    r = search_advanced("server", filters=SearchFilters(categories=["special"]), limit=5)
    assert [x.permalink for x in r] == ["special"]


def test_review7_search_fts_returns_limit_distinct_notes(home):
    from myclaw.knowledge import search_notes, write_note

    for i in range(12):   # title, text and fact all match: 3 index rows per note
        write_note(f"Server {i}", content="server notes", observations=[
            __import__("myclaw.knowledge.parser", fromlist=["Observation"]).Observation(
                "fact", "server fact", [])])
    assert len(search_notes("server", limit=10)) == 10


def test_review9_floor_keeps_agent_strategy_two_and_gap_logging(home, sem, agent, monkeypatch):
    _three_notes()
    assert sem.wait_idle(10)
    sem.get_embedder(wait=5)
    monkeypatch.setitem(sem._OPTIONS, "min_score", 0.99)   # nothing is similar enough
    agent._kb_gaps.pop("default", None)
    res = agent._search_knowledge_context_sync("quantum chromodynamics lattice", "default",
                                               return_structured=True)
    assert res.has_results is False and res.gap_logged is True


def test_review9_default_floors():
    from myclaw.knowledge import semantic

    assert semantic.MODELS["BAAI/bge-small-en-v1.5"].min_score == 0.75
    assert semantic.MODELS[
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"].min_score == 0.6


def test_review10_one_inference_at_a_time_and_queries_first(home, sem):
    import threading

    from myclaw.knowledge import write_note
    from myclaw.knowledge.semantic import HashEmbedder

    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    class Counting(HashEmbedder):
        def _track(self, fn, *a):
            with lock:
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
            time.sleep(0.01)
            try:
                return fn(*a)
            finally:
                with lock:
                    live["now"] -= 1

        def embed_passages(self, texts):
            return self._track(super().embed_passages, texts)

        def embed_query(self, text):
            return self._track(super().embed_query, text)

    sem.set_embedder_factory(lambda m: Counting(model=m))
    for i in range(20):
        write_note(f"C{i}", content=f"note number {i} " * 50)
    t = threading.Thread(target=sem.backfill_embeddings, kwargs={"all_notes": True})
    t.start()
    for i in range(10):
        sem.query_vector(f"q{i}", timeout=2, wait_load=2)
    t.join(20)
    assert sem.wait_idle(20)
    assert live["max"] == 1

    # priority: a query submitted after a waiting batch runs first
    gate = threading.Event()
    order = []
    sem._ENGINE.submit(1, gate.wait, 5)                       # occupies the thread
    time.sleep(0.05)
    f1 = sem._ENGINE.submit(sem.PRIO_PASSAGES, lambda: order.append("batch"))
    f2 = sem._ENGINE.submit(sem.PRIO_QUERY, lambda: order.append("query"))
    gate.set()
    f1.result(5), f2.result(5)
    assert order == ["query", "batch"]


def test_review11_updater_reinstalls_extras_with_declared_range(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace

    from updater import steps

    new = tmp_path / "new"
    new.mkdir()
    (new / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.optional-dependencies]\n'
        'semantic = ["fastembed>=0.5,<1.0"]\ningest = ["python-docx>=1.1,<2.0"]\n')
    old = tmp_path / "old"
    calls = []

    def fake_sh(cmd, check=True, timeout=None):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if "list" in cmd and "--not-required" in cmd:
            out = [{"name": "fastembed"}, {"name": "python_docx"}, {"name": "anthropic"}]
        elif "list" in cmd:
            out = []
        else:
            out = None
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out) if out is not None else "")

    monkeypatch.setattr(steps, "sh", fake_sh)
    monkeypatch.setattr(steps, "venv_python", lambda d: Path(__file__))   # "exists"
    ctx = SimpleNamespace(current_dir=old, new_dir=new, note=lambda m: None)
    steps.BuildRelease()._keep_optional_packages(ctx, ["py", "-m", "pip"])
    install = [c for c in calls if "install" in c][0]
    assert "fastembed>=0.5,<1.0" in install and "python-docx>=1.1,<2.0" in install
    assert "anthropic" in install
    import updater
    assert updater.UPDATER_PROTOCOL >= 4
