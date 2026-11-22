"""Focused tests for deterministic Medic workflow review proposals."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from myclaw.jobs import store

    store._migrated.clear()


def test_tool_review_detects_errors_slow_calls_and_repetition(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from myclaw.jobs import medic, store

    job = store.create_job({"name": "workflow", "prompt": "do work"})
    run_id = store.start_run(job["id"])
    first = store.record_tool_event(
        run_id, job["id"], tool="fetch", args={"url": "example"}, ok=False,
        result={"error": "offline"}, duration_ms=31_000)
    store.record_tool_event(
        run_id, job["id"], tool="fetch", args={"url": "example"}, ok=False,
        result={"error": "offline"}, duration_ms=31_000)
    events = store.list_tool_events(run_id=run_id, limit=10)
    second = next(event["id"] for event in events if event["id"] != first)

    review = asyncio.run(medic.review_tool_event(second))
    assert review["severity"] == "error"
    assert review["ok"] is False and review["repeated"] is True
    assert review["duration_ms"] == 31_000 and review["repetition_count"] == 2
    assert review["proposal_only"] is True


def test_chat_tool_review_only_compares_same_context(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from myclaw.jobs import medic, store

    store.record_tool_event(
        context_id="channel-a:scope:agent", tool="fetch",
        args={"url": "same"}, ok=False, result="offline",
    )
    other = store.record_tool_event(
        context_id="channel-b:scope:agent", tool="fetch",
        args={"url": "same"}, ok=False, result="offline",
    )
    same = store.record_tool_event(
        context_id="channel-b:scope:agent", tool="fetch",
        args={"url": "same"}, ok=False, result="offline",
    )

    assert asyncio.run(medic.review_tool_event(same))["repetition_count"] == 2
    assert asyncio.run(medic.review_tool_event(other))["repetition_count"] == 1


def test_run_review_persists_evidence_and_never_calls_model_for_healthy_run(
    tmp_path, monkeypatch,
):
    _home(tmp_path, monkeypatch)
    from myclaw.jobs import medic, store

    job = store.create_job({"name": "healthy", "prompt": "check"})
    run_id = store.start_run(job["id"])
    store.record_tool_event(run_id, job["id"], tool="read", result="ok", duration_ms=4)
    store.finish_run(run_id, "ok", "done")

    async def must_not_call(*args, **kwargs):
        raise AssertionError("healthy runs must not be model-enriched")

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", must_not_call)
    saved = asyncio.run(medic.review_run(run_id, enrich=True))
    review = saved["review"]
    assert saved["status"] == "pending"
    assert review["severity"] == "info" and review["anomalous"] is False
    assert review["proposal_only"] is True
    assert any("Tool event #1" in item for item in review["evidence"])
    assert review["recommendations"]


def test_api_lists_and_updates_workflow_review_status(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from myclaw.jobs import store
    from myclaw.web import medic_api, settings_api

    review = store.save_medic_review(
        7, "job-x",
        {"severity": "warning", "summary": "Repeated fetch", "evidence": ["same result x2"],
         "recommendations": ["Narrow the tool input."], "proposal_only": True},
    )
    app = FastAPI()
    app.include_router(medic_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None

    with TestClient(app) as client:
        listed = client.get("/api/medic/workflow-reviews")
        assert listed.status_code == 200
        item = listed.json()["reviews"][0]
        assert item["summary"] == "Repeated fetch"
        assert item["recommendations"] == ["Narrow the tool input."]
        updated = client.post(
            f"/api/medic/workflow-reviews/{review['id']}/status",
            json={"status": "acknowledged"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "acknowledged"
        assert store.get_medic_review(review["id"])["status"] == "acknowledged"
        invalid = client.post(
            f"/api/medic/workflow-reviews/{review['id']}/status",
            json={"status": "approved"},
        )
        assert invalid.status_code == 422
