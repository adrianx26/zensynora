"""2026-11-06: jobs stopped in the middle with "I needed more tool steps than this job allows"
after about 4 of their 8–30 steps (mindy01: every job run since 2026-11-02).

Cause: the semantic cache found answers by similarity to the LAST USER MESSAGE only. In
the tool loop that message never changes (tool results are added after it), so every
follow-up call got the first call's cached answer back in ~1 ms ("call the same tool
again"): 445 of 848 model answers were such cache hits. The repeat guard then ended the
loop, and the job reported it as the step limit, so the medic kept raising "max tool steps".

* Follow-up calls in the tool loop use exact cache matches only.
* A run ended by the repeat guard says so; it is not reported as the step limit.
"""
import asyncio
import importlib.util

import pytest

from tests.test_repeat_tool_calls import _call, _run, cfg  # noqa: F401  (fixture)


@pytest.fixture
def fake_embedder(monkeypatch):
    from myclaw.knowledge import semantic

    semantic.set_embedder_factory(lambda model: semantic.HashEmbedder(model=model))
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "sentence_transformers" else real_find(name, *a))
    yield semantic
    semantic.set_embedder_factory(None)


def test_follow_up_calls_do_not_get_the_first_answer_back(tmp_path, fake_embedder):
    import time

    from myclaw import semantic_cache as sc

    cache = sc.SemanticCache(cache_dir=tmp_path, similarity_threshold=0.8)
    for _ in range(50):  # the embedding model loads in the background
        if cache._get_embedding("warm up") is not None:
            break
        time.sleep(0.05)
    task = [{"role": "user", "content": "Research today's trending AI repositories"}]
    first = [{"id": "c0", "type": "function", "function": {"name": "web_research", "arguments": {}}}]
    cache.set(task, "m", "", tool_calls=first)
    followup = task + [{"role": "assistant", "content": "", "tool_calls": first},
                       {"role": "tool", "tool_call_id": "c0", "content": "capabilities: …"}]
    assert cache.get(followup, "m") is not None              # the bug: same last user message
    with sc.exact_only():
        assert cache.get(followup, "m") is None              # follow-ups: no similarity match
        assert cache.get(task, "m")[1] == first              # exact matches still work
    assert not sc.EXACT_ONLY.get()


def test_the_tool_loop_asks_for_exact_matches_only(cfg, monkeypatch):  # noqa: F811
    from myclaw import semantic_cache as sc
    from myclaw.tools import TOOLS

    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda n=0: f"r{n}", "desc": "probe"})
    flags: list = []

    class Seen(list):
        def append(self, msgs):
            flags.append(sc.EXACT_ONLY.get())
            super().append(msgs)

    out = _run([("", _call(1, {"n": 1})), ("done", None)], _call(0, {"n": 0}), Seen())
    assert out == "done" and flags == [True, True] and not sc.EXACT_ONLY.get()


def test_a_run_stopped_for_repeats_is_not_reported_as_the_step_limit(cfg, monkeypatch):  # noqa: F811
    from myclaw.agent import is_failed_answer
    from myclaw.agent_internals import tool_executor as te
    from myclaw.jobs.medic import rule_diagnosis
    from myclaw.tools import TOOLS

    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda n=0: "same", "desc": "probe"})
    same = {"n": 1}
    token = te.MAX_ROUNDS.set(30)                            # a job with 30 steps
    try:
        out = _run([("", _call(i, same)) for i in range(1, 40)] + [("partial answer", None)],
                   _call(0, same), [])
    finally:
        te.MAX_ROUNDS.reset(token)
    assert out.startswith(te.REPEAT_STOP_MARK) and te.TOOL_LIMIT_MARK not in out
    assert is_failed_answer(out)                             # the run still counts as failed
    d = rule_diagnosis({"max_steps": 30}, {"status": "error", "output": out})
    assert d and "max_steps" not in d["job_changes"] and d["category"] == "tools"
