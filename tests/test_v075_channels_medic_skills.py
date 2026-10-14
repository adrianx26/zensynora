"""v0.7.5: deleted agents left in channels ("zombies"), medic for failed jobs, agent skills.

Agents and LLM calls are faked; everything runs offline in a temporary HOME.
"""

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_v071_features import FakeAgent, _wait_turn, home  # noqa: F401  (fixture)


# ── Channels: zombie members ────────────────────────────────────────────────
@pytest.fixture
def chat_app(home):  # noqa: F811
    from myclaw.chat import store
    from myclaw.web import chat_api

    # The real case from a user's chat.db: "agent3" was renamed to "Coder".
    store.create_channel("general", ["default", "Executor", "agent3"], channel_id="general")
    store.create_channel("#house", ["default"], channel_id="house-1")
    store.dm_channel("agent3")
    store.dm_channel("default")
    agents = {"default": FakeAgent("default"), "Executor": FakeAgent("Executor"), "Coder": FakeAgent("Coder")}
    chat_api.set_registry_getter(lambda: agents)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as c:
        yield c, agents


def test_deleted_agents_are_removed_from_channels(chat_app):
    c, _ = chat_app
    chans = {ch["id"]: ch for ch in c.get("/api/channels").json()["channels"]}
    assert chans["general"]["members"] == ["default", "Executor"]
    assert chans["house-1"]["name"] == "house"          # no more "##house"
    assert chans["dm-agent3"]["orphan"] is True and chans["dm-default"]["orphan"] is False
    msgs = c.get("/api/channels/general/messages").json()["messages"]
    assert any("Removed @agent3" in m["text"] for m in msgs if m["role"] == "system")
    # Posting to the deleted agent's DM explains instead of silently answering as default.
    r = c.post("/api/channels/dm-agent3/messages", json={"text": "hi"})
    assert r.status_code == 400 and "no longer exists" in r.json()["detail"]


def test_member_changes_work_even_with_a_zombie_in_the_list(home):  # noqa: F811
    """Before: PATCH with the old member list failed with 'Unknown agent(s): agent3'."""
    from myclaw.chat import store
    from myclaw.web import chat_api

    store.create_channel("general", ["default", "Executor", "agent3"], channel_id="general")
    chat_api.set_registry_getter(lambda: {"default": 1, "Executor": 1, "Coder": 1})
    app = FastAPI()
    app.include_router(chat_api.router)
    c = TestClient(app)  # no GET /api/channels first: the zombie is still stored
    r = c.patch("/api/channels/general", json={"members": ["default", "Executor", "agent3", "coder"]})
    assert r.status_code == 200, r.text
    assert r.json()["members"] == ["default", "Executor", "Coder"]   # zombie dropped, case fixed
    assert c.patch("/api/channels/general", json={"members": ["default", "ghost"]}).status_code == 400


def test_mentions_ignore_case(chat_app):
    c, agents = chat_app
    c.post("/api/channels/general/messages", json={"text": "@executor what do you think?"})
    msgs = _wait_turn(c, "general", 1)
    assert [m["sender"] for m in msgs if m["role"] == "agent"] == ["Executor"]


def test_answers_cut_off_by_a_restart_do_not_stay_typing(home):  # noqa: F811
    from myclaw.chat import store
    from myclaw.web import chat_api

    store.ensure_default_channel()
    m1 = store.add_message("general", "agent", "default", "", {"streaming": True})
    m2 = store.add_message("general", "agent", "default", "half an ans", {"streaming": True})
    chat_api.set_registry_getter(lambda: {"default": 1})   # = server start
    by_id = {m["id"]: m for m in store.list_messages("general")}
    assert "restarted while this answer" in by_id[m1["id"]]["text"]
    assert by_id[m2["id"]]["text"].endswith("…[interrupted]")
    assert not by_id[m1["id"]]["meta"].get("streaming") and by_id[m1["id"]]["meta"]["interrupted"]


def test_settings_change_repairs_channels(home, monkeypatch):  # noqa: F811
    from myclaw.chat import store
    from myclaw.web import api as web_api

    store.create_channel("general", ["default", "agent3"], channel_id="general")
    monkeypatch.setattr(web_api, "_agent_registry", {})
    asyncio.run(web_api._reset_agent_registry())       # what saving Settings calls
    assert store.get_channel("general")["members"] == ["default"]


