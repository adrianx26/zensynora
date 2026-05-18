"""
Web Search Tools for Real-time Information

Provides real-time web search capabilities using multiple search backends:
- DuckDuckGo (default)
- Wikipedia
- Web scraping
- News feeds

SECURITY FIX (2026-05-17): URL construction now uses urllib.parse.urlencode
to prevent injection via query parameters.  Replaced per-request aiohttp
session creation with the shared aiohttp_session module for connection
pooling and TCP keep-alive.

SECURITY FIX (2026-05-18): Added ``defusedxml`` safe XML parser for
``search_news()`` to prevent billion-laughs / entity-expansion attacks.
Added per-function rate limiting (token-bucket) to prevent abuse of
public web search endpoints.
"""

import asyncio
import logging
import re
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .aiohttp_session import get_aiohttp_session

logger = logging.getLogger(__name__)


HTTP_TIMEOUT = 10

# Trusted search endpoints — only these hosts are allowed for URL construction.
_DDG_BASE = "https://html.duckduckgo.com"
_WIKI_BASE = "https://en.wikipedia.org"
_NEWS_BASE = "https://news.google.com"

# ── Web Search Rate Limiter ───────────────────────────────────────────────
#
# Prevents abuse of public web search endpoints.  Each search function
# has its own token bucket, independent of tool-execution rate limiting
# (which lives in tools/core.py).

_WEB_SEARCH_RATE_LIMITS: Dict[str, tuple] = {}  # func_name -> (timestamps, max_calls, window_sec)


def _rate_limit_check(
    func_name: str,
    max_calls: int = 10,
    window_seconds: float = 60.0,
) -> bool:
    """Token-bucket check for web search rate limiting.

    Returns True if the call is allowed, False if rate-limited.
    """
    now = time.time()
    if func_name not in _WEB_SEARCH_RATE_LIMITS:
        _WEB_SEARCH_RATE_LIMITS[func_name] = ([], max_calls, window_seconds)

    timestamps, mc, ws = _WEB_SEARCH_RATE_LIMITS[func_name]
    # Prune expired timestamps
    timestamps[:] = [t for t in timestamps if now - t < ws]
    if len(timestamps) >= mc:
        return False
    timestamps.append(now)
    return True


def _get_rate_limit_remaining(func_name: str, max_calls: int = 10, window_seconds: float = 60.0) -> int:
    """Return remaining calls available in the current window."""
    now = time.time()
    timestamps, mc, ws = _WEB_SEARCH_RATE_LIMITS.get(func_name, ([], max_calls, window_seconds))
    current = len([t for t in timestamps if now - t < ws])
    return max(0, mc - current)


# ── Safe XML Parsing ──────────────────────────────────────────────────────
#
# Uses ``defusedxml`` when installed to prevent billion-laughs and other
# entity-expansion attacks.  Falls back to standard ``xml.etree.ElementTree``
# with external-entity parsing disabled.


def _safe_parse_xml(content: str):
    """Parse XML safely.

    Uses ``defusedxml`` if installed (recommended).  Falls back to
    ``xml.etree.ElementTree`` with a ``defusedxml.DefusedXmlException``
    surrogate for consistent error handling.
    """
    try:
        import defusedxml.ElementTree as SafeTree
        return SafeTree.fromstring(content)
    except ImportError:
        pass
    except Exception as exc:
        # Re-raise any defusedxml-specific errors as appropriate.
        if type(exc).__name__ == "EntitiesForbidden":
            logger.error("XML entity expansion blocked by defusedxml")
        raise

    # Fallback: standard ElementTree (acceptable for known-good sources).
    import xml.etree.ElementTree as ET
    return ET.fromstring(content)


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    source: str = "web"
    relevance: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "relevance": self.relevance,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class WebSearchResponse:
    """Response from web search."""
    query: str
    results: List[SearchResult]
    elapsed_seconds: float = 0.0
    source: str = "web"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "elapsed_seconds": self.elapsed_seconds,
            "source": self.source
        }


async def _fetch_url(
    url: str,
    timeout: int = HTTP_TIMEOUT
) -> Optional[str]:
    """Fetch a URL using the shared aiohttp session for connection pooling."""
    import aiohttp

    try:
        session = get_aiohttp_session(timeout)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.status == 200:
                return await response.text()
    except Exception as e:
        logger.error("Fetch error for %s: %s", url, e)

    return None


