"""One write budget for the knowledge base's background writers (KB phase 6, 2026-10-25).

memopad's lesson: every background writer (sync, embeddings, distillation,
imports, the file watcher) went at the database on its own, and they fought
over SQLite's single write lock. Here they share one budget:

* **Per base, one background writer at a time.** A writer takes the base's
  slot (``background(key, who, prio)``); others wait in priority order
  (``PRIO_SYNC`` — the watcher and background syncs — before imports, before
  embeddings, before distillation), first come first served within a priority.
* **At most ``GLOBAL_SLOTS`` background writers in the whole process.**
* **Foreground writes win.** An index write that is not made under a slot (an
  agent's ``write_note``, a save in the Web UI) never waits for the budget:
  ``KnowledgeDB._write_txn`` only marks it (``foreground``). A background writer
  does not start while one is marked (at most ``FOREGROUND_GRACE`` seconds), and
  a long-running one calls ``checkpoint()`` between its write transactions,
  which hands the base over to foreground writes and to waiting writers of a
  higher priority first. A foreground write therefore waits at most for one
  background batch (SQLite's own lock, ``busy_timeout``).
* **Never a deadlock.** A slot is re-entrant in its thread; a writer that waited
  ``timeout`` seconds either gives up (``on_timeout="raise"`` → :class:`Busy`) or
  goes ahead anyway (``"proceed"``, logged once) — SQLite still serialises the
  writes.

The budget only orders writers inside one process (the Web UI). Other
processes (command-line commands) are kept apart by SQLite's ``busy_timeout``
and, for distillation, its database lease. :func:`retry_locked` retries a
write transaction only on "database is locked" / busy, with a bounded backoff.
"""

from __future__ import annotations

import itertools
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

logger = logging.getLogger(__name__)

PRIO_SYNC = 10        # file watcher, background sync (a user is waiting for the result)
PRIO_INGEST = 20      # imports / assimilate jobs
PRIO_SEMANTIC = 30    # embedding batches
PRIO_DISTILL = 40     # distillation runs (long; yield to everyone)

GLOBAL_SLOTS = 2              # background writers at once in this process (all bases)
FOREGROUND_GRACE = 10.0       # a background writer waits at most this long for foreground writes
DEFAULT_TIMEOUT = 300.0       # then "proceed" (logged once) or Busy
_POLL = 0.25

T = TypeVar("T")


class Busy(Exception):
    """The base's write slot could not be taken in time."""


@dataclass
class _Ticket:
    key: str
    who: str
    prio: int
    seq: int
    thread: int
    since: float = 0.0


@dataclass
class _Slot:
    owner: Optional[_Ticket] = None
    depth: int = 0
    waiters: List[_Ticket] = field(default_factory=list)
    foreground: int = 0
    fg_since: float = 0.0
    granted: int = 0           # how often a background writer got the slot
    yielded: int = 0           # how often a checkpoint handed it over


_CV = threading.Condition()
_SLOTS: Dict[str, _Slot] = {}
_ACTIVE = [0]                  # slots owned now (all bases)
_SEQ = itertools.count(1)
_LOCAL = threading.local()
_LOGGED: set = set()


def _log_once(key: str, level: int, msg: str, *args: Any) -> None:
    with _CV:
        if key in _LOGGED:
            return
        if len(_LOGGED) > 500:
            _LOGGED.clear()
        _LOGGED.add(key)
    logger.log(level, msg, *args)


def key_for(base_or_path: Any) -> str:
    """The budget key of a knowledge base: its index database path.

    Accepts a database path (``Path`` / ``str`` ending in ``.db``) or a base name.
    """
    if isinstance(base_or_path, Path) or str(base_or_path).endswith(".db"):
        return str(Path(str(base_or_path)))
    from .scope import kb_user

    return str(Path.home() / ".myclaw" / f"knowledge_{kb_user(str(base_or_path or 'default'))}.db")


