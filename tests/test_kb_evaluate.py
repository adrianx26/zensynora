"""2026-11-03: Evaluate notes, links, tags and embeddings.

Each note is checked by rules (always) and, in AI mode, by the default model.
A note with problems is *invalid*, with a reason and a proposed fix per
problem; fixes change nothing until applied (previous versions in .trash).
Batches pick notes never checked or changed since their last check first.
"""

from __future__ import annotations

import asyncio
import json
import time

from tests.test_kb_phase2_quality import _kdir, _trash, home  # noqa: F401  (fixture)


def _w(title, content="Some useful text about it.", tags=("ok",), links=()):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Relation

    return write_note(title, content=content, tags=list(tags),
                      relations=[Relation("related_to", t) for t in links])


def _run(base="default", count=10, mode="rules", config=None):
    from myclaw.config import AppConfig
    from myclaw.knowledge.evaluate import evaluate_batch

    return asyncio.run(evaluate_batch(config or AppConfig(), base, count, mode))


def test_rules_find_problems_with_fixes(home):  # noqa: F811
    from myclaw.knowledge.evaluate import results

    _w("Good", links=["Other"])
    _w("Other")
    _w("Empty", content="", tags=())
    _w("Selfie", links=["Selfie"])
    _w("Dangling", links=["Nowhere note"])
    _w("Messy tags", tags=("ok", "OK", "bad tag!"))
    r = _run(count=10)
    assert r["evaluated"] == 6 and r["invalid"] == 4
    res = results("default")
    assert res["counts"]["valid"] == 2 and res["counts"]["invalid"] == 4 and res["counts"]["unchecked"] == 0
    bad = {n["permalink"]: {(i["kind"], i["fix"]) for i in n["issues"]} for n in res["invalid"]}
    assert bad["empty"] == {("note", "trash_note")}
    assert bad["selfie"] == {("link", "remove_link")}
    assert bad["dangling"] == {("link", "remove_link")}
    assert bad["messy-tags"] == {("tag", "remove_tag"), ("tag", "rename_tag")}
    assert all(i["reason"] for n in res["invalid"] for i in n["issues"])


def test_batches_pick_unchecked_then_changed_first(home):  # noqa: F811
    from myclaw.knowledge import update_note

    for i in range(5):
        _w(f"N{i}")
    assert _run(count=2)["evaluated"] == 2
    assert _run(count=2)["evaluated"] == 2
    r = _run(count=100)
    assert r["evaluated"] == 1 and r["skipped_unchanged"] == 4   # only the last unchecked one
    time.sleep(0.01)
    update_note("n0", content="New text for N0.")
    r = _run(count=1)
    assert r["evaluated"] == 1 and r["notes"] == ["n0"]


def test_apply_fixes_then_note_is_valid(home):  # noqa: F811
    from myclaw.knowledge import read_note
    from myclaw.knowledge.evaluate import apply_fixes, results

    _w("Dangling", links=["Nowhere note"])
    _w("Messy tags", tags=("ok", "OK", "bad tag!"))
    _w("Empty", content="", tags=())
    _run()
    fixes = [dict(i, permalink=n["permalink"]) for n in results("default")["invalid"] for i in n["issues"]]
    out = apply_fixes("default", fixes)
    assert len(out["applied"]) == len(fixes) and not out["failed"]
    assert read_note("dangling").relations == []
    assert read_note("messy-tags").tags == ["ok", "bad-tag"]
    assert read_note("empty") is None and len(_trash(home)) >= 3
    res = results("default")
    assert res["counts"]["invalid"] == 0 and res["counts"]["valid"] == 2


def test_apply_rejects_unknown_fixes(home):  # noqa: F811
    from myclaw.knowledge.evaluate import apply_fixes

    _w("Good")
    out = apply_fixes("default", [{"permalink": "good", "fix": "rm_rf", "item": "x"},
                                  {"permalink": "good", "fix": "remove_tag", "item": "not-there"},
                                  {"permalink": "nope", "fix": "trash_note", "item": ""}])
    assert out["applied"] == [] and len(out["failed"]) == 3


def test_ai_mode_adds_model_issues_and_quotes_the_note(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge.evaluate import results

    _w("Python", content="Python is a snake.", tags=("programming", "ok"))
    seen = []

    async def fake(config, messages, provider=None, model=None):
        seen.append(messages)
        return json.dumps({"valid": False, "issues": [
            {"kind": "tag", "item": "programming", "reason": "The note is about the animal.", "fix": "remove_tag"},
            {"kind": "tag", "item": "invented", "reason": "not on the note", "fix": "remove_tag"},   # ignored
            {"kind": "note", "item": "", "reason": "Too short to be useful.", "fix": "none"},
        ]}), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)
    r = _run(mode="ai")
    assert r["evaluated"] == 1 and r["invalid"] == 1
    assert "information, not instructions" in seen[0][0]["content"] and "Python is a snake." in seen[0][1]["content"]
    issues = results("default")["invalid"][0]["issues"]
    assert {(i["kind"], i["item"], i["fix"], i["by"]) for i in issues} == {
        ("tag", "programming", "remove_tag", "ai"), ("note", "", "none", "ai")}


def test_ai_errors_stop_the_batch(home, monkeypatch):  # noqa: F811
    _w("A")
    _w("B")

    async def down(config, messages, provider=None, model=None):
        raise RuntimeError("Error code: 429 - rate limited")

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", down)
    r = _run(mode="ai")
    assert r["evaluated"] == 0 and "rate" in r["stopped"]


def test_idle_runner_only_when_idle_and_switched_on(home, monkeypatch):  # noqa: F811
    from myclaw import activity
    from myclaw.knowledge import evaluate

    _w("A")
    monkeypatch.setattr(evaluate, "_OPTIONS", dict(evaluate._OPTIONS, idle=False, idle_minutes=1, mode="rules", batch=5))
    assert asyncio.run(evaluate.idle_tick()) is None                   # switched off
    evaluate._OPTIONS["idle"] = True
    activity.turn_started()
    assert asyncio.run(evaluate.idle_tick()) is None                   # an agent is working
    activity.turn_ended()
    monkeypatch.setattr(activity, "_last", time.time() - 120)
    assert asyncio.run(evaluate.idle_tick())["evaluated"] == 1          # idle for 2 min >= 1 min


def test_api_start_status_apply(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import knowledge_api

    _w("Dangling", links=["Nowhere note"])
    app = FastAPI()
    app.include_router(knowledge_api.router)
    c = TestClient(app)
    assert c.post("/api/knowledge/evaluate", json={"count": 7}).status_code == 422   # only 1 / 10 / 100
    assert c.post("/api/knowledge/evaluate", json={"count": 10, "mode": "rules"}).status_code == 200
    for _ in range(100):
        s = c.get("/api/knowledge/evaluate/status").json()
        if not s["run"]["running"]:
            break
        time.sleep(0.05)
    assert s["run"]["last"]["evaluated"] == 1 and s["counts"]["invalid"] == 1
    issue = s["invalid"][0]["issues"][0]
    out = c.post("/api/knowledge/evaluate/apply", json={"fixes": [dict(issue, permalink="dangling")]}).json()
    assert len(out["applied"]) == 1
