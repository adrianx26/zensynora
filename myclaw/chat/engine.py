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
_PERSIST = {"delegation_start", "delegation_end", "tool_end", "job_run", "agent_created"}


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

    async def _open_channel(self, channel_id: str) -> dict[str, Any]:
        ch = await asyncio.to_thread(store.get_channel, channel_id)
        if ch is None:
            raise KeyError(channel_id)
        if ch["kind"] == "dm" and ch["members"][0] not in self._registry():  # v0.7.5
            raise ValueError(f"@{ch['members'][0]} no longer exists (renamed or deleted in Settings). "
                             "Delete this conversation on the Channels page.")
        return ch

    async def post(self, channel_id: str, text: str, sender: str = "you") -> dict[str, Any]:
        """Store your message, broadcast it and start the agents' turn in the background."""
        text = text.strip()
        if not text:
            raise ValueError("Empty message")
        await self._open_channel(channel_id)
        msg = await asyncio.to_thread(store.add_message, channel_id, "user", sender, text)
        events.publish(channel_id, {"type": "message", "message": msg})
        task = asyncio.create_task(self._run_turn(channel_id, text))
        self._tasks[channel_id] = task
        return msg

    async def rerun(self, channel_id: str, message_id: int) -> None:
        """Answer one of your messages again (after "edit and resend").

        The message is not stored a second time. The agents' memory of this
        channel is rebuilt from the messages before it first, so they don't
        see the old version of the question as the last turn.
        """
        await self._open_channel(channel_id)
        msg = await asyncio.to_thread(store.get_message, message_id)
        if msg is None or msg["channel_id"] != channel_id or msg["role"] != "user":
            raise KeyError(message_id)
        task = asyncio.create_task(self._run_turn(channel_id, msg["text"], anchor=msg))
        self._tasks[channel_id] = task

    async def regenerate(self, channel_id: str, message_id: int) -> dict[str, Any]:
        """Write one agent's answer again, in the same place (same message id).

        Returns the message of yours that is answered again.
        """
        await self._open_channel(channel_id)
        answer = await asyncio.to_thread(store.get_message, message_id)
        if answer is None or answer["channel_id"] != channel_id or answer["role"] != "agent":
            raise KeyError(message_id)
        before = await asyncio.to_thread(store.all_messages, channel_id, message_id)
        question = next((m for m in reversed(before) if m["role"] == "user"), None)
        if question is None:
            raise ValueError("This answer does not answer a message of yours, so it can't be regenerated.")
        # Activity of the old answer (tool calls, delegations) goes with it.
        stale = []
        for m in await asyncio.to_thread(store.messages_after, channel_id, message_id):
            if m["role"] != "event":
                break
            meta = m["meta"] or {}
            if answer["sender"] in (m["sender"], meta.get("agent"), meta.get("from")):
                stale.append(m["id"])
        if stale:
            gone = await asyncio.to_thread(store.delete_messages, channel_id, stale)
            events.publish(channel_id, {"type": "messages_deleted", "ids": gone})
        task = asyncio.create_task(self._run_turn(channel_id, question["text"], anchor=question,
                                                  only=answer["sender"], reuse=answer))
        self._tasks[channel_id] = task
        return question

    async def resync_memory(self, channel_id: str, agents: list[str] | None = None,
                            before_id: int | None = None) -> None:
        """Rebuild the agents' private memory of this channel from the chat history.

        Used after messages are edited, deleted or answered again, so an agent
        remembers the conversation as it is shown (tool details of earlier
        turns are not kept). ``before_id`` limits it to messages before that id.
        """
        from ..agent import MEMORY_SCOPE

        ch = await asyncio.to_thread(store.get_channel, channel_id)
        if ch is None:
            return
        msgs = await asyncio.to_thread(store.all_messages, channel_id, before_id)
        registry = self._registry()
        names = list(dict.fromkeys(agents if agents is not None else ch["members"]))
        for name in names:
            get_mem = getattr(registry.get(name), "_get_memory", None)
            if get_mem is None:
                continue
            turns = memory_turns(msgs, name)[-MEMORY_REPLAY:]
            token = MEMORY_SCOPE.set(memory_scope(channel_id, name))
            try:
                mem = await get_mem(USER_ID)
                await mem.clear()
                add = getattr(mem, "add", None)
                if add is not None:
                    for role, text in turns:
                        await add(role, text)
                    flush = getattr(mem, "flush", None)
                    if flush is not None:
                        await flush()
            except Exception as e:  # memory is a convenience; never break the chat
                logger.warning("Could not rebuild memory of @%s in %s: %s", name, channel_id, e)
            finally:
                MEMORY_SCOPE.reset(token)

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
        # v0.7.5: @executor finds "Executor" (mentions ignore upper/lower case)
        picked, _ = store.canonical_members(list(dict.fromkeys(mentioned)), list(known))
        if picked:
            return picked
        if ch["kind"] == "dm" or ch["reply_mode"] == "lead":
            return members[:1]
        return members

    async def _run_turn(self, channel_id: str, text: str, anchor: dict[str, Any] | None = None,
                        only: str | None = None, reuse: dict[str, Any] | None = None) -> None:
        """One turn. ``anchor`` = an existing message of yours answered again
        (edit and resend, regenerate); ``only`` = just this agent answers;
        ``reuse`` = the old answer whose bubble the new answer is written into."""
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:  # one turn at a time per channel; later messages queue
            ch = await asyncio.to_thread(store.get_channel, channel_id)
            if ch is None:
                return
            targets = [only] if only else self.resolve_targets(ch, text)
            new_members = [t for t in targets if t not in ch["members"]]
            if new_members:
                ch = await asyncio.to_thread(store.update_channel, channel_id,
                                             members=ch["members"] + new_members)
                events.publish(channel_id, {"type": "channel", "channel": ch})
                note = await asyncio.to_thread(store.add_message, channel_id, "system", "",
                                               f"Added {', '.join('@' + m for m in new_members)} to this channel.")
                events.publish(channel_id, {"type": "message", "message": note})

            history = None
            replies: dict[str, str] = {}
            if anchor is not None:
                await self.resync_memory(channel_id, list(dict.fromkeys(ch["members"] + targets)), anchor["id"])
                history = (await asyncio.to_thread(store.all_messages, channel_id, anchor["id"]))[-40:]
                if only and ch["reply_mode"] != "parallel":  # what the others answered before it
                    for m in await asyncio.to_thread(store.messages_after, channel_id, anchor["id"]):
                        if m["role"] == "user" or (reuse and m["id"] >= reuse["id"]):
                            break
                        if m["role"] == "agent" and m["sender"] != only and m["text"]:
                            replies[m["sender"]] = m["text"]

            events.publish(channel_id, {"type": "turn_start", "agents": targets})
            try:
                if ch["reply_mode"] == "parallel" and len(targets) > 1:
                    await asyncio.gather(*(self._answer(ch, a, text, {}, history) for a in targets))
                else:
                    for agent in targets:
                        replies[agent] = await self._answer(ch, agent, text, replies, history,
                                                            reuse if agent == only else None)
            except asyncio.CancelledError:
                note = await asyncio.to_thread(store.add_message, channel_id, "system", "", "Stopped.")
                events.publish(channel_id, {"type": "message", "message": note})
                raise
            finally:
                if only and anchor is not None:  # turns after the regenerated one stay remembered
                    try:
                        await self.resync_memory(channel_id, [only])
                    except Exception as e:  # pragma: no cover - resync_memory logs its own errors
                        logger.debug("memory resync after regenerate failed: %s", e)
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
        if ch.get("topic"):  # v0.7.3: the channel's purpose guides every member
            lines.append(f"Channel topic: {ch['topic']}")
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
                      replies: dict[str, str], history: list[dict[str, Any]] | None = None,
                      reuse: dict[str, Any] | None = None) -> str:
        from ..agent import MEMORY_SCOPE

        channel_id = ch["id"]
        agent = self._registry().get(agent_name)
        if agent is None:
            note = await asyncio.to_thread(store.add_message, channel_id, "system", "",
                                           f"Agent @{agent_name} does not exist (any more).")
            events.publish(channel_id, {"type": "message", "message": note})
            return ""
        if history is None:
            history = (await asyncio.to_thread(store.list_messages, channel_id, None, 40))[:-1]
        prompt = self._prompt(ch, agent_name, text, replies, history)

        if reuse is not None:  # regenerate: the new answer replaces the old one in place
            await asyncio.to_thread(store.update_message, reuse["id"], "", {"streaming": True})
            msg = {**reuse, "text": "", "meta": {"streaming": True}}
        else:
            msg = await asyncio.to_thread(store.add_message, channel_id, "agent", agent_name, "",
                                          {"streaming": True})
        events.publish(channel_id, {"type": "stream_start", "message": msg})
        tokens = (events.CURRENT_CHANNEL.set(channel_id), events.CURRENT_AGENT.set(agent_name),
                  MEMORY_SCOPE.set(memory_scope(channel_id, agent_name)))
        parts: list[str] = []
        started = time.monotonic()
        first_at = last_at = None  # v0.7.5: token speed
        error = None
        try:
            async for chunk in agent.stream_think(prompt, user_id=USER_ID):
                if not chunk or chunk in _MARKERS or chunk.startswith('{"tool":'):
                    continue
                last_at = time.monotonic()
                if first_at is None:
                    first_at = last_at
                parts.append(chunk)
                events.publish(channel_id, {"type": "chunk", "id": msg["id"], "text": chunk})
        except asyncio.CancelledError:
            parts.append(" …[stopped]")
            raise
        except Exception as e:
            logger.exception("Agent %s failed in channel %s", agent_name, channel_id)
            from ..llm_errors import describe_llm_error  # v0.7.3

            error = describe_llm_error(e)
            parts.append(f"\n\n[Error] {error}")
        finally:
            for var, tok in zip((events.CURRENT_CHANNEL, events.CURRENT_AGENT, MEMORY_SCOPE), tokens):
                var.reset(tok)
            full = "".join(parts).strip()
            used = getattr(agent, "last_model_used", None) or (getattr(agent, "_provider_name", ""), agent.model)
            meta = {"model": used[1], "provider": used[0], "ms": int((time.monotonic() - started) * 1000)}
            meta.update(token_speed(full, len(parts), started, first_at, last_at))
            if error:
                meta["error"] = error
            await asyncio.to_thread(store.update_message, msg["id"], full, meta)
            events.publish(channel_id, {"type": "stream_end", "id": msg["id"], "text": full, "meta": meta})
        return full


