"""Fixes for the diagnostics report of 2026-10-29 (mindy01)."""
import asyncio
import unittest.mock

import pytest

from tests.test_agent import create_mock_agent


# ── 1. Task timers leaked when two runs of one agent overlapped ─────────────
# Report: two "Task … FAILED - exceeded 330s timeout" CRITICALs, both started
# 08:00:04 when three jobs started at once; steps_completed 0. The agent kept
# the running task's id in one attribute, so the second run overwrote the
# first's id and the first timer was never completed.
def test_overlapping_runs_of_one_agent_complete_their_own_timers(tmp_path):
    agent = create_mock_agent(tmp_path)
    started = asyncio.Event()

    async def slow_chat(*a, **k):
        started.set()
        await asyncio.sleep(0.2)
        return ("ok", None)

    agent._provider.chat = slow_chat

    async def run():
        with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
            r = await asyncio.gather(agent.think("first", "u1"), agent.think("second", "u2"))
        timer = agent._task_timer
        active = [t for t, s in timer._task_status.items() if timer.is_task_active(t)]
        return r, active

    replies, active = asyncio.run(run())
    assert all(replies)
    assert active == [], f"timers still running after both runs finished: {active}"


# ── 2. Extra arguments the tool does not take ───────────────────────────────
# Report: "shell() got an unexpected keyword argument 'description'" and 15×
# "agent_reach_web_research() got an unexpected keyword argument 'url' /
# 'platform' / 'tool' …" logged as ERROR; each failed call cost a tool step
# until the jobs ran out of steps.
def _run_tool_calls(tmp_path, calls, monkeypatch, caplog):
    import logging

    from myclaw.agent_internals.tool_executor import execute_tools

    agent = create_mock_agent(tmp_path)

    class Mem:
        def __init__(self):
            self.items = []

        async def add(self, role, content):
            self.items.append((role, content))

    mem = Mem()

    async def go():
        with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
            return await execute_tools(agent, calls, [], "msg", "u1", mem, 0, False)

    with caplog.at_level(logging.INFO):
        try:
            asyncio.run(go())
        except Exception:
            pass
    return [c for r, c in mem.items if r == "tool"]


def test_unknown_arguments_are_ignored_with_a_note(tmp_path, monkeypatch, caplog):
    from myclaw.tools import TOOLS

    seen = {}

    def no_args_tool():
        seen["called"] = True
        return "description of the tool"

    def shell_like(cmd: str, user_id: str = "default"):
        seen["cmd"] = cmd
        return "listing"

    monkeypatch.setitem(TOOLS, "no_args_tool", {"func": no_args_tool, "desc": "t"})
    monkeypatch.setitem(TOOLS, "shell_like", {"func": shell_like, "desc": "t"})
    out = _run_tool_calls(tmp_path, [
        {"id": "1", "function": {"name": "no_args_tool", "arguments": {"url": "https://x", "platform": "gh"}}},
        {"id": "2", "function": {"name": "shell_like", "arguments": {"cmd": "ls", "description": "list"}}},
    ], monkeypatch, caplog)
    assert seen == {"called": True, "cmd": "ls"}, out
    assert "description of the tool" in out[0] and "ignored" in out[0] and "url" in out[0]
    assert "listing" in out[1] and "description" in out[1]
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]


def test_missing_required_argument_gives_usage_without_error_log(tmp_path, monkeypatch, caplog):
    from myclaw.tools import TOOLS

    def needs_path(path: str):
        return "read " + path

    monkeypatch.setitem(TOOLS, "needs_path", {"func": needs_path, "desc": "t"})
    out = _run_tool_calls(tmp_path, [
        {"id": "1", "function": {"name": "needs_path", "arguments": {"file": "a.txt"}}},
    ], monkeypatch, caplog)
    assert "path" in out[0] and ("missing" in out[0].lower() or "required" in out[0].lower())
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]


def test_parallel_executor_survives_a_loop_closed_mid_run():
    """A loop closed while a run held a permit must not block later loops."""
    from myclaw.tools.core import ParallelToolExecutor

    ex = ParallelToolExecutor(max_concurrent=1, timeout=2)

    async def hold():
        async with ex._semaphore:
            await asyncio.sleep(100)

    loop = asyncio.new_event_loop()
    task = loop.create_task(hold())
    loop.run_until_complete(asyncio.sleep(0.05))
    task.cancel()
    loop.close()

    async def run():
        return await ex.execute_tools([{"function": {"name": "no_such_tool", "arguments": {}}}])

    out = asyncio.run(run())
    assert out and out[0]["tool_name"] != "parallel_executor", out


