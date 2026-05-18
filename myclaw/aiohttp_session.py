"""Shared :class:`aiohttp.ClientSession` used throughout the async codebase.

Having a single, lazily-initialised session enables HTTP connection
pooling, TCP keep-alive, and connection reuse across async components
(web search, MCP HTTP transport, knowledge fetching, etc.), reducing
latency for frequent outbound calls.

Usage:
    from myclaw.aiohttp_session import get_aiohttp_session

    session = get_aiohttp_session()
    async with session.get(url) as response:
        ...

The session is closed at process exit via an atexit hook.  For
long-running servers (FastAPI, gateway), call ``close_aiohttp_session()``
explicitly during graceful shutdown.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from threading import Lock

logger = logging.getLogger(__name__)

_session: "aiohttp.ClientSession | None" = None  # noqa: F821
_lock = Lock()
_closed = False
# Default timeout for all requests made through the shared session
DEFAULT_TIMEOUT = 10  # seconds


def get_aiohttp_session(timeout: int = DEFAULT_TIMEOUT) -> "aiohttp.ClientSession":  # noqa: F821
    """Return a lazily-created, thread-safe ``aiohttp.ClientSession``.

    The session is created once on the first call and reused across the
    entire application lifetime.  This enables TCP connection pooling
    and HTTP keep-alive.

    Args:
        timeout: Default total timeout in seconds for requests through
                 this session (used only at creation time).
    """
    global _session, _closed
    if _closed:
        # Session was explicitly closed; create a new one.
        with _lock:
            if _closed:
                import aiohttp
                _session = _create_session(timeout)
                _closed = False
                return _session
    if _session is None or _session.closed:
        with _lock:
            if _session is None or _session.closed:
                import aiohttp
                _session = _create_session(timeout)
    return _session


def _create_session(timeout: int) -> "aiohttp.ClientSession":
    import aiohttp
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    return aiohttp.ClientSession(
        timeout=timeout_obj,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ZenSynora/0.4)",
        },
    )


def close_aiohttp_session() -> None:
    """Close the shared aiohttp session.

    Safe to call multiple times.  After closing, the next call to
    ``get_aiohttp_session()`` will create a fresh session.
    """
    global _session, _closed
    if _session is not None and not _session.closed:
        try:
            # aiohttp.ClientSession.close() is a coroutine but we need
            # to call it from a sync context during shutdown.  We
            # delegate to asyncio.get_event_loop() if available.
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_session.close())
            else:
                loop.run_until_complete(_session.close())
        except Exception as exc:
            logger.debug("Error closing aiohttp session: %s", exc)
    _session = None
    _closed = True


# Auto-close on process exit
atexit.register(close_aiohttp_session)
