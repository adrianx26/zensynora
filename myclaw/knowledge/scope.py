"""Which knowledge base a request uses (v0.7.5).

Before 0.7.5 the knowledge base was split by an internal "user id": the Web UI
chat saved and searched notes as ``webui``, delegated tasks as
``__delegate__``, while the knowledge tools an agent calls itself
(``search_knowledge``, ``read_knowledge``, ``list_knowledge``…) and the medic's
lessons used ``default``. Agents therefore could not find what they (or the
automatic extraction) had saved — "My knowledge base has no entries on this
topic" although the notes existed.

Now every internal id maps to the one shared knowledge base, ``default``.
Real separate users (for example different Telegram chats) still get their own
knowledge base. :data:`KB_USER` is set for the duration of an agent's turn, so
the tools it calls use the same knowledge base as the automatic search — a
model cannot pick another (empty) one by passing a made-up ``user_id``.
Notes saved under the old internal ids are moved into ``default`` once.
"""

from __future__ import annotations

import logging
import shutil
import threading
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger(__name__)

SHARED = "default"
# Ids used by ZenSynora itself, not by a person.
INTERNAL = {"", "default", "webui", "__delegate__", "local", "cli", "console", "user", "me", "you", "none", "null"}
KB_USER: ContextVar[str | None] = ContextVar("kb_user", default=None)

_migrated: set[str] = set()
# Ids of people who share the main knowledge base (e.g. the allowed Telegram
# users when channels.telegram.shared_knowledge is on).
_SHARED_IDS: set[str] = set()


def share_main_knowledge(*user_ids: str) -> None:
    """Let these (external) user ids use the shared knowledge base."""
    _SHARED_IDS.update(str(u).strip() for u in user_ids if str(u).strip())


def unshare_main_knowledge(*user_ids: str) -> None:
    for u in user_ids:
        _SHARED_IDS.discard(str(u).strip())
_lock = threading.Lock()


def is_internal(user_id: str | None) -> bool:
    return (user_id or "").strip().lower() in INTERNAL


def kb_user(user_id: str | None = None) -> str:
    """The knowledge base for this id: internal ids share ``default``."""
    if is_internal(user_id) or str(user_id).strip() in _SHARED_IDS:
        return SHARED
    return str(user_id).strip()


def for_turn(user_id: str | None) -> str:
    """Knowledge base of an agent turn: a nested call (delegation) keeps the
    caller's knowledge base."""
    if is_internal(user_id):
        return KB_USER.get() or SHARED
    return kb_user(user_id)  # an id in _SHARED_IDS maps to the shared base


def for_tool(user_id: str | None = None) -> str:
    """Knowledge base for a tool call: the current turn's, if there is one."""
    return KB_USER.get() or kb_user(user_id)


def migrate_internal_scopes() -> dict[str, int]:
    """Move notes saved under old internal ids into the shared knowledge base."""
    base = Path.home() / ".myclaw" / "knowledge"
    key = str(base)
    if key in _migrated:
        return {}
    with _lock:
        if key in _migrated:
            return {}
        _migrated.add(key)
        moved: dict[str, int] = {}
        if not base.is_dir():
            return moved
        target = base / SHARED
        for d in base.iterdir():
            if not d.is_dir() or d.name == SHARED or not is_internal(d.name):
                continue
            n = 0
            for f in d.rglob("*.md"):
                if f.stat().st_size < 8:  # empty notes from an old summariser bug
                    continue
                dest = target / f.relative_to(d)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    if dest.read_bytes() == f.read_bytes():
                        f.unlink()
                        continue
                    dest = dest.with_name(f"{dest.stem}-{d.name.strip('_')}{dest.suffix}")
                shutil.move(str(f), str(dest))
                n += 1
            if not any(p.is_file() and p.stat().st_size >= 8 for p in d.rglob("*")):
                shutil.rmtree(d, ignore_errors=True)  # nothing useful left
            if n:
                moved[d.name] = n
                logger.info("Knowledge: moved %d note(s) from '%s' into the shared knowledge base", n, d.name)
        if moved:
            try:
                from .sync import sync_knowledge

                sync_knowledge(SHARED)
            except Exception as e:  # the index is rebuilt on the next sync anyway
                logger.warning("Knowledge: re-index after moving notes failed: %s", e)
        return moved
