"""
LLM provider layer for MyClaw.

Supported providers
───────────────────
Local
  ollama      – Ollama  (http://localhost:11434)
  lmstudio    – LM Studio  (OpenAI-compat, http://localhost:1234/v1)
  llamacpp    – llama-server  (OpenAI-compat, http://localhost:8080/v1)

Online
  openai      – OpenAI  (gpt-4o, gpt-4-turbo, …)
  anthropic   – Anthropic Claude  (claude-3-5-sonnet, …)
  gemini      – Google Gemini  (gemini-1.5-pro, …)
  groq        – Groq  (llama3-70b-8192, mixtral-8x7b-32768, …)
  openrouter  – OpenRouter  (any model via openrouter.ai)

Select with  agents.defaults.provider  (or per named-agent  provider  field)
in ~/.myclaw/config.json.
"""

from __future__ import annotations

import json as _json
import logging
import asyncio
import time
import hashlib
import threading
from abc import ABC, abstractmethod
from functools import wraps, lru_cache
from typing import List, Dict, Tuple, Optional, AsyncIterator
from collections import OrderedDict

import httpx
import requests

from .semantic_cache import get_semantic_cache, SemanticCache

# Lazy import of TOOL_SCHEMAS to avoid circular import risk
# tools.py imports from many modules, so we import at runtime
_TOOL_SCHEMAS_CACHE = None

def _get_tool_schemas() -> List[Dict]:
    """Lazy import TOOL_SCHEMAS to prevent circular imports."""
    global _TOOL_SCHEMAS_CACHE
    if _TOOL_SCHEMAS_CACHE is None:
        from .tools import TOOL_SCHEMAS
        _TOOL_SCHEMAS_CACHE = TOOL_SCHEMAS
    return _TOOL_SCHEMAS_CACHE


# ── Configuration Constants ──────────────────────────────────────────────────────
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 30.0
DEFAULT_BACKOFF_EXPONENTIAL = 2.0


# ── LRU Cache with TTL Decorator ─────────────────────────────────────────────────

from dataclasses import dataclass, field
from typing import Any, Callable
import functools


@dataclass
class _CacheInfo:
    """Cache statistics similar to functools.lru_cache."""
    hits: int = 0
    misses: int = 0
    maxsize: int = 128
    currsize: int = 0
    ttl: int = 300


class _CacheEntry:
    """A single cache entry with value and expiration time."""
    __slots__ = ['value', 'expires_at']
    
    def __init__(self, value: Any, expires_at: float):
        self.value = value
        self.expires_at = expires_at


