"""Persistence and sanitization for Medic job/tool reviews."""

import json
import sqlite3
import threading

import pytest


@pytest.fixture
def jobs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from myclaw.jobs import store

    store._migrated.clear()
    yield store
    store._migrated.clear()


def test_tool_events_are_sanitized_bounded_and_use_job_context(jobs_home):
    store = jobs_home
    run_token = store.CURRENT_RUN_ID.set(42)
    job_token = store.CURRENT_JOB_ID.set("weather")
    try:
        event_id = store.record_tool_event(
            agent="default",
            tool="http",
            args={"api_key": "super-secret-value", "email": "alice@example.com"},
            ok=False,
            result="owner bob@example.net " + ("x" * 20_000),
            duration_ms=17.9,
        )
    finally:
        store.CURRENT_RUN_ID.reset(run_token)
        store.CURRENT_JOB_ID.reset(job_token)

    event = store.get_tool_event(event_id)
    assert event["run_id"] == 42 and event["job_id"] == "weather"
    assert event["args"]["api_key"] == "[redacted]"
    assert "alice@example.com" not in json.dumps(event["args"])
    assert "bob@example.net" not in event["result"]
    assert "[truncated" in event["result"]
    assert event["status"] == "error" and event["duration_ms"] == 17
    assert store.list_tool_events(run_id=42, tool="http") == [event]


def test_tool_event_context_filters_isolate_non_job_chats(jobs_home):
    store = jobs_home
    store.record_tool_event(
        agent="writer", tool="web", context_id="channel:a:default", ok=True, result="a")
    store.record_tool_event(
        agent="coder", tool="web", context_id="channel:b:default", ok=False, result="b")

    assert [e["tool"] for e in store.list_tool_events(context_id="channel:a:default")] == ["web"]
    assert store.list_tool_events(context_id="channel:a:default")[0]["ok"] is True


def test_tool_event_writes_are_thread_safe(jobs_home):
    store = jobs_home
    threads = [
        threading.Thread(
            target=store.record_tool_event,
            kwargs={"job_id": "parallel", "agent": "a", "tool": str(index)},
        )
        for index in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.list_tool_events(job_id="parallel", limit=50)) == 12


def test_medic_review_crud_filtering_and_status(jobs_home):
    store = jobs_home
    review = store.save_medic_review(
        7, "job-1", {"summary": "Try again", "severity": "warning"}, source="rules"
    )
    assert review["status"] == "pending"
    assert review["review"]["summary"] == "Try again"
    assert store.list_medic_reviews(run_id=7, status="pending")[0]["id"] == review["id"]

    updated = store.set_medic_review_status(review["id"], "dismissed", "handled manually")
    assert updated["status"] == "dismissed" and updated["note"] == "handled manually"
    assert store.get_medic_review(review["id"]) == updated
    assert store.list_medic_reviews(status="pending") == []
    assert store.delete_medic_review(review["id"])


def test_event_and_review_row_limits_are_enforced(jobs_home, monkeypatch):
    store = jobs_home
    monkeypatch.setattr(store, "TOOL_EVENT_MAX_ROWS", 2)
    monkeypatch.setattr(store, "MEDIC_REVIEW_MAX_ROWS", 2)
    for index in range(4):
        store.record_tool_event(job_id="j", tool=str(index))
        store.save_medic_review(index, "j", {"index": index})

    assert [event["tool"] for event in store.list_tool_events()] == ["3", "2"]
    assert [review["review"]["index"] for review in store.list_medic_reviews()] == [3, 2]


def test_idle_delete_checks_all_runs_not_only_latest(jobs_home):
    store = jobs_home
    job = store.create_job({"name": "benchmark", "prompt": "work"})
    older = store.start_run(job["id"], kind="bench")
    newer = store.start_run(job["id"], kind="bench")
    store.finish_run(newer, "ok", "done")

    assert store.delete_job_if_idle(job["id"]) == "running"
    assert store.get_job(job["id"]) is not None
    store.finish_run(older, "cancelled", "", "stopped")
    assert store.delete_job_if_idle(job["id"]) == "deleted"


def test_old_database_gets_event_and_review_schema(jobs_home):
    store = jobs_home
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "prompt TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL)"
        )
    store._migrated.clear()

    assert store.list_tool_events() == []
    assert store.list_medic_reviews() == []
    with sqlite3.connect(path) as conn:
        for table, required in (
            ("tool_events", {"id", "run_id", "job_id", "context_id", "args_json", "result_json"}),
            ("medic_reviews", {"id", "run_id", "job_id", "review_json", "status"}),
        ):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert required <= columns
        indexes = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "ix_tool_events_run" in indexes
        assert "ix_medic_reviews_status_created" in indexes
