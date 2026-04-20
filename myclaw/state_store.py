"""
State Store — Shared state abstraction for multi-worker deployments.

Phase 6.1: Extracts in-memory globals (_agent_registry, _HOOKS, _rate_limiter,
_user_chat_ids) into a pluggable state store.

Backends:
    - InMemoryStateStore (default): single-process, zero dependencies
    - RedisStateStore (optional): multi-worker shared state when redis-py
      is installed and REDIS_URL is configured.

Usage:
    from myclaw.state_store import get_state_store, StateStore
    store = get_state_store()          # InMemory by default
    store.set_agent_registry(registry) # share agent registry
    store.set_hook(event_type, callback_name)  # track hooks
    store.rate_limit_check(tool_name)  # distributed rate limiting

Configuration (optional):
    Set environment variable ZEN_REDIS_URL to enable Redis backend:
        export ZEN_REDIS_URL="redis://localhost:6379/0"
    Or pass via config:
        config.state_store.backend = "redis"
        config.state_store.redis_url = "redis://localhost:6379/0"
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── StateStore Abstract Base Class ────────────────────────────────────────────

class StateStore(ABC):
    """Abstract base for shared state across workers.

    Implementations must be thread-safe (InMemory uses locking internally;
    Redis is naturally multi-process safe).
    """

    # ── Agent Registry ────────────────────────────────────────────────────────

    @abstractmethod
    def set_agent_registry(self, registry: Dict[str, Any]) -> None:
        """Store the agent registry (names only are synced; objects stay local)."""

    @abstractmethod
    def get_agent_registry_names(self) -> Set[str]:
        """Return registered agent names (suitable for multi-worker discovery)."""

    @abstractmethod
    def get_default_agent_name(self) -> Optional[str]:
        """Return the name of the default agent, if any."""

    # ── Hooks ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def set_hook(self, event_type: str, callback_name: str) -> None:
        """Record that a hook has been registered (metadata only)."""

    @abstractmethod
    def clear_hooks(self, event_type: Optional[str] = None) -> None:
        """Clear hook metadata."""

    @abstractmethod
    def list_hook_events(self) -> Dict[str, List[str]]:
        """Return map of event_type -> list of registered callback names."""

    # ── Rate Limiter ──────────────────────────────────────────────────────────

    @abstractmethod
    def rate_limit_add(self, tool_name: str, max_calls: int, window: int) -> None:
        """Initialise or update rate-limit bucket config for a tool."""

    @abstractmethod
    def rate_limit_check(self, tool_name: str, max_calls: int, window: int) -> bool:
        """Token-bucket check. Returns True if call is allowed."""

    @abstractmethod
    def rate_limit_get_remaining(self, tool_name: str, max_calls: int, window: int) -> int:
        """Remaining calls in current window."""

    # ── User Chat IDs ─────────────────────────────────────────────────────────

    @abstractmethod
    def set_chat_id(self, user_id: str, chat_id: int) -> None:
        """Store user_id -> chat_id mapping."""

    @abstractmethod
    def get_chat_id(self, user_id: str) -> Optional[int]:
        """Retrieve chat_id for user_id."""

    @abstractmethod
    def get_all_chat_ids(self) -> Dict[str, int]:
        """Return full mapping."""

    # ── Notification Callback ─────────────────────────────────────────────────

    @abstractmethod
    def set_notification_callback(self, callback: Optional[Callable]) -> None:
        """Store notification callback (local only; Redis backend logs a warning)."""

    @abstractmethod
    def get_notification_callback(self) -> Optional[Callable]:
        """Retrieve notification callback."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    def close(self) -> None:
        """Release resources."""


# ── InMemoryStateStore (default, zero dependencies) ───────────────────────────

