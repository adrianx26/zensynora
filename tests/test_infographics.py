"""2026-11-01: data behind the Knowledge and Doctor infographics.

Knowledge: growth by *note date* (not the re-index date), change over 7/30
days, new and growing groups, what needs attention. Doctor: errors, failures,
restarts and job runs per time bucket.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

from tests.test_v071_features import home  # noqa: F401  (fixture)


def _note(name, tags=("t",), facts=(), links=(), days_ago=0.0, home_dir=None):
    from myclaw.knowledge.parser import Observation, Relation
    from myclaw.knowledge.storage import write_note

    permalink = write_note(name, observations=[Observation(category="fact", content=f) for f in facts],
                      relations=[Relation(relation_type="related_to", target=t) for t in links], tags=list(tags))
    if days_ago:
        from pathlib import Path

        path = next((Path.home() / ".myclaw" / "knowledge" / "default").rglob(f"{permalink}.md"))
        ts = time.time() - days_ago * 86_400
        os.utime(path, (ts, ts))
    return permalink


def test_knowledge_overview(home):  # noqa: F811
    from myclaw.knowledge import benchmarks as bm
    from myclaw.knowledge.overview import overview
    from myclaw.knowledge.sync import sync_knowledge

    # an old group of three, a new group of two, two unlinked notes (one without tags or facts)
    _note("Old A", facts=["a"], links=["Old B"], days_ago=40)
    _note("Old B", facts=["b"], links=["Old C"], days_ago=40)
    _note("Old C", facts=["c"], days_ago=20)
    _note("New X", facts=["x"], links=["New Y"], days_ago=1)
    _note("New Y", facts=["y"], days_ago=1)
    _note("Lonely", facts=["l"], days_ago=3)
    _note("Bare", tags=(), days_ago=2)
    _note("Linker", facts=["k"], links=["Old A", "Ghost note"], days_ago=1)
    sync_knowledge("default", force=True)  # every index date is now "today"
    bm.clear_cache()

    o = overview("default")
    assert o["totals"]["notes"] == 8
    # growth uses the note's file date, not the index date
    assert o["change"]["7d"]["notes"] == 5 and o["change"]["30d"]["notes"] == 6
    assert o["growth"][-1]["total_notes"] == 8 and o["growth"][0]["day"] <= (
        datetime.now() - timedelta(days=39)).strftime("%Y-%m-%d")
    days = {g["day"]: g for g in o["growth"]}
    assert sum(g["notes"] for g in days.values()) == 8

    g = o["groups"]
    assert g["count"] == 2 and g["largest"] == 4  # Old A-B-C + Linker
    assert g["new_count"] == 1 and g["new"][0]["size"] == 2
    assert g["growing_count"] == 1 and g["growing"][0]["new_notes"] == 1

    att = {a["id"]: a for a in o["attention"]}
    assert att["unlinked"]["count"] == 2 and {e["title"] for e in att["unlinked"]["examples"]} == {"Lonely", "Bare"}
    assert att["missing"]["count"] == 1 and att["missing"]["examples"][0]["title"].lower().startswith("ghost")
    assert att["no_facts"]["count"] == 1 and att["no_tags"]["count"] == 1
    assert att["stale"]["count"] == 0 and 0 <= att["unlinked"]["share"] <= 1
    assert set(o["usage"]) >= {"searches", "hits", "misses"} and o["cache"]["active"] in (True, False)


def test_knowledge_overview_empty_base(home):  # noqa: F811
    from myclaw.knowledge.overview import overview

    o = overview("default")
    assert o["totals"]["notes"] == 0 and o["growth"] == [] and o["groups"]["count"] == 0


def _log(home, records):  # noqa: F811
    lf = home / ".myclaw" / "logs" / "zensynora.log"
    lf.parent.mkdir(parents=True, exist_ok=True)
    lf.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _rec(level, message, ago):
    ts = (datetime.now(UTC) - timedelta(seconds=ago)).isoformat()
    return {"timestamp": ts, "level": level, "message": message, "module": "m"}


def test_doctor_timeline(home):  # noqa: F811
    from myclaw.doctor import report
    from myclaw.jobs import store

    _log(home, [
        _rec("INFO", "ZenSynora v0.8.0 initialised (log_level=INFO, cmd=webui)", 3 * 3600),
        _rec("INFO", "ZenSynora v0.8.0 initialised (log_level=INFO, cmd=doctor)", 3 * 3600),  # CLI: not counted
        _rec("ERROR", "Tool execution error (x): boom", 2 * 3600),
        _rec("WARNING", "Rate limit exceeded for browse", 2 * 3600),
        _rec("WARNING", "Config reloaded", 600),
        _rec("CRITICAL", "Task FAILED - exceeded 330s timeout", 300),
        _rec("ERROR", "too old", 30 * 3600),
    ])
    job = store.create_job({"name": "j", "prompt": "p", "agent": "default"})
    store.finish_run(store.start_run(job["id"]), "ok", output="fine")
    store.finish_run(store.start_run(job["id"]), "timeout", error="slow")

    t = report.timeline(24)
    assert t["bucket_seconds"] == 1800 and 48 <= len(t["buckets"]) <= 50
    assert t["totals"] == {"WARNING": 2, "ERROR": 1, "CRITICAL": 1, "failures": 3, "restarts": 1}
    assert t["jobs"] == {"ok": 1, "failed": 1, "other": 0}
    assert sum(b["jobs_ok"] for b in t["buckets"]) == 1 and sum(b["jobs_failed"] for b in t["buckets"]) == 1
    assert all(b["label"] for b in t["buckets"])
    assert t["top"][0]["count"] >= 1 and {x["level"] for x in t["top"]} <= {"WARNING", "ERROR", "CRITICAL"}
    assert report.timeline(2)["bucket_seconds"] == 300
    assert report.timeline(168)["bucket_seconds"] == 6 * 3600


def test_api_routes(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import doctor_api, knowledge_usage_api, settings_api

    app = FastAPI()
    app.include_router(doctor_api.router)
    app.include_router(knowledge_usage_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        t = c.get("/api/doctor/timeline?hours=8").json()
        assert t["hours"] == 8 and t["bucket_seconds"] == 900 and "jobs" in t
        o = c.get("/api/knowledge/overview").json()
        assert o["base"] == "default" and "attention" in o and "growth" in o
