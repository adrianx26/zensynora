"""2026-10-28: fixes from the independent review of agent levels / capabilities / fusion.

One test (or a few) per review item; offline, temporary HOME.
"""

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myclaw.web.agents_api import _ORIGIN as WEB
from tests.test_agent_levels_fusion import AGENTS, _write_config, gaps  # noqa: F401  (fixture)
from tests.test_v071_features import FakeAgent, _wait_turn, home  # noqa: F401  (fixture)


@pytest.fixture
def team(home):  # noqa: F811
    from myclaw.chat import store
    from myclaw.jobs import store as jobs

    _write_config(home, [dict(a) for a in AGENTS])
    store.create_channel("general", ["default", "Writer", "Editor"], channel_id="general")
    store.create_channel("lead", ["Editor", "Writer"], channel_id="lead")
    store.create_channel("docs", ["Editor"], channel_id="docs")
    for ag in ("Writer", "Editor"):
        dm = store.dm_channel(ag)["id"]
        store.add_message(dm, "user", "you", f"hi {ag}")
        store.add_message(dm, "agent", ag, f"hello from {ag}")
    jobs.create_job({"name": "Proofread", "prompt": "p", "agent": "Editor", "channel_id": "dm-editor"})
    jobs.create_job({"name": "Other's report", "prompt": "p", "agent": "Other", "channel_id": "dm-editor"})
    jobs.create_job({"name": "Write", "prompt": "p", "agent": "Writer", "channel_id": "general"})
    return home


def snap(home_dir):
    from myclaw.config import CONFIG_FILE, load_encrypted_or_plain

    c = sqlite3.connect(home_dir / ".myclaw" / "chat.db")
    c.row_factory = sqlite3.Row
    chans = sorted(({k: r[k] for k in r.keys() if k != "updated"} for r in c.execute("SELECT * FROM channels")),
                   key=lambda r: r["id"])
    msgs = [dict(r) for r in c.execute("SELECT * FROM messages ORDER BY id")]
    c.close()
    j = sqlite3.connect(home_dir / ".myclaw" / "jobs.db")
    j.row_factory = sqlite3.Row
    jobs = [{k: r[k] for k in ("id", "agent", "channel_id")} for r in j.execute("SELECT * FROM jobs ORDER BY id")]
    j.close()
    return {"agents": load_encrypted_or_plain(CONFIG_FILE)["agents"], "channels": chans, "messages": msgs,
            "jobs": jobs}


# ── 1. concurrent approve ────────────────────────────────────────────────────
def test_concurrent_approve_applies_once_and_undo_restores_everything(team):
    from myclaw import agent_fusion as F

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", "Combined", "r", use_model=False)
    orig = F._raw_config

    def slow():
        r = orig()
        time.sleep(0.2)
        return r

    F._raw_config = slow
    res, barrier = [], threading.Barrier(4)

    def go():
        barrier.wait()
        try:
            F.apply_fusion(p["id"], WEB)
            res.append("ok")
        except Exception as e:  # every loser gets a readable FusionError, never KeyError
            res.append(type(e).__name__)

    try:
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        F._raw_config = orig
    assert sorted(res) == ["FusionError"] * 3 + ["ok"]
    bk = F.get_backup(p["id"])
    assert [len(m["channels"]) for m in bk["chat_moves"]] == [3, 4] and bk["stage"] == "done"
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before


def test_the_lock_holds_across_processes(team):
    from myclaw import agent_fusion as F

    p = F.create_fusion("Writer", "Editor", use_model=False)
    code = ("import time, sys; sys.path.insert(0, %r)\n"
            "from myclaw import agent_fusion as F\n"
            "with F._locked():\n    print('locked', flush=True); time.sleep(1.5)\n") % str(F.Path.cwd())
    import os

    env = {**os.environ, "HOME": str(team)}
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    assert proc.stdout.readline().strip() == "locked"
    t0 = time.time()
    F.apply_fusion(p["id"], WEB)
    assert time.time() - t0 > 0.8  # waited for the other process
    proc.wait(10)