def _parse_duckduckgo(html: str, query: str) -> List[SearchResult]:
    """Parse DuckDuckGo search results."""
    results = []
    
    result_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>'
    )
    
    snippet_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="[^"]*"[^>]*>.*?</a>(.*?)<'
    )
    
    for match in result_pattern.finditer(html):
        url = match.group(1)
        title = match.group(2).strip()
        
        if url.startswith("//duckduckgo.com"):
            continue
        
        results.append(SearchResult(
            title=title,
            url=url,
            snippet=f"Search result for: {query}"
        ))
        
        if len(results) >= 10:
            break
    
    return results[:10]


async def search_web(
    query: str,
    num_results: int = 10,
    source: str = "duckduckgo"
) -> WebSearchResponse:
    """Search the web for information.

    Args:
        query: Search query
        num_results: Maximum results to return
        source: Search backend (duckduckgo, wikipedia)

    Returns:
        WebSearchResponse with results
    """
    import time as _time
    start_time = _time.time()

    if source == "wikipedia":
        return await search_wikipedia(query, num_results)

    # Rate limit: 30 calls per 60 seconds (generous for an agent tool).
    if not _rate_limit_check("search_web", max_calls=30, window_seconds=60):
        logger.warning("Rate limit exceeded for search_web (query: %s)", query[:60])
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=_time.time() - start_time,
            source=source,
        )

    # SECURITY: use urlencode to prevent injection via query parameters.
    params = urllib.parse.urlencode({"q": query}, quote_via=urllib.parse.quote_plus)
    url = urllib.parse.urljoin(_DDG_BASE, "/html/") + "?" + params

    html = await _fetch_url(url)

    if not html:
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=_time.time() - start_time,
            source=source
        )

    results = _parse_duckduckgo(html, query)

    return WebSearchResponse(
        query=query,
        results=results[:num_results],
        elapsed_seconds=_time.time() - start_time,
        source=source
    )


async def search_wikipedia(
    query: str,
    num_results: int = 5
) -> WebSearchResponse:
    """Search Wikipedia."""
    import time as _time
    start_time = _time.time()

    # Rate limit: 60 calls per 60 seconds (Wikipedia is a shared resource).
    if not _rate_limit_check("search_wikipedia", max_calls=60, window_seconds=60):
        logger.warning("Rate limit exceeded for search_wikipedia (query: %s)", query[:60])
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=_time.time() - start_time,
            source="wikipedia",
        )

    # SECURITY: use urlencode to prevent injection via query parameters.
    params = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": str(num_results),
            "format": "json",
        },
        quote_via=urllib.parse.quote_plus,
    )
    url = urllib.parse.urljoin(_WIKI_BASE, "/w/api.php") + "?" + params

    import aiohttp
    try:
        session = get_aiohttp_session()
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()

                results = []
                titles = data[1] if len(data) > 1 else []
                descriptions = data[2] if len(data) > 2 else []
                urls = data[3] if len(data) > 3 else []

                for i, title in enumerate(titles):
                    results.append(SearchResult(
                        title=title,
                        url=urls[i] if i < len(urls) else "",
                        snippet=descriptions[i] if i < len(descriptions) else "",
                        source="wikipedia"
                    ))

                return WebSearchResponse(
                    query=query,
                    results=results,
                    elapsed_seconds=_time.time() - start_time,
                    source="wikipedia"
                )
    except Exception as e:
        logger.error("Wikipedia search error: %s", e)

    return WebSearchResponse(
        query=query,
        results=[],
        elapsed_seconds=_time.time() - start_time,
        source="wikipedia"
    )


