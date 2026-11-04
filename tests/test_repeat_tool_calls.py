"""2026-11-04: an agent repeated the same job_create call 51 times per message (501
after the step limit was raised), creating 50 identical jobs and then hitting
"agents already created 50 jobs" on every call.

* The same tool with the same arguments that already gave the same result twice
  in one message is not run again; the model is told to use the earlier result.
* Two rounds in a row made only of such repeats end the tool loop: the agent
  answers with what it has (no more model calls).
* Polling (same call, changing result) and different arguments keep working.
* job_create does not make a second identical job; it names the existing one.
"""
import asyncio

import pytest

from tests.test_tool_step_limit import _Mem, _agent


def _call(i, args):
    import json

    return [{"id": f"c{i}", "type": "function",
             "function": {"name": "probe_tool", "arguments": dict(args), "arguments_str": json.dumps(args)}}]


@pytest.fixture
def cfg(monkeypatch):
    from myclaw.config import AppConfig

    c = AppConfig()
    c.agents.max_tool_rounds = 500
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: c)
    return c


def _run(replies, first, seen):
    from myclaw.agent_internals import tool_executor

    return asyncio.run(tool_executor.execute_tools(_agent(iter(replies), seen), first,
                                                   [{"role": "user", "content": "x"}], "x", "u", _Mem(), 0, True))


def test_identical_calls_with_the_same_result_stop(cfg, monkeypatch):
    from myclaw.tools import TOOLS

    ran = []
    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda name="": ran.append(1) or "Error: limit reached",
                                              "desc": "probe"})
    same = {"name": "Research n8n"}
    seen: list = []
    # the model would repeat the call forever (500 steps allowed)
    replies = [("", _call(i, same)) for i in range(1, 600)]
    out = _run(replies, _call(0, same), seen)
    assert len(ran) == 2                                   # run twice, then not again
    assert len(seen) <= 6                                  # a few model calls, not 500
    blocked = [m["content"] for msgs in seen for m in msgs if m.get("role") == "tool" and "not run again" in m["content"]]
    assert blocked and "same arguments" in blocked[0]
    assert "continue" in out or out                       # ends with an answer


def test_polling_with_changing_results_is_not_stopped(cfg, monkeypatch):
    from myclaw.tools import TOOLS

    states = iter(["running", "running 40 %", "running 80 %", "done"])
    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda job_id="": next(states), "desc": "probe"})
    same = {"job_id": "job-1"}
    replies = [("", _call(1, same)), ("", _call(2, same)), ("", _call(3, same)), ("The job is done.", None)]
    assert _run(replies, _call(0, same), []) == "The job is done."


def test_two_identical_results_are_still_allowed(cfg, monkeypatch):
    """One retry (e.g. a page that failed to load) is fine."""
    from myclaw.tools import TOOLS

    ran = []
    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda url="": ran.append(1) or "timeout", "desc": "probe"})
    same = {"url": "https://example.com"}
    assert _run([("", _call(1, same)), ("It does not load.", None)], _call(0, same), []) == "It does not load."
    assert len(ran) == 2


def test_job_create_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.jobs import store
    from myclaw.tools import jobs as jt

    monkeypatch.setattr(jt, "_allowed", lambda: "")
    monkeypatch.setattr(jt, "_agents", lambda: ["default"])
    monkeypatch.setattr(jt, "_runner", lambda: object())
    a = jt.job_create("Research n8n", "Research https://github.com/n8n-io/n8n")
    b = jt.job_create("Research n8n", "Research https://github.com/n8n-io/n8n ")
    assert a.startswith("Created job") and "already exists" in b
    assert len(store.list_jobs()) == 1
    c = jt.job_create("Research n8n", "Research another repository")
    assert c.startswith("Created job") and len(store.list_jobs()) == 2
