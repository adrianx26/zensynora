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


# ── 2026-11-08: a context set per provider, kept and adapted across the routing ─────────────
def _cfg(monkeypatch, **ctx):
    from myclaw.config import AppConfig

    cfg = AppConfig()
    for name, value in ctx.items():
        getattr(cfg.providers, name).context_length = value
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    return cfg


def test_the_context_follows_each_model_of_the_rotation(monkeypatch):
    from myclaw import context_budget as cb

    _cfg(monkeypatch, lmstudio=8192, freetoken=32768)
    token = cb.CONTEXT_LENGTH.set(16384)                       # the agent's own context length
    try:
        assert cb.limit_for("lmstudio", "qwen") == 8192          # the smaller one wins
        assert cb.limit_for("freetoken", "glm") == 16384         # the agent's setting is kept
        assert cb.limit_for("openrouter", "x") == 16384
        assert cb.explicit_context("ollama") == 16384            # Ollama's num_ctx
    finally:
        cb.CONTEXT_LENGTH.reset(token)
    assert cb.limit_for("freetoken", "glm") == 32768             # no agent setting: the provider's
    assert cb.limit_for("openrouter", "x") == 0                  # nothing known: as it is
    cb._MAX_CACHE[("lmstudio", "big", None)] = (9e18, 4096, "provider")   # what the server reported
    try:
        assert cb.limit_for("lmstudio", "big") == 4096
    finally:
        cb._MAX_CACHE.pop(("lmstudio", "big", None), None)


def test_ollama_gets_num_ctx_of_the_model_it_runs(monkeypatch):
    """The agent's call to a routed Ollama model sends that provider's context, not another's."""
    from myclaw import context_budget as cb

    _cfg(monkeypatch, ollama=12288)
    seen = []

    async def send(m):
        seen.append(cb.CONTEXT_LENGTH.get())
        return "ok"

    from myclaw.agent import Agent

    a = Agent.__new__(Agent)
    a.name = "x"
    asyncio.run(a._fitted("ollama", "qwen3", [{"role": "user", "content": "hi"}], send))
    asyncio.run(a._fitted("lmstudio", "qwen", [{"role": "user", "content": "hi"}], send))
    assert seen == [12288, 0] and cb.CONTEXT_LENGTH.get() == 0
