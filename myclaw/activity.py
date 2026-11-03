"""Agent activity, for work that should only run while the app is idle (2026-11-03).

Every agent turn (Web UI, Telegram, jobs, delegation) calls
:func:`turn_started` / :func:`turn_ended`. :func:`idle_seconds` is 0 while a
turn runs, otherwise the time since the last turn ended. Per process.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_active = 0
_last = time.time()   # a fresh start counts as activity


def turn_started() -> None:
    global _active, _last
    with _lock:
        _active += 1
        _last = time.time()


def turn_ended() -> None:
    global _active, _last
    with _lock:
        _active = max(0, _active - 1)
        _last = time.time()


def idle_seconds() -> float:
    with _lock:
        return 0.0 if _active else max(0.0, time.time() - _last)