class LRUCacheWithTTL:
    """Thread-safe LRU cache with TTL support.
    
    Improvements over basic implementation:
    - Actual thread-safety using RLock
    - Faster key generation (no MD5 overhead)
    - Cache statistics via cache_info()
    - Efficient entry storage using __slots__
    - Automatic cleanup of expired entries during access
    """
    
    def __init__(self, maxsize: int = 128, ttl: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        self._info = _CacheInfo(maxsize=maxsize, ttl=ttl)
    
    def _make_key(self, *args, **kwargs) -> int:
        """Create a fast hashable cache key from arguments.
        
        Uses hash() for speed instead of MD5. Handles unhashable types
        by converting to a hashable representation.
        """
        # Fast path for common cases
        if not kwargs:
            try:
                return hash(args)
            except TypeError:
                pass
        
        # Build a hashable key
        key_parts = []
        for i, arg in enumerate(args):
            if i >= 1:  # Skip self (index 0)
                try:
                    key_parts.append(hash(arg))
                except TypeError:
                    # For unhashable types, use the string representation hash
                    key_parts.append(hash(str(arg)))
        
        if kwargs:
            for k in sorted(kwargs.keys()):
                v = kwargs[k]
                try:
                    key_parts.append((k, hash(v)))
                except TypeError:
                    key_parts.append((k, hash(str(v))))
        
        return hash(tuple(key_parts))
    
    def _cleanup_expired(self):
        """Remove expired entries efficiently."""
        now = time.time()
        expired_keys = [
            k for k, entry in self._cache.items()
            if now > entry.expires_at
        ]
        for k in expired_keys:
            del self._cache[k]
    
    def get(self, key: int) -> Any:
        """Get value from cache if exists and not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            if time.time() > entry.expires_at:
                # Expired - remove it
                del self._cache[key]
                self._info.currsize = len(self._cache)
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._info.hits += 1
            return entry.value
    
    def set(self, key: int, value: Any):
        """Set value in cache with TTL."""
        with self._lock:
            now = time.time()
            
            if key in self._cache:
                # Update existing entry
                self._cache[key] = _CacheEntry(value, now + self.ttl)
                self._cache.move_to_end(key)
            else:
                # Check if we need to evict
                if len(self._cache) >= self.maxsize:
                    # First, try to clean up expired entries
                    self._cleanup_expired()
                    
                    # If still at capacity, evict oldest
                    if len(self._cache) >= self.maxsize:
                        oldest_key = next(iter(self._cache))
                        del self._cache[oldest_key]
                
                self._cache[key] = _CacheEntry(value, now + self.ttl)
            
            self._info.currsize = len(self._cache)
    
    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._info.currsize = 0
    
    def cache_info(self) -> _CacheInfo:
        """Return cache statistics."""
        with self._lock:
            self._info.currsize = len(self._cache)
            return self._info


def lru_cache_with_ttl(maxsize: int = 128, ttl: int = 300):
    """Decorator that adds LRU caching with TTL to an async function.
    
    Args:
        maxsize: Maximum number of entries to cache (default 128)
        ttl: Time-to-live in seconds (default 300 = 5 minutes)
    """
    def decorator(func):
        cache = LRUCacheWithTTL(maxsize=maxsize, ttl=ttl)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key from messages and model
            cache_key = cache._make_key(*args, **kwargs)
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Call the original function
            result = await func(*args, **kwargs)
            
            # Store in cache
            cache.set(cache_key, result)
            return result
        
        # Expose cache management methods on the wrapper
        def clear_cache():
            """Clear all cache entries."""
            cache.clear()
        
        def cache_info() -> _CacheInfo:
            """Return cache statistics (hits, misses, maxsize, currsize, ttl)."""
            return cache.cache_info()
        
        wrapper.clear_cache = clear_cache
        wrapper.cache_info = cache_info
        return wrapper
    return decorator


# Global cache instance for provider chat methods
_provider_chat_cache = LRUCacheWithTTL(maxsize=128, ttl=300)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60  # seconds


# ── HTTP Connection Pool ───────────────────────────────────────────────────────────

class HTTPClientPool:
    """Shared HTTP client with connection pooling."""
    
    _instance: Optional[httpx.AsyncClient] = None
    _timeout: int = DEFAULT_TIMEOUT
    
    @classmethod
    def get_client(cls, timeout: int = None) -> httpx.AsyncClient:
        """Get or create the shared async client with connection pooling."""
        if timeout is not None:
            cls._timeout = timeout
        
        if cls._instance is None:
            cls._instance = httpx.AsyncClient(
                timeout=httpx.Timeout(cls._timeout),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                    keepalive_expiry=30.0
                ),
                http2=True  # Enable HTTP/2 for better multiplexing
            )
        return cls._instance
    
    @classmethod
    async def close(cls):
        """Close the shared client."""
        if cls._instance is not None:
            await cls._instance.aclose()
            cls._instance = None


# ── Retry Logic with Exponential Backoff ─────────────────────────────────────────

def retry_with_backoff(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BACKOFF_BASE,
    max_delay: float = DEFAULT_BACKOFF_MAX,
    exponential_base: float = DEFAULT_BACKOFF_EXPONENTIAL,
    retriable_exceptions: tuple = (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)
):
    """Decorator for retrying async functions with exponential backoff."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retriable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (exponential_base ** attempt), max_delay)
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"All {max_retries + 1} attempts failed")
            raise last_exception
        return wrapper
    return decorator


# ── Helpers ────────────────────────────────────────────────────────────────────

def _openai_tool_calls_to_dict(tool_calls) -> Optional[List[Dict]]:
    """Convert openai SDK ToolCall objects to the dict format agent.py expects."""
    if not tool_calls:
        return None
    result = []
    for tc in tool_calls:
        args = tc.function.arguments
        if isinstance(args, str):
            try:
                args = _json.loads(args)
            except Exception:
                args = {}
        result.append({
            "function": {
                "name": tc.function.name,
                "arguments": args,
            }
        })
    return result or None


