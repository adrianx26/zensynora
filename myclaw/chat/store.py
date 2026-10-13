"""Chat channels and their messages, stored in ``~/.myclaw/chat.db`` (v0.7.1).

A *channel* is a conversation with one or more agents:

* ``kind="channel"`` — a group room such as ``#general`` with several members;
* ``kind="dm"`` — a direct conversation with one agent (created on demand).

Messages have a ``role``:

* ``user``   — something you wrote;
* ``agent``  — an agent's reply (``sender`` = agent name);
* ``event``  — agent-to-agent activity: delegations, swarm work, tool calls,
               job runs (``meta`` holds the event);
* ``system`` — notices (errors, "agent added", …).

Plain ``sqlite3`` with one short-lived connection per call keeps this safe to
use from threads (``asyncio.to_thread``) and from several processes.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPLY_MODES = ("lead", "all", "parallel")
# lead     — the first member answers (others only when @mentioned)
# all      — every member answers in turn and sees the earlier answers
# parallel — every member answers at the same time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'channel',
    topic TEXT NOT NULL DEFAULT '',
    members TEXT NOT NULL DEFAULT '[]',
    reply_mode TEXT NOT NULL DEFAULT 'lead',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    role TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_channel ON messages(channel_id, id);
"""


def db_path() -> Path:
    return Path.home() / ".myclaw" / "chat.db"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _channel(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["members"] = json.loads(d.get("members") or "[]")
    return d


def _message(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s[:40] or "channel"


# ── Channels ─────────────────────────────────────────────────────────────────
def list_channels() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ch.*, (SELECT MAX(id) FROM messages m WHERE m.channel_id = ch.id) AS last_id, "
            "(SELECT text FROM messages m WHERE m.channel_id = ch.id AND m.role IN ('user','agent') "
            " ORDER BY id DESC LIMIT 1) AS last_text "
            "FROM channels ch ORDER BY ch.kind, ch.name").fetchall()
    out = []
    for r in rows:
        d = _channel(r)
        d["last_text"] = (d.get("last_text") or "")[:120]
        out.append(d)
    return out


def get_channel(channel_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    return _channel(row) if row else None


def create_channel(name: str, members: list[str], topic: str = "", reply_mode: str = "lead",
                   kind: str = "channel", channel_id: str | None = None) -> dict[str, Any]:
    if reply_mode not in REPLY_MODES:
        raise ValueError(f"reply_mode must be one of {', '.join(REPLY_MODES)}")
    members = list(dict.fromkeys(m for m in members if m))  # unique, keep order
    if not members:
        raise ValueError("A channel needs at least one agent.")
    cid = channel_id or f"{slug(name)}-{secrets.token_hex(3)}"
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO channels (id, name, kind, topic, members, reply_mode, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, name.strip() or cid, kind, topic, json.dumps(members), reply_mode, now, now))
    return get_channel(cid)  # type: ignore[return-value]


def update_channel(channel_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "topic", "members", "reply_mode"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "reply_mode" and v not in REPLY_MODES:
            raise ValueError(f"reply_mode must be one of {', '.join(REPLY_MODES)}")
        if k == "members":
            v = list(dict.fromkeys(m for m in v if m))
            if not v:
                raise ValueError("A channel needs at least one agent.")
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        values.append(v)
    if sets:
        with _conn() as c:
            c.execute(f"UPDATE channels SET {', '.join(sets)}, updated = ? WHERE id = ?",
                      (*values, time.time(), channel_id))
    return get_channel(channel_id)


def delete_channel(channel_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        n = c.execute("DELETE FROM channels WHERE id = ?", (channel_id,)).rowcount
    return n > 0


def dm_channel(agent: str) -> dict[str, Any]:
    """The direct-message channel for one agent (created on first use)."""
    cid = f"dm-{slug(agent)}"
    ch = get_channel(cid)
    if ch:
        return ch
    return create_channel(agent, [agent], kind="dm", channel_id=cid)


def ensure_default_channel(default_agent: str = "default") -> dict[str, Any]:
    ch = get_channel("general")
    if ch:
        return ch
    return create_channel("general", [default_agent], topic="Talk to all your agents here",
                          channel_id="general")


# ── Messages ─────────────────────────────────────────────────────────────────
def add_message(channel_id: str, role: str, sender: str, text: str,
                meta: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (channel_id, role, sender, text, meta, created) VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, role, sender, text, json.dumps(meta or {}), now))
        mid = cur.lastrowid
        c.execute("UPDATE channels SET updated = ? WHERE id = ?", (now, channel_id))
    return {"id": mid, "channel_id": channel_id, "role": role, "sender": sender,
            "text": text, "meta": meta or {}, "created": now}


def update_message(message_id: int, text: str | None = None,
                   meta: dict[str, Any] | None = None) -> None:
    with _conn() as c:
        if text is not None:
            c.execute("UPDATE messages SET text = ? WHERE id = ?", (text, message_id))
        if meta is not None:
            c.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), message_id))


def list_messages(channel_id: str, before: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    with _conn() as c:
        if before:
            rows = c.execute("SELECT * FROM messages WHERE channel_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                             (channel_id, before, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
                             (channel_id, limit)).fetchall()
    return [_message(r) for r in reversed(rows)]


def clear_messages(channel_id: str) -> int:
    with _conn() as c:
        return c.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,)).rowcount


def recent_activity(limit: int = 100) -> list[dict[str, Any]]:
    """Latest agent-to-agent events across all channels (for the activity feed)."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE role = 'event' ORDER BY id DESC LIMIT ?",
                         (max(1, min(limit, 500)),)).fetchall()
    return [_message(r) for r in rows]
