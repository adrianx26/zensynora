"""Job insight settings (v0.8.5): per-job prior-runs context and the opt-in
insight note / profile proposal toggles — stored with defaults, migrated into
old databases, and accepted + validated by the jobs API."""

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_v071_features import home  # noqa: F401  (shared temp-home fixture)


def _app() -> FastAPI:
    from myclaw.web import jobs_api, settings_api

    jobs_api.set_registry_getter(lambda: {"default": object()}, lambda: None)
    app = FastAPI()
    app.include_router(jobs_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    return app


def test_insight_opt_ins_default_off_and_round_trip(home):  # noqa: F811
    from myclaw.jobs import store

    job = store.create_job({"name": "insights", "prompt": "p"})
    assert job["insight_notes"] is False and job["insight_profile"] is False
    assert job["context_runs"] == 0

    job = store.update_job(job["id"], {"insight_notes": True, "insight_profile": True,
                                       "context_runs": 5})
    assert job["insight_notes"] is True and job["insight_profile"] is True
    assert job["context_runs"] == 5

    # stored as 0/1 integers, always served as booleans
    again = store.get_job(job["id"])
    assert again["insight_notes"] is True and isinstance(again["insight_notes"], bool)
    assert again["insight_profile"] is True and isinstance(again["insight_profile"], bool)

    off = store.update_job(job["id"], {"insight_notes": False})
    assert off["insight_notes"] is False and off["insight_profile"] is True


def test_old_jobs_db_gets_the_insight_columns(home):  # noqa: F811
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
    job = store.get_job("j1")
    assert job["insight_notes"] is False and job["insight_profile"] is False
    assert job["context_runs"] == 0


def test_jobs_api_accepts_and_validates_insight_fields(home):  # noqa: F811
    with TestClient(_app()) as client:
        created = client.post("/api/jobs", json={
            "name": "api insights", "prompt": "p",
            "context_runs": 4, "insight_notes": True, "insight_profile": True,
        })
        assert created.status_code == 200, created.text
        job = created.json()
        assert job["context_runs"] == 4
        assert job["insight_notes"] is True and job["insight_profile"] is True

        listed = client.get("/api/jobs").json()["jobs"]
        assert listed[0]["insight_notes"] is True and listed[0]["context_runs"] == 4

        patched = client.patch(f"/api/jobs/{job['id']}", json={"insight_notes": False})
        assert patched.status_code == 200
        assert patched.json()["insight_notes"] is False
        assert patched.json()["insight_profile"] is True  # untouched fields stay

        too_many = client.post("/api/jobs", json={"name": "x", "prompt": "p", "context_runs": 99})
        assert too_many.status_code == 422
        negative = client.patch(f"/api/jobs/{job['id']}", json={"context_runs": -1})
        assert negative.status_code == 422
