"""v0.7.3: chat and job reliability fixes found on a real install.

Each test reproduces a failure seen in a user's log:
* Cohere (OpenRouter) rejected messages with empty content;
* that 400 was mistaken for "server cannot do tools" and tools were switched off;
* follow-up tool calls were discarded, so multi-step jobs never finished;
* real errors (403 model refused, 429 rate limit) were reported as "empty answer";
* the response cache ignored the model and system prompt;
* LM Studio URLs without /v1 failed; failed job runs were marked "ok".
"""

import asyncio
import subprocess  # noqa: F401  (kept for parity with other suites)

import pytest

from myclaw import provider as prov
from myclaw.llm_errors import cooldown_for, describe_llm_error


class _Err(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


def test_no_message_with_empty_content_reaches_the_provider():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},                       # empty reply in history
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
                                                             "function": {"name": "browse", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": ""},
        {"role": "user", "content": "  "},
    ]
    out = prov._fill_empty_content(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["content"] == "(calling browse)" and out[3]["content"] == "(no output)"
    assert all((m.get("content") or "").strip() for m in out)


def test_message_errors_are_not_mistaken_for_missing_tool_support():
    cohere = _Err(400, "Error code: 400 - {'message': 'invalid request: invalid message provided at index 13: "
                       "must have non-empty content', 'provider_name': 'Cohere', 'role': 'tool'}")
    assert prov.OpenAICompatProvider._is_tools_rejection(cohere) is False
    assert prov.OpenAICompatProvider._is_tools_rejection(_Err(429, "tool rate limited")) is False
    assert prov.OpenAICompatProvider._is_tools_rejection(
        _Err(500, "tools param requires --jinja flag")) is True
    assert prov.OpenAICompatProvider._is_tools_rejection(
        _Err(400, "This model does not support tools")) is True


def test_errors_are_explained_and_cooled_down():
    refused = "Error code: 403 - thinkingmachines/inkling:free is only available on agentic harnesses."
    assert "cannot be used by ZenSynora" in describe_llm_error(refused)
    assert cooldown_for(refused) >= 600
    assert "rate-limited" in describe_llm_error("Error code: 429 - temporarily rate-limited upstream")
    assert cooldown_for("429 Too Many Requests") == 60
    assert "/v1" in describe_llm_error("Unexpected endpoint or method. (POST /chat/completions)")
    assert cooldown_for("something odd") == 0


def test_lmstudio_url_gets_v1():
    assert prov._with_v1("http://192.168.8.102:1235") == "http://192.168.8.102:1235/v1"
    assert prov._with_v1("http://localhost:1234/v1/") == "http://localhost:1234/v1"
    assert prov._with_v1("http://host:8080/api/v2") == "http://host:8080/api/v2"


def test_cache_key_covers_model_and_system_prompt_and_skips_empty(tmp_path):
    from myclaw.semantic_cache import SemanticCache

    c = SemanticCache(cache_dir=tmp_path) if "cache_dir" in SemanticCache.__init__.__code__.co_varnames else SemanticCache()
    a = [{"role": "system", "content": "You are writer."}, {"role": "user", "content": "hi"}]
    b = [{"role": "system", "content": "You are coder."}, {"role": "user", "content": "hi"}]
    c.set(a, "m1", "hello from writer", None)
    assert c.get(a, "m1")[0] == "hello from writer"
    assert c.get(a, "m2") is None          # other model
    assert c.get(b, "m1") is None          # other agent (system prompt)
    c.set(b, "m1", "", None)               # empty answers are not cached
    assert c.get(b, "m1") is None


def test_rotation_skips_a_model_that_was_refused(monkeypatch, tmp_path):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    calls = []

    class P:
        def __init__(self, name):
            self.name = name

        async def chat(self, messages, model, stream=False):
            calls.append(model)
            if model == "refused":
                raise RuntimeError("Error code: 403 - only available on agentic harnesses")
            return "fine", None

    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n: P(n))
    monkeypatch.setattr(agent_mod, "_MODEL_COOLDOWN", {})
    a = agent_mod.Agent(cfg, name="default")
    cands = [("openrouter", "refused"), ("openrouter", "ok")]
    assert asyncio.run(a._rotating_chat(cands, [{"role": "user", "content": "x"}], False))[0] == "fine"
    assert asyncio.run(a._rotating_chat(cands, [{"role": "user", "content": "x"}], False))[0] == "fine"
    assert calls == ["refused", "ok", "ok"]  # second request skipped the refused model


