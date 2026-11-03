"""2026-11-03: Evolve — agent levels, agent fusion, AI change requests.

* Every agent has a level 1-9 (default 1; a label for now).
* Fusion merges two named agents into one: the model drafts name, description
  and prompt (you review and edit), skills are combined, the model comes from
  the agent you pick, the level is the lower one. Channels, direct messages
  and jobs move to the new agent; the two originals go to an agent trash and
  can be restored.
* A change request becomes a one-off job with a precise prompt: the agent
  reads the code and files a change proposal, which is installed only after
  you approve it (existing self-update pipeline).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


def _agents(**named):
    from myclaw.config import NamedAgentConfig, load_config, save_config

    cfg = load_config()
    cfg.agents.named = [NamedAgentConfig(name=n, **kw) for n, kw in named.items()]
    save_config(cfg)
    return cfg


def test_levels_default_and_bounds():
    from pydantic import ValidationError

    from myclaw.config import AgentDefaults, NamedAgentConfig

    assert NamedAgentConfig(name="x").level == 1 and AgentDefaults().level == 1
    for bad in (0, 10):
        with pytest.raises(ValidationError):
            NamedAgentConfig(name="x", level=bad)


def test_set_level(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(Coder={"model": "m"})
    asyncio.run(evolve.set_level("Coder", 4))
    asyncio.run(evolve.set_level("default", 2))
    cfg = load_config(force_reload=True)
    assert cfg.agents.named[0].level == 4 and cfg.agents.defaults.level == 2
    with pytest.raises(ValueError):
        asyncio.run(evolve.set_level("Nobody", 3))


def _fake(monkeypatch, answer, seen=None):
    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer) if not isinstance(answer, str) else answer, "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def test_fusion_preview_then_apply(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.chat import store as chat
    from myclaw.config import load_config
    from myclaw.jobs import store as jobs

    _agents(Coder={"model": "m1", "provider": "openrouter", "level": 5, "skills": ["git"], "system_prompt": "You write code."},
            Tester={"model": "m2", "level": 3, "skills": ["pytest", "git"], "system_prompt": "You test code."},
            Other={"model": "m3"})
    ch = chat.create_channel("team", ["Coder", "Tester", "Other"])
    job = jobs.create_job({"name": "nightly", "prompt": "p", "agent": "Tester"})
    seen: list = []
    _fake(monkeypatch, {"name": "Builder", "description": "Writes and tests code.",
                        "system_prompt": "You write code and test it."}, seen)

    p = asyncio.run(evolve.fusion_preview("Coder", "Tester"))
    assert p["name"] == "Builder" and p["level"] == 3 and p["skills"] == ["git", "pytest"]
    assert p["model_from"] == "Coder" and "You write code." in seen[0][1]["content"]
    assert len(load_config(force_reload=True).agents.named) == 3        # preview changes nothing

    out = asyncio.run(evolve.fuse("Coder", "Tester", name="Builder", description=p["description"],
                                  system_prompt="Edited prompt.", model_from="Tester"))
    cfg = load_config(force_reload=True)
    names = [a.name for a in cfg.agents.named]
    assert names == ["Other", "Builder"] and out["agent"]["name"] == "Builder"
    b = cfg.agents.named[1]
    assert (b.model, b.provider, b.level, b.skills, b.system_prompt) == ("m2", "", 3, ["git", "pytest"], "Edited prompt.")
    assert chat.get_channel(ch["id"])["members"] == ["Builder", "Other"]
    assert jobs.get_job(job["id"])["agent"] == "Builder"
    trash = evolve.agent_trash()
    assert {t["agent"]["name"] for t in trash} == {"Coder", "Tester"} and all(t["fused_into"] == "Builder" for t in trash)

    asyncio.run(evolve.restore_agent("Coder"))
    cfg = load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Other", "Builder", "Coder"]
    assert cfg.agents.named[2].level == 5 and {t["agent"]["name"] for t in evolve.agent_trash()} == {"Tester"}


@pytest.mark.parametrize("a,b,name", [("Coder", "Coder", "X"), ("Coder", "Nope", "X"), ("default", "Coder", "X"),
                                      ("Coder", "Tester", "default"), ("Coder", "Tester", "Other"), ("Coder", "Tester", " ")])
def test_fusion_rejects_bad_input(home, a, b, name):  # noqa: F811
    from myclaw import evolve

    _agents(Coder={"model": "m1"}, Tester={"model": "m2"}, Other={"model": "m3"})
    with pytest.raises(ValueError):
        asyncio.run(evolve.fuse(a, b, name=name, description="", system_prompt="p", model_from=a))


def test_fusion_preview_without_a_usable_answer_still_works(home, monkeypatch):  # noqa: F811
    from myclaw import evolve

    _agents(Coder={"model": "m1", "system_prompt": "A"}, Tester={"model": "m2", "system_prompt": "B"})
    _fake(monkeypatch, RuntimeError("429 rate limited"))
    p = asyncio.run(evolve.fusion_preview("Coder", "Tester"))
    assert p["name"] == "Coder-Tester" and "A" in p["system_prompt"] and "B" in p["system_prompt"]
    assert "rate" in p["note"]


def test_change_request_becomes_a_job(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.jobs import store as jobs

    started = []
    monkeypatch.setattr(evolve, "_start_job", lambda job_id: started.append(job_id))
    r = evolve.request_change(action="optimise", target_kind="function", target="myclaw/knowledge/overview.py:_fill",
                              goal="Make it faster on 5000 notes.", agent="default")
    job = jobs.get_job(r["job_id"])
    assert started == [r["job_id"]] and job["name"].startswith("Evolve: ") and job["schedule"] == "manual"
    p = job["prompt"]
    for must in ("myclaw/knowledge/overview.py:_fill", "Make it faster on 5000 notes.", "read_source_file",
                 "propose_code_change", "approve", "smallest"):
        assert must in p
    assert job["max_steps"] >= 20
    with pytest.raises(ValueError):
        evolve.request_change(action="optimise", target_kind="function", target="", goal="  ", agent="default")
    llm = evolve.request_change(action="optimise", target_kind="llm", target="", goal="Cheaper models.", agent="default")
    assert "Do not change code" in jobs.get_job(llm["job_id"])["prompt"]
    assert [j["id"] for j in evolve.change_requests()] == [llm["job_id"], r["job_id"]]

    def boom(job_id):
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(evolve, "_start_job", boom)
    with pytest.raises(RuntimeError):
        evolve.request_change(action="edit", target_kind="code", target="", goal="x", agent="default")
    assert len(evolve.change_requests()) == 2          # the job that could not start is gone


def test_api_routes(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import evolve
    from myclaw.web import evolve_api

    _agents(Coder={"model": "m1"})
    started = []

    class Runner:  # the real _start_job runs; starting a job needs the server's event loop
        def run_in_background(self, job_id, trigger="manual"):
            asyncio.get_running_loop()
            started.append(job_id)

    monkeypatch.setattr("myclaw.web.jobs_api.runner", lambda: Runner())
    app = FastAPI()
    app.include_router(evolve_api.router)
    c = TestClient(app)
    o = c.get("/api/evolve/overview").json()
    assert {"agents", "proposals", "requests", "trash", "self_update", "models"} <= set(o)
    assert [a["name"] for a in o["agents"]] == ["default", "Coder"] and o["agents"][1]["level"] == 1
    assert c.put("/api/evolve/agents/Coder/level", json={"level": 7}).json()["level"] == 7
    assert c.put("/api/evolve/agents/Coder/level", json={"level": 12}).status_code == 422
    r = c.post("/api/evolve/request", json={"action": "add", "target_kind": "script", "target": "scripts/x.sh",
                                            "goal": "Back up the notes.", "agent": "default"})
    assert r.status_code == 200 and started == [r.json()["job_id"]]
    assert c.post("/api/evolve/fuse", json={"a": "Coder", "b": "Coder", "name": "X", "description": "",
                                            "system_prompt": "p", "model_from": "Coder"}).status_code == 400
