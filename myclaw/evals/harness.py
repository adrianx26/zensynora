"""Eval runner: load JSONL → run target → score → report."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional

from .metrics import Metric

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """One row of an eval dataset.

    The ``input`` is what the target callable receives. ``expected`` is
    the per-metric expected value, keyed by metric name. ``metadata``
    is opaque pass-through (tags, source, difficulty, etc.).
    """
    case_id: str
    input: Any
    expected: Mapping[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], default_id: str = "") -> "EvalCase":
        case_id = str(data.get("id", default_id))
        return cls(
            case_id=case_id,
            input=data["input"],
            expected=dict(data.get("expected", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class _CaseResult:
    case_id: str
    success: bool
    error: Optional[str]
    latency_ms: float
    predicted: Any
    scores: Dict[str, float]


@dataclass
class EvalReport:
    """Aggregate results from a single run."""
    dataset_size: int
    cases: List[_CaseResult]
    metric_means: Dict[str, float]
    latency_p50_ms: float
    latency_p95_ms: float
    failure_count: int
    duration_seconds: float

    @property
    def overall_score(self) -> float:
        """Mean of metric means. Convenient single number for trend tracking."""
        if not self.metric_means:
            return 0.0
        return sum(self.metric_means.values()) / len(self.metric_means)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["overall_score"] = self.overall_score
        return d


# ── Loading & saving ─────────────────────────────────────────────────────


def load_jsonl(path: Path) -> List[EvalCase]:
    """Load a JSONL dataset. One ``EvalCase`` per non-blank line.

    The file format is intentionally simple: each line is an object with
    ``input`` (required), optional ``expected`` (dict keyed by metric),
    optional ``id``, optional ``metadata``.
    """
    path = Path(path)
    cases: List[EvalCase] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i + 1}: invalid JSON: {e}") from e
        cases.append(EvalCase.from_dict(data, default_id=f"case-{i + 1}"))
    return cases


def save_report(report: EvalReport, path: Path) -> None:
    """Persist a report as pretty-printed JSON next to the dataset."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
    )


# ── Runner ────────────────────────────────────────────────────────────────


# Type alias for the target the eval drives.
TargetFn = Callable[[Any], Awaitable[Any]]


class Evaluator:
    """Runs an async ``target`` over a dataset and computes per-metric scores.

    Args:
        target: ``async (input) -> output``. The thing being evaluated —
            an Agent's ``think`` method, a tool, a deterministic stub.
        metrics: Mapping of metric name → callable. The same metric name
            in each case's ``expected`` is fed the corresponding value.
        concurrency: Maximum simultaneous target calls. ``1`` = strict
            sequential (good for reproducibility on rate-limited APIs).
        timeout_per_case: Seconds before a case is marked failed.
    """

    def __init__(
        self,
        target: TargetFn,
        metrics: Mapping[str, Metric],
        concurrency: int = 1,
        timeout_per_case: Optional[float] = None,
    ) -> None:
        if not metrics:
            raise ValueError("Evaluator requires at least one metric")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._target = target
        self._metrics = dict(metrics)
        self._concurrency = concurrency
        self._timeout = timeout_per_case

    async def run(self, cases: Iterable[EvalCase]) -> EvalReport:
        cases = list(cases)
        if not cases:
            return EvalReport(
                dataset_size=0,
                cases=[],
                metric_means={name: 0.0 for name in self._metrics},
                latency_p50_ms=0.0,
                latency_p95_ms=0.0,
                failure_count=0,
                duration_seconds=0.0,
            )

        sem = asyncio.Semaphore(self._concurrency)
        run_start = time.monotonic()
        results = await asyncio.gather(*(self._run_one(sem, c) for c in cases))
        duration = time.monotonic() - run_start

        # Aggregate metric scores; failed cases score 0 across all metrics.
        metric_scores: Dict[str, List[float]] = {name: [] for name in self._metrics}
        latencies: List[float] = []
        failures = 0
        for r in results:
            latencies.append(r.latency_ms)
            if not r.success:
                failures += 1
                for name in self._metrics:
                    metric_scores[name].append(0.0)
                continue
            for name in self._metrics:
                metric_scores[name].append(r.scores.get(name, 0.0))

        means = {n: (sum(s) / len(s) if s else 0.0) for n, s in metric_scores.items()}
        return EvalReport(
            dataset_size=len(cases),
            cases=results,
            metric_means=means,
            latency_p50_ms=_percentile(latencies, 0.50),
            latency_p95_ms=_percentile(latencies, 0.95),
            failure_count=failures,
            duration_seconds=duration,
        )

    async def _run_one(self, sem: asyncio.Semaphore, case: EvalCase) -> _CaseResult:
        async with sem:
            t0 = time.monotonic()
            try:
                if self._timeout:
                    predicted = await asyncio.wait_for(
                        self._target(case.input), timeout=self._timeout
                    )
                else:
                    predicted = await self._target(case.input)
            except Exception as e:
                latency_ms = (time.monotonic() - t0) * 1000
                logger.warning("Eval case %s failed", case.case_id, exc_info=e)
                return _CaseResult(
                    case_id=case.case_id,
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                    latency_ms=latency_ms,
                    predicted=None,
                    scores={n: 0.0 for n in self._metrics},
                )

            latency_ms = (time.monotonic() - t0) * 1000

            # Score every metric for which the case has an expected value.
            scores: Dict[str, float] = {}
            for name, metric in self._metrics.items():
                if name not in case.expected:
                    # No expected value for this metric — neutral 0.
                    scores[name] = 0.0
                    continue
                try:
                    scores[name] = float(metric(predicted, case.expected[name]))
                except Exception as e:
                    logger.warning(
                        "Metric %s failed on case %s", name, case.case_id, exc_info=e
                    )
                    scores[name] = 0.0
            return _CaseResult(
                case_id=case.case_id,
                success=True,
                error=None,
                latency_ms=latency_ms,
                predicted=predicted,
                scores=scores,
            )


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    # Nearest-rank percentile — matches what most ops folks expect from
    # "p95"; full linear interpolation isn't worth the complexity here.
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]
