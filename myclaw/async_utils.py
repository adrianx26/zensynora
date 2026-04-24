"""Utility helpers for safely running async functions.

The codebase historically called ``asyncio.run`` inside library code, which
creates a new event loop every time and can break when the caller already has
an active loop (e.g., when the function is used from another async context).

``run_async`` detects an existing running loop and schedules the coroutine
with ``await``; otherwise it falls back to ``asyncio.run`` for the top‑level
synchronous entry points.
"""

import asyncio
from typing import Any, Awaitable, Callable


def _has_running_loop() -> bool:
    """Return ``True`` if we are inside a running asyncio event loop."""
    try:
        return asyncio.get_running_loop() is not None
    except RuntimeError:
        return False


async def _run_in_new_loop(coro: Awaitable[Any]) -> Any:
    """Run ``coro`` in a brand‑new loop (used when no loop is active)."""
    return await coro


def run_async(func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    """Execute an async ``func`` safely.

    If a loop is already running, ``await func(*args, **kwargs)`` is returned
    to the caller (the caller must be async).  When called from sync code, the
    function falls back to ``asyncio.run`` which creates a temporary loop.
    """
    if _has_running_loop():
        # In an async context – return the coroutine so the caller can ``await`` it.
        return func(*args, **kwargs)
    else:
        # No event loop – execute synchronously.
        return asyncio.run(func(*args, **kwargs))
