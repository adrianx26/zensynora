"""Tests for circuit breaker + fallback chain."""

import asyncio
import pytest

from myclaw.resilience import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
    FallbackChain,
    FallbackExhausted,
)


# ── CircuitBreaker ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_breaker_starts_closed_and_passes_calls_through():
    cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.05)

    async def ok():
        return "ok"

    assert cb.state is CircuitState.CLOSED
    assert await cb.call(ok) == "ok"
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.05)

    async def boom():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(boom)

    assert cb.state is CircuitState.OPEN

    # Subsequent call should fail-fast without invoking the function.
    invocations = 0

    async def counted():
        nonlocal invocations
        invocations += 1
        return "ok"

    with pytest.raises(CircuitBreakerError):
        await cb.call(counted)
    assert invocations == 0


@pytest.mark.asyncio
async def test_breaker_half_open_recovers_on_success():
    cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.02)

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(boom)
    assert cb.state is CircuitState.OPEN

    # Wait for the reset timeout, then a successful probe should close it.
    await asyncio.sleep(0.05)

    async def ok():
        return "ok"

    assert await cb.call(ok) == "ok"
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_excluded_exceptions_do_not_trip_breaker():
    class UserError(Exception):
        pass

    cb = CircuitBreaker(
        "test",
        failure_threshold=1,
        reset_timeout=0.05,
        excluded_exceptions=(UserError,),
    )

    async def user_err():
        raise UserError("bad input")

    for _ in range(5):
        with pytest.raises(UserError):
            await cb.call(user_err)
    assert cb.state is CircuitState.CLOSED


# ── FallbackChain ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_returns_first_success():
    async def primary(x):
        return ("primary", x)

    async def secondary(x):
        return ("secondary", x)

    chain = FallbackChain([("p", primary), ("s", secondary)])
    assert await chain.execute(1) == ("primary", 1)


@pytest.mark.asyncio
async def test_fallback_falls_through_on_failure():
    async def primary(_):
        raise RuntimeError("nope")

    async def secondary(x):
        return ("secondary", x)

    chain = FallbackChain([("p", primary), ("s", secondary)])
    assert await chain.execute(2) == ("secondary", 2)


@pytest.mark.asyncio
async def test_fallback_exhausted_when_all_fail():
    async def boom(_):
        raise RuntimeError("boom")

    chain = FallbackChain([("a", boom), ("b", boom)])
    with pytest.raises(FallbackExhausted) as ei:
        await chain.execute(3)
    assert len(ei.value.errors) == 2


@pytest.mark.asyncio
async def test_open_breaker_skips_provider_without_calling():
    invocations = {"a": 0, "b": 0}

    async def fail_a(_):
        invocations["a"] += 1
        raise RuntimeError("fail")

    async def ok_b(x):
        invocations["b"] += 1
        return ("b", x)

    chain = FallbackChain(
        [("a", fail_a), ("b", ok_b)],
        failure_threshold=1,
        reset_timeout=10.0,
    )
    # First call: a fails, b succeeds, a's breaker trips.
    assert await chain.execute(1) == ("b", 1)
    # Second call: a should be skipped (breaker OPEN), b runs again.
    assert await chain.execute(2) == ("b", 2)
    assert invocations["a"] == 1, "Provider a should NOT be invoked while breaker OPEN"
    assert invocations["b"] == 2