def test_an_existing_backup_is_never_overwritten(team):
    from myclaw import agent_fusion as F

    p = F.create_fusion("Writer", "Editor", use_model=False)
    F._path(p["id"], ".backup.json").write_text('{"keep": true}')
    with pytest.raises(F.FusionError, match="never overwritten"):
        F.apply_fusion(p["id"], WEB)
    assert json.loads(F._path(p["id"], ".backup.json").read_text()) == {"keep": True}


def test_api_turns_every_failure_into_409(team, monkeypatch):
    from myclaw import agent_fusion as F
    from myclaw.web import agents_api

    app = FastAPI()
    app.include_router(agents_api.router)
    c = TestClient(app, raise_server_exceptions=False)
    fid = c.post("/api/agents/fusions", json={"agent_a": "Writer", "agent_b": "Editor", "use_model": False}).json()["id"]

    def boom(*a, **k):
        raise KeyError("Writer")

    monkeypatch.setattr(F, "apply_fusion", boom)
    r = c.post(f"/api/agents/fusions/{fid}/approve")
    assert r.status_code == 409 and "KeyError" in r.json()["detail"]


# ── 2. only you may apply; only the medic proposes ───────────────────────────
def test_apply_undo_reject_need_the_ui_or_cli_origin(team):
    from myclaw import agent_fusion as F

    p = F.create_fusion("Writer", "Editor", use_model=False)
    for bad in (None, object(), "web", "you (Web UI)"):
        with pytest.raises(F.FusionError, match="Only you"):
            F.apply_fusion(p["id"], bad)
        with pytest.raises(F.FusionError, match="Only you"):
            F.reject_fusion(p["id"], bad)
    with pytest.raises(ValueError):
        F.register_origin(object(), "web")  # the Web UI's origin is taken
    with pytest.raises(ValueError):
        F.register_origin("web", "cli")  # strings are not origins
    applied = F.apply_fusion(p["id"], WEB)
    assert applied["history"][-1]["by"] == "you (Web UI)"
    with pytest.raises(F.FusionError, match="Only you"):
        F.undo_fusion(p["id"], object())


def test_only_the_medic_proposes_fusions(team):
    from myclaw import agent_fusion as F
    from myclaw.agent_caps import QUESTION_BUDGET, new_turn_budget
    from myclaw.agent_team import team_section
    from myclaw.config import load_config
    from myclaw.events import CURRENT_AGENT
    from myclaw.jobs import store as jobs

    def ask(agent, budget=None):
        toks = (CURRENT_AGENT.set(agent), QUESTION_BUDGET.set(budget))
        try:
            return asyncio.run(F.propose_agent_fusion("Writer", "Editor", reason="overlap"))
        finally:
            CURRENT_AGENT.reset(toks[0])
            QUESTION_BUDGET.reset(toks[1])

    out = ask("Writer")
    assert out.startswith("Error: only the medic") and F.list_fusions() == []
    review = jobs.create_job({"name": "Medic self-review", "prompt": "p", "created_by": "medic"})
    assert ask("default", new_turn_budget(job_run=f"{review['id']}-3")).startswith("Fusion proposal")
    cfg = load_config(force_reload=True)
    assert "propose_agent_fusion" not in team_section(cfg, "Writer")
    assert "propose_agent_fusion" in team_section(cfg, "medic")


