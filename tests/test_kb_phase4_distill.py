"""KB phase 4 (2026-10-24): L0–L3 distillation.

Facts deduplicated across notes with confidence and provenance, incremental
updates (edit / delete a note), full rebuild = step-by-step, deterministic
scenarios, the persona (from facts about the user, never secrets),
drill-down, the agent context (on: first and inside the budget; off:
unchanged), the scheduler (no sleeping), the API, the tools, old databases
and semantic near-duplicates with the fake embedder.
"""
import json
import sqlite3
import time

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    from myclaw.knowledge import distill

    # no background thread in tests; the scheduler is driven with tick()
    monkeypatch.setattr(distill._SCHED, "autostart", False)
    monkeypatch.setattr(distill, "_OPTIONS", dict(distill._OPTIONS, distill=False, kb_enabled=True,
                                                  distill_every=20, idle_minutes=10))
    return tmp_path


@pytest.fixture
def on(home, monkeypatch):
    from myclaw.knowledge import distill

    monkeypatch.setitem(distill._OPTIONS, "distill", True)
    return distill


def _db(home):
    return home / ".myclaw" / "knowledge_default.db"


def _rows(home, sql, args=()):
    conn = sqlite3.connect(_db(home))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def O(cat, text, source=None):
    from myclaw.knowledge import Observation

    return Observation(category=cat, content=text, tags=[], provenance_path=source)


def R(rel, target):
    from myclaw.knowledge import Relation

    return Relation(relation_type=rel, target=target)


def _notes():
    from myclaw.knowledge import write_note

    write_note("Home server", content="My server sits in the closet. I prefer quiet fans.",
               tags=["homelab"],
               observations=[O("spec", "32 GB RAM", "tool shell free -g"),
                             O("setting", "Ollama listens on port 11434")],
               relations=[R("runs", "ZenSynora")])
    write_note("ZenSynora", content="The agent app.", tags=["homelab"],
               observations=[O("specs", "32GB ram."), O("preference", "Answers in English")])
    write_note("Family", content="My wife Ana likes hiking.",
               observations=[O("person", "Ana is my wife"), O("password", "hunter2"),
                             O("setting", "wifi password is hunter2"),
                             O("fact", "token ghp_abcdefghijklmnopqrstuvwxyz0123456789")])


def _facts(distill, **kw):
    return {f["text"]: f for f in distill.list_facts(limit=500, **kw)["facts"]}


# ── L1: dedupe, confidence, provenance ─────────────────────────────────────

def test_facts_are_deduplicated_across_notes_with_provenance(home):
    from myclaw.knowledge import distill

    _notes()
    r = distill.distill()
    assert r["ok"] and r["notes_read"] == 3
    facts = _facts(distill)
    ram = [f for t, f in facts.items() if "ram" in t.lower()]
    assert len(ram) == 1, facts.keys()          # "32 GB RAM" = "32GB ram."
    assert ram[0]["support"] == 2 and ram[0]["category"] == "spec"
    d = distill.drill_down(f"f{ram[0]['id']}")
    assert d["notes"] == ["home-server", "zensynora"]
    lines = {e["line"] for e in d["item"]["evidence"]}
    assert "- [spec] 32 GB RAM (source: tool shell free -g)" in lines
    assert "- [specs] 32GB ram." in lines
    # first-person sentences of the user's notes are facts too
    assert "I prefer quiet fans." in facts
    assert facts["I prefer quiet fans."]["kind"] == "sentence"


def test_secrets_are_never_distilled(home):
    from myclaw.knowledge import distill

    _notes()
    distill.distill()
    texts = " ".join(_facts(distill)).lower()
    assert "hunter2" not in texts and "ghp_" not in texts and "password" not in texts
    all_rows = " ".join(r[0] for r in _rows(home, "SELECT text FROM fact_sources")).lower()
    assert "hunter2" not in all_rows and "ghp_" not in all_rows
    assert distill.is_secret("api_key: sk-abcdefghijklmnopqrstuvwxyz")
    assert distill.is_secret("hunter2", "password")
    assert not distill.is_secret("Ollama listens on port 11434", "setting")
    assert not distill.is_secret("/home/adrian/projects/zensynora/myclaw/knowledge", "path")