# ── Abstract Base ──────────────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """All providers implement this interface."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict],
        model: str,
        stream: bool = False,
    ) -> Tuple[str, Optional[List[Dict]]]:
        """Send messages to the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            model: Model identifier string.
            stream: If True, return an async iterator yielding content chunks.

        Returns:
            If stream=False: (response_text, tool_calls) - tool_calls is None when no tools were invoked.
            If stream=True: (async_iterator, None) - iterator yields content chunks for streaming.
        """

    async def stream_chat(
        self,
        messages: List[Dict],
        model: str,
    ) -> AsyncIterator[str]:
        """Stream chat response as an async iterator.
        
        Default implementation falls back to non-streaming and yields chunks.
        Override this in subclasses for true streaming support.
        """
        response, _ = await self.chat(messages, model, stream=False)
        # Yield in small chunks to simulate streaming
        for i in range(0, len(response), 4):
            yield response[i:i+4]
            await asyncio.sleep(0.01)


# ── Local Providers ────────────────────────────────────────────────────────────

class OllamaProvider(BaseLLMProvider):
    """Ollama native API (http://localhost:11434)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            self.base_url = config.providers.ollama.base_url
        except Exception:
            self.base_url = "http://localhost:11434"
        self.timeout = timeout

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def chat(self, messages, model="llama3.2", stream: bool = False):
        # Check semantic cache first (skip for streaming)
        if not stream:
            cache = get_semantic_cache()
            cached = cache.get(messages, model)
            if cached:
                logger.debug(f"Semantic cache hit for {model}")
                return cached
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "tools": _get_tool_schemas(),
        }
        try:
            # Use pooled client instead of creating new one
            client = HTTPClientPool.get_client(self.timeout)
            
            if stream:
                # Streaming response
                async def generate():
                    async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                        r.raise_for_status()
                        async for line in r.aiter_lines():
                            if line.strip():
                                try:
                                    data = _json.loads(line)
                                    if "message" in data and "content" in data["message"]:
                                        content = data["message"]["content"]
                                        if content:
                                            yield content
                                except _json.JSONDecodeError:
                                    continue
                return generate(), None
            
            r = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            r.raise_for_status()
            msg = r.json()["message"]
            tool_calls = msg.get("tool_calls") or None
            result = (msg.get("content", ""), tool_calls)
            
            # Cache the response
            cache = get_semantic_cache()
            cache.set(messages, model, result[0], result[1])
            
            return result
        except httpx.TimeoutException:
            raise TimeoutError(f"Ollama request timed out after {self.timeout}s")
        except httpx.ConnectError as e:
            raise ConnectionError(f"Could not connect to Ollama at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama HTTP error: {e}") from e

    async def stream_chat(self, messages: List[Dict], model: str = "llama3.2") -> AsyncIterator[str]:
        """Stream chat response from Ollama."""
        async for chunk in await self.chat(messages, model, stream=True):
            yield chunk


class OpenAICompatProvider(BaseLLMProvider):
    """
    Generic OpenAI-compatible provider.

    Works for: LM Studio, llama.cpp server, Groq, OpenRouter — all expose
    the same /chat/completions endpoint with OpenAI request/response schema.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for this provider.\n"
                "Install it with:  pip install openai"
            )
        self.client = OpenAI(
            api_key=api_key or "no-key",
            base_url=base_url,
            timeout=timeout,
            default_headers=extra_headers or {},
        )

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def chat(self, messages, model="gpt-4o-mini", stream: bool = False):
        # Check semantic cache first (skip for streaming)
        if not stream:
            cache = get_semantic_cache()
            cached = cache.get(messages, model)
            if cached:
                logger.debug(f"Semantic cache hit for {model}")
                return cached
        
        if stream:
            # Streaming response
            async def generate():
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=_get_tool_schemas(),
                    stream=True,
                )
                for chunk in response:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            yield delta.content
            return generate(), None
        
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=_get_tool_schemas(),
        )
        msg = response.choices[0].message
        tool_calls = _openai_tool_calls_to_dict(msg.tool_calls)
        result = (msg.content or "", tool_calls)
        
        # Cache the response
        cache = get_semantic_cache()
        cache.set(messages, model, result[0], result[1])
        
        return result

    async def stream_chat(self, messages: List[Dict], model: str = "gpt-4o-mini") -> AsyncIterator[str]:
        """Stream chat response from OpenAI-compatible providers."""
        async for chunk in await self.chat(messages, model, stream=True):
            yield chunk


class LMStudioProvider(OpenAICompatProvider):
    """LM Studio local server (OpenAI-compat, default http://localhost:1234/v1)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.lmstudio
            base_url = cfg.base_url
            api_key  = cfg.api_key.get_secret_value()
        except Exception:
            base_url = "http://localhost:1234/v1"
            api_key  = "lm-studio"
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class LlamaCppProvider(OpenAICompatProvider):
    """llama-server (llama.cpp) — OpenAI-compat, default http://localhost:8080/v1."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.llamacpp
            base_url = cfg.base_url
            api_key  = cfg.api_key.get_secret_value()
        except Exception:
            base_url = "http://localhost:8080/v1"
            api_key  = "no-key"
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


