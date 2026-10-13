"""Chat channels API + WebSocket (added in v0.7.1).

REST
----
GET    /api/channels                    channels with last message preview
POST   /api/channels                    create {name, members, topic, reply_mode}
GET    /api/channels/dm/{agent}         direct channel with one agent (created on demand)
PATCH  /api/channels/{id}               rename, change members/topic/reply mode
DELETE /api/channels/{id}               delete channel, messages and its memory files
GET    /api/channels/{id}/messages      history (?before=<id>&limit=100)
DELETE /api/channels/{id}/messages      clear the channel
POST   /api/channels/{id}/messages      post a message (same as over the socket)
POST   /api/channels/{id}/stop          stop the agents answering right now
GET    /api/activity                    latest agent-to-agent events, all channels

WebSocket  /ws/channels/{id}
----------------------------
Client → server: {"type": "message", "text": "..."} | {"type": "stop"} | "__ping__"
Server → client: JSON events — message, stream_start, chunk, stream_end,
turn_start, turn_end, tool_start, tool_end, delegation_start, delegation_end,
job_run, channel, error.

Reading and chatting follow the same rule as the original chat socket (open
to whoever can reach the Web UI, with the two-factor code when MFA is on);
posting over REST, deleting and clearing need the settings guard.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from myclaw import events
from myclaw.chat import store
from myclaw.chat.engine import USER_ID, ChatEngine, memory_scope, to_json
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

_registry_getter: Callable[[], dict[str, Any]] | None = None
_engine: ChatEngine | None = None


def set_registry_getter(fn: Callable[[], dict[str, Any]]) -> None:
    global _registry_getter, _engine
    _registry_getter = fn
    _engine = ChatEngine(fn)


def engine() -> ChatEngine:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Chat engine not ready")
    return _engine


def _get_channel(channel_id: str) -> dict[str, Any] | None:
    """Channel by id; ``general`` is created on first use (fresh installs)."""
    ch = store.get_channel(channel_id)
    if ch is None and channel_id == "general":
        ch = store.ensure_default_channel()
    return ch


def _check_agents(names: list[str]) -> None:
    known = set(engine().agents())
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown agent(s): {', '.join(unknown)}")


class ChannelIn(BaseModel):
    name: str
    members: list[str] = ["default"]
    topic: str = ""
    reply_mode: str = "lead"


class ChannelPatch(BaseModel):
    name: str | None = None
    members: list[str] | None = None
    topic: str | None = None
    reply_mode: str | None = None


class PostIn(BaseModel):
    text: str


@router.get("/api/channels")
async def list_channels():
    await asyncio.to_thread(store.ensure_default_channel)
    chans = await asyncio.to_thread(store.list_channels)
    for c in chans:
        c["busy"] = engine().busy(c["id"])
    return {"channels": chans, "agents": engine().agents(), "reply_modes": list(store.REPLY_MODES)}


@router.post("/api/channels")
async def create_channel(body: ChannelIn):
    _check_agents(body.members)
    try:
        return await asyncio.to_thread(store.create_channel, body.name, body.members, body.topic, body.reply_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/channels/dm/{agent}")
async def dm_channel(agent: str):
    _check_agents([agent])
    return await asyncio.to_thread(store.dm_channel, agent)


@router.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: str, body: ChannelPatch):
    if body.members is not None:
        _check_agents(body.members)
    try:
        ch = await asyncio.to_thread(store.update_channel, channel_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if ch is None:
        raise HTTPException(status_code=404, detail="No such channel")
    events.publish(channel_id, {"type": "channel", "channel": ch})
    return ch


def _memory_files(channel_id: str, members: list[str]) -> list[Path]:
    base = Path.home() / ".myclaw"
    out = []
    for m in members:
        scope = memory_scope(channel_id, m)
        out += [base / f"memory_{USER_ID}__{scope}.db", base / f"memory___delegate____{scope}.db"]
    return out


@router.delete("/api/channels/{channel_id}", dependencies=guard)
async def delete_channel(channel_id: str):
    ch = await asyncio.to_thread(store.get_channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="No such channel")
    await engine().stop(channel_id)
    await asyncio.to_thread(store.delete_channel, channel_id)
    for f in _memory_files(channel_id, ch["members"]):
        for p in (f, f.with_suffix(".db-wal"), f.with_suffix(".db-shm")):
            try:
                p.unlink()
            except OSError:
                pass
    return {"status": "deleted"}


@router.get("/api/channels/{channel_id}/messages")
async def channel_messages(channel_id: str, before: int | None = None, limit: int = 100):
    ch = await asyncio.to_thread(_get_channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="No such channel")
    msgs = await asyncio.to_thread(store.list_messages, channel_id, before, limit)
    return {"channel": ch, "messages": msgs, "busy": engine().busy(channel_id)}


@router.delete("/api/channels/{channel_id}/messages", dependencies=guard)
async def clear_channel(channel_id: str):
    n = await asyncio.to_thread(store.clear_messages, channel_id)
    events.publish(channel_id, {"type": "cleared"})
    return {"status": "cleared", "removed": n}


@router.post("/api/channels/{channel_id}/messages", dependencies=guard)
async def post_message(channel_id: str, body: PostIn):
    try:
        return await engine().post(channel_id, body.text)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such channel")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/channels/{channel_id}/stop")
async def stop_channel(channel_id: str):
    return {"stopped": await engine().stop(channel_id)}


@router.get("/api/activity")
async def activity(limit: int = 100):
    return {"events": await asyncio.to_thread(store.recent_activity, limit)}


@router.websocket("/ws/channels/{channel_id}")
async def channel_socket(ws: WebSocket, channel_id: str):
    await ws.accept()
    ch = await asyncio.to_thread(_get_channel, channel_id)
    if ch is None:
        await ws.send_text(to_json({"type": "error", "error": "No such channel"}))
        await ws.close()
        return
    queue = events.subscribe(channel_id)

    async def pump():
        while True:
            ev = await queue.get()
            await ws.send_text(to_json(ev))

    # Same two-factor rule as the original chat socket: when MFA is on for
    # "webui", the first message must be {"type": "mfa", "code": "123456"}.
    from myclaw.mfa import MFAAuth

    mfa = MFAAuth()
    verified = not (mfa.is_available() and mfa.is_enabled_for_user("webui"))
    sender = asyncio.create_task(pump())
    await ws.send_text(to_json({"type": "hello", "channel": ch, "busy": engine().busy(channel_id),
                                "mfa_required": not verified}))
    try:
        while True:
            raw = await ws.receive_text()
            if raw == "__ping__":
                await ws.send_text(to_json({"type": "pong"}))
                continue
            if not verified:
                try:
                    import json
                    msg = json.loads(raw)
                except ValueError:
                    msg = {}
                if msg.get("type") == "mfa" and mfa.verify("webui", str(msg.get("code", "")).strip()):
                    verified = True
                    await ws.send_text(to_json({"type": "mfa_ok"}))
                    continue
                await ws.send_text(to_json({"type": "error", "error": "Two-factor code required or wrong."}))
                continue
            try:
                import json
                data = json.loads(raw)
            except ValueError:
                data = {"type": "message", "text": raw}
            if data.get("type") == "stop":
                await engine().stop(channel_id)
            elif data.get("type") == "message":
                try:
                    await engine().post(channel_id, str(data.get("text", "")))
                except (KeyError, ValueError) as e:
                    await ws.send_text(to_json({"type": "error", "error": str(e)}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("channel socket closed: %s", e)
    finally:
        sender.cancel()
        events.unsubscribe(queue, channel_id)