# ── 3. crash in the middle ───────────────────────────────────────────────────
@pytest.mark.parametrize("when", ["before", "after"])
def test_a_crash_mid_apply_is_interrupted_and_undo_restores(team, monkeypatch, when):
    from myclaw import agent_fusion as F
    from myclaw.chat import store

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", "Combined", use_model=False)
    orig, calls = store.move_member, {"n": 0}

    def dying(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            if when == "after":  # the move ran, the process died before it was recorded
                orig(*a, **k)
            raise SystemExit("killed")
        return orig(*a, **k)

    monkeypatch.setattr(store, "move_member", dying)
    with pytest.raises(SystemExit):
        F.apply_fusion(p["id"], WEB)
    monkeypatch.setattr(store, "move_member", orig)
    assert F.get_fusion(p["id"])["status"] == "applying" and F.get_backup(p["id"]) is not None
    monkeypatch.setattr(F, "INTERRUPTED_AFTER_S", 0)
    assert F.list_fusions()[0]["interrupted"] is True
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before
    assert F.get_fusion(p["id"])["status"] == "undone"


def test_load_config_survives_the_file_watcher_resetting_the_cache(home, monkeypatch):  # noqa: F811
    import myclaw.config as cm

    _write_config(home, [{"name": "Writer"}])
    real = cm._apply_knowledge_dir

    def watcher_fires(cfg):  # what the watchdog thread did right after save_config
        cm._cached_config = None
        return real(cfg)

    monkeypatch.setattr(cm, "_apply_knowledge_dir", watcher_fires)
    cfg = cm.load_config(force_reload=True)
    assert cfg.agents.named[0].name == "Writer"


# ── 4. a failed move rolls back ──────────────────────────────────────────────
def test_a_failed_move_rolls_everything_back(team, monkeypatch):
    from myclaw import agent_fusion as F
    from myclaw.jobs import store as jobs

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", "Combined", use_model=False)
    orig, calls = jobs.move_agent_jobs, {"n": 0}

    def locked(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return orig(*a, **k)

    monkeypatch.setattr(jobs, "move_agent_jobs", locked)
    with pytest.raises(F.FusionError, match="everything was put back"):
        F.apply_fusion(p["id"], WEB)
    prop = F.get_fusion(p["id"])
    assert prop["status"] == "failed" and "database is locked" in prop["error"]
    assert snap(team) == before


# ── 5. undo and later changes ────────────────────────────────────────────────
def test_undo_refuses_to_lose_later_edits_unless_forced(team):
    from myclaw import agent_fusion as F
    from myclaw.config import CONFIG_FILE, load_encrypted_or_plain, save_config
    import myclaw.config as cm

    p = F.create_fusion("Writer", "Editor", use_model=False)
    F.apply_fusion(p["id"], WEB)
    raw = load_encrypted_or_plain(CONFIG_FILE)
    raw["agents"]["named"][0]["system_prompt"] = "EDITED AFTER THE FUSION"
    save_config(raw)
    cm._cached_config = None
    assert F.preview(p["id"])["undo_check"]["changed"] == ["@Writer: system_prompt changed"]
    with pytest.raises(F.FusionConflict) as e:
        F.undo_fusion(p["id"], WEB)
    assert e.value.details == ["@Writer: system_prompt changed"]
    app = FastAPI()
    from myclaw.web import agents_api

    app.include_router(agents_api.router)
    r = TestClient(app).post(f"/api/agents/fusions/{p['id']}/undo", json={})
    assert r.status_code == 409 and r.json()["conflicts"] == ["@Writer: system_prompt changed"]
    assert TestClient(app).post(f"/api/agents/fusions/{p['id']}/undo", json={"force": True}).json()["status"] == "undone"
    assert "forced" in F.get_fusion(p["id"])["history"][-1]["note"]


def test_undo_is_refused_while_a_later_fusion_uses_the_fused_agent(team):
    from myclaw import agent_fusion as F

    p1 = F.create_fusion("Writer", "Editor", use_model=False)
    F.apply_fusion(p1["id"], WEB)
    p2 = F.create_fusion("Writer", "Other", use_model=False)
    F.apply_fusion(p2["id"], WEB)
    with pytest.raises(F.FusionError, match="later fusion"):
        F.undo_fusion(p1["id"], WEB, force=True)
    F.undo_fusion(p2["id"], WEB)
    F.undo_fusion(p1["id"], WEB)


# ── 6 / 7. question routing ──────────────────────────────────────────────────
class Asker(FakeAgent):
    def __init__(self, name, ask_on=(1,), n=1):
        super().__init__(name)
        self.ask_on, self.n, self.results = ask_on, n, []

    async def stream_think(self, prompt, user_id="default"):
        from myclaw.agent_caps import request_information

        self.prompts.append(prompt)
        if len(self.prompts) in self.ask_on:
            for i in range(self.n):
                self.results.append(await request_information(f"t{i}-{self.name}", f"q{i} from {self.name}?"))
        yield f"{self.name} ok"


def _client(agents):
    from myclaw.web import chat_api

    chat_api.set_registry_getter(lambda: agents)
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _settle(c, channel, n):
    msgs = _wait_turn(c, channel, n)
    time.sleep(0.3)
    return c.get(f"/api/channels/{channel}/messages").json()["messages"]


def _after_last_user(msgs):
    i = [k for k, m in enumerate(msgs) if m["role"] == "user"][-1]
    return msgs[i:]


def test_a_question_from_a_non_member_never_takes_a_dm_over(gaps):
    from myclaw.agent import MEMORY_SCOPE
    from myclaw.agent_caps import request_information
    from myclaw.chat import store
    from myclaw.events import CURRENT_AGENT, CURRENT_CHANNEL

    store.dm_channel("Editor")

    async def job():  # a job of @Other reports to @Editor's DM and asks there
        t = (CURRENT_CHANNEL.set("dm-editor"), CURRENT_AGENT.set("Other"), MEMORY_SCOPE.set("job-x"))
        try:
            return await request_information("budget", "What budget?")
        finally:
            for v, k in zip((CURRENT_CHANNEL, CURRENT_AGENT, MEMORY_SCOPE), t):
                v.reset(k)

    assert "not a member" in asyncio.run(job())
    q = [m for m in store.list_messages("dm-editor") if m["role"] == "agent"][0]
    assert "answer it in a conversation with @Other" in q["text"]
    agents = {"default": FakeAgent("default"), "Editor": FakeAgent("Editor"), "Other": FakeAgent("Other")}
    with _client(agents) as c:
        c.post("/api/channels/dm-editor/messages", json={"text": "please fix the typo"})
        msgs = _settle(c, "dm-editor", 2)
    assert [m["sender"] for m in _after_last_user(msgs) if m["role"] == "agent"] == ["Editor"]
    assert store.get_channel("dm-editor")["members"] == ["Editor"] and agents["Other"].prompts == []


def test_questions_expire_and_close_once_answered(gaps):
    from myclaw.chat import store

    store.create_channel("w", ["default", "Writer"], channel_id="w")
    old = time.time() - 25 * 3600
    store.add_message("w", "agent", "Writer", "old?", {"question": {"topic": "x", "question": "old?",
                                                                     "asked_at": old}})
    agents = {"default": FakeAgent("default"), "Writer": FakeAgent("Writer")}
    with _client(agents) as c:
        c.post("/api/channels/w/messages", json={"text": "hello"})
        msgs = _settle(c, "w", 2)
        assert [m["sender"] for m in _after_last_user(msgs) if m["role"] == "agent"] == ["default"]  # expired
        store.add_message("w", "agent", "Writer", "new?", {"question": {"topic": "deadline", "question": "When?",
                                                                         "asked_at": time.time()}})
        c.post("/api/channels/w/messages", json={"text": "Friday"})
        msgs = _settle(c, "w", 3)
        assert [m["sender"] for m in _after_last_user(msgs) if m["role"] == "agent"] == ["Writer"]
        c.post("/api/channels/w/messages", json={"text": "and something else"})
        msgs = _settle(c, "w", 4)
    assert [m["sender"] for m in _after_last_user(msgs) if m["role"] == "agent"] == ["default"]  # answered: closed


def test_two_askers_the_latest_answers_and_the_user_is_told_about_the_other(gaps):
    from myclaw.chat import store

    store.create_channel("work", ["Writer", "Editor", "default"], channel_id="work", reply_mode="all")
    agents = {"default": FakeAgent("default"), "Writer": Asker("Writer"), "Editor": Asker("Editor")}
    with _client(agents) as c:
        c.post("/api/channels/work/messages", json={"text": "plan please"})
        _settle(c, "work", 5)
        c.post("/api/channels/work/messages", json={"text": "Friday and 100 EUR"})
        msgs = _settle(c, "work", 6)
        tail = _after_last_user(msgs)
        assert [m["sender"] for m in tail if m["role"] == "agent"] == ["Editor"]
        assert any(m["role"] == "system" and "Still open: @Writer" in m["text"] for m in tail)
        assert "q0 from Editor?" in agents["Editor"].prompts[-1]
        c.post("/api/channels/work/messages", json={"text": "@Writer the deadline is Monday"})
        msgs = _settle(c, "work", 7)
    assert [m["sender"] for m in _after_last_user(msgs) if m["role"] == "agent"] == ["Writer"]
    assert "q0 from Writer?" in agents["Writer"].prompts[-1]


def test_at_most_three_questions_per_user_message_across_agents(gaps):
    from myclaw.chat import store

    store.create_channel("par", ["Writer", "Editor"], channel_id="par", reply_mode="parallel")
    agents = {"default": FakeAgent("default"), "Writer": Asker("Writer", n=5), "Editor": Asker("Editor", n=5)}
    with _client(agents) as c:
        c.post("/api/channels/par/messages", json={"text": "go"})
        msgs = _settle(c, "par", 5)
    assert sum(1 for m in msgs if (m.get("meta") or {}).get("question")) == 3
    results = agents["Writer"].results + agents["Editor"].results
    assert sum(r.startswith("Not sent") for r in results) == 7


def test_question_text_is_plain():
    from myclaw.agent_caps import question_text

    t = question_text("Writer", "invoice", "Which number?", "to quote it")
    assert "**" not in t and "_Why" not in t and t.startswith("Question from @Writer about invoice:")


# ── 8. the executing agent is the caller ─────────────────────────────────────
def test_tools_run_as_the_agent_that_calls_them(monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.events import CURRENT_AGENT

    seen = []

    async def fake(*a, **k):
        seen.append(CURRENT_AGENT.get())
        return "done"

    monkeypatch.setattr(tool_executor, "_execute_tools", fake)

    class A:
        name = "Writer"

    assert CURRENT_AGENT.get() is None  # e.g. Telegram or the CLI: nothing set
    asyncio.run(tool_executor.execute_tools(A(), [], [], "", "u", None, 0, False))
    assert seen == ["Writer"] and CURRENT_AGENT.get() is None
    from myclaw.tools.core import is_tool_independent

    assert not any(is_tool_independent(t) for t in ("create_skill", "assign_skill", "request_information"))


# ── 9. no masked secrets ─────────────────────────────────────────────────────
def test_assign_skill_on_a_fresh_install_keeps_real_secrets(home, monkeypatch):  # noqa: F811
    import myclaw.config as cm
    from myclaw.agent_caps import assign_skill, create_skill
    from myclaw.config import AppConfig

    cfg = AppConfig(**{"providers": {"openai": {"api_key": "sk-real-secret-123"}}})
    real = cm.load_config
    monkeypatch.setattr(cm, "load_config", lambda force_reload=False: cfg if not cm.CONFIG_FILE.exists()
                        else real(force_reload))
    create_skill("tone", "Tone", "Be kind.")
    assert assign_skill("tone").startswith("Gave the skill")
    text = cm.CONFIG_FILE.read_text()
    assert "**********" not in text and "sk-real-secret-123" in text


# ── 10. bad levels in the file ───────────────────────────────────────────────
def test_bad_levels_in_the_file_are_clamped_not_fatal(home, caplog):  # noqa: F811
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _write_config(home, [{"name": "a", "level": 12}, {"name": "b", "level": 0}, {"name": "c", "level": None},
                         {"name": "d", "level": "high"}], defaults={"level": "x"})
    cfg = load_config(force_reload=True)
    assert [a.level for a in cfg.agents.named] == [9, 1, 1, 1] and cfg.agents.defaults.level == 1
    app = FastAPI()
    app.include_router(settings_api.router)
    c = TestClient(app)
    assert c.put("/api/settings", json={"changes": {"agents.summarization_threshold": 12}}).status_code == 200
    assert c.put("/api/settings", json={"changes": {"agents.defaults.level": 12}}).status_code == 422


# ── 11. impact and diff ──────────────────────────────────────────────────────
def test_impact_lists_only_real_moves_and_diff_has_no_false_changes(team):
    from myclaw import agent_fusion as F

    p = F.create_fusion("Writer", "Editor", use_model=False)
    im = F.preview(p["id"])["impact"]
    assert {c["id"] for c in im["dms"]} == {"dm-editor"}  # not the kept agent's own conversation
    assert {c["id"] for c in im["channels"]} == {"general", "lead", "docs"}
    assert {j["name"] for j in im["jobs"]} == {"Proofread", "Other's report"}  # not @Writer's own job
    d = F._diff("line one\nline two", "line one\nline two\nline three", "x")
    assert "-line two" not in d and "+line three" in d


# ── plausibles ───────────────────────────────────────────────────────────────
def test_create_skill_is_exclusive_and_safe(home):  # noqa: F811
    from myclaw.agent_caps import create_skill

    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(create_skill("race", f"d{i}", f"body {i}")))
          for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(o.startswith("Created") for o in out) == 1
    assert create_skill("con", "d", "b").startswith("Error") and "reserved" in create_skill("nul", "d", "b")
    leaked = create_skill("deploy", "Deploy", "Use api_key=sk-abcdefghijklmnopqrstuvwxyz123456 for the server")
    assert leaked.startswith("Error") and "secret" in leaked


def test_job_questions_are_per_run_and_not_judged(gaps, monkeypatch):
    import myclaw.provider as prov
    from myclaw.agent_caps import request_information
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    class Asker2(FakeAgent):
        async def think(self, prompt, user_id="default", _depth=0):
            await request_information("servers", f"Which servers? ({len(self.prompts)})")
            self.prompts.append(prompt)
            return "Checked what I could."

    job = store.create_job({"name": "check", "prompt": "check"})
    runner = JobRunner(lambda: {"default": Asker2("default")})
    asyncio.run(runner.run_job(job["id"]))
    asyncio.run(runner.run_job(job["id"]))
    runs = store.list_runs(job["id"])
    assert all(r["output"].count("Which servers?") == 1 for r in runs)  # each run only its own question
    seen = []

    async def judge(cfg, messages, *a, **k):
        seen.append(messages[1]["content"])
        return '{"score": 8, "verdict": "pass", "reasons": "ok"}', "ollama", "m"

    monkeypatch.setattr(prov, "complete_with_fallback", judge)
    asyncio.run(runner.evaluate_run(runs[0]["id"]))
    assert "Checked what I could." in seen[0] and "Which servers?" not in seen[0]


def test_pending_fusions_are_capped(team, monkeypatch):
    from myclaw import agent_fusion as F

    monkeypatch.setattr(F, "MAX_PENDING", 1)
    F.create_fusion("Writer", "Editor", use_model=False)
    with pytest.raises(F.FusionError, match="already 1"):
        F.create_fusion("Writer", "Other", use_model=False)