class InMemoryStateStore(StateStore):
    """Single-process in-memory state store.

    This is the default backend and behaves exactly like the pre-Phase 6.1
    module-level globals, ensuring 100% backward compatibility.
    """

    def __init__(self):
        self._registry: Dict[str, Any] = {}
        self._hooks: Dict[str, List[str]] = defaultdict(list)
        self._rate_limits: Dict[str, tuple] = {}   # tool -> (timestamps[], max, window)
        self._chat_ids: Dict[str, int] = {}
        self._notification_callback: Optional[Callable] = None
        self._closed = False

    # Registry
    def set_agent_registry(self, registry: Dict[str, Any]) -> None:
        self._registry = registry

    def get_agent_registry_names(self) -> Set[str]:
        return set(self._registry.keys())

    def get_default_agent_name(self) -> Optional[str]:
        return "default" if "default" in self._registry else None

    # Hooks
    def set_hook(self, event_type: str, callback_name: str) -> None:
        if callback_name not in self._hooks[event_type]:
            self._hooks[event_type].append(callback_name)

    def clear_hooks(self, event_type: Optional[str] = None) -> None:
        if event_type:
            self._hooks[event_type] = []
        else:
            self._hooks.clear()

    def list_hook_events(self) -> Dict[str, List[str]]:
        return dict(self._hooks)

    # Rate Limiter (token bucket with per-tool lists)
    def _ensure_bucket(self, tool_name: str, max_calls: int, window: int):
        if tool_name not in self._rate_limits:
            self._rate_limits[tool_name] = ([], max_calls, window)
        else:
            # Update config if changed
            ts, _, _ = self._rate_limits[tool_name]
            self._rate_limits[tool_name] = (ts, max_calls, window)

    def rate_limit_add(self, tool_name: str, max_calls: int, window: int) -> None:
        self._ensure_bucket(tool_name, max_calls, window)

    def rate_limit_check(self, tool_name: str, max_calls: int, window: int) -> bool:
        self._ensure_bucket(tool_name, max_calls, window)
        now = time.time()
        timestamps, mc, w = self._rate_limits[tool_name]
        # Prune old timestamps
        timestamps[:] = [t for t in timestamps if now - t < w]
        if len(timestamps) >= mc:
            return False
        timestamps.append(now)
        return True

    def rate_limit_get_remaining(self, tool_name: str, max_calls: int, window: int) -> int:
        self._ensure_bucket(tool_name, max_calls, window)
        now = time.time()
        timestamps, mc, w = self._rate_limits[tool_name]
        current = len([t for t in timestamps if now - t < w])
        return max(0, mc - current)

    # Chat IDs
    def set_chat_id(self, user_id: str, chat_id: int) -> None:
        self._chat_ids[user_id] = chat_id

    def get_chat_id(self, user_id: str) -> Optional[int]:
        return self._chat_ids.get(user_id)

    def get_all_chat_ids(self) -> Dict[str, int]:
        return dict(self._chat_ids)

    # Notification callback
    def set_notification_callback(self, callback: Optional[Callable]) -> None:
        self._notification_callback = callback

    def get_notification_callback(self) -> Optional[Callable]:
        return self._notification_callback

    def close(self) -> None:
        self._closed = True


# ── RedisStateStore (optional, multi-worker) ──────────────────────────────────

