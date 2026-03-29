"""
Web Search Tools for Real-time Information

Provides real-time web search capabilities using multiple search backends:
- DuckDuckGo (default)
- Wikipedia
- Web scraping
- News feeds
"""

import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


HTTP_TIMEOUT = 10


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
    """Fetch a URL with async HTTP client."""
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MyClaw/1.0)"
                }
            ) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        logger.error(f"Fetch error for {url}: {e}")
    
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
    import time
    start_time = time.time()
    
    if source == "wikipedia":
        return await search_wikipedia(query, num_results)
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
    
    html = await _fetch_url(url)
    
    if not html:
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=time.time() - start_time,
            source=source
        )
    
    results = _parse_duckduckgo(html, query)
    
    return WebSearchResponse(
        query=query,
        results=results[:num_results],
        elapsed_seconds=time.time() - start_time,
        source=source
    )


async def search_wikipedia(
    query: str,
    num_results: int = 5
) -> WebSearchResponse:
    """Search Wikipedia."""
    import time
    start_time = time.time()
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={encoded_query}&limit={num_results}&format=json"
    
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
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
                        elapsed_seconds=time.time() - start_time,
                        source="wikipedia"
                    )
    except Exception as e:
        logger.error(f"Wikipedia search error: {e}")
    
    return WebSearchResponse(
        query=query,
        results=[],
        elapsed_seconds=time.time() - start_time,
        source="wikipedia"
    )


async def search_news(
    query: str,
    num_results: int = 10
) -> WebSearchResponse:
    """Search for recent news."""
    import time
    start_time = time.time()
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}"
    
    content = await _fetch_url(url)
    
    if not content:
        return WebSearchResponse(
            query=query,
            results=[],
            elapsed_seconds=time.time() - start_time,
            source="news"
        )
    
    import xml.etree.ElementTree as ET
    
    try:
        root = ET.fromstring(content)
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
            elapsed_seconds=time.time() - start_time,
            source="news"
        )
    except Exception as e:
        logger.error(f"News parse error: {e}")
    
    return WebSearchResponse(
        query=query,
        results=[],
        elapsed_seconds=time.time() - start_time,
        source="news"
    )


async def get_webpage_content(url: str) -> str:
    """Get the content of a webpage.
    
    Args:
        url: URL to fetch
        
    Returns:
        Page content or error message
    """
    content = await _fetch_url(url)
    
    if not content:
        return f"Could not fetch URL: {url}"
    
    import re
    script_pattern = re.compile(r'<script[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE)
    style_pattern = re.compile(r'<style[^>]*>.*?</style>', re.DOTALL | re.IGNORECASE)
    comment_pattern = re.compile(r'<!--.*?-->', re.DOTALL)
    
    cleaned = script_pattern.sub('', content)
    cleaned = style_pattern.sub('', cleaned)
    cleaned = comment_pattern.sub('', cleaned)
    
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    text = re.sub(r'<[^>]+>', '', cleaned)
    
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