def _held() -> Dict[str, _Ticket]:
    h = getattr(_LOCAL, "held", None)
    if h is None:
        h = _LOCAL.held = {}
    return h


def holds(key: Optional[str] = None) -> bool:
    """Does this thread own a background slot (for ``key``, or any)?"""
    h = _held()
    return bool(h) if key is None else key in h


def _can_take(slot: _Slot, t: _Ticket, now: float) -> bool:
    if slot.owner is not None or _ACTIVE[0] >= GLOBAL_SLOTS:
        return False
    if slot.foreground > 0 and now - slot.fg_since < FOREGROUND_GRACE:
        return False
    best = min(slot.waiters, key=lambda w: (w.prio, w.seq))
    return best is t


def _take(key: str, t: _Ticket, timeout: Optional[float]) -> bool:
    """Wait for the slot (with _CV held). True = taken."""
    slot = _SLOTS.setdefault(key, _Slot())
    slot.waiters.append(t)
    end = None if timeout is None else time.monotonic() + max(0.0, timeout)
    try:
        while True:
            now = time.monotonic()
            if _can_take(slot, t, now):
                slot.owner, slot.depth = t, 1
                t.since = time.time()
                slot.granted += 1
                _ACTIVE[0] += 1
                return True
            if end is not None and now >= end:
                return False
            wait = _POLL if end is None else max(0.01, min(_POLL, end - now))
            _CV.wait(wait)
    finally:
        slot.waiters.remove(t)


def _give(key: str) -> None:
    """Release the slot fully (with _CV held)."""
    slot = _SLOTS.get(key)
    if slot is None or slot.owner is None:
        return
    slot.owner, slot.depth = None, 0
    _ACTIVE[0] = max(0, _ACTIVE[0] - 1)
    _CV.notify_all()


def acquire(key: str, who: str, prio: int = PRIO_SYNC, timeout: Optional[float] = DEFAULT_TIMEOUT) -> bool:
    """Take the base's background write slot (re-entrant in this thread)."""
    held = _held()
    if key in held:
        with _CV:
            _SLOTS[key].depth += 1
        return True
    t = _Ticket(key=key, who=who, prio=int(prio), seq=next(_SEQ), thread=threading.get_ident())
    with _CV:
        ok = _take(key, t, timeout)
    if ok:
        held[key] = t
    return ok


def release(key: str) -> None:
    held = _held()
    if key not in held:
        return
    with _CV:
        slot = _SLOTS.get(key)
        if slot is None or slot.owner is None:
            held.pop(key, None)
            return
        slot.depth -= 1
        if slot.depth <= 0:
            held.pop(key, None)
            _give(key)


@contextmanager
def background(base_or_path: Any, who: str, prio: int = PRIO_SYNC,
               timeout: Optional[float] = DEFAULT_TIMEOUT, on_timeout: str = "proceed") -> Iterator[bool]:
    """Run a background write under the base's slot. Yields True when the slot
    is held; after ``timeout`` either raises :class:`Busy` (``on_timeout="raise"``)
    or goes ahead without it (``"proceed"``, logged once; yields False)."""
    key = key_for(base_or_path)
    ok = acquire(key, who, prio, timeout)
    if not ok:
        if on_timeout == "raise":
            raise Busy(f"another background writer is using {Path(key).name}")
        _log_once(f"proceed:{who}:{key}", logging.INFO,
                  "Knowledge: %s waited %.0f s for %s; writing anyway (SQLite serialises the writes)",
                  who, timeout or 0, Path(key).name)
    try:
        yield ok
    finally:
        if ok:
            release(key)


