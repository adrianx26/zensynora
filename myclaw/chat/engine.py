"""Multi-agent chat engine for the Web UI (v0.7.1).

One *turn* = one message from you in a channel. The engine decides which
agents answer, streams each answer to every open browser tab through
``myclaw.events`` and stores everything in ``chat.store``.

Who answers
-----------
* ``@name`` mentions pick agents explicitly (``@all`` = every member). A
  mentioned agent that is not yet a member is added to the channel.
* Without mentions the channel's ``reply_mode`` decides:
  ``lead`` (first member), ``all`` (each member in turn, later ones see the
  earlier answers) or ``parallel`` (all at once).

Agent-to-agent talk
-------------------
While an agent works, ``CURRENT_CHANNEL``/``CURRENT_AGENT`` are set, so tool
calls and delegations (``delegate`` tool, swarms) are published as events
and saved as ``event`` messages — the chat shows who asked whom for what.

Memory
------
Each (channel, agent) pair has its own conversation history (``MEMORY_SCOPE``)
while the knowledge base stays shared (``user_id="webui"``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from .. import events
from . import store

logger = logging.getLogger(__name__)

USER_ID = "webui"
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_.-]+)")
_MARKERS = {"[TOOL_CALLS_NONE]", "__TOOL_CALLS_START__", "__TOOL_CALLS_END__",
            "__STREAM_START__", "__STREAM_END__"}
# Event types worth keeping in the channel history (tool_start is transient).
_PERSIST = {"delegation_start", "delegation_end", "tool_end", "job_run"}


def _persist_event(ev: dict[str, Any]) -> None:
    """events listener: save agent-to-agent activity as ``event`` messages."""
    if ev.get("type") not in _PERSIST or not ev.get("channel") or ev.get("_persisted"):
        return
    try:
        sender = ev.get("from") or ev.get("agent") or ""
        msg = store.add_message(ev["channel"], "event", sender, "", meta={
            k: v for k, v in ev.items() if k not in ("channel",)})
        ev["message_id"] = msg["id"]
        ev["_persisted"] = True
    except Exception as e:  # never break the agent because the DB is busy
        logger.debug("could not persist event: %s", e)


events.add_listener(_persist_event)


def memory_scope(channel_id: str, agent: str) -> str:
    return f"ch-{channel_id}-{store.slug(agent)}"


class ChatEngine:
    def __init__(self, registry: Callable[[], dict[str, Any]]):
        self._registry = registry
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ── public API ───────────────────────────────────────────────────────────
    def agents(self) -> list[str]:
        return list(self._registry().keys())

    def busy(self, channel_id: str) -> bool:
        t = self._tasks.get(channel_id)
        return bool(t and not t.done())

    async def post(self, channel_id: str, text: str, sender: str = "you") -> dict[str, Any]:
        """Store your message, broadcast it and start the agents' turn in the background."""
        text = text.strip()
        if not text:
            raise ValueError("Empty message")
        ch = await asyncio.to_thread(store.get_channel, channel_id)
        if ch is None:
            raise KeyError(channel_id)
        msg = await asyncio.to_thread(store.add_message, channel_id, "user", sender, text)
        events.publish(channel_id, {"type": "message", "message": msg})
        task = asyncio.create_task(self._run_turn(channel_id, text))
        self._tasks[channel_id] = task
        return msg

    async def stop(self, channel_id: str) -> bool:
        t = self._tasks.get(channel_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def post_as_agent(self, channel_id: str, agent: str, text: str,
                            meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """Show a finished agent message (used by jobs)."""
        msg = await asyncio.to_thread(store.add_message, channel_id, "agent", agent, text, meta or {})
        events.publish(channel_id, {"type": "message", "message": msg})
        return msg

    # ── turn handling ────────────────────────────────────────────────────────
    def resolve_targets(self, ch: dict[str, Any], text: str) -> list[str]:
        known = self._registry()
        members = [m for m in ch["members"] if m in known] or ["default"]
        mentioned = [m for m in _MENTION.findall(text)]
        if any(m.lower() in ("all", "everyone", "channel") for m in mentioned):
            return members
        picked = [m for m in dict.fromkeys(mentioned) if m in known]
        if picked:
            return picked
        if ch["kind"] == "dm" or ch["reply_mode"] == "lead":
            return members[:1]
        return members

    async def _run_turn(self, channel_id: str, text: str) -> None:
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:  # one turn at a time per channel; later messages queue
            ch = await asyncio.to_thread(store.get_channel, channel_id)
            if ch is None:
                return
            targets = self.resolve_targets(ch, text)
            new_members = [t for t in targets if t not in ch["members"]]
            if new_members:
                ch = await asyncio.to_thread(store.update_channel, channel_id,
                                             members=ch["members"] + new_members)
                events.publish(channel_id, {"type": "channel", "channel": ch})
                note = await asyncio.to_thread(store.add_message, channel_id, "system", "",
                                               f"Added {', '.join('@' + m for m in new_members)} to this channel.")
                events.publish(channel_id, {"type": "message", "message": note})

            events.publish(channel_id, {"type": "turn_start", "agents": targets})
            try:
                if ch["reply_mode"] == "parallel" and len(targets) > 1:
                    await asyncio.gather(*(self._answer(ch, a, text, {}) for a in targets))
                else:
                    replies: dict[str, str] = {}
                    for agent in targets:
                        replies[agent] = await self._answer(ch, agent, text, replies)
            except asyncio.CancelledError:
                note = await asyncio.to_thread(store.add_message, channel_id, "system", "", "Stopped.")
                events.publish(channel_id, {"type": "message", "message": note})
                raise
            finally:
                events.publish(channel_id, {"type": "turn_end"})

    def _prompt(self, ch: dict[str, Any], agent: str, text: str, replies: dict[str, str],
                history: list[dict[str, Any]]) -> str:
        """What the agent receives. Group context only when others take part."""
        others = [m for m in ch["members"] if m != agent]
        if ch["kind"] == "dm" or not others:
            return text
        lines = [f"[Group chat #{ch['name']}. You are @{agent}. Other agents here: "
                 f"{', '.join('@' + o for o in others)}. Reply as @{agent} only; don't repeat what "
                 f"others said. To hand a task to another agent use the delegate tool.]"]
        # What other agents said since this agent last spoke (earlier turns).
        recent: list[str] = []
        for m in reversed(history):
            if m["role"] == "agent" and m["sender"] == agent:
                break
            if m["role"] == "agent" and m["sender"] != agent and m["text"]:
                recent.append(f"@{m['sender']}: {events.short(m['text'], 600)}")
            if len(recent) >= 6:
                break
        if recent:
            lines.append("Earlier in this channel:\n" + "\n".join(reversed(recent)))
        if replies:
            lines.append("Answers so far to this message:\n" + "\n".join(
                f"@{a}: {events.short(r, 1200)}" for a, r in replies.items()))
        lines.append(f"User: {text}")
        return "\n\n".join(lines)

    async def _answer(self, ch: dict[str, Any], agent_name: str, text: str,
                      replies: dict[str, str]) -> str:
        from ..agent import MEMORY_SCOPE

        channel_id = ch["id"]
        agent = self._registry().get(agent_name)
        if agent is None:
            note = await asyncio.to_thread(store.add_message, channel_id, "system", "",
                                           f"Agent @{agent_name} does not exist (any more).")
            events.publish(channel_id, {"type": "message", "message": note})
            return ""
        history = await asyncio.to_thread(store.list_messages, channel_id, None, 40)
        prompt = self._prompt(ch, agent_name, text, replies, history[:-1])

        msg = await asyncio.to_thread(store.add_message, channel_id, "agent", agent_name, "",
                                      {"streaming": True})
        events.publish(channel_id, {"type": "stream_start", "message": msg})
        tokens = (events.CURRENT_CHANNEL.set(channel_id), events.CURRENT_AGENT.set(agent_name),
                  MEMORY_SCOPE.set(memory_scope(channel_id, agent_name)))
        parts: list[str] = []
        started = time.monotonic()
        error = None
        try:
            async for chunk in agent.stream_think(prompt, user_id=USER_ID):
                if not chunk or chunk in _MARKERS or chunk.startswith('{"tool":'):
                    continue
                parts.append(chunk)
                events.publish(channel_id, {"type": "chunk", "id": msg["id"], "text": chunk})
        except asyncio.CancelledError:
            parts.append(" …[stopped]")
            raise
        except Exception as e:
            logger.exception("Agent %s failed in channel %s", agent_name, channel_id)
            error = str(e)
            parts.append(f"\n\n[Error: {e}]")
        finally:
            for var, tok in zip((events.CURRENT_CHANNEL, events.CURRENT_AGENT, MEMORY_SCOPE), tokens):
                var.reset(tok)
            full = "".join(parts).strip()
            used = getattr(agent, "last_model_used", None) or (getattr(agent, "_provider_name", ""), agent.model)
            meta = {"model": used[1], "provider": used[0], "ms": int((time.monotonic() - started) * 1000)}
            if error:
                meta["error"] = error
            await asyncio.to_thread(store.update_message, msg["id"], full, meta)
            events.publish(channel_id, {"type": "stream_end", "id": msg["id"], "text": full, "meta": meta})
        return full


def to_json(event: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in event.items() if not k.startswith("_")}, default=str)