# ── Medic for failed jobs ────────────────────────────────────────────────────
class _StepAgent:
    """Fails with 'needed more tool steps' unless it may use enough rounds."""

    name, model, _provider_name = "default", "m", "p"

    def __init__(self, need=10):
        self.need = need
        self.seen_limits = []

    async def think(self, prompt, user_id="default"):
        from myclaw.agent_internals.tool_executor import MAX_ROUNDS

        self.seen_limits.append(MAX_ROUNDS.get())
        if MAX_ROUNDS.get() < self.need:
            return "I needed more tool steps than allowed for one message. Please ask again with a narrower request."
        return "Bucharest: clear, 21 °C"

    async def _get_memory(self, u):
        class M:
            async def clear(self):
                pass
        return M()


def test_rules_recognise_the_real_failures(home):  # noqa: F811
    from myclaw.jobs import medic

    job = {"max_steps": 8, "timeout_s": 300, "retries": 0}
    steps = medic.rule_diagnosis(job, {"status": "error", "error": "I needed more tool steps than allowed …"})
    assert steps["category"] == "tool_steps" and steps["job_changes"] == {"max_steps": 16}
    down = medic.rule_diagnosis(job, {"status": "error", "error": "[Error] the model server cannot be reached — "
                                                                   "is it running? (Details: Request timed out.)"})
    assert down["category"] == "model" and not down["job_changes"] and down["manual_steps"]
    slow = medic.rule_diagnosis(job, {"status": "timeout", "error": "No answer within 300 s"})
    assert slow["job_changes"] == {"timeout_s": 600}


def test_diagnose_uses_a_model_and_keeps_only_safe_changes(home, monkeypatch):  # noqa: F811
    import myclaw.provider as prov
    from myclaw.jobs import medic, store

    job = store.create_job({"name": "weather status", "prompt": "weather?", "agent": "default"})
    run = store.start_run(job["id"])
    store.finish_run(run, "error", "", "Error code: 500 something odd")

    async def fake(config, messages, provider=None, model=None):
        assert "weather status" in messages[1]["content"]
        return (json.dumps({"cause": "The prompt is too vague.", "category": "prompt", "explanation": "x",
                            "job_changes": {"prompt": "Get the weather for Bucharest from wttr.in",
                                            "schedule": "daily", "agent": "DEFAULT", "timeout_s": 99999},
                            "manual_steps": [], "code_fix_needed": False, "confidence": 0.8}), "or", "m1")

    monkeypatch.setattr(prov, "complete_with_fallback", fake)
    d = asyncio.run(medic.diagnose(run))
    # schedule is not a medic field; "DEFAULT" = the current agent; timeout capped
    assert d["job_changes"] == {"prompt": "Get the weather for Bucharest from wttr.in", "timeout_s": 7200}
    assert d["by"] == "or/m1" and store.get_run(run)["diagnosis"]["cause"] == "The prompt is too vague."

    async def down(*a, **k):
        raise RuntimeError("all models failed")

    monkeypatch.setattr(prov, "complete_with_fallback", down)
    store.finish_run(run, "error", "", "I needed more tool steps than allowed")
    d = asyncio.run(medic.diagnose(run))
    assert d["by"] == "rules" and d["category"] == "tool_steps" and "No model" in d["note"]


