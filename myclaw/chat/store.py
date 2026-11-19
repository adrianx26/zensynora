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

import errno
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPLY_MODES = ("lead", "all", "parallel")
_database_lock = threading.RLock()
_database_lock_state = threading.local()


class ChatRecoveryError(ValueError):
    """Chat cannot be accessed until an interrupted restore is recovered."""
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
CREATE TABLE IF NOT EXISTS fusion_message_origins (
    message_id INTEGER NOT NULL,
    source_channel_id TEXT NOT NULL,
    destination_channel_id TEXT NOT NULL,
    PRIMARY KEY (message_id, source_channel_id, destination_channel_id)
);
CREATE TABLE IF NOT EXISTS fusion_retired_dms (
    channel_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    destination_channel_id TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path.home() / ".myclaw" / "chat.db"


def restore_generation() -> str:
    try:
        return (db_path().parent / ".chat-restore-generation").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


@contextmanager
def database_guard() -> Iterator[None]:
    """Keep chat operations and database replacement mutually exclusive."""
    with _database_lock:
        path = db_path().with_suffix(".lock")
        held = getattr(_database_lock_state, "path", None)
        if held is not None:
            if held != path:
                raise RuntimeError("Cannot change chat database while its lock is held.")
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as lock:
            if os.name == "nt":
                import msvcrt

                while True:
                    lock.seek(0)
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _database_lock_state.path = path
            try:
                yield
            finally:
                del _database_lock_state.path
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    with database_guard():
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if (path.parent / ".lifecycle-restore-pending.json").exists():
            raise ChatRecoveryError("Backup restore needs recovery; restore the named safety backup before using chat.")
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
def clean_name(name: str) -> str:
    """'#house ' -> 'house' (the UI adds the # itself; v0.7.5)."""
    return (name or "").strip().lstrip("#").strip()


def canonical_members(names: list[str], known: list[str]) -> tuple[list[str], list[str]]:
    """Match names to existing agents ignoring case. Returns (members, unknown)."""
    by_lower = {k.lower(): k for k in known}
    members, unknown = [], []
    for n in names:
        k = by_lower.get((n or "").strip().lstrip("@").lower())
        if k:
            if k not in members:
                members.append(k)
        elif n:
            unknown.append(n)
    return members, unknown


def repair_channels(known: list[str], default: str = "default") -> list[dict[str, Any]]:
    """Remove agents that no longer exist from group channels (v0.7.5).

    An agent that was renamed or deleted in Settings stayed a member of its
    channels; every later member change was then refused ("Unknown agent"),
    so the "zombie" could neither be removed nor new agents added. Also
    strips a leading '#' from channel names. Returns what was changed.
    """
    changes: list[dict[str, Any]] = []
    with _conn() as c:
        rows = c.execute("SELECT * FROM channels").fetchall()
        for r in rows:
            ch = _channel(r)
            sets: dict[str, Any] = {}
            if ch["kind"] == "channel":
                members, gone = canonical_members(ch["members"], known)
                if gone or members != ch["members"]:
                    sets["members"] = json.dumps(members or [default])
            name = clean_name(ch["name"])
            if name and name != ch["name"]:
                sets["name"] = name
            if not sets:
                continue
            c.execute(f"UPDATE channels SET {', '.join(k + ' = ?' for k in sets)}, updated = ? WHERE id = ?",
                      (*sets.values(), time.time(), ch["id"]))
            removed = [m for m in ch["members"] if m not in json.loads(sets.get("members", "[]") or "[]")] \
                if "members" in sets else []
            if removed:
                c.execute("INSERT INTO messages (channel_id, role, sender, text, meta, created) "
                          "VALUES (?, 'system', '', ?, '{}', ?)",
                          (ch["id"], f"Removed {', '.join('@' + m for m in removed)} from this channel — "
                                     f"{'that agent no longer exists' if len(removed) == 1 else 'those agents no longer exist'}.",
                           time.time()))
            changes.append({"id": ch["id"], "removed": removed, **({"name": sets["name"]} if "name" in sets else {})})
    return changes


def rename_member(old: str, new: str) -> None:
    """An agent was renamed: channels keep it, its direct messages move along."""
    with _conn() as c:
        for r in c.execute("SELECT id, kind, members FROM channels").fetchall():
            members = json.loads(r["members"] or "[]")
            if old not in members:
                continue
            members = list(dict.fromkeys(new if m == old else m for m in members))
            c.execute("UPDATE channels SET members = ?, updated = ? WHERE id = ?",
                      (json.dumps(members), time.time(), r["id"]))
        old_dm, new_dm = f"dm-{slug(old)}", f"dm-{slug(new)}"
        if old_dm != new_dm and c.execute("SELECT 1 FROM channels WHERE id = ?", (old_dm,)).fetchone() \
                and not c.execute("SELECT 1 FROM channels WHERE id = ?", (new_dm,)).fetchone():
            c.execute("UPDATE channels SET id = ?, name = ? WHERE id = ?", (new_dm, new, old_dm))
            c.execute("UPDATE messages SET channel_id = ? WHERE channel_id = ?", (new_dm, old_dm))


def fuse_members(sources: list[str], destination: str) -> None:
    """Move channel membership and DM messages atomically, retaining source DM records."""
    dest_id = f"dm-{slug(destination)}"
    source_ids = list(dict.fromkeys(f"dm-{slug(name)}" for name in sources))
    if len(source_ids) != len(sources) or dest_id in source_ids:
        raise ValueError("These agent names share a DM address; choose a different fusion name.")
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        existing_sources = []
        for source_id in source_ids:
            row = c.execute("SELECT kind, members FROM channels WHERE id = ?", (source_id,)).fetchone()
            if row and (row["kind"] not in ("dm", "retired_dm") or
                        any(m not in [destination, *sources] for m in json.loads(row["members"] or "[]"))):
                raise ValueError(f"Source DM {source_id} belongs to another conversation; resolve the collision.")
            if row:
                existing_sources.append(source_id)
        dest = c.execute("SELECT * FROM channels WHERE id = ?", (dest_id,)).fetchone()
        if dest and (dest["kind"] != "dm" or
                     any(m not in [destination, *sources] for m in json.loads(dest["members"] or "[]"))):
            raise ValueError(f"Destination DM {dest_id} belongs to another conversation; rename the agent.")
        if dest is None:
            for source_id in existing_sources:
                source = c.execute("SELECT * FROM channels WHERE id = ? AND kind IN ('dm', 'retired_dm')",
                                   (source_id,)).fetchone()
                if source:
                    c.execute("INSERT INTO channels (id, name, kind, topic, members, reply_mode, created, updated) "
                              "VALUES (?, ?, 'dm', ?, ?, ?, ?, ?)",
                              (dest_id, destination, source["topic"], json.dumps([destination]),
                               source["reply_mode"], source["created"], time.time()))
                    break
        for row in c.execute("SELECT id, members FROM channels").fetchall():
            if row["id"] in existing_sources:
                continue
            members = json.loads(row["members"] or "[]")
            replaced = list(dict.fromkeys(destination if m in sources else m for m in members))
            if replaced != members:
                c.execute("UPDATE channels SET members = ?, updated = ? WHERE id = ?",
                          (json.dumps(replaced), time.time(), row["id"]))
        for source_id in existing_sources:
            if source_id != dest_id:
                c.execute("INSERT OR IGNORE INTO fusion_message_origins "
                          "(message_id, source_channel_id, destination_channel_id) "
                          "SELECT id, channel_id, ? FROM messages WHERE channel_id = ?", (dest_id, source_id))
                c.execute("UPDATE messages SET channel_id = ? WHERE channel_id = ?", (dest_id, source_id))
                source_name = next(name for name in sources if f"dm-{slug(name)}" == source_id)
                c.execute("INSERT OR REPLACE INTO fusion_retired_dms VALUES (?, ?, ?)",
                          (source_id, source_name, dest_id))
                c.execute("UPDATE channels SET kind = 'retired_dm', updated = ? WHERE id = ?",
                          (time.time(), source_id))


def close_interrupted() -> int:
    """Answers left "typing…" because the server stopped mid-answer (v0.7.5)."""
    n = 0
    with _conn() as c:
        rows = c.execute("SELECT id, text, meta FROM messages WHERE role = 'agent' "
                         "AND meta LIKE '%\"streaming\": true%'").fetchall()
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except ValueError:
                meta = {}
            if not meta.get("streaming"):
                continue
            meta.pop("streaming", None)
            meta["interrupted"] = True
            text = (r["text"] or "").strip()
            text = f"{text} …[interrupted]" if text else \
                "(No answer — ZenSynora stopped or restarted while this answer was being written. Ask again.)"
            c.execute("UPDATE messages SET text = ?, meta = ? WHERE id = ?", (text, json.dumps(meta), r["id"]))
            n += 1
    return n


def list_channels() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ch.*, (SELECT MAX(id) FROM messages m WHERE m.channel_id = ch.id) AS last_id, "
            "(SELECT text FROM messages m WHERE m.channel_id = ch.id AND m.role IN ('user','agent') "
            " ORDER BY id DESC LIMIT 1) AS last_text, "
            "(SELECT COUNT(*) FROM messages m WHERE m.channel_id = ch.id AND m.role IN ('user','agent')) "
            " AS message_count, "
            "(SELECT MAX(created) FROM messages m WHERE m.channel_id = ch.id) AS last_activity "
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
    name = clean_name(name)
    cid = channel_id or f"{slug(name)}-{secrets.token_hex(3)}"
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO channels (id, name, kind, topic, members, reply_mode, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, name or cid, kind, topic, json.dumps(members), reply_mode, now, now))
    return get_channel(cid)  # type: ignore[return-value]


