"""Structured progress shared by maintenance processes (stdlib only)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

PREFIX = "PROCESS "
COLORS = {"ok": 32, "failed": 31, "skipped": 33, "waiting": 33, "running": 36,
          "rollback-running": 33, "rolled-back": 32, "rollback-failed": 31, "detail": 37}
_sink: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = contextvars.ContextVar(
    "maintenance_progress", default=None)
_current: contextvars.ContextVar[str] = contextvars.ContextVar("maintenance_step", default="")


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        text = record.getMessage()
        if text.startswith(PREFIX):
            event = json.loads(text[len(PREFIX):])
            text = f"[{event['status'].upper()}] {event['step']}: {event['text']}"
            if sys.stdout.isatty():
                return f"\033[{COLORS.get(event['status'], 37)}m{text}\033[0m"
            # Web UI reads the structured events from redirected stdout.
            return record.getMessage()
        return text


@contextmanager
def capture(sink: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(step: str, status: str, text: str, **data: Any) -> None:
    event = {"step": step, "status": status, "text": text, "at": time.time(), **data}
    sink = _sink.get()
    if sink is not None:
        sink(event)
    else:
        logging.getLogger("zensynora.updater").info("%s%s", PREFIX, json.dumps(event))


@contextmanager
def stage(name: str, text: str) -> Iterator[None]:
    token = _current.set(name)
    started = time.monotonic()
    emit(name, "running", text)
    try:
        yield
    except BaseException as exc:
        emit(name, "failed", str(exc), seconds=round(time.monotonic() - started, 3))
        raise
    else:
        emit(name, "ok", text, seconds=round(time.monotonic() - started, 3))
    finally:
        _current.reset(token)


def detail(text: str, **data: Any) -> None:
    emit(_current.get(), "detail", text, **data)


@contextmanager
def scope(name: str) -> Iterator[None]:
    token = _current.set(name)
    try:
        yield
    finally:
        _current.reset(token)