def test_on_failure_fix_raises_the_limit_and_the_rerun_succeeds(home, monkeypatch):  # noqa: F811
    """The user's 'weather status' job: failed 3x with 'needed more tool steps'."""
    import myclaw.provider as prov
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    async def down(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(prov, "complete_with_fallback", down)   # rules alone must be enough
    agent = _StepAgent(need=10)
    job = store.create_job({"name": "weather status", "prompt": "weather in Bucharest", "on_failure": "fix"})
    final = asyncio.run(JobRunner(lambda: {"default": agent}).run_job(job["id"]))
    runs = store.list_runs(job["id"])
    assert [r["status"] for r in runs] == ["ok", "error"] and runs[0]["id"] == final
    assert runs[1]["diagnosis"]["applied"] and runs[1]["diagnosis"]["rerun_id"] == final
    assert store.get_job(job["id"])["max_steps"] == 16 and agent.seen_limits == [8, 16]


def test_fix_endpoints(home, monkeypatch):  # noqa: F811
    import myclaw.provider as prov
    from myclaw.jobs import store
    from myclaw.web import jobs_api, settings_api

    async def down(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(prov, "complete_with_fallback", down)
    agent = _StepAgent(need=1)
    jobs_api.set_registry_getter(lambda: {"default": agent}, lambda: None)
    app = FastAPI()
    app.include_router(jobs_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    job = store.create_job({"name": "w", "prompt": "p", "timeout_s": 30})
    run = store.start_run(job["id"])
    store.finish_run(run, "timeout", "", "No answer within 30 s")
    with TestClient(app) as c:
        d = c.post(f"/api/jobs/runs/{run}/diagnose").json()
        assert d["category"] == "timeout" and d["job_changes"] == {"timeout_s": 600}
        r = c.post(f"/api/jobs/runs/{run}/fix", json={"run_again": False})
        assert r.status_code == 200 and r.json()["job"]["timeout_s"] == 600
        assert c.get(f"/api/jobs/{job['id']}/runs").json()["runs"][0]["diagnosis"]["applied"]
        code = c.post(f"/api/jobs/runs/{run}/code-fix").json()
        assert code["name"] == "Medic code fix: w"
        medic_job = store.get_job(code["job_id"])
        assert medic_job["created_by"] == "medic" and "propose_code_change" in medic_job["prompt"]
        assert c.post("/api/jobs/runs/999/diagnose").status_code == 404


def test_old_jobs_db_gets_the_new_columns(home):  # noqa: F811
    import sqlite3

    from myclaw.jobs import store

    path = home / ".myclaw" / "jobs.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL, "
                      "agent TEXT NOT NULL DEFAULT 'default', schedule TEXT NOT NULL DEFAULT 'manual', "
                      "every_minutes INTEGER NOT NULL DEFAULT 60, at_time TEXT NOT NULL DEFAULT '09:00', "
                      "days TEXT NOT NULL DEFAULT '', run_at REAL, enabled INTEGER NOT NULL DEFAULT 1, "
                      "channel_id TEXT NOT NULL DEFAULT '', success_criteria TEXT NOT NULL DEFAULT '', "
                      "auto_evaluate INTEGER NOT NULL DEFAULT 0, fresh_context INTEGER NOT NULL DEFAULT 1, "
                      "timeout_s INTEGER NOT NULL DEFAULT 300, retries INTEGER NOT NULL DEFAULT 0, "
                      "created_by TEXT NOT NULL DEFAULT 'you', created REAL NOT NULL, updated REAL NOT NULL, "
                      "next_run REAL, last_run REAL, last_status TEXT NOT NULL DEFAULT '');"
                      "INSERT INTO jobs (id, name, prompt, created, updated) VALUES ('j1', 'old', 'p', 1, 1);")
    con.close()
    store._migrated.clear()
    assert store.get_job("j1")["max_steps"] == 8 and store.get_job("j1")["on_failure"] == ""


# ── Agent skills ────────────────────────────────────────────────────────────
def test_builtin_skills_ship_with_the_app(home):  # noqa: F811
    from myclaw import agent_skills

    names = {s["name"] for s in agent_skills.list_skills()}
    assert {"web-research", "python-pro", "code-reviewer", "powershell-scripting", "knowledge-keeper"} <= names
    assert all(s["description"] for s in agent_skills.list_skills())


def test_skill_files_parse_and_yours_replace_builtin(home):  # noqa: F811
    from myclaw import agent_skills

    sk = agent_skills.parse("---\nname: My Skill\ndescription: Does x\nalways: true\n---\n# Title\nStep 1")
    assert sk == {"name": "my-skill", "description": "Does x", "always": True, "body": "# Title\nStep 1"}
    assert agent_skills.parse("# Weather helper\nUse wttr.in", "weather")["description"] == "Weather helper"
    agent_skills.save_skill("web-research", "My own way", "Always use example.org")
    sk = agent_skills.get_skill("web-research")
    assert sk["source"] == "yours" and sk["body"] == "Always use example.org"
    assert agent_skills.delete_skill("web-research")
    assert agent_skills.get_skill("web-research")["source"] == "builtin"   # the built-in one is back
    with pytest.raises(ValueError):
        agent_skills.save_skill("x", "", "   ")


def test_agents_see_their_skills_and_can_load_them(home):  # noqa: F811
    import myclaw.config as cfg_mod
    from myclaw import agent_skills
    from myclaw.agent import Agent
    from myclaw.tools import TOOLS

    agent_skills.save_skill("house-rules", "How we name files", "Use kebab-case.", always=True)
    cfg = cfg_mod.load_config()
    cfg.agents.defaults.skills = ["web-research", "house-rules", "deleted-one"]
    prompt = asyncio.run(Agent(cfg, name="default")._load_system_prompt())
    assert "## Your skills" in prompt and "- web-research: Look up current facts" in prompt
    assert "Use kebab-case." in prompt                      # always: true → full text included
    assert "deleted-one" not in prompt                     # unknown names are ignored
    assert "wttr.in" not in prompt                         # on-demand skills are not inlined
    assert "wttr.in" in TOOLS["use_skill"]["func"]("web-research")
    from myclaw.tools import TOOL_SCHEMAS, ensure_tool_schemas

    ensure_tool_schemas()
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "use_skill")
    assert "name" in schema["function"]["parameters"]["properties"]
    assert "No skill named" in agent_skills.use_skill("nope")


def test_skills_api_assign_and_import(home):  # noqa: F811
    from myclaw.config import load_config, save_config
    from myclaw.web import settings_api, skills_api

    save_config({"agents": {"defaults": {"model": "m", "provider": "ollama"},
                            "named": [{"name": "Executor", "model": "x", "provider": ""}]}})
    load_config(force_reload=True)
    reloads = []

    async def hook():
        reloads.append(1)

    settings_api.set_config_changed_hook(hook)
    app = FastAPI()
    app.include_router(skills_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        r = c.put("/api/skills/web-research/agents", json={"agents": ["default", "Executor"]})
        assert r.status_code == 200, r.text
        assert r.json()["agents"] == ["default", "Executor"]
        cfg = load_config(force_reload=True)
        assert cfg.agents.defaults.skills == ["web-research"] and cfg.agents.named[0].skills == ["web-research"]
        c.put("/api/skills/web-research/agents", json={"agents": ["Executor"]})
        assert load_config(force_reload=True).agents.defaults.skills == []
        assert c.put("/api/skills/web-research/agents", json={"agents": ["ghost"]}).status_code == 400

        r = c.post("/api/skills/import", json={"text": "---\nname: weather\ndescription: Weather for a city\n"
                                                       "---\nUse https://wttr.in/<City>?format=3"})
        assert r.status_code == 200 and r.json()["source"] == "yours"
        listing = c.get("/api/skills").json()
        row = next(s for s in listing["skills"] if s["name"] == "web-research")
        assert row["agents"] == ["Executor"] and "browse" in listing["tools"]
        assert c.post("/api/skills/python-pro/copy").json()["source"] == "yours"
        assert c.delete("/api/skills/code-reviewer").status_code == 404     # built-in stays
        assert c.delete("/api/skills/weather").status_code == 200
    assert reloads  # agents were rebuilt so they see the change


def test_renaming_an_agent_keeps_its_channels_and_jobs(home):  # noqa: F811
    from myclaw.chat import store as chat
    from myclaw.config import load_config, save_config
    from myclaw.jobs import store as jobs
    from myclaw.web import settings_api

    save_config({"agents": {"defaults": {"model": "m", "provider": "ollama"},
                            "named": [{"name": "agent3", "model": "x", "provider": ""}]}})
    load_config(force_reload=True)
    chat.create_channel("general", ["default", "agent3"], channel_id="general")
    dm = chat.dm_channel("agent3")
    chat.add_message(dm["id"], "user", "you", "hello")
    job = jobs.create_job({"name": "w", "prompt": "p", "agent": "agent3"})
    asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(
        changes={"agents.named": [{"name": "Coder", "model": "x", "provider": ""}]})))
    assert chat.get_channel("general")["members"] == ["default", "Coder"]
    assert chat.get_channel("dm-coder")["members"] == ["Coder"] and chat.list_messages("dm-coder")[0]["text"] == "hello"
    assert jobs.get_job(job["id"])["agent"] == "Coder"
    with pytest.raises(Exception) as e:
        asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(
            changes={"agents.named": [{"name": "Coder", "model": "x"}, {"name": "coder", "model": "y"}]})))
    assert "names must differ" in str(e.value.detail)


