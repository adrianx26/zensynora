"""Private, durable progress for backups and restores."""

from __future__ import annotations

import json
import errno
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from updater.progress import capture

from .config_encryption import _atomic_write
from .doctor.logscan import redact
from .executions import _lock_file

_lock = threading.Lock()
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


def _path() -> Path:
    return Path.home() / ".myclaw" / "maintenance-progress.json"


def status() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if data.get("status") == "running":
        import psutil

        try:
            alive = psutil.Process(data["pid"]).create_time() == data["process_started"]
        except psutil.NoSuchProcess:
            alive = False
        if not alive:
            data["status"] = "interrupted"
            data["error"] = "Process stopped. Check restore recovery before retrying."
        elif not _lock.locked():
            with open(path.with_suffix(".lock"), "r+b") as handle:
                try:
                    _lock_file(handle)
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                else:
                    data["status"] = "progress-unavailable"
                    data["error"] = "Progress recording stopped. Check the operation result and backup list before retrying."
    return data


def run(action: str, fn: Callable[[], _T]) -> _T:
    if not _lock.acquire(blocking=False):
        raise ValueError("A backup or restore is already running; wait for it to finish.")
    handle = None
    try:
        import psutil

        _path().parent.mkdir(parents=True, exist_ok=True)
        handle = open(_path().with_suffix(".lock"), "a+b")
        if not handle.seek(0, os.SEEK_END):
            handle.write(b"0")
            handle.flush()
        try:
            _lock_file(handle)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ValueError("A backup or restore is already running in another process; wait for it to finish.") from exc
            raise
        state: dict[str, Any] = {
            "id": uuid.uuid4().hex, "action": action, "status": "running", "pid": os.getpid(),
            "started": time.time(), "events": [], "steps": {},
            "process_started": psutil.Process(os.getpid()).create_time(),
        }

        def save() -> None:
            _path().parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(_path(), json.dumps(state))

        last_detail = 0.0
        progress_warning: str | None = None

        def persist() -> None:
            nonlocal progress_warning
            try:
                save()
            except OSError as exc:
                if progress_warning is None:
                    progress_warning = redact(f"Progress could not be saved; check storage. The operation continues: {exc}")
                    logger.warning("%s", progress_warning)
                state["progress_warning"] = progress_warning

        def record(event: dict[str, Any]) -> None:
            nonlocal last_detail
            now = time.monotonic()
            if event["status"] == "detail" and now - last_detail < 0.2:
                return
            last_detail = now
            event = {**event, "text": redact(str(event["text"]))[:1000]}
            state["events"] = [*state["events"][-499:], event]
            if event["status"] != "detail":
                state["steps"][event["step"]] = event
            persist()

        persist()
        try:
            with capture(record):
                result = fn()
        except BaseException as exc:
            for name, event in list(state["steps"].items()):
                if event["status"] == "running":
                    record({**event, "status": "failed", "text": str(exc), "at": time.time()})
            state.update(status="failed", error=redact(str(exc))[:2000], finished=time.time())
            persist()
            raise
        else:
            state.update(status="ok", finished=time.time())
            persist()
            if progress_warning and isinstance(result, dict):
                result["progress_warning"] = progress_warning
            return result
    finally:
        try:
            if handle is not None:
                handle.close()
        finally:
            _lock.release()