def test_worker_pool_works_in_a_second_event_loop():
    """Workers started in a finished loop must not swallow later submissions."""
    from myclaw.worker_pool import WorkerPoolManager

    pool = WorkerPoolManager(max_workers=1, task_timeout=5)

    async def use():
        return await asyncio.wait_for(pool.submit(lambda: 42), timeout=5)

    assert asyncio.run(use()) == 42
    assert asyncio.run(use()) == 42  # new loop: would hang before the fix


@pytest.mark.parametrize("cmd,hidden", [
    ('grep -rln "x" . 2>/dev/null | grep -v node_modules | head -30', [True, False, False]),
    ("ls . 2>/dev/null | head -5", [True, False]),
    ("ls /nonexistent 2>&1", [False]),
])
def test_shell_accepts_the_two_stderr_forms(cmd, hidden):
    from myclaw.tools.shell import parse

    groups = parse(cmd)
    assert [getattr(a, "hide_stderr", False) for _, pl in groups for a in pl] == hidden
    assert all("\x00" not in arg for _, pl in groups for a in pl for arg in a)


@pytest.mark.parametrize("cmd", [
    "ls 2>/tmp/x", "ls >/dev/null", "ls 2> /dev/null", "2>/dev/null ls",
    "ls 2>/dev/nullx", "ls \x00zs-stderr-null", "cat < /etc/passwd",
])
def test_shell_still_refuses_other_redirections(cmd):
    from myclaw.tools.shell import ShellError, parse

    with pytest.raises(ShellError):
        parse(cmd)


def test_shell_quoted_stderr_form_is_an_argument():
    from myclaw.tools.shell import parse

    (_, [argv]), = parse('echo "2>/dev/null"')
    assert list(argv) == ["echo", "2>/dev/null"] and not getattr(argv, "hide_stderr", False)


def test_shell_2_dev_null_hides_errors():
    import importlib

    sh = importlib.import_module("myclaw.tools.shell")
    assert sh.shell("ls /nonexistent-zs 2>/dev/null") == ""
    assert "nonexistent-zs" in sh.shell("ls /nonexistent-zs 2>&1")


def test_unknown_tool_suggests_the_real_name(caplog):
    import myclaw.tools  # noqa: F401  (registers the tools)
    from myclaw.tools.core import unknown_tool_message

    msg = unknown_tool_message("list_jobs")
    assert msg.startswith("Unknown tool: list_jobs. Did you mean: job_list")
    assert "Use only the tools" in unknown_tool_message("xyzzy_nothing")


def test_unknown_tool_in_sequential_path_is_info_with_suggestion(tmp_path, monkeypatch, caplog):
    import logging

    import myclaw.tools  # noqa: F401
    out = _run_tool_calls(tmp_path, [{"id": "c1", "function": {"name": "list_jobs", "arguments": {}}}],
                          monkeypatch, caplog)
    assert "job_list" in str(out), out
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "Unknown tool" in r.getMessage()]


def test_health_skips_leftover_index_of_internal_ids(tmp_path, monkeypatch):
    """knowledge_webui.db stays after its notes moved into 'default' — not a base."""
    import sqlite3

    from myclaw.knowledge import health

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    for name in ("default", "webui", "__delegate__", "alice"):
        sqlite3.connect(tmp_path / ".myclaw" / f"knowledge_{name}.db").close()
    monkeypatch.setattr("myclaw.knowledge.storage.knowledge_root", lambda: tmp_path / ".myclaw" / "knowledge")
    assert health.bases() == ["default", "alice"]


def test_semantic_stale_ids_on_an_index_without_checksum_column():
    import sqlite3

    from myclaw.knowledge import semantic

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, permalink TEXT, "
                "file_path TEXT, created_at TEXT, updated_at TEXT)")
    con.execute("INSERT INTO entities (name, updated_at) VALUES ('a', '1')")
    con.execute("CREATE TABLE note_embed_state (entity_id INTEGER, model TEXT, file_checksum TEXT, "
                "text_checksum TEXT)")
    assert semantic._stale_ids(con, "m") == [1]


def test_semantic_note_sources_on_an_old_index():
    import sqlite3

    from myclaw.knowledge import semantic

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, permalink TEXT, "
                "file_path TEXT, created_at TEXT, updated_at TEXT)")
    con.execute("INSERT INTO entities (name) VALUES ('a')")
    try:
        out = semantic._note_sources(con, [1])
    except sqlite3.OperationalError as e:  # later tables (observations…) are not the point here
        assert "checksum" not in str(e) and "title" not in str(e), e
        return
    assert out[1]["title"] == "a" and out[1]["checksum"] is None
