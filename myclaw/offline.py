"""Offline mode support — automatic fallback to local LLM providers.

When cloud providers are unreachable (connection error, timeout, or HTTP error),
ZenSynora can automatically fall back to local providers:
    1. Ollama (localhost:11434)
    2. LM Studio (localhost:1234)
    3. llama.cpp (localhost:8080)

Usage:
    from myclaw.offline import get_fallback_provider

    fallback = get_fallback_provider(config)
    if fallback:
        response = await fallback.chat(messages, model)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, List

from .provider import get_provider, BaseLLMProvider

logger = logging.getLogger(__name__)

# Local provider fallback chain (in priority order)
_LOCAL_PROVIDERS = ["ollama", "lmstudio", "llamacpp"]


async def _check_provider_alive(provider: BaseLLMProvider) -> bool:
    """Check if a provider is responsive by sending a minimal health check."""
    try:
        # Try a minimal chat to verify connectivity
        # Use a very short timeout for the health check
        test_messages = [{"role": "user", "content": "hi"}]
        # We can't easily change timeout per-call, so just try and catch
        await provider.chat(test_messages, provider.model if hasattr(provider, "model") else "llama3.2")
        return True
    except Exception as e:
        logger.debug(f"Provider health check failed: {e}")
        return False


def get_fallback_provider(config, prefer_cloud: bool = True) -> Optional[BaseLLMProvider]:
    """Get a fallback local provider if cloud providers are unavailable.

    Args:
        config: AppConfig instance
        prefer_cloud: If True, don't return a fallback unless explicitly needed
                      (this function is meant to be called after a cloud failure)

    Returns:
        A local provider instance, or None if no local provider is available.
    """
    if prefer_cloud:
        # This function should be called after a cloud failure
        pass

    for name in _LOCAL_PROVIDERS:
        try:
            provider = get_provider(config, name)
            logger.info(f"Offline fallback: using local provider '{name}'")
            return provider
        except Exception as e:
            logger.debug(f"Local provider '{name}' not available: {e}")
            continue

    logger.warning("No local LLM provider available for offline fallback")
    return None


class FallbackChatWrapper:
    """Wraps a provider.chat() call with automatic fallback to local providers.

    Usage:
        wrapper = FallbackChatWrapper(agent.provider, agent.config)
        response, tool_calls = await wrapper.chat(messages, model)
    """

    def __init__(self, primary_provider: BaseLLMProvider, config):
        self.primary = primary_provider
        self.config = config
        self._fallback: Optional[BaseLLMProvider] = None
        self._used_fallback = False

    async def chat(self, messages, model=None, stream: bool = False):
        """Chat with automatic fallback on connection errors."""
        request_model = model or getattr(self.primary, "model", "llama3.2")

        try:
            return await self.primary.chat(messages, request_model, stream=stream)
        except (ConnectionError, TimeoutError, OSError) as e:
            # Network-level failure — try fallback
            logger.warning(f"Primary provider failed ({type(e).__name__}): {e}")
            return await self._try_fallback(messages, request_model, stream=stream)
        except Exception as e:
            # Check if it's an HTTP connection error wrapped in another exception
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("connection", "timeout", "unreachable", "refused", "dns")):
                logger.warning(f"Primary provider failed (HTTP): {e}")
                return await self._try_fallback(messages, request_model, stream=stream)
            raise

    async def _try_fallback(self, messages, model, stream: bool = False):
        """Try fallback local providers."""
        if self._fallback is None:
            self._fallback = get_fallback_provider(self.config, prefer_cloud=False)

        if self._fallback is None:
            raise ConnectionError(
                "Primary provider unreachable and no local fallback available.\n"
                "Start Ollama, LM Studio, or llama.cpp server for offline mode."
            )

        self._used_fallback = True
        logger.info(f"Falling back to local provider for model '{model}'")

        # Try to use the same model name, but local providers may need remapping
        fallback_model = self._map_model_for_fallback(model)

        try:
            return await self._fallback.chat(messages, fallback_model, stream=stream)
        except Exception as e:
            logger.error(f"Fallback provider also failed: {e}")
            raise ConnectionError(
                f"Primary provider unreachable. Fallback also failed: {e}\n"
                f"Ensure a local LLM server is running (Ollama: http://localhost:11434, "
                f"LM Studio: http://localhost:1234, llama.cpp: http://localhost:8080)"
            )

    def _map_model_for_fallback(self, model: str) -> str:
        """Map cloud model names to local equivalents."""
        # Common cloud models and their local fallbacks
        local_defaults = {
            "gpt-4o": "llama3.2",
            "gpt-4o-mini": "llama3.2",
            "gpt-4-turbo": "llama3.1",
            "gpt-4": "llama3.1",
            "claude-3-5-sonnet-20241022": "llama3.2",
            "claude-3-opus-20240229": "llama3.1",
            "claude-3-haiku-20240307": "phi3",
            "gemini-1.5-pro": "llama3.1",
            "gemini-1.5-flash": "llama3.2",
            "gemini-2.0-flash": "llama3.2",
            "llama3-70b-8192": "llama3.1",
            "mixtral-8x7b-32768": "mixtral",
        }
        return local_defaults.get(model, model)

    def fallback_was_used(self) -> bool:
        """Return True if the fallback provider was used."""
        return self._used_fallback


def wrap_provider_with_fallback(provider: BaseLLMProvider, config) -> FallbackChatWrapper:
    """Wrap a provider with automatic offline fallback.

    Args:
        provider: The primary (usually cloud) provider
        config: AppConfig instance

    Returns:
        FallbackChatWrapper that delegates to provider with fallback
    """
    return FallbackChatWrapper(provider, config)