def test_confidence_support_kind_and_conflicts(home):
    from myclaw.knowledge import distill, write_note

    write_note("A", content="x", observations=[O("preference", "Dark mode everywhere"),
                                                O("setting", "Ollama listens on port 11434"),
                                                O("setting", "Ollama listens on port 8080")])
    write_note("B", content="y", observations=[O("preference", "Dark mode everywhere")])
    write_note("session-summary-20261024", content="The user prefers short answers.",
               tags=["session_summary", "auto-generated"],
               observations=[O("preference", "Short answers")])
    write_note("C", content="z", observations=[O("preference", "Long walks")])
    distill.distill()
    f = _facts(distill)
    assert f["Dark mode everywhere"]["confidence"] > f["Long walks"]["confidence"]
    assert f["Short answers"]["confidence"] < f["Long walks"]["confidence"]   # session summary weighs less
    assert f["The user prefers short answers."]["about_user"]
    assert f["Ollama listens on port 8080"]["conflicted"]
    assert f["Ollama listens on port 8080"]["confidence"] < f["Long walks"]["confidence"]
    # pure function
    assert distill.confidence("preference", "fact", [1, 1]) > distill.confidence("preference", "fact", [1])
    assert distill.confidence("fact", "sentence", [1]) < distill.confidence("fact", "fact", [1])
    assert distill.confidence("fact", "fact", [1], age_days=900) < distill.confidence("fact", "fact", [1])


def test_snapshots_skipped_and_ingest_bookkeeping_dropped(home):
    from myclaw.knowledge import distill, write_note

    write_note("Health 2026-10-24", content="All services up.", tags=["health"],
               observations=[O("status", "CPU at 12 percent")])
    write_note("Some page", content="text", tags=["assimilated"],
               observations=[O("source", "https://example.com"), O("fact", "Example is a domain")])
    distill.distill()
    f = _facts(distill)
    assert "CPU at 12 percent" not in f
    assert "https://example.com" not in f and "Example is a domain" in f
    assert not f["Example is a domain"]["about_user"]


# ── incremental ────────────────────────────────────────────────────────────

def test_edit_and_delete_change_facts_incrementally(home):
    from myclaw.knowledge import delete_note, distill, write_note

    _notes()
    distill.distill()
    assert distill.distill()["notes_read"] == 0          # nothing changed
    write_note("ZenSynora", content="The agent app.", tags=["homelab"],
               observations=[O("preference", "Answers in Romanian")])
    r = distill.distill()
    assert r["notes_read"] == 1
    f = _facts(distill)
    assert "Answers in English" not in f and "Answers in Romanian" in f
    ram = [x for t, x in f.items() if "ram" in t.lower()][0]
    assert ram["support"] == 1                          # lost the ZenSynora support
    assert delete_note("home-server")
    r = distill.distill()
    assert r["notes_removed"] == 1
    f = _facts(distill)
    assert not [t for t in f if "ram" in t.lower()]
    assert "I prefer quiet fans." not in f
    assert not _rows(home, "SELECT 1 FROM fact_sources WHERE permalink = 'home-server'")


def _snapshot(distill):
    facts = sorted((f["text"], f["category"], f["confidence"], f["support"], f["about_user"])
                   for f in distill.list_facts(limit=500)["facts"])
    scen = sorted((s["title"], s["size"], tuple(sorted(x["text"] for x in s["facts"])))
                  for s in distill.list_scenarios(limit=500)["scenarios"])
    p = distill.get_persona()
    return facts, scen, (p or {}).get("summary")


