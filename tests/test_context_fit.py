"""2026-11-08: "request (14407 tokens) exceeds the available context size (8192 tokens)" (mindy01).

LM Studio models loaded with an 8192-token context got a 14,407-token request: the 128 tool
definitions alone are ~9,000 tokens. The error was not recognised (not a context overflow, not a
model-server error), LM Studio's *loaded* context was never read (only its maximum), nothing was
trimmed unless the agent had a context length set, and the failure was logged with a traceback.

Now: the limit is read from the error (and LM Studio's loaded_context_length), remembered, and the
same model is asked again with a request that fits — the oldest messages left out and, when the
tool list alone is too big, shorter tool descriptions with the most useful tools first.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

LMSTUDIO_ERR = ('Engine protocol predict request returned 400: {"error":{"code":400,"message":"request '
                '(14407 tokens) exceeds the available context size (8192 tokens), try increasing it",'
                '"type":"exceed_context_size_error","n_prompt_tokens":14407,"n_ctx":8192}}')


@pytest.fixture(autouse=True)
def _clean():
    from myclaw import context_budget as cb

    cb._LEARNED.clear()
    yield
    cb._LEARNED.clear()


@pytest.mark.parametrize("text,limit", [
    (LMSTUDIO_ERR, 8192),
    ("This model's maximum context length is 4097 tokens. However, your messages resulted in 5000 tokens.", 4097),
    ('{"error": {"message": "the request exceeds the available context size", "n_ctx": 4096}}', 4096),
    ("prompt is too long: 210000 tokens > 200000 maximum", 200000),
])
def test_the_limit_is_read_from_the_error(text, limit):
    from myclaw import context_budget as cb
    from myclaw.llm_errors import describe_llm_error, is_context_overflow, is_provider_error

    assert is_context_overflow(text) and is_provider_error(text)
    assert cb.learn_from_error("lmstudio", "m", RuntimeError(text)) == limit
    assert cb.limit_for("lmstudio", "m") == limit
    assert "context" in describe_llm_error(text).lower()


def test_lm_studio_loaded_context_wins_over_its_maximum():
    from myclaw.context_budget import _find_ctx

    assert _find_ctx({"id": "qwen", "max_context_length": 131072, "loaded_context_length": 8192}) == 8192
    assert _find_ctx({"id": "qwen", "max_context_length": 131072}) == 131072


def test_a_small_context_gets_short_tool_descriptions_most_useful_first():
    from myclaw import context_budget as cb
    from myclaw.provider import _get_tool_schemas

    full = _get_tool_schemas()
    full_tokens = cb.estimate_tokens(json.dumps(full, separators=(",", ":")))
    assert full_tokens > 8000                                   # why 8192 could never fit
    token = cb.TOOL_BUDGET.set(2000)
    try:
        small = _get_tool_schemas()
    finally:
        cb.TOOL_BUDGET.reset(token)
    names = [s["function"]["name"] for s in small]
    assert cb.estimate_tokens(json.dumps(small, separators=(",", ":"))) <= 2000
    assert names[:3] == [n for n in cb.ESSENTIAL_TOOLS if n in {s["function"]["name"] for s in full}][:3]
    assert len(_get_tool_schemas()) == len(full)                # unchanged outside the fitted call
    token = cb.TOOL_BUDGET.set(-1)
    try:
        assert _get_tool_schemas() == []
    finally:
        cb.TOOL_BUDGET.reset(token)


class _LMStudio:
    """A server loaded with an 8192-token context, counting what it gets."""

    def __init__(self):
        self.sizes: list[int] = []

    async def chat(self, messages, model, stream=False):
        from myclaw import context_budget as cb
        from myclaw.provider import _get_tool_schemas

        tools = cb.estimate_tokens(json.dumps(_get_tool_schemas(), separators=(",", ":")))
        size = tools + sum(cb.message_tokens(m) for m in messages)
        self.sizes.append(size)
        if size > 8192:
            raise RuntimeError(LMSTUDIO_ERR.replace("14407", str(size)))
        return "answer", None


def _agent(prov):
    from myclaw.agent import Agent

    a = Agent.__new__(Agent)
    a.name, a.model, a._provider_name = "Mindy", "qwen", "lmstudio"
    a.config = None
    a._get_provider_for = lambda name, key=None: prov
    a.last_model_used = None
    return a


def _messages():
    history = []
    for i in range(30):
        history += [{"role": "user", "content": f"question {i} " + "x" * 400},
                    {"role": "assistant", "content": f"answer {i} " + "y" * 400}]
    return [{"role": "system", "content": "You are Mindy. " + "z" * 6000}] + history + [
        {"role": "user", "content": "What is next?"}]


def test_the_same_model_is_asked_again_with_a_request_that_fits():
    prov = _LMStudio()
    a = _agent(prov)
    out = asyncio.run(a._chat_once("lmstudio", "qwen", None, _messages(), False, []))
    assert out == ("answer", None)
    assert prov.sizes[0] > 8192 and prov.sizes[1] <= 8192 and len(prov.sizes) == 2
    prov.sizes.clear()                                          # next turn: fitted at once
    asyncio.run(a._chat_once("lmstudio", "qwen", None, _messages(), False, []))
    assert len(prov.sizes) == 1 and prov.sizes[0] <= 8192


def test_a_failed_turn_is_a_warning_not_a_crash(caplog, monkeypatch):
    from myclaw.chat import engine

    caplog.set_level(logging.WARNING)
    engine.log_agent_failure("Mindy", "dm-mindy", RuntimeError("All models in the rotation failed — " + LMSTUDIO_ERR))
    rec = [r for r in caplog.records if "Mindy" in r.getMessage()][0]
    assert rec.levelno == logging.WARNING and rec.exc_info is None
    caplog.clear()
    engine.log_agent_failure("Mindy", "dm-mindy", KeyError("bug"))   # a real bug keeps its traceback
    assert caplog.records[0].levelno == logging.ERROR and caplog.records[0].exc_info


def test_a_daily_limit_is_skipped_for_an_hour_not_a_minute():
    from myclaw.llm_errors import cooldown_for, describe_llm_error, is_rate_limit

    err = ("Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day-high-balance. ', "
           "'code': 429}}")
    assert cooldown_for(err) == 3600 and "daily limit" in describe_llm_error(err)
    assert not is_rate_limit(err)                               # no quick retry for a daily limit
    assert cooldown_for("Error code: 429 - Too Many Requests") == 60 and is_rate_limit("429 Too Many Requests")
