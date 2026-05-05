"""
Async Scheduler — asyncio-native job queue for background tasks.

Phase 6.2: Replaces ``apscheduler.BackgroundScheduler`` with a lightweight,
asyncio-based scheduler that runs inside the main event loop. No external
dependencies required (arq/celery can be plugged in later if needed).

Features:
    - Interval triggers (recurring jobs)
    - Date triggers (one-shot jobs)
    - Graceful start/stop with pending job completion
    - Job persistence to JSONL for durability across restarts
    - Compatible API with apscheduler for easy migration

Usage:
    from myclaw.async_scheduler import AsyncScheduler

    scheduler = AsyncScheduler()
    scheduler.add_job(my_func, 'interval', hours=2, args=(...))
    scheduler.add_job(my_func, 'date', run_date=datetime(...), kwargs={...})
    await scheduler.start()
    ...
    await scheduler.shutdown(wait=True)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from .scheduler_features import (
    Checkpoint,
    ComplexityAnalyzer,
    ComplexityScore,
    JobContext,
    OutputValidator,
    RetryPolicy,
    TaskBudget,
    ValidationError,
)

logger = logging.getLogger(__name__)


def _exc_matches(exc: BaseException, types: tuple) -> bool:
    """Return True if *exc* is an instance of any class in *types*."""
    return isinstance(exc, types) if types else False


class TriggerType(Enum):
    INTERVAL = "interval"
    DATE = "date"


class Job:
    """Represents a scheduled job."""

    def __init__(
        self,
        func: Callable,
        trigger: TriggerType,
        job_id: str,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        # Interval-specific
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        # Date-specific
        run_date: Optional[datetime] = None,
        # Common
        data: Optional[Any] = None,
        max_instances: int = 1,
        # Reliability extensions (Phase 6.3) — all optional / additive
        retry_policy: Optional[RetryPolicy] = None,
        budget: Optional[TaskBudget] = None,
        validator: Optional[OutputValidator] = None,
        checkpoint_enabled: bool = False,
        complexity: Optional[ComplexityScore] = None,
    ):
        self.id = job_id
        self.func = func
        self.trigger = trigger
        self.args = args
        self.kwargs = kwargs or {}
        self.data = data
        self.max_instances = max_instances
        self.enabled = True
        self._running = 0
        # Reliability extensions
        self.retry_policy = retry_policy
        self.budget = budget
        self.validator = validator
        self.checkpoint_enabled = checkpoint_enabled
        self.complexity = complexity

        # Interval config
        self.interval_seconds = seconds + minutes * 60 + hours * 3600

        # Date config
        self.run_date = run_date

        # Runtime state
        self.next_run: Optional[datetime] = None
        self._compute_next_run()

    def _compute_next_run(self) -> None:
        """Calculate the next execution time."""
        if self.trigger == TriggerType.INTERVAL:
            if self.next_run is None:
                self.next_run = datetime.now() + timedelta(seconds=self.interval_seconds)
            else:
                self.next_run = self.next_run + timedelta(seconds=self.interval_seconds)
        elif self.trigger == TriggerType.DATE:
            self.next_run = self.run_date

    def to_dict(self) -> Dict[str, Any]:
        """Serialise job metadata (func name only; func object is local)."""
        return {
            "id": self.id,
            "trigger": self.trigger.value,
            "func_name": getattr(self.func, "__name__", str(self.func)),
            "interval_seconds": self.interval_seconds,
            "run_date": self.run_date.isoformat() if self.run_date else None,
            "args": list(self.args),
            "kwargs": self.kwargs,
            "data": self.data,
            "enabled": self.enabled,
            "max_instances": self.max_instances,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], func: Callable) -> "Job":
        """Reconstruct a Job from serialised metadata (requires func reference)."""
        trigger = TriggerType(d["trigger"])
        run_date = datetime.fromisoformat(d["run_date"]) if d.get("run_date") else None
        job = cls(
            func=func,
            trigger=trigger,
            job_id=d["id"],
            args=tuple(d.get("args", [])),
            kwargs=d.get("kwargs", {}),
            seconds=d.get("interval_seconds", 0),
            run_date=run_date,
            data=d.get("data"),
            max_instances=d.get("max_instances", 1),
        )
        job.enabled = d.get("enabled", True)
        return job


class AsyncScheduler:
    """Asyncio-native scheduler for background tasks.

    Replaces ``apscheduler.BackgroundScheduler`` with a solution that runs
    inside the existing event loop, eliminating thread-safety issues and
    reducing dependencies.
    """

    def __init__(
        self,
        persistence_path: Optional[Path] = None,
        poll_interval: float = 1.0,
        max_concurrency: int = 10,
    ):
        self._jobs: Dict[str, Job] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._poll_interval = poll_interval
        self._persistence_path = persistence_path
        self._shutdown_event = asyncio.Event()
        # SECURITY / STABILITY FIX (2026-04-23): Global concurrency limit to prevent
        # thundering herd when many jobs are due simultaneously.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ── Public API (apscheduler-compatible) ───────────────────────────────────

    def add_job(
        self,
        func: Callable,
        trigger: str,
        args: Optional[tuple] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        id: Optional[str] = None,  # noqa: A002
        *,
        retry_policy: Optional[RetryPolicy] = None,
        budget: Optional[TaskBudget] = None,
        validator: Optional[OutputValidator] = None,
        checkpoint_enabled: bool = False,
        complexity: Optional[ComplexityScore] = None,
        **trigger_kwargs,
    ) -> Job:
        """Add a job to the scheduler.

        Args:
            func: Coroutine function or regular callable to execute.
            trigger: ``'interval'`` or ``'date'``.
            args: Positional arguments for ``func``.
            kwargs: Keyword arguments for ``func``.
            id: Unique job identifier (auto-generated if omitted).
            retry_policy: Optional :class:`RetryPolicy` for failed/invalid runs.
            budget: Optional :class:`TaskBudget` enforcing wall-clock timeout.
            validator: Optional :class:`OutputValidator`; failures trigger retry.
            checkpoint_enabled: If True, a :class:`Checkpoint` is created and
                injected into a ``ctx`` kwarg of *func* (if *func* accepts one).
            complexity: Optional pre-computed :class:`ComplexityScore`.
            **trigger_kwargs:
                For ``interval``: ``seconds``, ``minutes``, ``hours``
                For ``date``: ``run_date`` (datetime object)
        """
        job_id = id or f"job_{uuid.uuid4().hex[:8]}"
        ttype = TriggerType(trigger)
        common = dict(
            retry_policy=retry_policy,
            budget=budget,
            validator=validator,
            checkpoint_enabled=checkpoint_enabled,
            complexity=complexity,
        )

        if ttype == TriggerType.INTERVAL:
            seconds = trigger_kwargs.get("seconds", 0)
            minutes = trigger_kwargs.get("minutes", 0)
            hours = trigger_kwargs.get("hours", 0)
            job = Job(
                func=func,
                trigger=ttype,
                job_id=job_id,
                args=args or (),
                kwargs=kwargs,
                seconds=seconds,
                minutes=minutes,
                hours=hours,
                data=trigger_kwargs.get("data"),
                **common,
            )
        elif ttype == TriggerType.DATE:
            run_date = trigger_kwargs.get("run_date")
            if run_date is None:
                raise ValueError("'date' trigger requires 'run_date' parameter")
            job = Job(
                func=func,
                trigger=ttype,
                job_id=job_id,
                args=args or (),
                kwargs=kwargs,
                run_date=run_date,
                data=trigger_kwargs.get("data"),
                **common,
            )
        else:
            raise ValueError(f"Unsupported trigger: {trigger}")

        self._jobs[job_id] = job
        logger.info(f"Scheduled job '{job_id}' ({trigger}) -> {func.__name__}")
        self._persist_jobs()
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID. Returns True if found and removed."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._persist_jobs()
            logger.info(f"Removed job '{job_id}'")
            return True
        return False

    def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve a job by ID."""
        return self._jobs.get(job_id)

    def get_jobs(self) -> List[Job]:
        """Return all scheduled jobs."""
        return list(self._jobs.values())

    def remove_all_jobs(self) -> None:
        """Clear all jobs."""
        self._jobs.clear()
        self._persist_jobs()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._running = True
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="AsyncScheduler")
        # Brief yield so the loop begins before caller continues
        await asyncio.sleep(0)
        logger.info("AsyncScheduler started")

    async def shutdown(self, wait: bool = True) -> None:
        """Stop the scheduler.

        Args:
            wait: If True, wait for currently-running jobs to finish.
        """
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()

        if self._task:
            if wait:
                try:
                    await asyncio.wait_for(self._task, timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("Scheduler shutdown timed out; cancelling pending jobs")
                    self._task.cancel()
            else:
                self._task.cancel()
            self._task = None

        logger.info("AsyncScheduler shutdown complete")

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main scheduling loop."""
        while self._running:
            now = datetime.now()
            due_jobs: List[Job] = []

            for job in list(self._jobs.values()):
                if not job.enabled:
                    continue
                if job.next_run and job.next_run <= now:
                    due_jobs.append(job)

            # Execute due jobs concurrently
            if due_jobs:
                await asyncio.gather(
                    *[self._execute_job(job) for job in due_jobs],
                    return_exceptions=True,
                )

            # Wait for next poll or shutdown
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._poll_interval,
                )
            except asyncio.TimeoutError:
                pass

    async def _execute_job(self, job: Job) -> None:
        """Execute a single job and reschedule if interval.

        Honors (when configured on the job):
            - ``budget`` — wall-clock timeout via ``asyncio.wait_for``
            - ``validator`` — post-run output check; failure triggers retry
            - ``retry_policy`` — retries with backoff; ``fallback`` after exhaustion
            - ``checkpoint_enabled`` — creates ``Checkpoint`` and injects ``ctx``
        """
        if job._running >= job.max_instances:
            logger.warning(f"Job '{job.id}' skipped: max_instances ({job.max_instances}) reached")
            return

        # STABILITY FIX: Acquire global concurrency semaphore to prevent
        # thundering herd when many jobs are due simultaneously.
        async with self._semaphore:
            job._running += 1
            try:
                await self._run_with_reliability(job)
            finally:
                job._running -= 1
                if job.trigger == TriggerType.INTERVAL:
                    job._compute_next_run()
                elif job.trigger == TriggerType.DATE:
                    # One-shot: remove after execution
                    self._jobs.pop(job.id, None)
                    self._persist_jobs()

    async def _run_with_reliability(self, job: Job) -> Any:
        """Run a job with retry, budget, validation, and context injection."""
        policy = job.retry_policy or RetryPolicy(max_attempts=1)
        budget = job.budget
        ckpt = Checkpoint(job.id) if job.checkpoint_enabled else None
        ctx = JobContext(job_id=job.id, scheduler=self, checkpoint=ckpt, budget=budget or TaskBudget())

        # Inject ctx into kwargs only if the function declares it
        sig_params: set[str] = set()
        try:
            sig_params = set(inspect.signature(job.func).parameters.keys())
        except (TypeError, ValueError):
            pass
        ctx_kw: Optional[str] = None
        for name in ("ctx", "context", "job_ctx"):
            if name in sig_params:
                ctx_kw = name
                break

        last_exc: Optional[BaseException] = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                logger.debug(f"Executing job '{job.id}' (attempt {attempt}/{policy.max_attempts})")
                if ckpt is not None:
                    ckpt.save(step=f"attempt_{attempt}_start", status="in_progress")

                kwargs = dict(job.kwargs)
                if ctx_kw is not None:
                    kwargs[ctx_kw] = ctx

                # Build the awaitable (coroutine or threaded sync call)
                if inspect.iscoroutinefunction(job.func):
                    awaitable = job.func(*job.args, **kwargs)
                else:
                    loop = asyncio.get_running_loop()
                    awaitable = loop.run_in_executor(None, lambda: job.func(*job.args, **kwargs))

                # Apply wall-clock timeout from budget if set
                if budget is not None and budget.duration_s > 0:
                    result = await asyncio.wait_for(awaitable, timeout=budget.duration_s)
                else:
                    result = await awaitable

                # Output validation
                if job.validator is not None:
                    err = job.validator(result)
                    if err:
                        raise ValidationError(f"output validation failed: {err}")

                if ckpt is not None:
                    ckpt.save(
                        step="completed",
                        data={"result_preview": str(result)[:200] if result is not None else None},
                        status="completed",
                    )
                logger.debug(f"Job '{job.id}' succeeded on attempt {attempt}")
                return result

            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    f"Job '{job.id}' attempt {attempt}/{policy.max_attempts} TIMED OUT "
                    f"after {budget.duration_s if budget else '?'}s"
                )
                if ckpt is not None:
                    ckpt.save(step=f"attempt_{attempt}_timeout", status="failed",
                              data={"error": "timeout", "budget_s": budget.duration_s if budget else None})
                if not _exc_matches(exc, policy.retry_on):
                    break
            except policy.retry_on as exc:
                last_exc = exc
                logger.warning(
                    f"Job '{job.id}' attempt {attempt}/{policy.max_attempts} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                if ckpt is not None:
                    ckpt.save(step=f"attempt_{attempt}_failed", status="failed",
                              data={"error": str(exc), "error_type": type(exc).__name__})
            except Exception as exc:
                # Not in retry_on — fail immediately
                last_exc = exc
                logger.error(f"Job '{job.id}' failed with non-retryable error: {exc}")
                if ckpt is not None:
                    ckpt.save(step="non_retryable_error", status="failed",
                              data={"error": str(exc), "error_type": type(exc).__name__})
                break

            # Backoff before next attempt
            if attempt < policy.max_attempts:
                delay = policy.compute_delay(attempt)
                logger.info(f"Job '{job.id}' retrying in {delay:.2f}s")
                await asyncio.sleep(delay)

        # All retries exhausted (or non-retryable). Try fallback.
        if policy.fallback is not None:
            try:
                logger.info(f"Job '{job.id}' invoking fallback after failure")
                if inspect.iscoroutinefunction(policy.fallback):
                    fb_result = await policy.fallback(job, last_exc, ctx)
                else:
                    loop = asyncio.get_running_loop()
                    fb_result = await loop.run_in_executor(
                        None, lambda: policy.fallback(job, last_exc, ctx)
                    )
                if ckpt is not None:
                    ckpt.save(step="fallback_completed", status="completed_fallback",
                              data={"fallback_result_preview": str(fb_result)[:200]})
                return fb_result
            except Exception as exc:
                logger.error(f"Job '{job.id}' fallback also failed: {exc}")
                if ckpt is not None:
                    ckpt.save(step="fallback_failed", status="failed",
                              data={"fallback_error": str(exc)})

        if ckpt is not None:
            ckpt.save(step="exhausted", status="failed",
                      data={"final_error": str(last_exc) if last_exc else "unknown"})
        logger.error(
            f"Job '{job.id}' permanently failed after {policy.max_attempts} attempt(s): {last_exc}"
        )
        return None

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_jobs(self) -> None:
        """Write job metadata to disk for durability."""
        if self._persistence_path is None:
            return
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            records = [j.to_dict() for j in self._jobs.values()]
            self._persistence_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not persist jobs: {exc}")

    def load_jobs(self, func_resolver: Dict[str, Callable]) -> None:
        """Restore jobs from disk (func_resolver maps func_name -> callable)."""
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        try:
            records = json.loads(self._persistence_path.read_text(encoding="utf-8"))
            for rec in records:
                func_name = rec.get("func_name")
                func = func_resolver.get(func_name)
                if func is None:
                    logger.warning(
                        f"Cannot restore job '{rec['id']}': function '{func_name}' not found"
                    )
                    continue
                job = Job.from_dict(rec, func)
                self._jobs[job.id] = job
            logger.info(f"Restored {len(self._jobs)} jobs from persistence")
        except Exception as exc:
            logger.warning(f"Could not load persisted jobs: {exc}")


# ── Convenience: global singleton (similar to apscheduler pattern) ────────────

_scheduler_instance: Optional[AsyncScheduler] = None


def get_scheduler(persistence_path: Optional[Path] = None) -> AsyncScheduler:
    """Return the global AsyncScheduler singleton."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = AsyncScheduler(persistence_path=persistence_path)
    return _scheduler_instance
