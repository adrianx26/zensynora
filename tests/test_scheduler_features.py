"""Tests for the scheduler reliability layer (Phase 6.3).

Covers:
    - RetryPolicy backoff math
    - OutputValidator built-ins (NotEmpty, Regex, MaxLength, JSONSchema, Custom, Chain)
    - ComplexityAnalyzer + decompose_task
    - Checkpoint round-trip
    - TaskBudget.from_complexity
    - AsyncScheduler integration: retry on failure, budget timeout,
      validator triggers retry, ctx injection, sub-task spawn, fallback.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from myclaw.async_scheduler import AsyncScheduler
from myclaw.scheduler_features import (
    Chain,
    Checkpoint,
    ComplexityAnalyzer,
    Custom,
    JSONSchema,
    JobContext,
    MaxLength,
    NotEmpty,
    Regex,
    RetryPolicy,
    TaskBudget,
    ValidationError,
    decompose_task,
)


# ── RetryPolicy ────────────────────────────────────────────────────────────


def test_retry_policy_fixed_backoff():
    p = RetryPolicy(max_attempts=4, backoff="fixed", base_delay=2.0, max_delay=10.0)
    assert p.compute_delay(1) == 2.0
    assert p.compute_delay(3) == 2.0


def test_retry_policy_linear_backoff_capped():
    p = RetryPolicy(max_attempts=5, backoff="linear", base_delay=3.0, max_delay=8.0)
    assert p.compute_delay(1) == 3.0
    assert p.compute_delay(2) == 6.0
    assert p.compute_delay(3) == 8.0  # capped


def test_retry_policy_exponential_backoff_capped():
    p = RetryPolicy(max_attempts=6, backoff="exponential", base_delay=1.0, max_delay=5.0)
    assert p.compute_delay(1) == 1.0
    assert p.compute_delay(2) == 2.0
    assert p.compute_delay(3) == 4.0
    assert p.compute_delay(4) == 5.0  # capped (would be 8)


# ── OutputValidator built-ins ──────────────────────────────────────────────


def test_not_empty_validator():
    v = NotEmpty()
    assert v(None) is not None
    assert v("") is not None
    assert v("   ") is not None
    assert v([]) is not None
    assert v("ok") is None
    assert v([1]) is None


def test_regex_validator():
    v = Regex(r"\d{3}-\d{4}")
    assert v("call 555-1234") is None
    assert v("no number here") is not None


def test_max_length_validator():
    v = MaxLength(10)
    assert v("short") is None
    assert v("this is way too long") is not None


def test_jsonschema_validator_dict_input():
    v = JSONSchema(required_keys=("name", "count"), types={"count": int})
    assert v({"name": "x", "count": 3}) is None
    assert v({"name": "x"}) is not None  # missing
    assert v({"name": "x", "count": "3"}) is not None  # wrong type


def test_jsonschema_validator_string_input():
    v = JSONSchema(required_keys=("ok",))
    assert v('{"ok": true}') is None
    assert v("not json") is not None


def test_chain_validator_short_circuits():
    v = Chain((NotEmpty(), Regex(r"^\d+$"), MaxLength(5)))
    assert v("123") is None
    assert v("") is not None  # NotEmpty fails first
    assert v("abc") is not None  # Regex fails
    assert v("999999") is not None  # MaxLength fails


def test_custom_validator_swallows_predicate_exceptions():
    v = Custom(lambda r: 1 / 0, "div-by-zero predicate")  # noqa: ARG005
    err = v("anything")
    assert err is not None
    assert "raised" in err


# ── ComplexityAnalyzer ─────────────────────────────────────────────────────


def test_complexity_simple_task_is_simple():
    c = ComplexityAnalyzer.analyze("ping the server")
    assert c.score < 0.4
    assert not c.decompose
    assert "complex" not in c.flags


def test_complexity_multi_step_keywords_trigger_decompose():
    c = ComplexityAnalyzer.analyze(
        "Browse the news site, then summarize the headlines, "
        "and finally email the digest to the team"
    )
    assert c.detected_steps >= 3
    assert c.decompose is True
    assert "decomposition-recommended" in c.flags


def test_complexity_numbered_list_detected():
    c = ComplexityAnalyzer.analyze(
        "Do the following:\n"
        "1. Pull latest commits\n"
        "2. Run the test suite\n"
        "3. Generate a report"
    )
    assert c.detected_steps >= 3
    assert "explicit-list" in c.flags
    assert c.decompose is True


def test_complexity_slow_ops_flagged():
    c = ComplexityAnalyzer.analyze(
        "Browse the web and scrape pricing data, then analyze trends and "
        "generate a comprehensive research summary"
    )
    assert "slow-ops" in c.flags
    assert c.estimated_duration_s > 30


# ── decompose_task ─────────────────────────────────────────────────────────


def test_decompose_numbered_list():
    parts = decompose_task("1. alpha\n2. beta\n3. gamma")
    assert parts == ["alpha", "beta", "gamma"]


def test_decompose_bullets():
    parts = decompose_task("- foo\n- bar\n- baz")
    assert parts == ["foo", "bar", "baz"]


def test_decompose_multi_step_keywords():
    parts = decompose_task("fetch the data then process it and finally write to db")
    assert len(parts) >= 2
    assert any("fetch" in p for p in parts)
    assert any("process" in p for p in parts)


def test_decompose_single_task_returns_self():
    parts = decompose_task("just check disk space")
    assert parts == ["just check disk space"]


def test_decompose_caps_at_max_subtasks():
    huge = "\n".join(f"{i}. step {i}" for i in range(1, 20))
    parts = decompose_task(huge, max_subtasks=5)
    assert len(parts) == 5


# ── Checkpoint ─────────────────────────────────────────────────────────────


def test_checkpoint_save_and_load(tmp_path: Path):
    cp = Checkpoint("job_abc", base_dir=tmp_path)
    cp.save("step1", {"ok": 1}, total_steps=3)
    cp.save("step2", {"ok": 2}, total_steps=3)
    state = cp.load()
    assert state["task_id"] == "job_abc"
    assert state["last_step"] == "step2"
    assert state["data"] == {"ok": 2}
    assert len(state["history"]) == 2
    assert state["progress"] == round(2 / 3, 3)


def test_checkpoint_clear(tmp_path: Path):
    cp = Checkpoint("job_clear", base_dir=tmp_path)
    cp.save("step", {"x": 1})
    assert cp.path.exists()
    cp.clear()
    assert not cp.path.exists()


def test_checkpoint_sanitises_unsafe_ids(tmp_path: Path):
    cp = Checkpoint("../evil/../id with spaces", base_dir=tmp_path)
    cp.save("step", {})
    assert cp.path.parent == tmp_path
    assert ".." not in cp.path.name


# ── TaskBudget ─────────────────────────────────────────────────────────────


def test_task_budget_from_complexity_respects_floor_and_ceiling():
    c = ComplexityAnalyzer.analyze("ping")
    b = TaskBudget.from_complexity(c, floor_s=120.0, ceiling_s=600.0)
    assert b.duration_s >= 120.0
    assert b.duration_s <= 600.0


# ── AsyncScheduler integration ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_retries_on_failure_then_succeeds():
    sched = AsyncScheduler(poll_interval=0.05)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("not yet")
        return "ok"

    sched.add_job(
        flaky,
        "date",
        run_date=datetime.now(),
        id="flaky",
        retry_policy=RetryPolicy(max_attempts=5, backoff="fixed", base_delay=0.01),
    )
    await sched.start()
    await asyncio.sleep(0.5)
    await sched.shutdown(wait=True)
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_scheduler_invokes_fallback_after_exhaustion():
    sched = AsyncScheduler(poll_interval=0.05)
    fb_called = {"n": 0}

    async def always_fails():
        raise RuntimeError("boom")

    def fallback(_job, _exc, _ctx):
        fb_called["n"] += 1

    sched.add_job(
        always_fails,
        "date",
        run_date=datetime.now(),
        id="always-fails",
        retry_policy=RetryPolicy(
            max_attempts=2, backoff="fixed", base_delay=0.01, fallback=fallback
        ),
    )
    await sched.start()
    await asyncio.sleep(0.5)
    await sched.shutdown(wait=True)
    assert fb_called["n"] == 1


@pytest.mark.asyncio
async def test_scheduler_validator_failure_triggers_retry():
    sched = AsyncScheduler(poll_interval=0.05)
    runs = {"n": 0}

    async def returns_changing():
        runs["n"] += 1
        return "" if runs["n"] < 3 else "real result"

    sched.add_job(
        returns_changing,
        "date",
        run_date=datetime.now(),
        id="validator-retry",
        validator=NotEmpty(),
        retry_policy=RetryPolicy(
            max_attempts=4, backoff="fixed", base_delay=0.01,
            retry_on=(ValidationError, Exception),
        ),
    )
    await sched.start()
    await asyncio.sleep(0.5)
    await sched.shutdown(wait=True)
    assert runs["n"] == 3


@pytest.mark.asyncio
async def test_scheduler_budget_enforces_timeout():
    sched = AsyncScheduler(poll_interval=0.05)
    completed = {"done": False}

    async def slow():
        await asyncio.sleep(2.0)
        completed["done"] = True

    sched.add_job(
        slow,
        "date",
        run_date=datetime.now(),
        id="slow",
        budget=TaskBudget(duration_s=0.1),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    await sched.start()
    await asyncio.sleep(0.4)
    await sched.shutdown(wait=True)
    assert completed["done"] is False  # was killed by timeout


@pytest.mark.asyncio
async def test_scheduler_injects_ctx_and_records_checkpoint(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    sched = AsyncScheduler(poll_interval=0.05)
    seen = {"ctx": None}

    async def with_ctx(ctx: JobContext = None):
        assert ctx is not None
        ctx.progress("phase-1", 1, 2)
        ctx.progress("phase-2", 2, 2)
        seen["ctx"] = ctx.job_id
        return "done"

    sched.add_job(
        with_ctx,
        "date",
        run_date=datetime.now(),
        id="with-ctx",
        checkpoint_enabled=True,
    )
    await sched.start()
    await asyncio.sleep(0.4)
    await sched.shutdown(wait=True)
    assert seen["ctx"] == "with-ctx"

    cp_file = tmp_path / ".myclaw" / "checkpoints" / "with-ctx.json"
    assert cp_file.exists()
    state = json.loads(cp_file.read_text())
    assert state["status"] == "completed"
    assert any(h["step"] == "phase-1" for h in state["history"])
    assert any(h["step"] == "phase-2" for h in state["history"])


@pytest.mark.asyncio
async def test_scheduler_subtask_spawn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    sched = AsyncScheduler(poll_interval=0.05)
    sub_ran = {"n": 0}

    async def child():
        sub_ran["n"] += 1

    async def parent(ctx: JobContext = None):
        ctx.spawn_subtask(child, sub_id="child-1")

    sched.add_job(
        parent,
        "date",
        run_date=datetime.now(),
        id="parent",
        checkpoint_enabled=True,
    )
    await sched.start()
    await asyncio.sleep(0.6)
    await sched.shutdown(wait=True)
    assert sub_ran["n"] == 1


@pytest.mark.asyncio
async def test_scheduler_existing_funcs_without_ctx_kwarg_still_work():
    """A function with no 'ctx'/'context' kwarg must still execute fine."""
    sched = AsyncScheduler(poll_interval=0.05)
    ran = {"n": 0}

    async def plain():
        ran["n"] += 1

    sched.add_job(plain, "date", run_date=datetime.now(), id="plain")
    await sched.start()
    await asyncio.sleep(0.3)
    await sched.shutdown(wait=True)
    assert ran["n"] == 1
