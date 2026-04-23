"""
Tools — Web Browsing & Download
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE,
    TOOLBOX_DIR,
    TOOLBOX_REG,
    TOOLBOX_DOCS,
    ALLOWED_COMMANDS,
    BLOCKED_COMMANDS,
    _rate_limiter,
    _tool_audit_logger,
    _agent_registry,
    _job_queue,
    _user_chat_ids,
    _notification_callback,
    _runtime_config,
    TOOLS,
    TOOL_SCHEMAS,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import ipaddress
import re
import httpx
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _is_safe_url(url: str) -> tuple[bool, str]:
    """SSRF guard: validate that a URL does not target internal/private resources.

    Returns:
        (is_safe, reason) where reason is empty if safe.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False, "Invalid URL: no hostname found."

    # Block non-HTTP(S) schemes
    if parsed.scheme not in {"http", "https"}:
        return False, f"URL scheme '{parsed.scheme}' is not allowed. Only http/https permitted."

    # Block localhost variants
    lowered = hostname.lower()
    if lowered in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return False, "Access to localhost is not allowed for security reasons."

    # Block private/reserved IP ranges
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast:
            return False, f"Access to IP address {hostname} is not allowed for security reasons."
    except ValueError:
        pass  # hostname is not an IP, continue

    return True, ""


# ── Internet & Download Tools ────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace to produce clean plain text."""
    # Remove <script> and <style> blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</(\1)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    html = (
        html.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    # Collapse whitespace
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


async def browse(url: str, max_length: int = 5000) -> str:
    """Browse a URL and return its plain-text content (HTML is stripped).

    Uses httpx.AsyncClient for non-blocking async HTTP requests.
    Implements specific, user-friendly error handling for common failure modes.

    Args:
        url: Full URL to fetch (e.g. 'https://example.com')
        max_length: Maximum characters to return (default: 5000).
                    Pages longer than this are truncated with a notice.

    Returns:
        Formatted page content on success, or structured error payload with guidance.
    """
    # SECURITY FIX (2026-04-23): SSRF guard before any outbound request.
    safe, reason = _is_safe_url(url)
    if not safe:
        logger.warning(f"SSRF blocked browse to {url}: {reason}")
        return f"Error: {reason}"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Strip HTML to plain text
            text = _strip_html(response.text)

            # Limit length
            if len(text) > max_length:
                text = text[:max_length] + "\n\n[Content truncated - reached max_length limit]"

            return f"URL: {url}\nStatus: {response.status_code}\n\nContent:\n{text}"

    except httpx.TimeoutException as e:
        logger.warning(f"Browse timeout for {url}: {e}")
        archive_url = f"https://web.archive.org/web/{url}"
        return (
            f"⏱️ Timeout Error accessing {url}\n\n"
            f"The page took too long to respond.\n\n"
            f"💡 Suggestions:\n"
            f"  • Try the cached version from Wayback Machine:\n"
            f"    {archive_url}\n"
            f"  • Check if the site is temporarily down\n"
            f"  • Try again later or with a different URL\n\n"
            f"📚 Alternative: Use search_knowledge() to find saved information about this topic"
        )

    except httpx.ConnectError as e:
        logger.warning(f"Browse connection error for {url}: {e}")
        return (
            f"🔌 Connection Error accessing {url}\n\n"
            f"Unable to establish a connection to the server.\n\n"
            f"💡 Suggestions:\n"
            f"  • Check your internet connection\n"
            f"  • Verify the URL is correct\n"
            f"  • The site may be temporarily unavailable\n"
            f"  • Try an alternate URL or mirror\n\n"
            f"📚 Alternative: Use search_knowledge() to find saved information about this topic"
        )

    except httpx.HTTPStatusError as e:
        # Handle specific HTTP status codes
        status_code = e.response.status_code if e.response is not None else None

        if status_code == 404:
            logger.warning(f"Browse 404 for {url}")
            return (
                f"❌ Page Not Found (404) for {url}\n\n"
                f"The page may have been moved, deleted, or the URL might be incorrect.\n\n"
                f"💡 Suggestions:\n"
                f"  • Check the URL for typos\n"
                f"  • Try visiting the main site and navigating from there\n"
                f"  • Search for the content using a web search\n"
                f"  • Try the Wayback Machine: https://web.archive.org/web/{url}\n\n"
                f"📚 Alternative: Use search_knowledge() to find saved information about this topic"
            )

        elif status_code == 403:
            logger.warning(f"Browse 403 for {url}")
            return (
                f"🚫 Access Denied (403) for {url}\n\n"
                f"The server is blocking access to this resource.\n\n"
                f"💡 Suggestions:\n"
                f"  • This site may require authentication or special access\n"
                f"  • Try accessing the site's main page first\n"
                f"  • The content may be restricted by region or policy\n\n"
                f"📚 Alternative: Use search_knowledge() to find publicly available information about this topic"
            )

        else:
            logger.error(f"Browse HTTP error {status_code} for {url}: {e}")
            return (
                f"⚠️ HTTP Error {status_code} accessing {url}\n\n"
                f"An HTTP error occurred while fetching the page.\n\n"
                f"💡 Suggestions:\n"
                f"  • The server may be experiencing issues\n"
                f"  • Try again later\n"
                f"  • Check if the URL is correct\n\n"
                f"📚 Alternative: Use search_knowledge() to find saved information"
            )

    except httpx.RequestError as e:
        logger.error(f"Browse request error for {url}: {e}")
        return (
            f"⚠️ Error accessing {url}\n\n"
            f"Details: {str(e)}\n\n"
            f"💡 Suggestions:\n"
            f"  • Verify the URL format (should include https:// or http://)\n"
            f"  • Check your network connection\n"
            f"  • The site may be temporarily unavailable\n\n"
            f"📚 Alternative: Use search_knowledge() to find saved information"
        )

    except Exception as e:
        logger.error(f"Unexpected browse error for {url}: {e}")
        return (
            f"⚠️ Unexpected error accessing {url}\n\n"
            f"Details: {str(e)}\n\n"
            f"💡 Please try again or use search_knowledge() to find saved information"
        )


async def download_file(url: str, path: str) -> str:
    """
    Download a file from a URL and save it to the workspace.

    Uses httpx.AsyncClient for non-blocking async HTTP requests.

    url: The URL to download from
    path: The path (relative to workspace) to save the file
    """
    # SECURITY FIX (2026-04-23): SSRF guard before any outbound request.
    safe, reason = _is_safe_url(url)
    if not safe:
        logger.warning(f"SSRF blocked download from {url}: {reason}")
        return f"Error: {reason}"

    try:
        # Validate the path
        target = validate_path(path)

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        # Download and save using async client
        async with httpx.AsyncClient(headers=headers, timeout=60) as client:
            async with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                with open(target, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

        # Get file size
        size = target.stat().st_size

        logger.info(f"Downloaded file from {url} to {path} ({size} bytes)")
        return f"[OK] Downloaded file from {url} to {path} ({size} bytes)"

    except ValueError as e:
        return f"Error: {e}"
    except httpx.RequestError as e:
        logger.error(f"Download error for {url}: {e}")
        return f"Error downloading from {url}: {e}"
    except Exception as e:
        logger.error(f"Unexpected download error: {e}")
        return f"Error: {e}"
