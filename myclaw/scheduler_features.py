"""
Scheduler Features — additive reliability layer for AsyncScheduler.

Provides:
    - RetryPolicy with backoff strategies and fallback hook
    - ComplexityAnalyzer: heuristic static analysis of task descriptions
    - decompose_task: split a complex task description into sub-tasks
    - Checkpoint: per-task JSON checkpoints under ~/.myclaw/checkpoints/
    - TaskBudget: per-task duration/tool-call budget, derivable from complexity
    - OutputValidator and built-ins (NotEmpty, Regex, JSONSchema, Custom, Chain)
    - JobContext: passed to job functions (opt-in via 'ctx' kwarg) — exposes
      checkpoint(), progress(), spawn_subtask(), elapsed(), budget queries

All public APIs are designed to be additive: existing callers of AsyncScheduler
keep working unchanged.

See tests/test_scheduler_features.py for usage examples.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Pattern, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Retry policy
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class RetryPolicy:
    """How to retry a failed job.

    Backoff strategies:
        "fixed"       → base_delay every attempt
        "linear"      → base_delay * attempt (capped at max_delay)
        "exponential" → base_delay * 2**(attempt-1) (capped at max_delay)
    """

    max_attempts: int = 3
    backoff: str = "exponential"
    base_delay: float = 1.0
    max_delay: float = 60.0
    retry_on: tuple = (Exception,)
    fallback: Optional[Callable[..., Any]] = None

    def compute_delay(self, attempt: int) -> float:
        if self.backoff == "fixed":
            d = self.base_delay
        elif self.backoff == "linear":
            d = self.base_delay * attempt
        else:  # exponential (default)
            d = self.base_delay * (2 ** max(0, attempt - 1))
        return min(d, self.max_delay)


# ────────────────────────────────────────────────────────────────────────────
# Output validators
# ────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class OutputValidator(Protocol):
    """Protocol for output validators.

    Implementations return None on success, or a string error message on
    failure. Raising is also acceptable — the scheduler treats it as failure.
    """

    def __call__(self, result: Any) -> Optional[str]: ...


class NotEmpty:
    """Pass if the result is not None / not empty string / not empty container."""

    def __call__(self, result: Any) -> Optional[str]:
        if result is None:
            return "result is None"
        if hasattr(result, "__len__") and len(result) == 0:
            return "result is empty"
        if isinstance(result, str) and not result.strip():
            return "result is whitespace-only"
        return None


@dataclass
class Regex:
    """Pass if str(result) matches the pattern."""

    pattern: str | Pattern[str]
    flags: int = 0

    def __call__(self, result: Any) -> Optional[str]:
        compiled = re.compile(self.pattern, self.flags) if isinstance(self.pattern, str) else self.pattern
        if not compiled.search(str(result)):
            return f"result did not match {compiled.pattern!r}"
        return None


@dataclass
class MaxLength:
    """Pass if len(result) <= max_chars."""

    max_chars: int = 100_000

    def __call__(self, result: Any) -> Optional[str]:
        s = result if isinstance(result, str) else str(result)
        if len(s) > self.max_chars:
            return f"result length {len(s)} exceeds limit {self.max_chars}"
        return None


@dataclass
class JSONSchema:
    """Pass if json.loads(result) matches required keys / types.

    Lightweight (no jsonschema dependency): checks required top-level keys
    and optionally their python type.
    """

    required_keys: tuple = ()
    types: dict = field(default_factory=dict)

    def __call__(self, result: Any) -> Optional[str]:
        try:
            obj = result if isinstance(result, dict) else json.loads(result)
        except Exception as exc:
            return f"result not valid JSON: {exc}"
        if not isinstance(obj, dict):
            return f"result is not a JSON object (got {type(obj).__name__})"
        for key in self.required_keys:
            if key not in obj:
                return f"missing required key '{key}'"
        for key, expected_t in self.types.items():
            if key in obj and not isinstance(obj[key], expected_t):
                return f"key '{key}' wrong type: got {type(obj[key]).__name__}, expected {expected_t.__name__}"
        return None


@dataclass
class Custom:
    """Wrap an arbitrary predicate. Predicate returns True for valid."""

    predicate: Callable[[Any], bool]
    description: str = "custom validator"

    def __call__(self, result: Any) -> Optional[str]:
        try:
            return None if self.predicate(result) else f"{self.description} failed"
        except Exception as exc:
            return f"{self.description} raised: {exc}"


@dataclass
class Chain:
    """Run a sequence of validators; first failure short-circuits."""

    validators: tuple

    def __call__(self, result: Any) -> Optional[str]:
        for v in self.validators:
            err = v(result)
            if err:
                return err
        return None


class ValidationError(Exception):
    """Raised when output validation fails."""


# ────────────────────────────────────────────────────────────────────────────
# Complexity analysis
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class ComplexityScore:
    score: float  # 0.0 – 1.0
    estimated_tool_calls: int
    estimated_duration_s: float
    flags: list[str] = field(default_factory=list)
    decompose: bool = False
    detected_steps: int = 1


class ComplexityAnalyzer:
    """Heuristic static analysis of a task description.

    Counts:
    - Slow-operation keywords (browse, scrape, search, analyze, ...)
    - Multi-step keywords (and, then, after, next, ...)
    - Explicit numbering / bullets
    - Length

    Output is a coarse estimate, used for budgeting and decomposition decisions.
    """

    SLOW_PATTERNS: tuple = (
        "browse", "scrape", "search", "analyz", "summari", "compile", "research",
        "comprehensive", "deep dive", "investigation", "crawl", "fetch", "download",
        "generate", "synthesi", "extract", "translate", "render",
    )
    MULTI_STEP_PATTERNS: tuple = (
        r"\band then\b", r"\bthen\b", r"\bafter that\b", r"\bafter\b",
        r"\bnext\b", r"\bfollowed by\b", r"\bfinally\b", r"\balso\b",
        r"\bonce that's done\b", r";",
    )
    NUMBERED_LIST: Pattern[str] = re.compile(r"(?:^|\n)\s*\d+[.)]\s+\S", re.MULTILINE)
    BULLET_LIST: Pattern[str] = re.compile(r"(?:^|\n)\s*[-*•]\s+\S", re.MULTILINE)
    TOOL_HINTS: tuple = (
        "shell", "browse", "search_kb", "search_knowledge", "write_file",
        "read_file", "register_tool", "schedule", "swarm",
    )

    @classmethod
    def analyze(cls, task: str) -> ComplexityScore:
        text = task.lower()
        flags: list[str] = []

        slow_count = sum(text.count(p) for p in cls.SLOW_PATTERNS)
        multi_step_count = sum(len(re.findall(p, text)) for p in cls.MULTI_STEP_PATTERNS)
        numbered = len(cls.NUMBERED_LIST.findall(task))
        bullets = len(cls.BULLET_LIST.findall(task))
        tool_mentions = sum(text.count(t) for t in cls.TOOL_HINTS)
        length_factor = len(task) / 400.0

        # detected_steps: the strongest signal among (numbered, bullets, multi-step keywords + 1)
        detected_steps = max(1, numbered, bullets, multi_step_count + 1 if multi_step_count else 1)

        # Estimates
        estimated_tool_calls = max(1, slow_count * 2 + tool_mentions + detected_steps)
        estimated_duration_s = 5.0 + slow_count * 30.0 + detected_steps * 8.0 + tool_mentions * 4.0

        # Composite 0..1 score
        score = min(
            1.0,
            (slow_count * 0.15)
            + (detected_steps * 0.10)
            + (tool_mentions * 0.05)
            + min(0.30, length_factor * 0.15),
        )

        if score >= 0.6:
            flags.append("complex")
        if slow_count >= 2:
            flags.append("slow-ops")
        if detected_steps >= 3:
            flags.append("multi-step")
        if estimated_duration_s > 120:
            flags.append("long-running")
        if numbered >= 2 or bullets >= 2:
            flags.append("explicit-list")

        decompose = (score >= 0.6) or (detected_steps >= 3) or (numbered >= 2) or (bullets >= 2)
        if decompose:
            flags.append("decomposition-recommended")

        return ComplexityScore(
            score=round(score, 3),
            estimated_tool_calls=estimated_tool_calls,
            estimated_duration_s=round(estimated_duration_s, 1),
            flags=flags,
            decompose=decompose,
            detected_steps=detected_steps,
        )


def decompose_task(task: str, max_subtasks: int = 8) -> list[str]:
    """Split a complex task description into sub-tasks.

    Strategy (first match wins):
        1. Explicit numbered list ("1. foo  2. bar  3. baz")
        2. Bullet list ("- foo\\n- bar")
        3. Multi-step keywords (split on " then ", " and then ", ", followed by ")
        4. Single sentence — return as-is (one element)
    """
    # 1. Numbered list
    numbered = re.findall(
        r"(?:^|\n)\s*\d+[.)]\s+(.+?)(?=(?:\n\s*\d+[.)])|\Z)",
        task,
        flags=re.DOTALL,
    )
    if len(numbered) >= 2:
        return [s.strip() for s in numbered[:max_subtasks] if s.strip()]

    # 2. Bullet list
    bullets = re.findall(
        r"(?:^|\n)\s*[-*•]\s+(.+?)(?=(?:\n\s*[-*•])|\Z)",
        task,
        flags=re.DOTALL,
    )
    if len(bullets) >= 2:
        return [s.strip() for s in bullets[:max_subtasks] if s.strip()]

    # 3. Multi-step keywords
    delim = re.compile(
        r"\s*(?:,?\s+(?:and then|then|after that|next|followed by|finally)|;)\s+",
        flags=re.IGNORECASE,
    )
    parts = [p.strip().rstrip(".") for p in delim.split(task) if p.strip()]
    parts = [p for p in parts if len(p) >= 8]
    if len(parts) >= 2:
        return parts[:max_subtasks]

    # 4. Fallback: single task
    return [task.strip()]


# ────────────────────────────────────────────────────────────────────────────
# Checkpointing
# ────────────────────────────────────────────────────────────────────────────


class Checkpoint:
    """Per-task JSON checkpoint persisted to disk.

    Default location: ``~/.myclaw/checkpoints/<task_id>.json``.
    """

    def __init__(self, task_id: str, base_dir: Optional[Path] = None) -> None:
        # Strip any path-traversal sequences then collapse unsafe chars.
        safe = re.sub(r"\.\.+", "_", task_id)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe)
        safe = safe.lstrip(".") or "task"
        safe = safe[:120]
        self.task_id = task_id
        base = base_dir or (Path.home() / ".myclaw" / "checkpoints")
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.path = base / f"{safe}.json"

    def save(
        self,
        step: str,
        data: Optional[dict] = None,
        *,
        total_steps: Optional[int] = None,
        status: str = "in_progress",
    ) -> None:
        state = self.load() or {"task_id": self.task_id, "history": [], "data": {}}
        now = datetime.now(timezone.utc).isoformat()
        state["last_step"] = step
        state["last_update"] = now
        state["status"] = status
        state.setdefault("history", []).append({"step": step, "at": now, "status": status})
        if data:
            state.setdefault("data", {}).update(data)
        if total_steps is not None:
            state["total_steps"] = total_steps
            state["progress"] = round(len(state["history"]) / max(1, total_steps), 3)
        try:
            self.path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"checkpoint save failed for '{self.task_id}': {exc}")

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
# Task budget
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class TaskBudget:
    """Per-task wall-clock and tool-call budget."""

    duration_s: float = 300.0
    max_tool_calls: int = 50
    soft_warning_at: float = 0.7  # ratio of duration_s

    @classmethod
    def from_complexity(
        cls,
        complexity: ComplexityScore,
        *,
        multiplier: float = 1.5,
        floor_s: float = 60.0,
        ceiling_s: float = 1800.0,
    ) -> "TaskBudget":
        duration = max(floor_s, min(ceiling_s, complexity.estimated_duration_s * multiplier))
        return cls(
            duration_s=duration,
            max_tool_calls=max(10, complexity.estimated_tool_calls * 2),
        )


# ────────────────────────────────────────────────────────────────────────────
# JobContext — handed to job functions that opt in (signature has 'ctx' or 'context')
# ────────────────────────────────────────────────────────────────────────────


class JobContext:
    """Runtime handle passed to scheduler jobs.

    A job function can opt in by declaring a ``ctx`` (or ``context``) kwarg::

        async def my_job(ctx: JobContext = None):
            ctx.progress("loading", 1, 3)
            ...
            ctx.progress("done", 3, 3)
    """

    def __init__(
        self,
        job_id: str,
        scheduler: Any,  # AsyncScheduler — typed loosely to avoid circular import
        checkpoint: Optional[Checkpoint] = None,
        budget: Optional[TaskBudget] = None,
    ) -> None:
        self.job_id = job_id
        self.scheduler = scheduler
        self.checkpoint = checkpoint
        self.budget = budget or TaskBudget()
        self._started_at = time.time()
        self._spawned: list[str] = []
        self._tool_calls: int = 0

    # ── progress / checkpoint ────────────────────────────────────────────
    def progress(self, step: str, n: int = 0, total: int = 0, **data: Any) -> None:
        """Record progress; persists to checkpoint if enabled."""
        payload = {"step_n": n, "step_total": total, **data} if (n or total) else dict(data)
        logger.info(f"[{self.job_id}] {step} ({n}/{total})" if total else f"[{self.job_id}] {step}")
        if self.checkpoint is not None:
            self.checkpoint.save(step=step, data=payload, total_steps=total or None)

    # ── budget queries ───────────────────────────────────────────────────
    def elapsed(self) -> float:
        return time.time() - self._started_at

    def remaining_budget(self) -> float:
        return max(0.0, self.budget.duration_s - self.elapsed())

    def is_over_soft_budget(self) -> bool:
        return self.elapsed() / self.budget.duration_s >= self.budget.soft_warning_at

    def is_over_budget(self) -> bool:
        return self.elapsed() >= self.budget.duration_s

    def record_tool_call(self) -> None:
        self._tool_calls += 1

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    # ── sub-task spawning ────────────────────────────────────────────────
    def spawn_subtask(
        self,
        func: Callable,
        *args: Any,
        delay_s: float = 0.0,
        sub_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Schedule a child job to run on the same scheduler.

        Returns the child job id.
        """
        sid = sub_id or f"{self.job_id}_sub{len(self._spawned)}"
        run_at = datetime.now() + timedelta(seconds=max(0.0, delay_s))
        self.scheduler.add_job(
            func,
            "date",
            args=args,
            kwargs=kwargs,
            id=sid,
            run_date=run_at,
        )
        self._spawned.append(sid)
        logger.info(f"[{self.job_id}] spawned sub-task '{sid}' (delay {delay_s:.1f}s)")
        return sid

    @property
    def spawned(self) -> list[str]:
        return list(self._spawned)


__all__ = [
    "RetryPolicy",
    "OutputValidator",
    "NotEmpty",
    "Regex",
    "MaxLength",
    "JSONSchema",
    "Custom",
    "Chain",
    "ValidationError",
    "ComplexityScore",
    "ComplexityAnalyzer",
    "decompose_task",
    "Checkpoint",
    "TaskBudget",
    "JobContext",
]