MEMORY_REPLAY = 40  # messages put back into an agent's memory after an edit


def memory_turns(messages: list[dict[str, Any]], agent: str) -> list[tuple[str, str]]:
    """(role, text) pairs an agent remembers from a channel: each message of
    yours that the agent answered, followed by its answer."""
    out: list[tuple[str, str]] = []
    question: str | None = None
    for m in messages:
        if m["role"] == "user":
            question = m["text"]
        elif m["role"] == "agent" and m["sender"] == agent and m["text"] \
                and not (m.get("meta") or {}).get("streaming"):
            if question is not None:
                out.append(("user", question))
                question = None
            out.append(("assistant", m["text"]))
    return out


def estimate_tokens(text: str) -> int:
    """About how many tokens a text is: a short word or a punctuation mark is
    one token, longer words one per ~4 characters (close to common tokenizers)."""
    if not text:
        return 0
    return max(1, sum(1 if not w[0].isalnum() else -(-len(w) // 5) for w in re.findall(r"\w+|[^\w\s]", text)))


def token_speed(text: str, chunks: int, started: float, first_at: float | None,
                last_at: float | None) -> dict[str, Any]:
    """Tokens, tokens per second and time to the first token of an answer (v0.7.5).

    Speed is measured from the first to the last streamed piece, so waiting for
    the model to start (and tool calls before it) do not lower it.
    """
    if not text or first_at is None:
        return {}
    tokens = estimate_tokens(text)
    span = (last_at or first_at) - first_at
    out: dict[str, Any] = {"tokens": tokens, "ttft_ms": int((first_at - started) * 1000)}
    if span >= 0.2 and tokens > 1:
        out["tps"] = round(tokens / span, 1)
    return out


def to_json(event: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in event.items() if not k.startswith("_")}, default=str)