class RedisStateStore(StateStore):
    """Redis-backed state store for multi-worker deployments.

    Requires ``redis>=4.0`` and a running Redis server.

    Limitations:
        - Agent objects themselves cannot be serialised; only names are stored.
          Each worker rebuilds its own Agent instances from config.
        - Hook callbacks are functions and cannot be serialised; only metadata
          (callback names) is stored. Workers must register callbacks locally.
        - Notification callbacks are stored locally (not in Redis).
    """

    def __init__(self, redis_url: str, key_prefix: str = "zensynora:state:"):
        try:
            import redis as _redis
        except ImportError as exc:
            raise ImportError(
                "RedisStateStore requires 'redis' package. "
                "Install: pip install redis>=4.0"
            ) from exc

        self._redis = _redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._notification_callback: Optional[Callable] = None
        self._closed = False
        logger.info(f"RedisStateStore initialised: {redis_url} (prefix={key_prefix})")

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    # Registry
    def set_agent_registry(self, registry: Dict[str, Any]) -> None:
        names = list(registry.keys())
        self._redis.set(self._key("registry"), json.dumps(names))

    def get_agent_registry_names(self) -> Set[str]:
        raw = self._redis.get(self._key("registry"))
        if raw:
            return set(json.loads(raw))
        return set()

    def get_default_agent_name(self) -> Optional[str]:
        names = self.get_agent_registry_names()
        return "default" if "default" in names else (next(iter(names)) if names else None)

    # Hooks
    def set_hook(self, event_type: str, callback_name: str) -> None:
        key = self._key(f"hooks:{event_type}")
        # Use Redis set for uniqueness
        self._redis.sadd(key, callback_name)

    def clear_hooks(self, event_type: Optional[str] = None) -> None:
        if event_type:
            self._redis.delete(self._key(f"hooks:{event_type}"))
        else:
            for k in self._redis.scan_iter(match=self._key("hooks:*")):
                self._redis.delete(k)

    def list_hook_events(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        prefix_len = len(self._key("hooks:"))
        for k in self._redis.scan_iter(match=self._key("hooks:*")):
            event = k[prefix_len:]
            result[event] = list(self._redis.smembers(k))
        return result

    # Rate Limiter (sliding window using Redis sorted sets)
    def _rl_key(self, tool_name: str) -> str:
        return self._key(f"ratelimit:{tool_name}")

    def rate_limit_add(self, tool_name: str, max_calls: int, window: int) -> None:
        # Config stored as a simple string for reference
        self._redis.hset(self._key("ratelimit_config"), tool_name, json.dumps([max_calls, window]))

    def rate_limit_check(self, tool_name: str, max_calls: int, window: int) -> bool:
        key = self._rl_key(tool_name)
        now = time.time()
        window_start = now - window
        pipe = self._redis.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Count current entries
        pipe.zcard(key)
        # Add current timestamp
        pipe.zadd(key, {str(now): now})
        # Set expiry on the key
        pipe.expire(key, window + 1)
        _, count, _, _ = pipe.execute()
        if count >= max_calls:
            # Rollback: remove the timestamp we just added
            self._redis.zrem(key, str(now))
            return False
        return True

    def rate_limit_get_remaining(self, tool_name: str, max_calls: int, window: int) -> int:
        key = self._rl_key(tool_name)
        now = time.time()
        window_start = now - window
        self._redis.zremrangebyscore(key, 0, window_start)
        count = self._redis.zcard(key)
        return max(0, max_calls - count)

    # Chat IDs
    def set_chat_id(self, user_id: str, chat_id: int) -> None:
        self._redis.hset(self._key("chat_ids"), user_id, str(chat_id))

    def get_chat_id(self, user_id: str) -> Optional[int]:
        val = self._redis.hget(self._key("chat_ids"), user_id)
        return int(val) if val is not None else None

    def get_all_chat_ids(self) -> Dict[str, int]:
        raw = self._redis.hgetall(self._key("chat_ids"))
        return {k: int(v) for k, v in raw.items()}

    # Notification callback (local only)
    def set_notification_callback(self, callback: Optional[Callable]) -> None:
        self._notification_callback = callback
        if callback is not None:
            logger.warning("RedisStateStore: notification callback is local-only; "
                           "other workers will not see it.")

    def get_notification_callback(self) -> Optional[Callable]:
        return self._notification_callback

    def close(self) -> None:
        if not self._closed:
            self._redis.close()
            self._closed = True


# ── Singleton Factory ─────────────────────────────────────────────────────────

_Store_INSTANCE: Optional[StateStore] = None
_Store_LOCK = False  # Simple import-time lock


def get_state_store(config: Any = None) -> StateStore:
    """Return the global StateStore singleton.

    The backend is selected in this priority:
        1. Explicit config: ``config.state_store.backend``
        2. Environment variable: ``ZEN_REDIS_URL``
        3. Default: ``InMemoryStateStore``

    Args:
        config: Optional runtime configuration object. If ``config.state_store.backend``
                is ``"redis"`` and ``config.state_store.redis_url`` is set, Redis is used.

    Returns:
        StateStore instance (singleton).
    """
    global _Store_INSTANCE, _Store_LOCK

    if _Store_INSTANCE is not None:
        return _Store_INSTANCE

    if _Store_LOCK:
        # Prevent re-entrant initialisation
        return InMemoryStateStore()
    _Store_LOCK = True

    backend = "memory"
    redis_url = os.environ.get("ZEN_REDIS_URL", "")

    if config is not None:
        ss_cfg = getattr(config, "state_store", None)
        if ss_cfg is not None:
            backend = getattr(ss_cfg, "backend", backend)
            redis_url = getattr(ss_cfg, "redis_url", redis_url)

    if backend == "redis" and redis_url:
        try:
            _Store_INSTANCE = RedisStateStore(redis_url)
        except Exception as exc:
            logger.error(f"Failed to initialise RedisStateStore: {exc}. "
                         "Falling back to InMemoryStateStore.")
            _Store_INSTANCE = InMemoryStateStore()
    else:
        _Store_INSTANCE = InMemoryStateStore()

    return _Store_INSTANCE


def reset_state_store() -> None:
    """Reset the singleton (useful for testing)."""
    global _Store_INSTANCE, _Store_LOCK
    if _Store_INSTANCE is not None:
        _Store_INSTANCE.close()
    _Store_INSTANCE = None
    _Store_LOCK = False