def test_full_rebuild_equals_step_by_step(home):
    from myclaw.knowledge import distill, write_note

    _notes()
    distill.distill()
    write_note("Garden", content="I water the plants every morning.", tags=["home"],
               observations=[O("habit", "Waters plants daily"), O("decision", "Use drip irrigation")],
               relations=[R("near", "Home server")])
    distill.distill()
    step = _snapshot(distill)
    r = distill.distill(full=True)
    assert r["notes_read"] == 4
    assert _snapshot(distill) == step
    # dropping every derived table row and rebuilding gives the same again
    conn = sqlite3.connect(_db(home))
    for t in ("scenario_facts", "scenarios", "fact_sources", "distilled_facts", "distill_notes"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    distill.distill()
    assert _snapshot(distill)[:2] == step[:2]


def test_budget_partial_run_continues(home):
    from myclaw.knowledge import distill, write_note

    for i in range(5):
        write_note(f"Note {i}", content="x", observations=[O("fact", f"Fact number {i} here")])
    r = distill.distill(max_notes=2)
    assert r["partial"] and r["pending"] == 3 and "left" in r["message"]
    r = distill.distill()
    assert not r["partial"] and r["notes_read"] == 3
    assert len(_facts(distill)) == 5


# ── L2 scenarios ───────────────────────────────────────────────────────────

def test_scenarios_cluster_linked_notes_deterministically(home):
    from myclaw.knowledge import distill

    _notes()
    distill.distill()
    a = distill.list_scenarios()["scenarios"]
    assert a, "expected at least one scenario"
    hs = [s for s in a if s["title"] in ("Home server", "ZenSynora", "Homelab")]
    assert hs and hs[0]["size"] >= 3 and hs[0]["notes"] == 2
    assert hs[0]["summary"].startswith(f"{hs[0]['size']} facts from 2 notes:")
    distill.distill(full=True)
    b = distill.list_scenarios()["scenarios"]
    assert [(s["id"], s["title"], s["size"]) for s in a] == [(s["id"], s["title"], s["size"]) for s in b]
    d = distill.drill_down(f"s{hs[0]['id']}")
    assert d["kind"] == "scenario" and set(d["notes"]) >= {"home-server", "zensynora"}


def test_scenario_by_category_and_by_tag(home):
    from myclaw.knowledge import distill, write_note

    write_note("Tea", content="x", observations=[O("preference", "Green tea in the morning")])
    write_note("Editor", content="y", observations=[O("preference", "Vim keybindings")])
    write_note("NAS", content="a", tags=["storage"], observations=[O("spec", "Four 8 TB disks")])
    write_note("Backup", content="b", tags=["storage"], observations=[O("setting", "Nightly rsync at 2am")])
    distill.distill()
    titles = {s["title"]: s for s in distill.list_scenarios()["scenarios"]}
    assert "Preferences" in titles and titles["Preferences"]["grouped_by"] == "category"
    assert "Storage" in titles and titles["Storage"]["size"] == 2


# ── L3 persona ─────────────────────────────────────────────────────────────

def test_persona_from_facts_about_the_user_never_secrets(home):
    from myclaw.knowledge import distill, write_note

    _notes()
    write_note("Notes", content="I decided to use Postgres. My password is hunter2 for now.",
               observations=[O("preference", "Keep my token in the vault")])
    distill.distill()
    p = distill.get_persona()
    assert p and p["version"] == 1
    s = p["summary"]
    assert "Answers in English" in s and "Ana is my wife" in s and "32 GB RAM" in s
    assert "hunter2" not in s and "token" not in s.lower() and "password" not in s.lower()
    assert set(p["sections"]) >= {"preferences", "people", "setup"}
    assert 0 < p["confidence"] <= 1
    # unchanged notes → no new version; a change → v2, old version kept
    distill.distill()
    assert distill.get_persona()["version"] == 1
    write_note("Food", content="z", observations=[O("preference", "Spicy food")])
    distill.distill()
    p2 = distill.get_persona()
    assert p2["version"] == 2 and "Spicy food" in p2["summary"] and p2["versions"] == [2, 1]
    assert distill.get_persona(version=1)["summary"] == s
    d = distill.drill_down("persona")
    assert "family" in d["notes"] and d["kind"] == "persona"


def test_persona_does_not_touch_user_profile(home):
    from myclaw.knowledge import distill

    _notes()
    distill.distill()
    assert not (home / ".myclaw" / "profiles" / "user_dialectic.md").exists()
    notes = sorted(p.name for p in (home / ".myclaw" / "knowledge" / "default").glob("*.md"))
    assert notes == ["family.md", "home-server.md", "zensynora.md"]   # no file written


# ── drill_down ─────────────────────────────────────────────────────────────

def test_drill_down_ids(home):
    from myclaw.knowledge import distill

    with pytest.raises(LookupError):
        distill.drill_down("f1")              # nothing distilled yet
    _notes()
    distill.distill()
    fid = distill.list_facts(limit=1)["facts"][0]["id"]
    for ref in (f"f{fid}", f"fact:{fid}", str(fid), f"F {fid}"):
        assert distill.drill_down(ref)["item"]["id"] == fid
    for bad in ("", "x12", "f", "s", "fact:abc", "f" + "9" * 30):
        with pytest.raises(ValueError):
            distill.drill_down(bad)
    with pytest.raises(LookupError):
        distill.drill_down("f999999")
    with pytest.raises(LookupError):
        distill.drill_down("s999999")


# ── agent context ──────────────────────────────────────────────────────────

@pytest.fixture
def agent(home):
    from myclaw.agent import Agent
    from myclaw.config import load_config

    a = Agent(load_config(), name="default")
    a._provider = object()
    a.set_gap_cache_enabled(False)
    return a


def test_agent_context_unchanged_when_off(home, agent):
    from myclaw.knowledge import distill

    _notes()
    before = agent._search_knowledge_context_sync("how much RAM has my home server", "default")
    distill.distill()
    after = agent._search_knowledge_context_sync("how much RAM has my home server", "default")
    assert before == after and "What I know about you" not in after
    assert "**Home server**" in after


def test_agent_context_with_distill_on_order_and_budget(on, agent):
    from myclaw.agent import KB_CONTEXT_BUDGET
    from myclaw.knowledge import write_note

    _notes()
    for i in range(6):
        write_note(f"Server log {i}", content="server RAM notes " + "words " * 200,
                   observations=[O("spec", f"server {i} has RAM slot {i}")])
    on.distill()
    res = agent._search_knowledge_context_sync("how much RAM has my home server", "default",
                                               return_structured=True)
    ctx = res.context
    assert res.metadata.get("distilled")
    i_persona = ctx.index("### What I know about you")
    i_facts = ctx.index("### Relevant facts")
    i_note = ctx.index("\n**")
    assert i_persona < i_facts < i_note             # L3 > L2/L1 > notes
    assert "32 GB RAM" in ctx[i_facts:i_note]
    assert "(f" in ctx and "confidence" in ctx
    assert len(ctx) <= KB_CONTEXT_BUDGET + 200     # the best note may exceed by its header only
    persona_block = ctx[i_persona:i_facts]
    assert len(persona_block) <= on.CONTEXT_PERSONA_CHARS + 40


def test_agent_context_distilled_only_and_persona_only_when_relevant(on, agent):
    from myclaw.knowledge import write_note

    write_note("Prefs", content="x", observations=[O("preference", "Tabs over spaces")])
    on.distill()
    items = on.context_items("default", "the weather in Paris tomorrow")
    assert items["persona"] is None and not items["facts"]
    items = on.context_items("default", "what do I like?")
    assert items["persona"] is not None
    # facts match even when no note does (all notes' text excluded by the query words)
    res = agent._search_knowledge_context_sync("tabs spaces", "default", return_structured=True)
    assert res.has_results and "Tabs over spaces" in res.context


def test_agent_context_knowledge_disabled_ignores_distill(on, agent, monkeypatch):
    _notes()
    on.distill()
    monkeypatch.setattr(type(agent), "_kb_enabled", property(lambda self: False))
    assert agent._search_knowledge_context_sync("RAM of my server", "default") == ""
    on.set_options(kb_enabled=False)
    try:
        assert not on.enabled()
    finally:
        on.set_options(kb_enabled=True)


# ── scheduler (no sleeping) ────────────────────────────────────────────────

def test_due_rules():
    from myclaw.knowledge.distill import _BaseState, due

    st = _BaseState(changed=0, last_change=100.0)
    assert not due(st, 1000.0, 20, 600)
    st.changed = 20
    assert due(st, 100.0, 20, 600)
    st.changed = 3
    assert not due(st, 200.0, 20, 600) and due(st, 700.0, 20, 600)
    assert not due(st, 10_000.0, 20, 0)               # idle off
    st.not_before = 800.0
    assert not due(st, 700.0, 20, 600)


def test_scheduler_runs_after_n_changes_or_idle(on, monkeypatch):
    clock = [1000.0]
    ran = []
    s = on._Scheduler(clock=lambda: clock[0], runner=lambda p: ran.append(p) or {"ok": True},
                      busy=lambda: False)
    s.autostart = False
    monkeypatch.setitem(on._OPTIONS, "distill_every", 3)
    monkeypatch.setitem(on._OPTIONS, "idle_minutes", 5)
    s.note_changed("/x.db")
    s.note_changed("/x.db")
    assert s.tick() == [] and ran == []
    s.note_changed("/x.db")
    assert s.tick() == ["/x.db"]
    assert s.tick() == []                              # counter reset
    s.note_changed("/x.db")
    clock[0] += 299
    assert s.tick() == []
    clock[0] += 2
    assert s.tick() == ["/x.db"]                       # idle for 5 minutes


def test_scheduler_waits_for_semantic_worker_and_continues_partial(on, monkeypatch):
    clock = [0.0]
    busy = [True]
    results = [{"ok": True, "partial": True}, {"ok": True}]
    ran = []

    def runner(p):
        ran.append(p)
        return results.pop(0)

    s = on._Scheduler(clock=lambda: clock[0], runner=runner, busy=lambda: busy[0])
    s.autostart = False
    monkeypatch.setitem(on._OPTIONS, "distill_every", 1)
    s.note_changed("/y.db")
    assert s.tick() == [] and ran == []                # semantic worker busy: postponed
    clock[0] += on.SEMANTIC_WAIT + 1
    busy[0] = False
    assert s.tick() == ["/y.db"]                       # partial → again soon
    clock[0] += 2
    assert s.tick() == ["/y.db"] and len(ran) == 2
    assert s.tick() == []


def test_scheduler_off_and_change_hook(home, monkeypatch):
    from myclaw.knowledge import distill, write_note

    calls = []
    monkeypatch.setattr(distill._SCHED, "note_changed", lambda p, n=1: calls.append(n))
    write_note("A", content="x")
    assert calls == [1]                                # every index write reports (no-op when off)
    s = distill._Scheduler(clock=lambda: 0.0, runner=lambda p: {"ok": True}, busy=lambda: False)
    s.autostart = False
    s.note_changed("/z.db", 50)
    assert s.bases == {} and s.tick(10_000) == []       # off: nothing counted, nothing runs


def test_scheduler_scan_finds_notes_changed_while_off(on, home, monkeypatch):
    from myclaw.knowledge import write_note

    write_note("A", content="x", observations=[O("fact", "Alpha fact here")])
    ran = []
    s = on._Scheduler(clock=lambda: 50.0, runner=lambda p: ran.append(p) or {"ok": True},
                      busy=lambda: False)
    s.autostart = False
    s.scan()
    assert str(_db(home)) in s.tick() and str(_db(home)) in ran   # (+ bases other tests left)


def test_set_options_and_config(home, monkeypatch):
    from myclaw.config import AppConfig, _apply_knowledge_distill
    from myclaw.knowledge import distill

    cfg = AppConfig(knowledge={"distill": True, "distill_every": 5, "distill_idle_minutes": 2})
    _apply_knowledge_distill(cfg)
    o = distill.options()
    assert o["distill"] and o["distill_every"] == 5 and o["idle_minutes"] == 2
    assert distill.enabled()
    _apply_knowledge_distill(AppConfig(knowledge={"enabled": False, "distill": True}))
    assert not distill.enabled()
    assert AppConfig().knowledge.distill is False and AppConfig().knowledge.distill_every == 20


# ── tools ──────────────────────────────────────────────────────────────────

def test_tools(home):
    from myclaw.tools import TOOLS
    from myclaw.tools.kb import distill_knowledge, drill_down, get_persona, list_facts, list_scenarios

    for name in ("list_facts", "list_scenarios", "get_persona", "drill_down", "distill_knowledge"):
        assert name in TOOLS
    assert "distill_knowledge()" in list_facts()
    assert "No persona yet" in get_persona()
    _notes()
    out = distill_knowledge()
    assert out.startswith("✅ Distilled") and "Automatic distillation is off" in out
    facts = list_facts("RAM")
    assert "32 GB RAM" in facts and "confidence" in facts
    import re

    fid = re.search(r"- f(\d+) ", facts).group(1)
    d = drill_down(f"f{fid}")
    assert "memory://home-server" in d and "memory://zensynora" in d
    assert "Use an id like" in drill_down("banana")
    assert "There is no fact" in drill_down("f99999")
    assert "Persona v1" in get_persona() and "hunter2" not in get_persona()
    assert "s" in list_scenarios() and "scenario" in list_scenarios()
    assert "No distilled fact matches" in list_facts("zzzunknownword")
    assert "limit must be a number" in list_facts("x", limit="lots")
    assert "Persona v1" in drill_down("persona") and "memory://family" in drill_down("persona")


# ── API ────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from myclaw.web import knowledge_api, knowledge_distill_api

    app = FastAPI()
    app.include_router(knowledge_api.router)
    app.include_router(knowledge_distill_api.router)
    return TestClient(app)


def test_api_routes(api, home):
    assert api.get("/api/knowledge/facts").json()["facts"] == []
    assert api.get("/api/knowledge/persona").json()["persona"] is None
    _notes()
    st = api.get("/api/knowledge/distill/status").json()
    assert st["pending"] == 3 and st["facts"] == 0
    assert api.post("/api/knowledge/distill").json()["ok"]
    end = time.time() + 15
    while time.time() < end:
        st = api.get("/api/knowledge/distill/status").json()
        if st["job"]["status"] != "running":
            break
        time.sleep(0.05)
    assert st["job"]["status"] == "done", st
    assert st["pending"] == 0 and st["facts"] > 3 and st["persona_version"] == 1
    facts = api.get("/api/knowledge/facts?q=RAM").json()["facts"]
    assert facts and "RAM" in facts[0]["text"]
    sc = api.get("/api/knowledge/scenarios").json()["scenarios"]
    assert sc and sc[0]["facts"]
    p = api.get("/api/knowledge/persona").json()["persona"]
    assert p["version"] == 1
    d = api.get(f"/api/knowledge/drill/fact/{facts[0]['id']}").json()
    assert "home-server" in d["notes"]
    assert api.get(f"/api/knowledge/drill/scenario/{sc[0]['id']}").status_code == 200
    assert api.get("/api/knowledge/drill/persona/latest").status_code == 200
    assert api.get("/api/knowledge/drill/fact/99999").status_code == 404
    assert api.get("/api/knowledge/drill/fact/abc").status_code == 400
    assert api.get("/api/knowledge/drill/nope/1").status_code == 400
    assert api.get("/api/knowledge/persona?version=7").status_code == 404
    assert api.get("/api/knowledge/facts?base=../x").status_code == 400


# ── old databases, rebuildable ─────────────────────────────────────────────

def test_old_database_opens_and_distils(home):
    """An index made before phase 4 (no distill tables): reading works (empty),
    opening adds the tables, distilling works; notes need no re-index."""
    from myclaw.knowledge import write_note

    write_note("Old", content="x", observations=[O("preference", "Old style answers")])
    conn = sqlite3.connect(_db(home))
    for t in ("scenario_facts", "scenarios", "fact_sources", "distilled_facts", "persona",
              "distill_notes", "distill_state", "distilled_facts_fts", "scenarios_fts"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.close()
    from myclaw.knowledge import distill

    assert distill.list_facts()["distilled"] is False
    assert distill.list_scenarios()["distilled"] is False
    assert distill.get_persona() is None
    assert distill.context_items("default", "old style") == {"persona": None, "scenarios": [], "facts": []}
    assert distill.status()["pending"] == 1
    r = distill.distill()
    assert r["ok"] and r["notes_read"] == 1
    assert "Old style answers" in _facts(distill)
    tables = {r[0] for r in _rows(home, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"distilled_facts", "fact_sources", "scenarios", "scenario_facts", "persona",
            "distill_notes", "distill_state"} <= tables


def test_sync_force_does_not_redistill(home):
    from myclaw.knowledge import distill
    from myclaw.knowledge.sync import sync_knowledge

    _notes()
    distill.distill()
    before = _snapshot(distill)
    sync_knowledge(force=True)
    r = distill.distill()
    assert r["notes_read"] == 0 and _snapshot(distill) == before


# ── semantic near-duplicates (fake embedder) ───────────────────────────────

@pytest.fixture
def sem(home, monkeypatch):
    from myclaw.knowledge import semantic
    from myclaw.knowledge.semantic import HashEmbedder

    monkeypatch.setattr(semantic, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(semantic, "_APPLIED", False)
    monkeypatch.setattr(semantic, "_OPTIONS", dict(semantic._OPTIONS, semantic_search=True,
                                                   embedding_model="test/hash", kb_enabled=True,
                                                   min_score=0.2))
    semantic.set_embedder_factory(lambda m: HashEmbedder(model=m, synonyms={"car": "automobile"}))
    yield semantic
    semantic.wait_idle(20)
    semantic.set_embedder_factory(None)


def test_semantic_near_duplicates_merge_but_not_other_numbers(sem, home):
    from myclaw.knowledge import distill, write_note

    write_note("A", content="x", observations=[O("spec", "The car is blue"), O("spec", "The car has 4 doors")])
    write_note("B", content="y", observations=[O("spec", "The automobile is blue"),
                                                O("spec", "The automobile has 2 doors")])
    sem.wait_idle(10)
    r = distill.distill()
    assert r["semantic"] and r["merged"] == 1
    f = _facts(distill)
    blue = [x for t, x in f.items() if "blue" in t]
    assert len(blue) == 1 and blue[0]["support"] == 2
    assert len([t for t in f if "doors" in t]) == 2      # different numbers: never merged
    d = distill.drill_down(f"f{blue[0]['id']}")
    assert {e["permalink"] for e in d["item"]["evidence"]} == {"a", "b"}
    # deterministic: a second run changes nothing
    snap = _snapshot(distill)
    distill.distill()
    assert _snapshot(distill) == snap
