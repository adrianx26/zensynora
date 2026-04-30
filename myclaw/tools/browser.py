"""Headless browser tool — Playwright-backed.

The current `myclaw.tools.web` module covers static HTTP fetches (good for
APIs, dead for SPAs). This module fills the gap: real DOM rendering, form
filling, screenshots, JS execution.

Optional dependency: ``playwright`` is not pulled in by default. Without
it, every tool here returns a structured error string instead of crashing,
so dynamic-tool authors can probe support at runtime.

Install:

    pip install playwright
    playwright install chromium

Browsers are pooled in a module-level dict keyed by browser type. A single
process never holds more than ``_MAX_CONTEXTS`` open contexts; the oldest
is closed when the cap is hit.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Optional dependency probing ──────────────────────────────────────────

try:  # pragma: no cover - import guard
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page

    _PW_AVAILABLE = True
except Exception:  # ImportError or missing browser binaries
    async_playwright = None  # type: ignore[assignment]
    Browser = BrowserContext = Page = None  # type: ignore[assignment]
    _PW_AVAILABLE = False


_MAX_CONTEXTS = 10
_CONTEXT_IDLE_TIMEOUT = 300.0  # seconds

# Lazily-initialized state. Guarded by `_lock` to keep concurrent calls safe.
_lock = asyncio.Lock()
_playwright_ctx: Any = None  # the async-playwright manager (kept open)
_browser: Optional["Browser"] = None
# Insertion-ordered map so we can evict the oldest context when full.
_contexts: "OrderedDict[str, _ContextEntry]" = OrderedDict()


class _ContextEntry:
    __slots__ = ("context", "page", "last_used")

    def __init__(self, context: "BrowserContext", page: "Page") -> None:
        self.context = context
        self.page = page
        self.last_used = time.monotonic()


def is_browser_available() -> bool:
    """True when Playwright is importable. Does not check browser binaries."""
    return _PW_AVAILABLE


def _unavailable() -> Dict[str, Any]:
    return {
        "ok": False,
        "error": (
            "Playwright is not installed. Install with: "
            "`pip install playwright && playwright install chromium`."
        ),
    }


async def _ensure_browser() -> Optional["Browser"]:
    """Idempotently launch a single shared headless browser."""
    global _playwright_ctx, _browser
    if not _PW_AVAILABLE:
        return None
    if _browser is not None and _browser.is_connected():
        return _browser
    _playwright_ctx = await async_playwright().start()
    _browser = await _playwright_ctx.chromium.launch(headless=True)
    return _browser


async def _get_context(session_id: str) -> Optional[_ContextEntry]:
    """Get-or-create a browser context keyed by session id.

    Caller must hold `_lock`.
    """
    if session_id in _contexts:
        entry = _contexts[session_id]
        entry.last_used = time.monotonic()
        # Mark as MRU.
        _contexts.move_to_end(session_id)
        return entry

    browser = await _ensure_browser()
    if browser is None:
        return None

    # Evict oldest if at capacity.
    while len(_contexts) >= _MAX_CONTEXTS:
        old_id, old_entry = _contexts.popitem(last=False)
        try:
            await old_entry.context.close()
        except Exception:
            logger.debug("Error closing evicted context %s", old_id, exc_info=True)

    context = await browser.new_context()
    page = await context.new_page()
    entry = _ContextEntry(context, page)
    _contexts[session_id] = entry
    return entry


async def shutdown() -> None:
    """Close all browser contexts and the underlying browser. Call on exit."""
    global _playwright_ctx, _browser
    async with _lock:
        for entry in _contexts.values():
            try:
                await entry.context.close()
            except Exception:
                logger.debug("Error closing context", exc_info=True)
        _contexts.clear()
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
            _browser = None
        if _playwright_ctx is not None:
            try:
                await _playwright_ctx.stop()
            except Exception:
                logger.debug("Error stopping playwright", exc_info=True)
            _playwright_ctx = None


# ── Public tools ──────────────────────────────────────────────────────────


async def browser_navigate(
    url: str,
    timeout: float = 30.0,
    wait_until: str = "load",
    session_id: str = "default",
) -> Dict[str, Any]:
    """Navigate to ``url`` and return the rendered HTML + final URL.

    Args:
        url: Absolute URL to load.
        timeout: Per-page navigation timeout in seconds.
        wait_until: ``"load"``, ``"domcontentloaded"``, or ``"networkidle"``.
        session_id: Key for the persistent browser context. Reuse the same
            id across calls to keep cookies/storage between navigations.
    """
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = await _get_context(session_id)
    if entry is None:
        return _unavailable()
    try:
        await entry.page.goto(url, timeout=timeout * 1000, wait_until=wait_until)
        html = await entry.page.content()
        return {
            "ok": True,
            "url": entry.page.url,
            "title": await entry.page.title(),
            "html": html,
        }
    except Exception as e:
        logger.warning("browser_navigate failed for %s", url, exc_info=e)
        return {"ok": False, "error": f"Navigation failed: {type(e).__name__}: {e}"}


async def browser_screenshot(
    selector: Optional[str] = None,
    session_id: str = "default",
) -> Dict[str, Any]:
    """Take a screenshot of the current page or a specific element.

    Returns the PNG as base64 to keep the result JSON-serializable.
    """
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = _contexts.get(session_id)
    if entry is None:
        return {"ok": False, "error": "No active session. Call browser_navigate first."}
    try:
        if selector:
            element = await entry.page.query_selector(selector)
            if element is None:
                return {"ok": False, "error": f"Selector not found: {selector}"}
            png = await element.screenshot()
        else:
            png = await entry.page.screenshot()
        return {"ok": True, "png_base64": base64.b64encode(png).decode("ascii")}
    except Exception as e:
        logger.warning("browser_screenshot failed", exc_info=e)
        return {"ok": False, "error": f"Screenshot failed: {type(e).__name__}: {e}"}


async def browser_fill_form(
    fields: Dict[str, str],
    submit_selector: Optional[str] = None,
    session_id: str = "default",
) -> Dict[str, Any]:
    """Fill form fields and optionally submit.

    Args:
        fields: Mapping of CSS selector -> value (e.g. ``{"#email": "...", "input[name=password]": "..."}``).
        submit_selector: Optional selector clicked after filling.
    """
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = _contexts.get(session_id)
    if entry is None:
        return {"ok": False, "error": "No active session. Call browser_navigate first."}
    try:
        for selector, value in fields.items():
            await entry.page.fill(selector, value)
        if submit_selector:
            await entry.page.click(submit_selector)
        return {"ok": True, "url": entry.page.url}
    except Exception as e:
        logger.warning("browser_fill_form failed", exc_info=e)
        return {"ok": False, "error": f"Form fill failed: {type(e).__name__}: {e}"}


async def browser_extract_text(
    selector: str = "body",
    session_id: str = "default",
) -> Dict[str, Any]:
    """Extract visible text content under a selector. Defaults to whole body."""
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = _contexts.get(session_id)
    if entry is None:
        return {"ok": False, "error": "No active session. Call browser_navigate first."}
    try:
        element = await entry.page.query_selector(selector)
        if element is None:
            return {"ok": False, "error": f"Selector not found: {selector}"}
        text = await element.inner_text()
        return {"ok": True, "text": text}
    except Exception as e:
        logger.warning("browser_extract_text failed", exc_info=e)
        return {"ok": False, "error": f"Text extraction failed: {type(e).__name__}: {e}"}


async def browser_wait_for(
    selector: str,
    timeout: float = 10.0,
    state: str = "visible",
    session_id: str = "default",
) -> Dict[str, Any]:
    """Wait for a selector to reach the desired state (visible/attached/hidden)."""
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = _contexts.get(session_id)
    if entry is None:
        return {"ok": False, "error": "No active session. Call browser_navigate first."}
    try:
        await entry.page.wait_for_selector(selector, timeout=timeout * 1000, state=state)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"Wait timed out: {type(e).__name__}: {e}"}


async def browser_close_session(session_id: str = "default") -> Dict[str, Any]:
    """Close one persistent context. Useful for clearing cookies/storage."""
    if not _PW_AVAILABLE:
        return _unavailable()
    async with _lock:
        entry = _contexts.pop(session_id, None)
    if entry is None:
        return {"ok": False, "error": f"No session: {session_id}"}
    try:
        await entry.context.close()
        return {"ok": True}
    except Exception as e:
        logger.warning("browser_close_session failed", exc_info=e)
        return {"ok": False, "error": str(e)}
