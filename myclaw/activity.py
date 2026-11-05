"""Agent activity (2026-11-03) and the activity log (2026-11-05).

Idle time: work that should only run while the app is idle.

Every agent turn (Web UI, Telegram, jobs, delegation) calls
:func:`turn_started` / :func:`turn_ended`. :func:`idle_seconds` is 0 while a
turn runs, otherwise the time since the last turn ended. Per process.
"""

from __future__ import annotations

import json as _json
import logging as _logging
import logging.handlers as _handlers
import threading
import time
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path as _Path
from typing import Any, Callable

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


# ── 2026-11-05: activity log (Doctor → Logs) ────────────────────────────────
# One JSON line per action the app does — agent turns, tools and scripts, jobs, model
# calls, settings saves (key names only), updates, backups, knowledge writes — in
# ~/.myclaw/logs/activity.jsonl (10 MB, 5 old files kept). Secrets are redacted.

_writer: "_logging.Logger | None" = None
_writer_path: "_Path | None" = None


def activity_file() -> _Path:
    return _Path.home() / ".myclaw" / "logs" / "activity.jsonl"


def _get_writer() -> "_logging.Logger":
    global _writer, _writer_path
    path = activity_file()
    if _writer is None or _writer_path != path:   # HOME can change (tests)
        log = _logging.getLogger("zensynora.activity")
        log.propagate = False
        log.setLevel(_logging.INFO)
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        h = _handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        h.setFormatter(_logging.Formatter("%(message)s"))
        log.addHandler(h)
        _writer, _writer_path = log, path
    return _writer


def record(kind: str, name: str, status: str = "ok", seconds: "float | None" = None,
           agent: str = "", detail: str = "") -> None:
    """Write one activity line. Never raises (the action itself must not fail because of it)."""
    try:
        from .doctor.logscan import redact

        if not agent:
            try:
                from .events import CURRENT_AGENT

                agent = CURRENT_AGENT.get() or ""
            except Exception:
                agent = ""
        row = {"ts": _dt.now(_tz.utc).isoformat(timespec="milliseconds"), "kind": kind,
               "name": redact(" ".join(str(name).split()))[:160], "status": status,
               "ms": None if seconds is None else int(round(seconds * 1000)), "agent": agent,
               "detail": redact(" ".join(str(detail or "").split()))[:400]}
        _get_writer().info(_json.dumps(row, ensure_ascii=False))
    except Exception:  # pragma: no cover - logging must never break the app
        pass


def recorded(kind: str, name_of: Callable[..., str]) -> Callable:
    """Decorator: record each call of a function (sync or async) as one activity line."""
    import functools
    import inspect

    def deco(fn: Callable) -> Callable:
        def _name(a: tuple, k: dict) -> str:
            try:
                return name_of(*a, **k)
            except Exception:
                return fn.__name__

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*a: Any, **k: Any) -> Any:
                t = time.time()
                try:
                    out = await fn(*a, **k)
                except Exception as e:
                    record(kind, _name(a, k), "error", time.time() - t, detail=str(e))
                    raise
                record(kind, _name(a, k), "ok", time.time() - t)
                return out
            return awrapper

        @functools.wraps(fn)
        def wrapper(*a: Any, **k: Any) -> Any:
            t = time.time()
            try:
                out = fn(*a, **k)
            except Exception as e:
                record(kind, _name(a, k), "error", time.time() - t, detail=str(e))
                raise
            record(kind, _name(a, k), "ok", time.time() - t)
            return out
        return wrapper
    return deco