@contextmanager
def foreground(base_or_path: Any) -> Iterator[None]:
    """Mark a foreground index write (never waits for the budget). A thread that
    owns the base's background slot is a background writer: nothing is marked."""
    try:
        key = key_for(base_or_path)
    except Exception:  # never let the marker break a write
        yield
        return
    if key in _held():
        yield
        return
    with _CV:
        slot = _SLOTS.setdefault(key, _Slot())
        if slot.foreground == 0:
            slot.fg_since = time.monotonic()
        slot.foreground += 1
    try:
        yield
    finally:
        with _CV:
            slot.foreground = max(0, slot.foreground - 1)
            _CV.notify_all()


def checkpoint(timeout: Optional[float] = DEFAULT_TIMEOUT) -> bool:
    """Between two write transactions of a long background writer: if foreground
    writes or waiting writers of a higher priority want this base, hand the slot
    over and take it back after them. True when nothing had to wait (or the slot
    is back); False when it could not be taken back in ``timeout`` seconds (the
    writer goes on without it — SQLite still serialises)."""
    held = _held()
    if not held:
        return True
    key, t = next(reversed(list(held.items())))
    with _CV:
        slot = _SLOTS.get(key)
        if slot is None or slot.owner is not t:
            return True
        now = time.monotonic()
        fg = slot.foreground > 0 and now - slot.fg_since < FOREGROUND_GRACE
        better = any(w.prio < t.prio for w in slot.waiters)
        if not (fg or better):
            return True
        depth = slot.depth
        slot.yielded += 1
        _give(key)
        ok = _take(key, t, timeout)
        if ok:
            slot.depth = depth
            return True
    held.pop(key, None)
    _log_once(f"checkpoint:{t.who}:{key}", logging.INFO,
              "Knowledge: %s could not take back the write slot of %s; continuing", t.who, Path(key).name)
    return False


def busy(base_or_path: Any = None) -> bool:
    """Is a background writer working on (or waiting for) this base — or any base?"""
    with _CV:
        if base_or_path is None:
            return any(s.owner is not None or s.waiters for s in _SLOTS.values())
        s = _SLOTS.get(key_for(base_or_path))
        return bool(s and (s.owner is not None or s.waiters))


def status(base_or_path: Any = None) -> Dict[str, Any]:
    """Who writes now / who waits (health route, doctor)."""
    with _CV:
        keys = list(_SLOTS) if base_or_path is None else [key_for(base_or_path)]
        out: Dict[str, Any] = {"active": _ACTIVE[0], "global_slots": GLOBAL_SLOTS, "bases": {}}
        for k in keys:
            s = _SLOTS.get(k)
            if s is None:
                continue
            out["bases"][Path(k).name] = {
                "writer": s.owner.who if s.owner else None,
                "since": s.owner.since if s.owner else None,
                "waiting": [w.who for w in sorted(s.waiters, key=lambda w: (w.prio, w.seq))],
                "foreground": s.foreground, "granted": s.granted, "yielded": s.yielded,
            }
        return out


def is_locked_error(e: BaseException) -> bool:
    """Only "database is locked" / busy errors are worth a retry."""
    if not isinstance(e, sqlite3.OperationalError):
        return False
    text = str(e).lower()
    return "locked" in text or "busy" in text


def retry_locked(fn: Callable[[], T], what: str = "knowledge write", attempts: int = 4,
                 delay: float = 0.5, max_delay: float = 4.0) -> T:
    """Run ``fn``; retry it only on "database is locked"/busy, with a bounded
    linear backoff (0.5 s, 1 s, 1.5 s …, at most ``max_delay``). The first retry
    of each ``what`` is logged once."""
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if not is_locked_error(e) or attempt >= attempts - 1:
                raise
            _log_once(f"retry:{what}", logging.INFO,
                      "Knowledge: the index database was busy during %s; retrying", what)
            time.sleep(min(max_delay, delay * (attempt + 1)))
    raise AssertionError("unreachable")


def _reset_for_tests() -> None:
    with _CV:
        _SLOTS.clear()
        _ACTIVE[0] = 0
        _LOGGED.clear()
    _LOCAL.held = {}