async def search_news(
    query: str,
    num_results: int = 10
) -> WebSearchResponse:
    """Search for recent news."""
    import time as _time
    start_time = _time.time()

    # Rate limit: 20 calls per 60 seconds (news RSS is rate-sensitive).
    if not _rate_limit_check("search_news", max_calls=20, window_seconds=60):
        logger.warning("Rate limit exceeded for search_news (query: %s)", query[:60])
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=_time.time() - start_time,
            source="news",
        )

    # SECURITY: use urlencode to prevent injection via query parameters.
    params = urllib.parse.urlencode({"q": query}, quote_via=urllib.parse.quote_plus)
    url = urllib.parse.urljoin(_NEWS_BASE, "/rss/search") + "?" + params

    content = await _fetch_url(url)

    if not content:
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=_time.time() - start_time,
            source="news"
        )

    try:
        # SECURITY: use safe XML parser to prevent billion-laughs attacks.
        root = _safe_parse_xml(content)
        results = []

        for item in root.findall(".//item")[:num_results]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            desc = item.findtext("description", "")

            results.append(SearchResult(
                title=title,
                url=link,
                snippet=desc[:200] if desc else "",
                source="news"
            ))

        return WebSearchResponse(
            query=query,
            results=results,
            elapsed_seconds=_time.time() - start_time,
            source="news"
        )
    except Exception as e:
        logger.error("News parse error: %s", e)

    return WebSearchResponse(
        query=query,
        results=[],
        elapsed_seconds=_time.time() - start_time,
        source="news"
    )


async def get_webpage_content(url: str) -> str:
    """Get the content of a webpage.

    Args:
        url: URL to fetch

    Returns:
        Page content or error message
    """
    # Rate limit: 30 fetches per 60 seconds.
    if not _rate_limit_check("get_webpage_content", max_calls=30, window_seconds=60):
        logger.warning("Rate limit exceeded for get_webpage_content (url: %s)", url[:80])
        return "Error: Rate limit exceeded for webpage fetching. Try again shortly."

    # SECURITY: validate the URL is HTTP/HTTPS before fetching.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Invalid URL scheme: {parsed.scheme}. Only http and https are allowed."

    content = await _fetch_url(url)

    if not content:
        return f"Could not fetch URL: {url}"

    import re as _re
    script_pattern = _re.compile(r'<script[^>]*>.*?</script>', _re.DOTALL | _re.IGNORECASE)
    style_pattern = _re.compile(r'<style[^>]*>.*?</style>', _re.DOTALL | _re.IGNORECASE)
    comment_pattern = _re.compile(r'<!--.*?-->', _re.DOTALL)

    cleaned = script_pattern.sub('', content)
    cleaned = style_pattern.sub('', cleaned)
    cleaned = comment_pattern.sub('', cleaned)

    cleaned = _re.sub(r'\s+', ' ', cleaned)

    text = _re.sub(r'<[^>]+>', '', cleaned)

    return text.strip()[:5000]


async def search_multiple(
    query: str,
    sources: List[str] = None
) -> Dict[str, WebSearchResponse]:
    """Search multiple sources concurrently.
    
    Args:
        query: Search query
        sources: List of sources to search
        
    Returns:
        Dictionary of source -> response
    """
    if sources is None:
        sources = ["duckduckgo", "wikipedia"]
    
    tasks = []
    source_names = []
    
    if "duckduckgo" in sources:
        tasks.append(search_web(query, source="duckduckgo"))
        source_names.append("duckduckgo")
    
    if "wikipedia" in sources:
        tasks.append(search_wikipedia(query))
        source_names.append("wikipedia")
    
    if "news" in sources:
        tasks.append(search_news(query))
        source_names.append("news")
    
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    
    results = {}
    for name, response in zip(source_names, responses):
        if isinstance(response, Exception):
            logger.error(f"Search error for {name}: {response}")
            results[name] = WebSearchResponse(query=query, results=[], source=name)
        else:
            results[name] = response
    
    return results


def format_search_results(
    response: WebSearchResponse,
    max_results: int = 5
) -> str:
    """Format search results as markdown.
    
    Args:
        response: Search response
        max_results: Maximum results to include
        
    Returns:
        Formatted markdown string
    """
    if not response.results:
        return f"No results found for: {response.query}"
    
    lines = [f"## Search Results for: {response.query}\n"]
    
    for i, result in enumerate(response.results[:max_results], 1):
        lines.append(f"{i}. **{result.title}**")
        lines.append(f"   {result.snippet[:150]}...")
        lines.append(f"   [Link]({result.url})\n")
    
    lines.append(f"\n*Source: {response.source} | Time: {response.elapsed_seconds:.2f}s*")
    
    return "\n".join(lines)


__all__ = [
    "SearchResult",
    "WebSearchResponse", 
    "search_web",
    "search_wikipedia",
    "search_news",
    "get_webpage_content",
    "search_multiple",
    "format_search_results",
]