# ── Online Providers ───────────────────────────────────────────────────────────

class OpenAIProvider(OpenAICompatProvider):
    """OpenAI cloud (gpt-4o, gpt-4-turbo, gpt-3.5-turbo, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.openai
            api_key  = cfg.api_key.get_secret_value()
            base_url = cfg.base_url
        except Exception:
            api_key  = ""
            base_url = "https://api.openai.com/v1"
        if not api_key:
            raise ValueError("openai.api_key is not set in config.")
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class GroqProvider(OpenAICompatProvider):
    """Groq cloud (llama3-70b-8192, mixtral-8x7b-32768, gemma-7b-it, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.groq
            api_key  = cfg.api_key.get_secret_value()
            base_url = cfg.base_url
        except Exception:
            api_key  = ""
            base_url = "https://api.groq.com/openai/v1"
        if not api_key:
            raise ValueError("groq.api_key is not set in config.")
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter cloud — routes to 100+ models via a single API."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.openrouter
            api_key   = cfg.api_key.get_secret_value()
            base_url  = cfg.base_url
            site_url  = cfg.site_url
            site_name = cfg.site_name
        except Exception:
            api_key   = ""
            base_url  = "https://openrouter.ai/api/v1"
            site_url  = ""
            site_name = ""
        if not api_key:
            raise ValueError("openrouter.api_key is not set in config.")
        headers = {}
        if site_url:
            headers["X-OpenRouter-Site-URL"] = site_url
        if site_name:
            headers["X-OpenRouter-Title"] = site_name
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, extra_headers=headers)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude (claude-3-5-sonnet-20241022, claude-3-haiku-20240307, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required.\n"
                "Install it with:  pip install anthropic"
            )
        try:
            api_key = config.providers.anthropic.api_key.get_secret_value()
        except Exception:
            api_key = ""
        if not api_key:
            raise ValueError("anthropic.api_key is not set in config.")
        self.client  = AsyncAnthropic(api_key=api_key)
        self.timeout = timeout

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def chat(self, messages, model="claude-3-5-sonnet-20241022", stream: bool = False):
        # Check semantic cache first (skip for streaming)
        if not stream:
            cache = get_semantic_cache()
            cached = cache.get(messages, model)
            if cached:
                logger.debug(f"Semantic cache hit for {model}")
                return cached
        
        # Anthropic separates the system prompt from the conversation
        system_parts = []
        conv_messages  = []
        for m in messages:
            role = m["role"]
            content = m.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                conv_messages.append({"role": role, "content": content})
            elif role == "tool":
                # Append tool result as a user turn
                conv_messages.append({"role": "user", "content": f"[tool result] {content}"})
        system_content = "\n".join(system_parts)

        # Build Anthropic tool definitions
        ant_tools = []
        for ts in _get_tool_schemas():
            f = ts["function"]
            ant_tools.append({
                "name":        f["name"],
                "description": f.get("description", ""),
                "input_schema": f.get("parameters", {"type": "object", "properties": {}}),
            })

        kwargs = dict(
            model=model,
            max_tokens=4096,
            messages=conv_messages,
            tools=ant_tools,
        )
        if system_content.strip():
            kwargs["system"] = system_content.strip()

        if stream:
            # Streaming response
            async def generate():
                async with self.client.messages.stream(**kwargs) as stream_response:
                    async for text in stream_response.text_stream:
                        yield text
            return generate(), None

        response = await self.client.messages.create(**kwargs)

        text_parts  = []
        tool_calls  = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append({
                    "function": {
                        "name":      block.name,
                        "arguments": args,
                    }
                })

        result = ("\n".join(text_parts), (tool_calls or None))
        
        # Cache the response
        cache = get_semantic_cache()
        cache.set(messages, model, result[0], result[1])
        
        return result

    async def stream_chat(self, messages: List[Dict], model: str = "claude-3-5-sonnet-20241022") -> AsyncIterator[str]:
        """Stream chat response from Anthropic."""
        async for chunk in await self.chat(messages, model, stream=True):
            yield chunk


