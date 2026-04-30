"""Provider fallback chain backed by per-provider circuit breakers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional, Tuple, TypeVar

from .circuit_breaker import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FallbackExhausted(RuntimeError):
    """Raised when every provider in the chain has failed."""

    def __init__(self, errors: List[Tuple[str, BaseException]]):
        self.errors = errors
        formatted = "; ".join(f"{name}: {type(e).__name__}: {e}" for name, e in errors)
        super().__init__(f"All fallback providers failed: {formatted}")


class FallbackChain:
    """Try providers in order; on failure, fall through to the next.

    Each provider gets its own ``CircuitBreaker``. When a breaker is OPEN,
    its provider is skipped (no probe call), so a flapping endpoint does
    not slow every request down.

    Usage:

        chain = FallbackChain([
            ("openai",   call_openai),
            ("anthropic", call_anthropic),
            ("ollama",   call_ollama),
        ])
        result = await chain.execute(messages, model="gpt-4o")
    """

    def __init__(
        self,
        providers: List[Tuple[str, Callable[..., Awaitable[T]]]],
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        excluded_exceptions: tuple = (),
    ) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")

        self._providers: List[Tuple[str, Callable[..., Awaitable[T]], CircuitBreaker]] = [
            (
                name,
                fn,
                CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    reset_timeout=reset_timeout,
                    excluded_exceptions=excluded_exceptions,
                ),
            )
            for name, fn in providers
        ]

    async def execute(self, *args: Any, **kwargs: Any) -> T:
        errors: List[Tuple[str, BaseException]] = []
        for name, fn, breaker in self._providers:
            try:
                return await breaker.call(fn, *args, **kwargs)
            except CircuitBreakerError as cbe:
                # Provider is OPEN — skip without running it.
                logger.debug("Skipping provider %s (circuit open)", name)
                errors.append((name, cbe))
            except Exception as e:
                logger.warning(
                    "Provider %s failed (%s: %s); falling back",
                    name, type(e).__name__, e,
                )
                errors.append((name, e))
        raise FallbackExhausted(errors)

    def snapshot(self) -> List[dict]:
        return [breaker.snapshot() for _, _, breaker in self._providers]

    def get_breaker(self, name: str) -> Optional[CircuitBreaker]:
        for n, _, breaker in self._providers:
            if n == name:
                return breaker
        return None
