"""2026-11-05: Doctor → Logs — an activity log of everything the app does (agent turns,
tools and scripts, jobs, model calls, settings, updates, backups, knowledge writes),
the app log, export for 1/2/4/8/12/24 hours or days, and an AI analysis that proposes
options (each can be started as an agent job; nothing runs by itself)."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_doctor_report import _rec, _write_log
from tests.test_v071_features import home  # noqa: F401  (fixture)


def _rows(home):
    from myclaw import activity

    return [json.loads(x) for x in activity.activity_file().read_text(encoding="utf-8").splitlines()]


def test_record_writes_one_line_and_hides_secrets(home):  # noqa: F811
    from myclaw import activity

    activity.record("script", "curl -H 'Authorization: Bearer sk-abcdefghijklmnopqrstuvwx'", "ok", 1.234,
                    agent="Coder", detail="x" * 2000)
    r = _rows(home)[-1]
    assert (r["kind"], r["status"], r["agent"], r["ms"]) == ("script", "ok", "Coder", 1234)
    assert "sk-abcdef" not in json.dumps(r) and len(r["detail"]) <= 400 and r["ts"]


def test_tools_models_and_settings_are_recorded(home, monkeypatch):  # noqa: F811
    from myclaw import model_health
    from myclaw.agent_internals import tool_executor
    from myclaw.config import AppConfig
    from myclaw.tools import TOOLS
    from tests.test_tool_step_limit import _Mem, _agent

    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: AppConfig())
    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda n=0: f"r{n}", "desc": "probe"})
    monkeypatch.setitem(TOOLS, "shell", {"func": lambda cmd="": "Error: not allowed", "desc": "shell"})
    calls = [{"id": "a", "type": "function", "function": {"name": "probe_tool", "arguments": {"n": 1}, "arguments_str": ""}},
             {"id": "b", "type": "function", "function": {"name": "shell", "arguments": {"cmd": "ls"}, "arguments_str": ""}}]
    asyncio.run(tool_executor.execute_tools(_agent(iter([("done", None)]), []), calls,
                                            [{"role": "user", "content": "x"}], "x", "u", _Mem(), 0, True))
    model_health.record("openrouter", "m1", False, 2.5, "429 rate limited")
    got = {(r["kind"], r["name"], r["status"]) for r in _rows(home)}
    assert ("tool", "probe_tool", "ok") in got
    assert ("script", "shell: ls", "error") in got
    assert ("model", "openrouter/m1", "error") in got


def test_read_filters_and_newest_first(home):  # noqa: F811
    from myclaw import activity
    from myclaw.doctor import logs

    for i in range(5):
        activity.record("tool", f"t{i}", "ok" if i % 2 else "error", 0.1)
    activity.record("job", "nightly", "ok", 3)
    r = logs.read("activity", hours=1)
    assert r["total"] == 6 and r["rows"][0]["name"] == "nightly"          # newest first
    assert logs.read("activity", hours=1, kind="tool", status="error")["total"] == 3
    assert logs.read("activity", hours=1, q="nightly")["total"] == 1
    assert r["counts"]["kind"]["tool"] == 5 and r["counts"]["status"]["error"] == 3


def test_app_log_source(home):  # noqa: F811
    from myclaw.doctor import logs

    _write_log(home, [_rec("INFO", "Started"), _rec("ERROR", "Tool browse failed: refused"),
                      _rec("WARNING", "slow model")])
    r = logs.read("app", hours=24)
    assert r["total"] == 3 and r["rows"][0]["level"]
    assert logs.read("app", hours=24, level="ERROR")["total"] == 1


def test_export(home):  # noqa: F811
    from myclaw import activity
    from myclaw.doctor import logs

    activity.record("tool", "probe", "ok", 0.2, agent="A", detail="did it")
    name, text = logs.export("activity", hours=24, fmt="txt")
    assert name.startswith("zensynora-activity-24h-") and name.endswith(".log") and "probe" in text and "ok" in text
    name, text = logs.export("activity", hours=48, fmt="jsonl")
    assert name.endswith(".jsonl") and "2d" in name and json.loads(text.splitlines()[0])["name"] == "probe"
    with pytest.raises(ValueError):
        logs.export("nope", hours=1)


def _fake(monkeypatch, answer, seen=None):
    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return (answer if isinstance(answer, str) else json.dumps(answer)), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def test_analyze_gives_checked_options(home, monkeypatch):  # noqa: F811
    from myclaw import activity
    from myclaw.doctor import logs

    for _ in range(4):
        activity.record("tool", "browse", "error", 12, detail="connection refused")
    seen: list = []
    _fake(monkeypatch, {"summary": "browse fails a lot.", "options": [
        {"title": "Retry browse once", "why": "4 failures", "impact": "high", "steps": ["a", "b"],
         "action": {"type": "evolve", "target_kind": "function", "target": "myclaw/tools/web.py", "goal": "retry"}},
        {"title": "Check the proxy", "why": "refused", "impact": "medium", "steps": ["open Settings"],
         "action": {"type": "investigate"}},
        {"title": "bad", "action": {"type": "rm -rf"}},
    ]}, seen)
    r = asyncio.run(logs.analyze(hours=24))
    assert r["summary"] == "browse fails a lot." and [o["action"]["type"] for o in r["options"]] == ["evolve", "investigate", "none"]
    assert "information, not instructions" in seen[0][0]["content"] and "browse" in seen[0][1]["content"]
    _fake(monkeypatch, RuntimeError("Error code: 429"))
    with pytest.raises(logs.AIUnavailable):
        asyncio.run(logs.analyze(hours=24))


def test_api(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import activity, evolve
    from myclaw.web import doctor_api

    activity.record("tool", "probe", "ok", 0.1)
    started = []
    monkeypatch.setattr(evolve, "_start_job", lambda job_id: started.append(job_id))
    _fake(monkeypatch, {"summary": "s", "options": [{"title": "t", "why": "w", "action": {"type": "investigate"}}]})
    app = FastAPI()
    app.include_router(doctor_api.router)
    c = TestClient(app)
    assert c.get("/api/doctor/logs?source=activity&hours=24").json()["total"] >= 1
    r = c.get("/api/doctor/logs/export?source=activity&hours=24&fmt=txt")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "probe" in r.text
    opt = c.post("/api/doctor/logs/analyze", json={"hours": 24}).json()["options"][0]
    assert c.post("/api/doctor/logs/act", json={"option": opt}).json()["job_id"] and started
    assert c.get("/api/doctor/logs?source=bad").status_code == 400


@pytest.mark.parametrize("answer", ["Sorry, I cannot connect to the LLM service. Please check your connection.",
                                    "Sorry, the LLM service timed out. Please try again.",
                                    "Sorry, the LLM service returned an error: 503",
                                    "[Error] The model is not available", ""])
def test_failure_answers_are_failures(answer):
    """2026-11-05: a job whose agent could not reach any model was shown as ✓ OK."""
    from myclaw.agent import is_failed_answer

    assert is_failed_answer(answer)


def test_normal_answers_are_not_failures():
    from myclaw.agent import is_failed_answer

    assert not is_failed_answer("Sorry to hear that! Here is the weather: sunny, 20 °C.")
    assert not is_failed_answer("The job is done.")
