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
