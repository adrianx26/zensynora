"""In-process activity events (added in v0.7.1).

Lets the Web UI show what agents do *between* user messages: tool calls,
delegations from one agent to another, swarm messages and job runs.

How it works
------------
* Code that runs on behalf of a chat channel sets two context variables:
  ``CURRENT_CHANNEL`` (where events go) and ``CURRENT_AGENT`` (who acts).
  ``asyncio`` copies context into child tasks, so a tool or a delegated agent
  called from inside that code inherits them automatically.
* ``emit(event)`` attaches the channel/agent/time and hands the event to every
  subscriber of that channel plus the global ``"*"`` feed. Nothing is emitted
  when no channel is set, so the CLI, Telegram and tests are unaffected.
* Subscribers are asyncio queues (one per open WebSocket). A full queue drops
  the event for that subscriber instead of blocking the agent.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

CURRENT_CHANNEL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "zensynora_channel", default=None)
CURRENT_AGENT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "zensynora_agent", default=None)

ALL = "*"
_subscribers: dict[str, set[asyncio.Queue]] = {}
# Synchronous listeners (e.g. the chat store persisting activity), called with every event.
_listeners: list[Callable[[dict[str, Any]], None]] = []


def subscribe(channel_id: str = ALL, maxsize: int = 500) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.setdefault(channel_id, set()).add(q)
    return q


def unsubscribe(q: asyncio.Queue, channel_id: str = ALL) -> None:
    subs = _subscribers.get(channel_id)
    if subs:
        subs.discard(q)
        if not subs:
            _subscribers.pop(channel_id, None)


def add_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def publish(channel_id: str, event: dict[str, Any]) -> None:
    """Deliver an event to one channel's subscribers and the global feed."""
    event.setdefault("channel", channel_id)
    event.setdefault("ts", time.time())
    for fn in list(_listeners):
        try:
            fn(event)
        except Exception as e:  # a listener must never break an agent
            logger.debug("event listener failed: %s", e)
    for key in (channel_id, ALL):
        for q in list(_subscribers.get(key, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("event queue full; dropping %s", event.get("type"))


def emit(event: dict[str, Any]) -> None:
    """Publish to the channel of the current context (no-op outside a channel)."""
    channel = CURRENT_CHANNEL.get()
    if not channel:
        return
    event.setdefault("agent", CURRENT_AGENT.get())
    publish(channel, event)


def short(value: Any, limit: int = 300) -> str:
    """Compact text for event payloads."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
