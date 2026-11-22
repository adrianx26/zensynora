"""Focused coverage for proposal-only AI job advice."""

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_v071_features import home  # noqa: F401


def _configure_agents(home):
    from myclaw.config import NamedAgentConfig, load_config, save_config

    config = load_config(force_reload=True)
    config.agents.named = [
        NamedAgentConfig(
            name="Coder",
            description="Python code fixes, debugging, tests, and traceback analysis.",
        ),
        NamedAgentConfig(
            name="Researcher",
            description="Web research, source discovery, and evidence gathering.",
        ),
    ]
    save_config(config)
    return {"default": object(), "Coder": object(), "Researcher": object()}


def test_ai_ask_uses_run_and_tool_evidence_without_exposing_secrets(home, monkeypatch):
    from myclaw.jobs import medic, store

    registry = _configure_agents(home)
    store._migrated.clear()
    job = store.create_job({
        "name": "repair importer",
        "prompt": (
            "Fix the Python traceback in the importer for alice@example.com. "
            "api_key=top-secret-value"
        ),
    })
    run_id = store.start_run(job["id"])
    store.record_tool_event(
        run_id, job["id"], tool="read_source_file", args={"path": "x.py"},
        result="Authorization: Bearer abcdefghijklmnop", ok=False,
    )
    store.finish_run(run_id, "error", "", "Traceback TypeError: bad input")
    store.save_medic_review(
        run_id,
        job["id"],
        {
            "severity": "error",
            "summary": "Python tool failed",
            "recommendations": ["Inspect traceback evidence."],
        },
    )

    captured = {}

    async def choose(config, content):
        captured["evidence"] = content
        return (
            json.dumps({
                "answer": "Coder is the specialist for this traceback.",
                "suggested_agent": "Coder",
                "reason": "The prompt and failed source-reading tool are about code debugging.",
                "confidence": 0.91,
                "proposed_job_changes": {
                    "agent": "Coder",
                    "timeout_s": 999999,
                    "schedule": "daily",
                },
            }),
            "local/test",
        )

    monkeypatch.setattr(medic, "_job_ask_local_model", choose)
    result = asyncio.run(medic.ai_ask(
        job["id"], run_id, "Which debugger should handle this?", registry))

    assert result["suggested_agent"] == "Coder"
    assert result["proposed_job_changes"] == {"agent": "Coder", "timeout_s": 7200}
    assert "Python" in captured["evidence"] and "read_source_file" in captured["evidence"]
    assert "top-secret-value" not in captured["evidence"]
    assert "abcdefghijklmnop" not in captured["evidence"]
    assert "alice@example.com" not in captured["evidence"]
    assert store.get_job(job["id"])["agent"] == "default"


def test_ai_ask_validates_unknown_model_agent_and_has_rule_fallback(home, monkeypatch):
    from myclaw.jobs import medic, store

    registry = _configure_agents(home)
    store._migrated.clear()
    job = store.create_job({
        "name": "debug Python worker",
        "prompt": "Debug Python code and fix the failing test.",
    })

    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(medic, "_job_ask_local_model", unavailable)
    result = asyncio.run(medic.ai_ask(job["id"], registry=registry))
    assert result["suggested_agent"] == "Coder"
    assert result["by"] == "rules"
    assert result["confidence"] > 0.3

    async def unknown(config, content):
        return json.dumps({
            "answer": "Use the missing agent.",
            "suggested_agent": "Ghost",
            "reason": "Invalid model choice.",
            "confidence": 0.9,
            "proposed_job_changes": {"agent": "Ghost"},
        }), "local/test"

    monkeypatch.setattr(medic, "_job_ask_local_model", unknown)
    validated = asyncio.run(medic.ai_ask(job["id"], registry=registry))
    assert validated["suggested_agent"] == "Coder"
    assert "Ghost" not in validated["proposed_job_changes"].values()


def test_ai_ask_endpoint_is_proposal_only_and_rejects_wrong_run(home, monkeypatch):
    from myclaw.jobs import medic, store
    from myclaw.web import jobs_api, settings_api

    registry = _configure_agents(home)
    store._migrated.clear()
    jobs_api.set_registry_getter(lambda: registry, lambda: None)
    app = FastAPI()
    app.include_router(jobs_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None

    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(medic, "_job_ask_local_model", unavailable)
    job = store.create_job({
        "name": "research release",
        "prompt": "Research current release notes and gather web sources.",
    })
    other = store.create_job({"name": "other", "prompt": "other"})
    wrong_run = store.start_run(other["id"])

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job['id']}/ai-ask", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["suggested_agent"] == "Researcher"
        assert set(body) == {
            "answer", "suggested_agent", "reason", "confidence",
            "proposed_job_changes", "by",
        }
        assert store.get_job(job["id"])["agent"] == "default"

        wrong = client.post(
            f"/api/jobs/{job['id']}/ai-ask", json={"run_id": wrong_run})
        assert wrong.status_code == 400
        missing = client.post("/api/jobs/missing/ai-ask", json={})
        assert missing.status_code == 404

        changed = client.patch(
            f"/api/jobs/{job['id']}",
            json={"agent": body["suggested_agent"]},
        )
        assert changed.status_code == 200
        assert changed.json()["agent"] == "Researcher"


def test_delete_refuses_active_job(home, monkeypatch):
    from types import SimpleNamespace

    from myclaw.jobs import store
    from myclaw.web import jobs_api, settings_api

    job = store.create_job({"name": "active", "prompt": "work"})
    monkeypatch.setattr(
        jobs_api,
        "runner",
        lambda: SimpleNamespace(running_job_ids=lambda: [job["id"]]),
    )
    app = FastAPI()
    app.include_router(jobs_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as client:
        response = client.delete(f"/api/jobs/{job['id']}")
    assert response.status_code == 409
    assert store.get_job(job["id"]) is not None
