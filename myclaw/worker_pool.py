"""Worker pool manager for tool execution and background task processing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(order=True)
class _QueuedTask:
    priority: int
    created_at: float
    fn: Callable[..., Any] = field(compare=False)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    future: asyncio.Future = field(compare=False, default=None)
    timeout: Optional[float] = field(compare=False, default=None)


class WorkerPoolManager:
    """Async worker pool with queueing, metrics and graceful shutdown."""

    def __init__(self, max_workers: int = 5, task_timeout: float = 30.0, queue_size: int = 100):
        self.max_workers = max(1, int(max_workers))
        self.task_timeout = float(task_timeout)
        self.queue_size = max(1, int(queue_size))
        self._queue: asyncio.PriorityQueue[_QueuedTask] = asyncio.PriorityQueue(maxsize=self.queue_size)
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._completed = 0
        self._failed = 0
        self._submitted = 0
        self._started_at: Optional[float] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        self._workers = [asyncio.create_task(self._worker_loop(i)) for i in range(self.max_workers)]
        logger.info("WorkerPoolManager started with %s workers", self.max_workers)

    async def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        priority: int = 5,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        if not self._running:
            await self.start()

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        task = _QueuedTask(
            priority=int(priority),
            created_at=time.time(),
            fn=fn,
            args=args,
            kwargs=kwargs,
            future=fut,
            timeout=timeout if timeout is not None else self.task_timeout,
        )
        await self._queue.put(task)
        self._submitted += 1
        return await fut

    async def _worker_loop(self, worker_id: int) -> None:
        while self._running:
            queued_task = await self._queue.get()
            try:
                result = await self._run_task(queued_task)
                if not queued_task.future.done():
                    queued_task.future.set_result(result)
                self._completed += 1
            except Exception as exc:
                if not queued_task.future.done():
                    queued_task.future.set_exception(exc)
                self._failed += 1
                logger.error("Worker %s task failed: %s", worker_id, exc)
            finally:
                self._queue.task_done()

    async def _run_task(self, queued_task: _QueuedTask) -> Any:
        fn = queued_task.fn
        timeout = queued_task.timeout

        if asyncio.iscoroutinefunction(fn):
            coro = fn(*queued_task.args, **queued_task.kwargs)
        else:
            coro = asyncio.to_thread(fn, *queued_task.args, **queued_task.kwargs)

        if timeout and timeout > 0:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    async def shutdown(self, wait: bool = True) -> None:
        if not self._running:
            return
        self._running = False
        if wait:
            await self._queue.join()
        for worker in self._workers:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()
        logger.info("WorkerPoolManager shutdown complete")

    async def resize(self, max_workers: int) -> Dict[str, Any]:
        max_workers = max(1, int(max_workers))
        if max_workers == self.max_workers:
            return {"resized": False, "max_workers": self.max_workers}
        await self.shutdown(wait=False)
        self.max_workers = max_workers
        self._queue = asyncio.PriorityQueue(maxsize=self.queue_size)
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        await self.start()
        return {"resized": True, "max_workers": self.max_workers}

    def get_stats(self) -> Dict[str, Any]:
        uptime = 0.0
        if self._started_at:
            uptime = time.time() - self._started_at
        return {
            "running": self._running,
            "max_workers": self.max_workers,
            "queue_size": self.queue_size,
            "queued_tasks": self._queue.qsize(),
            "submitted": self._submitted,
            "completed": self._completed,
            "failed": self._failed,
            "uptime_seconds": round(uptime, 2),
        }
