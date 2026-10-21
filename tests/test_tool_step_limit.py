"""'Review all tools one by one' stopped after 5 calls (2026-10-21).

* chat allowed only 4 follow-up tool rounds, then threw the results away and
  answered "I needed more tool steps…";
* get_tool_documentation returned nothing for built-in tools;
* list_tools only listed names.
"""
import asyncio

import pytest


class _Mem:
    async def add(self, *a):
        pass


def _agent(replies, seen):
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


def _call(i):
    return [{"id": f"c{i}", "type": "function",
             "function": {"name": "probe_tool", "arguments": {}, "arguments_str": "{}"}}]


@pytest.fixture
def probe(monkeypatch):
    from myclaw.tools import TOOLS

    ran = []
    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda: ran.append(1) or "result", "desc": "probe"})
    return ran


def test_chat_default_allows_more_rounds(probe, monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.config import AppConfig

    assert AppConfig().agents.max_tool_rounds == 12
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: AppConfig())
    # 8 follow-up tool rounds, then a normal answer: fits in the default 12.
    replies = iter([("", _call(i)) for i in range(8)] + [("All 9 tools reviewed.", None)])
    out = asyncio.run(tool_executor.execute_tools(_agent(replies, []), _call(0), [{"role": "user", "content": "x"}],
                                                  "x", "u", _Mem(), 0, True))
    assert out == "All 9 tools reviewed." and len(probe) == 9


def test_at_the_limit_the_agent_answers_with_what_it_found(probe, monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.config import AppConfig

    cfg = AppConfig()
    cfg.agents.max_tool_rounds = 2
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    seen = []
    replies = iter([("", _call(1)), ("", _call(2)), ("", _call(3)),
                    ("Reviewed 3 so far: probe_tool works. Say continue for the rest.", None)])
    out = asyncio.run(tool_executor.execute_tools(_agent(replies, seen), _call(0), [{"role": "user", "content": "x"}],
                                                  "x", "u", _Mem(), 0, True))
    assert out.startswith("Reviewed 3 so far")
    assert "Do NOT call any more tools" in seen[-1][-1]["content"]
    assert seen[-1][-2]["role"] == "tool"  # the results are still in the conversation


def test_wrap_up_falls_back_when_the_model_keeps_calling_tools(probe, monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.config import AppConfig

    cfg = AppConfig()
    cfg.agents.max_tool_rounds = 1
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    replies = iter([("", _call(1)), ("", _call(2)), ('{"tool": "probe_tool", "args": {}}', None)])
    out = asyncio.run(tool_executor.execute_tools(_agent(replies, []), _call(0), [{"role": "user", "content": "x"}],
                                                  "x", "u", _Mem(), 0, True))
    assert "I used all 2 tool steps" in out and "probe_tool" in out and "continue" in out


def test_jobs_keep_their_limit_and_failure_marker(probe):
    from myclaw.agent_internals import tool_executor

    token = tool_executor.MAX_ROUNDS.set(1)
    try:
        replies = iter([("", _call(1)), ("", _call(2)), ("Partial answer.", None)])
        out = asyncio.run(tool_executor.execute_tools(_agent(replies, []), _call(0), [{"role": "user", "content": "x"}],
                                                      "x", "u", _Mem(), 0, True))
    finally:
        tool_executor.MAX_ROUNDS.reset(token)
    # the job runner and the medic still recognise it
    assert out.startswith("I needed more tool steps") and "Partial answer." in out


def test_list_tools_details_and_builtin_docs():
    from myclaw.tools import TOOLS
    from myclaw.tools.toolbox import get_tool_documentation, list_tools

    short = list_tools()
    assert f"({len(TOOLS)})" in short and "details=True" in short
    full = list_tools(details=True)
    assert full.count("\n- ") == len(TOOLS)
    assert "- read_file(path" in full and " — " in full
    doc = get_tool_documentation("read_file")
    assert doc.startswith("# read_file") and "Arguments:" in doc and "path" in doc
    lazy = get_tool_documentation("check_system_health")  # a lazily loaded tool
    assert "health" in lazy.lower()
    assert "list_tools(details=True)" in get_tool_documentation("no_such_tool")