def _fake_agent(replies, seen):
    class FakeAgent:
        model = "m"
        name = "default"
        _kb_auto_extract = False
        _current_task_id = None
        _task_timer = None

        async def _provider_chat(self, msgs, model):
            seen.append(msgs)
            return next(replies)

        def _is_empty_response(self, t):
            return not (t or "").strip()

        def _detect_browse_failure(self, *a, **k):
            return False

        def _browse_alternative_hint(self, *a, **k):
            return ""

        def _record_kb_gap(self, *a, **k):
            pass

    return FakeAgent()


class _Mem:
    async def add(self, *a):
        pass


def test_follow_up_tool_calls_run_so_multi_step_tasks_finish(monkeypatch):
    """Search, then read: the second (native) tool call used to be discarded."""
    from myclaw.agent_internals import tool_executor
    from myclaw.tools import TOOLS

    ran = []
    monkeypatch.setitem(TOOLS, "step_one", {"func": lambda: ran.append("one") or "found note-42", "desc": ""})
    monkeypatch.setitem(TOOLS, "step_two", {"func": lambda note="": ran.append(note) or "note text", "desc": ""})
    second = [{"id": "c2", "type": "function", "function": {"name": "step_two", "arguments": {"note": "note-42"},
                                                            "arguments_str": '{"note": "note-42"}'}}]
    seen = []
    agent = _fake_agent(iter([("", second), ("Here is note-42: note text", None)]), seen)
    first = [{"id": "c1", "type": "function", "function": {"name": "step_one", "arguments": {}, "arguments_str": "{}"}}]
    out = asyncio.run(tool_executor.execute_tools(agent, first, [{"role": "user", "content": "x"}],
                                                  "x", "u", _Mem(), 0, True))
    assert ran == ["one", "note-42"] and out == "Here is note-42: note text"


def test_wrong_arguments_get_a_usage_hint(monkeypatch):
    from myclaw.agent_internals import tool_executor

    seen = []
    agent = _fake_agent(iter([("I will retry", None)]), seen)
    bad = [{"id": "c1", "type": "function", "function": {"name": "browse", "arguments": {}, "arguments_str": "{}"}}]
    asyncio.run(tool_executor.execute_tools(agent, bad, [{"role": "user", "content": "weather?"}],
                                            "weather?", "u", _Mem(), 0, True))
    tool_msgs = [m["content"] for m in seen[0] if m.get("role") == "tool"]
    assert "How to call browse" in tool_msgs[0] and "url (string, required)" in tool_msgs[0]


def test_failed_job_runs_are_marked_error(tmp_path, monkeypatch):
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    monkeypatch.setenv("HOME", str(tmp_path))

    class A:
        name, model, _provider_name = "default", "m", "p"

        async def think(self, prompt, user_id="default"):
            return "[Error] the model is rate-limited right now"

        async def _get_memory(self, u):
            class M:
                async def clear(self):
                    pass
            return M()

    job = store.create_job({"name": "w", "prompt": "weather"})
    run_id = asyncio.run(JobRunner(lambda: {"default": A()}).run_job(job["id"]))
    assert store.get_run(run_id)["status"] == "error"


def test_channels_can_be_renamed_and_the_topic_reaches_the_agents(tmp_path, monkeypatch):
    from myclaw.chat import store
    from myclaw.chat.engine import ChatEngine

    monkeypatch.setenv("HOME", str(tmp_path))
    ch = store.create_channel("research", ["default", "writer"], topic="", reply_mode="all")
    ch = store.update_channel(ch["id"], name="  blog  ", topic="Plan the weekly blog post")
    assert ch["name"] == "blog" and ch["topic"] == "Plan the weekly blog post"
    with pytest.raises(ValueError):
        store.update_channel(ch["id"], name="   ")
    row = next(c for c in store.list_channels() if c["id"] == ch["id"])
    assert row["message_count"] == 0
    prompt = ChatEngine(lambda: {})._prompt(ch, "writer", "draft it", {}, [])
    assert "Channel topic: Plan the weekly blog post" in prompt
