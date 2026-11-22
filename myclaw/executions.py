"""Cooperative cross-process leases for agent turns and jobs."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

_current = contextvars.ContextVar("agent_execution_lease", default=None)
logger = logging.getLogger(__name__)


def _db_path() -> Path:
    return Path.home() / ".myclaw" / "agent-executions.sqlite"


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=3)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS executions "
                   "(id TEXT PRIMARY KEY, agent TEXT NOT NULL, kind TEXT NOT NULL, pid INTEGER NOT NULL, started REAL NOT NULL)")
        with db:
            yield db
    finally:
        db.close()


def _start(name: str, kind: str) -> str:
    from .evolve import LifecycleError, _lifecycle_guard, fusion_blocks_agent

    try:
        with _lifecycle_guard():
            if fusion_blocks_agent(name):
                raise LifecycleError(f"Agent {name} is in the graveyard or its fusion needs recovery.")
            token = uuid.uuid4().hex
            with _connect() as db:
                db.execute("INSERT INTO executions VALUES (?, ?, ?, ?, ?)",
                           (token, name, kind, os.getpid(), time.time()))
            return token
    except (OSError, sqlite3.Error) as exc:
        raise LifecycleError(f"Cannot register agent execution; check execution storage and retry: {exc}") from exc


def _finish(token: str) -> None:
    try:
        with _connect() as db:
            db.execute("DELETE FROM executions WHERE id = ?", (token,))
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Cannot release execution lease; fusion will remain blocked until process exit: %s", exc)


def active_for(names: tuple[str, ...] | None) -> list[tuple[str, str, int]]:
    """Call under the lifecycle lock so no execution can start while checking."""
    from .evolve import LifecycleError

    try:
        with _connect() as db:
            rows = db.execute("SELECT id, agent, kind, pid FROM executions").fetchall()
            import psutil

            active = []
            wanted = {name.casefold() for name in names} if names is not None else None
            for token, name, kind, pid in rows:
                try:
                    alive = psutil.pid_exists(pid)
                except Exception:
                    alive = True
                if not alive:
                    db.execute("DELETE FROM executions WHERE id = ?", (token,))
                elif wanted is None or name.casefold() in wanted:
                    active.append((name, kind, pid))
            return active
    except (OSError, sqlite3.Error, ImportError) as exc:
        raise LifecycleError(f"Cannot inspect running agents; check execution storage and retry: {exc}") from exc


class _Lease:
    def __init__(self, token: str):
        self.token = token
        self._count = 1
        self._lock = threading.Lock()

    def track(self, task: asyncio.Future) -> None:
        with self._lock:
            if not self._count:
                return
            self._count += 1
        task.add_done_callback(self._background_done)

    def _drop(self) -> bool:
        with self._lock:
            self._count -= 1
            return self._count == 0

    def _background_done(self, task: asyncio.Future) -> None:
        if task.cancelled():
            return
        if self._drop():
            # A failed cleanup leaves a conservative stale lease until the process exits.
            asyncio.get_running_loop().run_in_executor(None, _finish, self.token)


def track_background(task: asyncio.Task) -> None:
    lease = _current.get()
    if lease is not None:
        lease.track(task)


def current_id() -> str | None:
    """Stable id of the current agent turn, or None outside an execution."""
    lease = _current.get()
    return str(lease.token) if lease is not None else None


def retain_until_exit() -> None:
    """Conservatively retain a lease after work may outlive a timeout."""
    lease = _current.get()
    if lease is not None:
        with lease._lock:
            lease._count += 1


async def tracked_to_thread(func, /, *args, **kwargs):
    """Keep the caller's lease until the worker really exits, even after timeout."""
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    future = loop.run_in_executor(None, functools.partial(context.run, func, *args, **kwargs))
    lease = _current.get()
    if lease is not None:
        lease.track(future)
    return await asyncio.shield(future)


@asynccontextmanager
async def execution(name: str, kind: str):
    from .evolve import LifecycleError

    for attempt in range(20):
        task = asyncio.create_task(asyncio.to_thread(_start, name, kind))
        try:
            token = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            def cleanup(done):
                if not done.cancelled() and done.exception() is None:
                    asyncio.get_running_loop().run_in_executor(None, _finish, done.result())

            task.add_done_callback(cleanup)
            raise
        except LifecycleError as exc:
            if "Another agent lifecycle operation is running" not in str(exc) or attempt == 19:
                raise
            await asyncio.sleep(0.05)
    lease = _Lease(token)
    current = _current.set(lease)
    abandoned = False
    try:
        yield
    except (asyncio.CancelledError, GeneratorExit):
        # A cancelled coroutine may have left work in a thread or subprocess.
        # Keep the lease until this process exits rather than risk a live agent.
        abandoned = True
        raise
    finally:
        try:
            _current.reset(current)
        except ValueError:
            pass
        if not abandoned and lease._drop():
            await asyncio.shield(asyncio.to_thread(_finish, lease.token))