# ── Token speed, creating agents, delegation ────────────────────────────────
def test_token_speed_is_measured_from_the_first_token():
    from myclaw.chat.engine import estimate_tokens, token_speed

    text = "word " * 100
    assert estimate_tokens(text) == 100
    assert 5 <= estimate_tokens("Internationalization, please!") <= 8  # long word split, punctuation counted
    m = token_speed(text, 100, started=10.0, first_at=12.0, last_at=14.0)   # 2 s waiting, 2 s writing
    assert m["ttft_ms"] == 2000 and m["tokens"] == 100 and m["tps"] == 50.0
    assert token_speed("", 0, 0, None, None) == {}
    assert "tps" not in token_speed("hi", 1, 0.0, 0.1, 0.1)                 # too short to measure


def test_chat_answers_carry_token_speed(chat_app):
    c, _ = chat_app
    c.post("/api/channels/general/messages", json={"text": "hello"})
    msgs = _wait_turn(c, "general", 1)
    meta = [m for m in msgs if m["role"] == "agent"][-1]["meta"]
    assert meta["tokens"] >= 3 and "ttft_ms" in meta


@pytest.fixture
def team(home):  # noqa: F811
    from myclaw.config import load_config, save_config
    from myclaw.tools import core

    save_config({"agents": {"defaults": {"model": "m", "provider": "ollama"},
                            "named": [{"name": "Executor", "model": "x", "provider": "", "description": "Runs tasks"}]}})
    cfg = load_config(force_reload=True)
    calls = []

    class A:
        def __init__(self, name, delegate_to=None):
            self.name = name
            self.delegate_to = delegate_to

        async def think(self, task, user_id="default", _depth=0):
            calls.append((self.name, task))
            if self.delegate_to:  # the helper tries to hand it on again
                from myclaw.tools.swarm import delegate
                return await delegate(self.delegate_to, task)
            return f"{self.name} did: {task}"

    reg = {"default": A("default"), "Executor": A("Executor")}
    core.set_registry(reg)
    yield cfg, reg, calls, A
    core.set_registry({})


