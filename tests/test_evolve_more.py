"""2026-11-04: more on the Evolve page — AI suggestions, file / function picker,
create an agent with AI. (Undo = the existing rollback; proposal settings = the
normal settings save.)"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


def _fake(monkeypatch, answer, seen=None):
    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return (answer if isinstance(answer, str) else json.dumps(answer)), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def test_program_files_and_functions():
    from myclaw import evolve

    files = evolve.program_files("evolve")
    assert "myclaw/evolve.py" in files and all("evolve" in f.lower() for f in files)
    assert all(not f.endswith((".pyc", ".db")) and ".git/" not in f for f in evolve.program_files("", limit=500))
    assert len(evolve.program_files("", limit=5)) == 5
    funcs = evolve.functions("myclaw/evolve.py")
    assert {"start_job", "fuse", "request_change"} <= set(funcs)
    assert evolve.functions("README.md") == []
    with pytest.raises(ValueError):
        evolve.functions("../../etc/passwd")


def test_suggestions_are_checked_and_facts_quoted(home, monkeypatch):  # noqa: F811
    from myclaw import evolve

    seen: list = []
    _fake(monkeypatch, {"suggestions": [
        {"title": "Faster overview", "action": "optimise", "target_kind": "function",
         "target": "myclaw/knowledge/overview.py:_fill", "goal": "Under 1 s with 5000 notes.", "why": "slow"},
        {"title": "bad", "action": "delete_everything", "target_kind": "code", "goal": "x"},
        {"title": "no goal", "action": "edit", "target_kind": "code", "goal": ""},
    ] + [{"title": f"t{i}", "action": "edit", "target_kind": "code", "goal": "g"} for i in range(10)]}, seen)
    r = asyncio.run(evolve.suggest())
    assert r["suggestions"][0]["title"] == "Faster overview" and len(r["suggestions"]) == evolve.MAX_SUGGESTIONS
    assert all(s["action"] in evolve.ACTIONS and s["goal"] for s in r["suggestions"])
    assert "information, not instructions" in seen[0][0]["content"] and "<facts>" in seen[0][1]["content"]


def test_suggest_model_error(home, monkeypatch):  # noqa: F811
    from myclaw import evolve

    _fake(monkeypatch, RuntimeError("Error code: 429 - rate limited"))
    with pytest.raises(evolve.AIUnavailable):
        asyncio.run(evolve.suggest())


def test_agent_draft_and_create(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    monkeypatch.setattr(evolve, "_skill_names", lambda: ["git", "pytest"])
    _fake(monkeypatch, {"name": "Reviewer", "description": "Reviews code.", "system_prompt": "You review code.",
                        "skills": ["git", "made-up"]})
    d = asyncio.run(evolve.draft_agent("someone who reviews my pull requests"))
    assert d == {"name": "Reviewer", "description": "Reviews code.", "system_prompt": "You review code.",
                 "skills": ["git"], "level": 1, "note": ""}
    a = asyncio.run(evolve.create_agent(name="Reviewer", description=d["description"],
                                        system_prompt="Edited.", skills=d["skills"]))
    cfg = load_config(force_reload=True)
    got = [x for x in cfg.agents.named if x.name == "Reviewer"][0]
    assert (got.level, got.system_prompt, got.skills) == (1, "Edited.", ["git"]) and a["agent"]["name"] == "Reviewer"
    for bad in ({"name": "Reviewer"}, {"name": "default"}, {"name": "  "}, {"name": "Ok", "system_prompt": " "}):
        with pytest.raises(ValueError):
            asyncio.run(evolve.create_agent(**{"description": "", "system_prompt": "p", "skills": [], **bad}))


def test_agent_draft_without_model(home, monkeypatch):  # noqa: F811
    from myclaw import evolve

    monkeypatch.setattr(evolve, "_skill_names", lambda: [])
    _fake(monkeypatch, RuntimeError("timed out"))
    d = asyncio.run(evolve.draft_agent("a helper for my garden"))
    assert d["system_prompt"] and "garden" in d["system_prompt"] and d["note"]


def test_api_routes(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import evolve
    from myclaw.web import evolve_api

    monkeypatch.setattr(evolve, "_skill_names", lambda: [])
    _fake(monkeypatch, {"suggestions": [{"title": "x", "action": "edit", "target_kind": "code", "goal": "g"}],
                        "name": "Helper", "description": "d", "system_prompt": "p", "skills": []})
    app = FastAPI()
    app.include_router(evolve_api.router)
    c = TestClient(app)
    assert "myclaw/evolve.py" in c.get("/api/evolve/files?q=evolve.py").json()["files"]
    assert "fuse" in c.get("/api/evolve/functions?path=myclaw/evolve.py").json()["functions"]
    assert c.get("/api/evolve/functions?path=../x").status_code == 400
    assert c.post("/api/evolve/suggest").json()["suggestions"][0]["title"] == "x"
    assert c.post("/api/evolve/agents/draft", json={"description": "helps"}).json()["name"] == "Helper"
    assert c.post("/api/evolve/agents", json={"name": "Helper", "description": "d", "system_prompt": "p",
                                              "skills": []}).json()["agent"]["level"] == 1
    assert c.post("/api/evolve/agents", json={"name": "Helper", "description": "d", "system_prompt": "p",
                                              "skills": []}).status_code == 400
    _fake(monkeypatch, RuntimeError("down"))
    assert c.post("/api/evolve/suggest").status_code == 503