class GeminiProvider(BaseLLMProvider):
    """Google Gemini (gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "The 'google-generativeai' package is required.\n"
                "Install it with:  pip install google-generativeai"
            )
        try:
            api_key = config.providers.gemini.api_key.get_secret_value()
        except Exception:
            api_key = ""
        if not api_key:
            raise ValueError("gemini.api_key is not set in config.")
        genai.configure(api_key=api_key)
        self._genai    = genai
        self.timeout   = timeout

    def _build_tools(self):
        """Convert _get_tool_schemas() to Gemini FunctionDeclaration list."""
        from google.generativeai.types import content_types
        declarations = []
        for ts in _get_tool_schemas():
            f = ts["function"]
            declarations.append(
                self._genai.protos.FunctionDeclaration(
                    name=f["name"],
                    description=f.get("description", ""),
                    parameters=self._genai.protos.Schema(
                        type=self._genai.protos.Type.OBJECT,
                        properties={
                            k: self._genai.protos.Schema(
                                type=self._genai.protos.Type.STRING,
                                description=v.get("description", ""),
                            )
                            for k, v in f.get("parameters", {}).get("properties", {}).items()
                        },
                        required=f.get("parameters", {}).get("required", []),
                    ),
                )
            )
        return [self._genai.protos.Tool(function_declarations=declarations)]

    async def chat(self, messages, model="gemini-1.5-flash", stream: bool = False):
        # Check semantic cache first (skip for streaming)
        if not stream:
            cache = get_semantic_cache()
            cached = cache.get(messages, model)
            if cached:
                logger.debug(f"Semantic cache hit for {model}")
                return cached
        
        system_parts = []
        history      = []
        last_user    = None

        for m in messages:
            role    = m["role"]
            content = m.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                last_user = content
                history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                history.append({"role": "model", "parts": [content]})
            elif role == "tool":
                history.append({"role": "user", "parts": [f"[tool result] {content}"]})

        gen_model = self._genai.GenerativeModel(
            model_name=model,
            system_instruction="\n".join(system_parts) if system_parts else None,
            tools=self._build_tools(),
        )

        chat_session = gen_model.start_chat(history=history[:-1] if history else [])
        
        if stream:
            # Streaming response
            async def generate():
                response = await chat_session.send_message_async(last_user or "", stream=True)
                async for chunk in response:
                    for part in chunk.parts:
                        if hasattr(part, "text") and part.text:
                            yield part.text
            return generate(), None

        response     = await chat_session.send_message_async(last_user or "")

        text_parts = []
        tool_calls = []

        for part in response.parts:
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                tool_calls.append({
                    "function": {
                        "name":      fc.name,
                        "arguments": dict(fc.args),
                    }
                })

        result = ("\n".join(text_parts), (tool_calls or None))
        
        # Cache the response
        cache = get_semantic_cache()
        cache.set(messages, model, result[0], result[1])
        
        return result

    async def stream_chat(self, messages: List[Dict], model: str = "gemini-1.5-flash") -> AsyncIterator[str]:
        """Stream chat response from Gemini."""
        async for chunk in await self.chat(messages, model, stream=True):
            yield chunk


# ── Provider Factory ──────────────────────────────────────────────────────────

_PROVIDER_MAP = {
    "ollama":     OllamaProvider,
    "lmstudio":   LMStudioProvider,
    "llamacpp":   LlamaCppProvider,
    "openai":     OpenAIProvider,
    "anthropic":  AnthropicProvider,
    "gemini":     GeminiProvider,
    "groq":       GroqProvider,
    "openrouter": OpenRouterProvider,
}

SUPPORTED_PROVIDERS = list(_PROVIDER_MAP.keys())

# Lazy provider caching - only create provider instance when needed
_provider_cache: dict = {}
_provider_lock = threading.Lock()


def get_provider(config, provider_name: str = "ollama") -> BaseLLMProvider:
    """Return an initialised provider instance for *provider_name*.

    Raises:
        ValueError  – unknown provider name
        ImportError – required SDK not installed
        ValueError  – API key missing for cloud providers
    """
    name = (provider_name or "ollama").lower().strip()
    
    # Thread-safe provider cache access
    with _provider_lock:
        # Return cached provider if already created
        if name in _provider_cache:
            return _provider_cache[name]
        
        cls  = _PROVIDER_MAP.get(name)
        if cls is None:
            raise ValueError(
                f"Unknown provider '{name}'. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
            )
        logger.debug(f"Initialising provider: {name}")
        provider = cls(config)
        _provider_cache[name] = provider
        return provider


def clear_provider_cache():
    """Clear the provider cache (useful for testing or config changes)."""
    global _provider_cache
    _provider_cache = {}


# ── Legacy alias (keeps old import `from .provider import LLMProvider` working) ─

LLMProvider = OllamaProvider