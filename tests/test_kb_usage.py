"""Knowledge base usage log and benchmarks (2026-10-27).

Hooks record the automatic context, the knowledge tools and agent writes with
the agent and "for what"; secrets are redacted; retention prunes; a locked or
unwritable database never breaks a turn; stats / benchmarks are correct on a
small knowledge base; the API routes; switched off = nothing recorded.
"""
import asyncio
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    from myclaw.knowledge import benchmarks, usage

    usage.flush(3)
    usage.set_options(usage_log=True, kb_enabled=True, retention_days=90, max_rows=200_000)
    benchmarks.clear_cache()
    yield tmp_path
    usage.flush(3)
    usage.set_options(usage_log=True, kb_enabled=True, retention_days=90, max_rows=200_000)


def _kb(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Observation, Relation

    write_note("Home server", content="Runs ubuntu and docker for the homelab.", tags=["homelab"],
               observations=[Observation("spec", "32 GB RAM", []), Observation("Spec", "8 cores", []),
                             Observation("os", "Ubuntu 24.04", [])],
               relations=[Relation("uses", "Docker"), Relation("backs_up_to", "Nas box")])
    write_note("Docker", content="Container runtime used everywhere.", tags=["tools", "homelab"])
    write_note("Lonely", content="Nothing links here.")


def _rows(**kw):
    from myclaw.knowledge import usage

    assert usage.flush(5)
    return usage.query_log("default", **kw)["rows"]


@pytest.fixture
def agent(home):
    from myclaw.agent import Agent
    from myclaw.config import load_config

    a = Agent(load_config(), name="researcher")
    a._provider = object()
    a.set_gap_cache_enabled(False)
    return a


# ── hooks ───────────────────────────────────────────────────────────────────

def test_auto_context_is_recorded_with_agent_channel_and_reason(home, agent):
    from myclaw import events
    from myclaw.knowledge import usage

    _kb(home)
    tok = usage.begin_turn("researcher", "What does my home server run?")
    ch = events.CURRENT_CHANNEL.set("chan-1")
    try:
        ctx = asyncio.run(agent._search_knowledge_context("what does my home server run", "webui"))
    finally:
        events.CURRENT_CHANNEL.reset(ch)
        usage.end_turn(tok)
    assert "Home server" in ctx
    (r,) = _rows(kind="auto-context")
    assert r["kind"] == "context" and r["agent"] == "researcher" and r["base"] == "default"
    assert r["channel"] == "chan-1"                       # the executor runs in a copy of the context
    assert r["notes"][0] == "home-server" and r["results"] >= 1
    assert r["reason"] == "What does my home server run?" and r["source"] == "chat"
    assert r["latency_ms"] is not None and r["mode"].startswith("fts")


def test_greeting_without_search_words_is_not_recorded(home, agent):
    _kb(home)
    agent._search_knowledge_context_sync("hi", "default")
    assert _rows() == []


def test_think_sets_the_turn_for_tools(home, agent, monkeypatch):
    from myclaw.tools.kb import search_knowledge

    _kb(home)

    async def impl(message, user_id, depth):
        return search_knowledge("docker")

    monkeypatch.setattr(agent, "_think_impl", impl)
    asyncio.run(agent.think("Which notes mention docker?", user_id="webui"))
    (r,) = _rows()
    assert (r["agent"], r["kind"], r["action"], r["query"]) == ("researcher", "tool", "search_knowledge", "docker")
    assert set(r["notes"]) == {"docker", "home-server"} and r["results"] == 2
    assert r["reason"] == "Which notes mention docker?"


def test_tool_calls_hits_misses_reads_and_writes(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import list_knowledge, read_knowledge, search_knowledge, write_to_knowledge

    _kb(home)
    tok = usage.begin_turn("ops", "check the server")
    try:
        search_knowledge("zzzz nothing here")
        read_knowledge("home-server")
        read_knowledge("does-not-exist")
        write_to_knowledge("New note", "some text", tags="x")
        list_knowledge()
    finally:
        usage.end_turn(tok)
    rows = {(r["action"], r["query"]): r for r in _rows()}
    miss = rows[("search_knowledge", "zzzz nothing here")]
    assert miss["results"] == 0 and miss["ok"] and miss["mode"] == "fts"
    assert rows[("read_knowledge", "home-server")]["notes"] == ["home-server"]
    bad = rows[("read_knowledge", "does-not-exist")]
    assert bad["ok"] is False and bad["results"] == 0 and bad["notes"] == []
    w = rows[("write_to_knowledge", None)]
    assert w["kind"] == "write" and w["notes"] == ["new-note"]
    assert rows[("list_knowledge", None)]["results"] == 4
    assert all(r["agent"] == "ops" and r["reason"] == "check the server" for r in rows.values())


def test_extraction_and_session_summary_writes(home, agent):
    from myclaw.knowledge import extract, usage

    tok = usage.begin_turn("researcher", "remember my NAS")
    try:
        n = extract.save_items([{"title": "Nas box", "content": "Synology DS920+", "tags": "homelab",
                                 "facts": [{"category": "spec", "text": "4 bays"}]}],
                               "webui", ["auto-extracted"], 5, "[KB-AUTO]", "chat 2026-10-27")
        pl = agent._store_session_summary("We talked about the NAS.", "webui")
    finally:
        usage.end_turn(tok)
    assert n == 1
    rows = {r["action"]: r for r in _rows(kind="write")}
    assert rows["extraction"]["notes"] == ["nas-box"] and rows["extraction"]["query"] == "chat 2026-10-27"
    assert rows["session_summary"]["notes"] == [pl]
    assert rows["extraction"]["reason"] == "remember my NAS"


def test_assimilate_is_recorded_as_a_write(home):
    from myclaw.tools.ingest import assimilate

    doc = home / "guide.md"
    doc.write_text("# Guide\n\nSome text about backups.\n\n## Part two\n\nMore text.\n")
    out = assimilate(str(doc))
    assert "Assimilated" in out
    (r,) = _rows(kind="assimilate")
    assert r["kind"] == "write" and r["results"] >= 1 and r["notes"]


def test_delegation_records_source_and_via(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge

    _kb(home)
    outer = usage.begin_turn("default", "plan the backup")
    try:
        inner = usage.begin_turn("researcher", "find what the NAS stores")
        try:
            search_knowledge("docker")
        finally:
            usage.end_turn(inner)
    finally:
        usage.end_turn(outer)
    (r,) = _rows()
    assert (r["agent"], r["source"], r["via"], r["reason"]) == (
        "researcher", "delegation", "default", "find what the NAS stores")


# ── redaction, retention, robustness ────────────────────────────────────────

def test_secrets_are_redacted_and_reason_is_capped(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge

    _kb(home)
    secret = "ghp_" + "a" * 36
    tok = usage.begin_turn("ops", f"use token={secret} and password: hunter2pass " + "x" * 400)
    try:
        search_knowledge(f"api_key={secret}")
    finally:
        usage.end_turn(tok)
    (r,) = _rows()
    assert secret not in r["reason"] and secret not in (r["query"] or "")
    assert "hunter2pass" not in r["reason"] and "[redacted]" in r["reason"]
    assert len(r["reason"]) <= usage.REASON_CHARS
    raw = sqlite3.connect(str(usage.db_path())).execute("SELECT group_concat(reason || query) FROM kb_usage").fetchone()[0]
    assert secret not in raw


def test_retention_prunes_old_rows_and_caps_rows(home):
    from myclaw.knowledge import usage

    con = usage.connect()
    now = time.time()
    with con:
        for i in range(1200):
            usage._insert(con, {"ts": now - (100 * 86_400 if i < 100 else i), "base": "default", "agent": "a",
                                "kind": "tool", "action": "search_knowledge", "query": "q", "notes": ["n"],
                                "results": 1, "reason": ""})
    usage.set_options(retention_days=90, max_rows=1000)
    deleted = usage.prune(con)
    left = con.execute("SELECT COUNT(*) FROM kb_usage").fetchone()[0]
    assert deleted == 200 and left == 1000
    assert con.execute("SELECT COUNT(*) FROM kb_usage WHERE ts < ?", (now - 90 * 86_400,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM kb_usage_notes").fetchone()[0] == 1000
    # at most once a day
    assert usage._maybe_prune(con) == 0
    con.close()


def test_locked_database_never_slows_or_breaks_a_turn(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge

    _kb(home)
    usage.connect().close()  # create it
    locker = sqlite3.connect(str(usage.db_path()), timeout=0.1)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.perf_counter()
        out = search_knowledge("docker")
        assert "Docker" in out
        assert time.perf_counter() - t0 < 1.0
    finally:
        time.sleep(0.1)
        locker.rollback()
        locker.close()
    usage.flush(5)


def test_unwritable_database_drops_records_quietly(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge

    _kb(home)
    usage.db_path().mkdir(parents=True)  # a folder where the database should be
    assert "Docker" in search_knowledge("docker")
    assert usage.flush(5)
    assert usage.query_log("default") == {"rows": [], "total": 0}
    assert usage._thread is not None and usage._thread.is_alive()  # the writer survived


def test_recording_is_cheap(home):
    from myclaw.knowledge import usage

    t0 = time.perf_counter()
    for _ in range(500):
        usage.record("tool", "search_knowledge", base="default", query="q", notes=["a", "b"],
                     results=2, latency_ms=1.0, reason="r")
    per_call_ms = (time.perf_counter() - t0) * 1000 / 500
    assert per_call_ms < 2.0
    usage.flush(10)


def test_switch_off_records_nothing(home, agent):
    from myclaw.config import AppConfig, _apply_knowledge_usage
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge

    _kb(home)
    _apply_knowledge_usage(AppConfig(knowledge={"usage_log": False}))
    assert usage.enabled() is False
    search_knowledge("docker")
    agent._search_knowledge_context_sync("what does my home server run", "default")
    assert usage.record("tool", "x") is False
    assert usage.snapshot("default", {"notes": 1}) is False
    _apply_knowledge_usage(AppConfig(knowledge={"enabled": False}))
    assert usage.enabled() is False
    search_knowledge("docker")
    _apply_knowledge_usage(AppConfig())
    assert usage.enabled() is True and AppConfig().knowledge.usage_log is True
    assert _rows() == []


# ── stats, benchmarks, API ──────────────────────────────────────────────────

def test_stats_and_benchmarks_on_a_small_kb(home):
    from myclaw.knowledge import usage
    from myclaw.knowledge.benchmarks import compute, titles
    from myclaw.tools.kb import read_knowledge, search_knowledge, write_to_knowledge

    _kb(home)
    tok = usage.begin_turn("ops", "why")
    try:
        search_knowledge("docker")             # hit: docker, home-server
        search_knowledge("qqqq unknown")      # gap
        read_knowledge("docker")
        write_to_knowledge("Extra", "text")
    finally:
        usage.end_turn(tok)
    usage.flush(5)
    s = usage.stats("default", 30, titles=titles("default"))
    assert s["accesses_7d"] == 3 and s["writes_7d"] == 1
    assert (s["searches"], s["hits"], s["hit_rate"]) == (2, 1, 0.5)
    assert [q["query"] for q in s["empty_queries"]] == ["qqqq unknown"]
    assert s["top_notes"][0] == {**s["top_notes"][0], "permalink": "docker", "count": 2, "title": "Docker"}
    assert s["notes_used"] == 2 and s["notes_total"] == 4 and s["never_used"] == 2
    assert {n["permalink"] for n in s["never_used_sample"]} == {"lonely", "extra"}
    today = s["per_day"][-1]
    assert (today["tool"], today["write"]) == (3, 1)
    assert s["per_agent"][0]["agent"] == "ops" and s["per_agent"][0]["total"] == 4
    assert s["latency"]["n"] == 3 and s["latency"]["p95"] >= s["latency"]["avg"] > 0

    b = compute("default")
    assert (b["notes"], b["facts"], b["links"], b["waiting_links"]) == (4, 3, 1, 1)
    assert b["tags"] == 2 and b["categories"] == 2      # "spec" and "Spec" are one category
    assert b["orphans"] == 2                            # Lonely, Extra
    assert (b["components"], b["largest_component"]) == (3, 2)
    assert b["hubs"][0]["links"] == 1 and b["links_per_note"] == 0.5
    assert b["density"] == round(2 * 1 / (4 * 3), 5)
    assert sum(m["count"] for m in b["notes_by_month"]) == 4
    assert b["facts_by_category"][0] == {"name": "spec", "count": 2}
    assert b["index_bytes"] > 0 and b["files"] == 4 and b["error"] is None


def test_api_routes(home):
    from myclaw.knowledge import usage
    from myclaw.tools.kb import search_knowledge
    from myclaw.web import knowledge_usage_api

    _kb(home)
    tok = usage.begin_turn("ops", "look up docker")
    try:
        search_knowledge("docker")
        search_knowledge("qqqzzzunknown")
    finally:
        usage.end_turn(tok)
    app = FastAPI()
    app.include_router(knowledge_usage_api.router)
    c = TestClient(app)

    log = c.get("/api/knowledge/usage?base=default&limit=1").json()
    assert log["total"] == 2 and len(log["rows"]) == 1 and log["agents"] == ["ops"]
    assert log["rows"][0]["query"] == "qqqzzzunknown"          # newest first
    assert c.get("/api/knowledge/usage?q=look+up&kind=tool").json()["total"] == 2
    assert c.get("/api/knowledge/usage?agent=nobody").json()["total"] == 0
    assert c.get("/api/knowledge/usage?base=../x").status_code == 400

    st = c.get("/api/knowledge/usage/stats?base=default&days=7").json()
    assert st["hit_rate"] == 0.5 and len(st["per_day"]) == 7 and st["snapshots"][-1]["notes"] == 3
    b = c.get("/api/knowledge/benchmarks?base=default").json()
    assert b["notes"] == 3 and b["cached"] is True                   # computed by the stats route
    c.get("/api/knowledge/usage/stats?base=default")
    assert len(usage.snapshots("default")) == 1                      # once a day
    n = c.get("/api/knowledge/usage/note/docker?base=default").json()
    assert n["count"] == 1 and n["last_agent"] == "ops" and n["last_action"] == "search_knowledge"


def test_api_without_any_log_yet(home):
    from myclaw.web import knowledge_usage_api

    app = FastAPI()
    app.include_router(knowledge_usage_api.router)
    c = TestClient(app)
    assert c.get("/api/knowledge/usage").json()["rows"] == []
    st = c.get("/api/knowledge/usage/stats").json()
    assert st["accesses_30d"] == 0 and st["hit_rate"] is None
    assert c.get("/api/knowledge/benchmarks").json()["notes"] == 0
    assert c.get("/api/knowledge/usage/note/x").json()["count"] == 0