def test_agents_know_their_team(team):
    from myclaw.agent_team import team_section

    cfg, *_ = team
    s = team_section(cfg, "default")
    assert "- @Executor: Runs tasks" in s and "delegate(agent_name, task)" in s and "create_agent" in s
    assert "@default" in team_section(cfg, "Executor") and "@Executor:" not in team_section(cfg, "Executor")
    cfg.agents.allow_agent_creation = False
    assert "create_agent" not in team_section(cfg, "default")


def test_delegation_finds_any_case_and_cannot_loop(team):
    from myclaw.events import CURRENT_AGENT
    from myclaw.tools import core
    from myclaw.tools.swarm import delegate

    cfg, reg, calls, A = team
    assert asyncio.run(delegate("executor", "check the weather")) == "Executor did: check the weather"
    tok = CURRENT_AGENT.set("Executor")
    assert "yourself" in asyncio.run(delegate("Executor", "x"))
    CURRENT_AGENT.reset(tok)
    # ping-pong: A asks B, B asks A, A asks B … used to run until the timeout
    core.set_registry({"default": A("default", delegate_to="Executor"), "Executor": A("Executor", delegate_to="default")})
    calls.clear()
    out = asyncio.run(delegate("Executor", "loop"))
    assert "handed over 2 times" in out and len(calls) == 2


def test_an_agent_can_create_an_agent(team):
    from myclaw import events
    from myclaw.agent_team import create_agent, on_agent_created
    from myclaw.config import load_config
    from myclaw.tools import TOOLS, core

    created, seen = [], []
    on_agent_created(lambda n, a: created.append(n))
    events.add_listener(lambda ev: seen.append(ev))
    tok = events.CURRENT_AGENT.set("Executor")
    ch = events.CURRENT_CHANNEL.set("general")
    try:
        out = create_agent("Weather Bot", "Reports the weather for a city", skills="web-research")
    finally:
        events.CURRENT_AGENT.reset(tok)
        events.CURRENT_CHANNEL.reset(ch)
    assert out.startswith("Created agent @Weather_Bot"), out
    cfg = load_config(force_reload=True)
    bot = next(a for a in cfg.agents.named if a.name == "Weather_Bot")
    assert bot.skills == ["web-research"] and "Reports the weather" in bot.system_prompt and bot.model == "m"
    assert "Weather_Bot" in core._agent_registry and created == ["Weather_Bot"]
    assert any(e.get("type") == "agent_created" and e["from"] == "Executor" for e in seen)
    assert "already exists" in create_agent("weather_bot", "dup")
    assert "unknown skill" in create_agent("Other", "x", skills="nope")
    assert "Error" in create_agent("1bad", "x")
    assert "create_agent" in TOOLS
    cfg.agents.allow_agent_creation = False
    from myclaw.config import save_config
    save_config(cfg.model_dump(mode="json"))
    load_config(force_reload=True)
    assert "turned off" in create_agent("Another", "x")
