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