def update_channel(channel_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "topic", "members", "reply_mode"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "name":  # v0.7.3: renaming from the Channels page
            v = clean_name(str(v))
            if not v:
                raise ValueError("A channel needs a name.")
        if k == "topic":
            v = str(v).strip()[:500]
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


def is_legacy_fusion_dm(ch: dict[str, Any]) -> bool:
    if ch["kind"] != "dm":
        return False
    from ..evolve import _read_trash

    return any(t.get("type") == "fusion" and t.get("state") in {"pending", "complete"}
               and any(ch["id"] == f"dm-{slug(source)}" for source in t.get("sources", []))
               and t.get("destination") in ch["members"] for t in _read_trash())


def dm_channel(agent: str) -> dict[str, Any]:
    """The direct-message channel for one agent (created on first use)."""
    cid = f"dm-{slug(agent)}"
    ch = get_channel(cid)
    if ch:
        if ch["kind"] == "retired_dm" or is_legacy_fusion_dm(ch):
            from ..config import load_config
            from ..evolve import fusion_blocks_agent

            with _conn() as c:
                retired = c.execute("SELECT agent FROM fusion_retired_dms WHERE channel_id = ?", (cid,)).fetchone()
                active = any(a.name == agent for a in load_config(force_reload=True).agents.named)
                matches = retired["agent"] == agent if retired else ch["name"] == agent
                if not matches or not active or fusion_blocks_agent(agent):
                    raise ValueError("This conversation is retired; open the fused agent's DM instead.")
                c.execute("UPDATE channels SET kind = 'dm', members = ?, updated = ? WHERE id = ?",
                          (json.dumps([agent]), time.time(), cid))
                c.execute("DELETE FROM fusion_retired_dms WHERE channel_id = ?", (cid,))
            return get_channel(cid)
        if ch["kind"] != "dm" or ch["members"] != [agent]:
            raise ValueError("This DM address belongs to another conversation; choose a different agent name.")
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
                meta: dict[str, Any] | None = None,
                expected_channel: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    with _conn() as c:
        channel = c.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if channel is None:
            raise ValueError("This channel no longer exists; reopen a channel before sending the message.")
        if channel["kind"] == "retired_dm":
            raise ValueError("This conversation is retired; open the fused agent's DM instead.")
        if expected_channel is not None:
            current = _channel(channel)
            keys = ("id", "name", "kind", "topic", "members", "reply_mode", "created")
            if any(current[key] != expected_channel[key] for key in keys):
                raise ValueError("This channel changed; reload it and retry sending the message.")
        cur = c.execute(
            "INSERT INTO messages (channel_id, role, sender, text, meta, created) VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, role, sender, text, json.dumps(meta or {}), now))
        mid = cur.lastrowid
        c.execute("UPDATE channels SET updated = ? WHERE id = ?", (now, channel_id))
    return {"id": mid, "channel_id": channel_id, "role": role, "sender": sender,
            "text": text, "meta": meta or {}, "created": now}


def update_message(message_id: int, text: str | None = None,
                   meta: dict[str, Any] | None = None,
                   expected: dict[str, Any] | None = None,
                   expected_generation: str | None = None) -> None:
    with _conn() as c:
        if expected_generation is not None and restore_generation() != expected_generation:
            raise ValueError("Chat history was restored; reload the channel and retry.")
        if expected is not None:
            row = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None or _message(row) != expected:
                raise ValueError("This message changed or disappeared; reload the channel and retry.")
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


def get_message(message_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _message(row) if row else None


def all_messages(channel_id: str, before: int | None = None) -> list[dict[str, Any]]:
    """Every message of a channel (oldest first), optionally only those before an id."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE channel_id = ? AND id < ? ORDER BY id",
                         (channel_id, before if before else 2 ** 62)).fetchall()
    return [_message(r) for r in rows]


def messages_after(channel_id: str, message_id: int) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE channel_id = ? AND id > ? ORDER BY id",
                         (channel_id, message_id)).fetchall()
    return [_message(r) for r in rows]


def reply_ids(channel_id: str, message_id: int) -> list[int]:
    """Ids of the answers to a message: everything after it up to the next message of yours."""
    out = []
    for m in messages_after(channel_id, message_id):
        if m["role"] == "user":
            break
        out.append(m["id"])
    return out


def delete_messages(channel_id: str, ids: list[int],
                    expected: list[dict[str, Any]] | None = None,
                    expected_generation: str | None = None) -> list[int]:
    """Delete messages of one channel; returns the ids that were deleted."""
    ids = [int(i) for i in ids]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with _conn() as c:
        if expected_generation is not None and restore_generation() != expected_generation:
            raise ValueError("Chat history was restored; reload the channel and retry.")
        rows = c.execute(
            f"SELECT * FROM messages WHERE channel_id = ? AND id IN ({marks}) ORDER BY id",
            (channel_id, *ids)).fetchall()
        if expected is not None and [_message(row) for row in rows] != sorted(expected, key=lambda msg: msg["id"]):
            raise ValueError("These messages changed or disappeared; reload the channel and retry.")
        found = [row["id"] for row in rows]
        if found:
            c.execute(f"DELETE FROM messages WHERE channel_id = ? AND id IN ({marks})", (channel_id, *ids))
    return found


def edit_message(message_id: int, text: str, expected: dict[str, Any] | None = None,
                 expected_generation: str | None = None) -> dict[str, Any] | None:
    """Change a message's text and mark it edited (meta.edited_at)."""
    with _conn() as c:
        if expected_generation is not None and restore_generation() != expected_generation:
            raise ValueError("Chat history was restored; reload the channel and retry.")
        row = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            if expected is not None:
                raise ValueError("This message changed or disappeared; reload the channel and retry.")
            return None
        message = _message(row)
        if expected is not None and message != expected:
            raise ValueError("This message changed or disappeared; reload the channel and retry.")
        meta = {**message["meta"], "edited_at": time.time()}
        c.execute("UPDATE messages SET text = ?, meta = ? WHERE id = ?", (text, json.dumps(meta), message_id))
        return {**message, "text": text, "meta": meta}


def clear_messages(channel_id: str, expected: list[dict[str, Any]] | None = None,
                   expected_generation: str | None = None) -> int:
    """Delete every message in one channel.

    A caller that already showed the history passes that snapshot and the
    restore generation. The check happens in the same guarded transaction as
    the delete, so a restore that reused these message ids cannot be cleared.
    """
    with _conn() as c:
        if expected_generation is not None and restore_generation() != expected_generation:
            raise ValueError("Chat history was restored; reload the channel and retry.")
        rows = c.execute("SELECT * FROM messages WHERE channel_id = ? ORDER BY id",
                         (channel_id,)).fetchall()
        current = [_message(row) for row in rows]
        if expected is not None and current != expected:
            raise ValueError("These messages changed or disappeared; reload the channel and retry.")
        if not current:
            return 0
        return c.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,)).rowcount


def to_markdown(channel: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """The conversation as Markdown (Copy / Download as Markdown in the chat)."""
    title = f"@{channel['members'][0]}" if channel.get("kind") == "dm" and channel.get("members") \
        else f"#{channel.get('name', channel.get('id', ''))}"
    lines = [f"# {title}", ""]
    if channel.get("topic"):
        lines += [f"_{channel['topic']}_", ""]
    for m in messages:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("created") or 0))
        edited = " (edited)" if (m.get("meta") or {}).get("edited_at") else ""
        if m["role"] == "user":
            lines += [f"**You** · {when}{edited}", "", m["text"], ""]
        elif m["role"] == "agent":
            lines += [f"**@{m['sender']}** · {when}{edited}", "", m["text"], ""]
        elif m["role"] == "system":
            lines += [f"_{m['text']}_", ""]
        else:  # agent-to-agent activity, one line
            ev = m.get("meta") or {}
            what = ev.get("type", "event")
            who = " → ".join(f"@{x}" for x in (ev.get("from"), ev.get("to")) if x) or \
                (f"@{ev['agent']}" if ev.get("agent") else "")
            detail = ev.get("tool") or ev.get("job") or ""
            lines += [f"> {what} {who} {detail}".rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def recent_activity(limit: int = 100) -> list[dict[str, Any]]:
    """Latest agent-to-agent events across all channels (for the activity feed)."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE role = 'event' ORDER BY id DESC LIMIT ?",
                         (max(1, min(limit, 500)),)).fetchall()
    return [_message(r) for r in rows]
