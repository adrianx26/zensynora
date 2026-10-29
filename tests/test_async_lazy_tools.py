"""Async tools behind lazy (sync) wrappers are awaited (2026-10-27).

`send_telegram_message` is registered lazily; the wrapper is a plain function,
so the executors ran it in a thread and returned the un-awaited coroutine:
nothing was sent and the agent saw "<coroutine object …>".
"""
import asyncio
import json

from myclaw.tools import TOOLS
from myclaw.tools.core import ParallelToolExecutor


def test_parallel_executor_awaits_coroutine_from_sync_wrapper(monkeypatch):
    sent = []

    async def real(text: str) -> str:
        sent.append(text)
        return f"sent: {text}"

    def wrapper(**kw):  # like the lazy registry wrapper
        return real(**kw)

    monkeypatch.setitem(TOOLS, "fake_async_tool", {"func": wrapper, "desc": "test"})
    calls = [{"id": "1", "function": {"name": "fake_async_tool", "arguments": json.dumps({"text": "hi"})}}]
    out = asyncio.run(ParallelToolExecutor().execute_tools(calls))
    assert sent == ["hi"]
    assert "sent: hi" in str(out[0].get("result")) and "coroutine" not in str(out[0])


def test_send_telegram_message_wrapper_returns_awaitable_that_runs(monkeypatch):
    import inspect

    import myclaw.channels.telegram as tg

    async def fake(text: str, to: str = "") -> str:
        return "ok " + text

    monkeypatch.setattr(tg, "send_telegram_message", fake)
    res = TOOLS["send_telegram_message"]["func"](text="x")
    if inspect.isawaitable(res):
        res = asyncio.run(res)
    assert res == "ok x"
