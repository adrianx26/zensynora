"""Async-safe circuit breaker.

Three states:

* **CLOSED**: requests pass through; failures are counted.
* **OPEN**: requests fail fast for ``reset_timeout`` seconds (no work done).
* **HALF_OPEN**: a probe request is allowed; ``success_threshold``
  consecutive successes return the breaker to CLOSED, any failure
  re-opens it.

Designed to wrap an LLM provider call. The breaker itself is in-process —
for distributed coordination, share state via Redis (left as future work).
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(RuntimeError):
    """Raised when a request is short-circuited because the breaker is OPEN."""


class CircuitBreaker:
    """Async-safe circuit breaker.

    Args:
        name: Identifier for logs/metrics (e.g. provider name).
        failure_threshold: Failures in CLOSED state that trip the breaker.
        reset_timeout: Seconds to stay in OPEN before testing recovery.
        success_threshold: Consecutive HALF_OPEN successes to close the
            breaker. Defaults to 1 (a single probe is enough).
        excluded_exceptions: Exception types that do NOT count as failures
            (e.g., user-input validation errors).
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        success_threshold: int = 1,
        excluded_exceptions: tuple = (),
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if reset_timeout <= 0:
            raise ValueError("reset_timeout must be > 0")

        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold
        self.excluded_exceptions = excluded_exceptions

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[float] = None
        self._lock = asyncio.Lock()

    # ── Public read-only properties ────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    # ── Core call wrapper ──────────────────────────────────────────────

    async def call(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Invoke ``fn(*args, **kwargs)`` through the breaker."""
        await self._before_call()
        try:
            result = await fn(*args, **kwargs)
        except self.excluded_exceptions:
            # Excluded errors are passed straight through without affecting
            # breaker state — typically validation/user-input errors.
            raise
        except Exception as e:
            await self._on_failure(e)
            raise
        else:
            await self._on_success()
            return result

    # ── State transitions ─────────────────────────────────────────────

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._opened_at is None:
                    return
                if time.monotonic() - self._opened_at >= self.reset_timeout:
                    logger.info("Circuit %s: OPEN -> HALF_OPEN (probe)", self.name)
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                else:
                    raise CircuitBreakerError(
                        f"Circuit '{self.name}' is OPEN; failing fast"
                    )

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info("Circuit %s: HALF_OPEN -> CLOSED", self.name)
                    self._reset_locked()
            elif self._state == CircuitState.CLOSED:
                # Reset partial failure count after any success.
                self._failure_count = 0

    async def _on_failure(self, exc: BaseException) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit %s: HALF_OPEN probe failed (%s) -> OPEN",
                    self.name, type(exc).__name__,
                )
                self._trip_locked()
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                logger.warning(
                    "Circuit %s: failure threshold reached (%d) -> OPEN",
                    self.name, self._failure_count,
                )
                self._trip_locked()

    def _trip_locked(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._success_count = 0

    def _reset_locked(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at = None

    # ── Test/admin helpers ────────────────────────────────────────────

    async def reset(self) -> None:
        """Force the breaker back to CLOSED. Use sparingly (admin tool)."""
        async with self._lock:
            self._reset_locked()

    def snapshot(self) -> dict:
        """Lock-free snapshot for metrics endpoints."""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "opened_at": self._opened_at,
        }
