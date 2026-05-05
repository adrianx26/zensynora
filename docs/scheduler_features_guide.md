# Scheduler Reliability Layer (Phase 6.3)

Adds six capabilities to `AsyncScheduler` and the `schedule` tool family:

| # | Capability                              | Module / API                                |
|---|-----------------------------------------|----------------------------------------------|
| 1 | Automatic output validation             | `OutputValidator` (built-ins below)          |
| 2 | Retry + fallback for failed jobs        | `RetryPolicy(max_attempts, backoff, fallback)` |
| 3 | Automatic task complexity evaluation    | `ComplexityAnalyzer.analyze(task)`           |
| 4 | Auto-splitting complex tasks            | `decompose_task(task)` / `auto_schedule()`   |
| 5 | Spawn sub-tasks from inside a job       | `JobContext.spawn_subtask(...)`              |
| 6 | Checkpointing + progress reporting      | `Checkpoint` + `JobContext.progress(...)`    |
| 7 | Smart time allocation per task          | `TaskBudget.from_complexity(...)`            |

All additions are **opt-in**: existing `add_job()` callers and existing job
functions continue to work unchanged. New behaviour is activated only when
you pass the corresponding kwargs.

---

## Files

- [`myclaw/scheduler_features.py`](../myclaw/scheduler_features.py) — building blocks (`RetryPolicy`, `ComplexityAnalyzer`, `decompose_task`, `Checkpoint`, `TaskBudget`, `JobContext`, validators)
- [`myclaw/async_scheduler.py`](../myclaw/async_scheduler.py) — `Job` carries the new fields; `_execute_job` runs through `_run_with_reliability`
- [`myclaw/tools/scheduler.py`](../myclaw/tools/scheduler.py) — agent-callable tools: `auto_schedule`, `estimate_complexity`, `get_checkpoint`, `list_checkpoints`, `clear_checkpoint`
- [`tests/test_scheduler_features.py`](../tests/test_scheduler_features.py) — 37 unit + integration tests

---

## 1 · Output validation

```python
from myclaw.scheduler_features import NotEmpty, Regex, JSONSchema, Chain
from myclaw.async_scheduler import AsyncScheduler

scheduler.add_job(
    fetch_summary, "interval", hours=1,
    validator=Chain((NotEmpty(), Regex(r"\d{4}-\d{2}-\d{2}"))),
)
```

A validator returns `None` on success, an error string on failure. Built-ins:

- `NotEmpty()` — non-empty result
- `Regex(pattern)` — regex match against `str(result)`
- `MaxLength(n)` — bounded result size
- `JSONSchema(required_keys=(...), types={...})` — lightweight, no `jsonschema` dep
- `Custom(predicate, description="...")` — wrap any `bool` predicate
- `Chain((v1, v2, ...))` — short-circuiting AND

A validation failure raises `ValidationError` internally, which counts as a retryable failure (so it triggers the `RetryPolicy` if one is set).

---

## 2 · Retry + fallback

```python
from myclaw.scheduler_features import RetryPolicy

def fallback(job, exc, ctx):
    log.error("job %s gave up: %s", job.id, exc)
    notify_oncall(job.id)

scheduler.add_job(
    flaky_job, "date", run_date=now,
    retry_policy=RetryPolicy(
        max_attempts=5,
        backoff="exponential",  # "fixed" | "linear" | "exponential"
        base_delay=2.0,
        max_delay=60.0,
        retry_on=(IOError, TimeoutError, ValidationError),
        fallback=fallback,
    ),
)
```

- Backoff is capped at `max_delay`.
- `retry_on` defaults to `(Exception,)`.
- The `fallback` callable receives `(job, last_exc, ctx)` and may be sync or async. Its return value is propagated as the job result.
- Non-retryable exceptions skip remaining attempts and go straight to fallback.

---

## 3 · Complexity evaluation

```python
from myclaw.scheduler_features import ComplexityAnalyzer

c = ComplexityAnalyzer.analyze(
    "Browse the news site, summarize headlines, then email the digest"
)
# ComplexityScore(score=0.62, estimated_tool_calls=8,
#                 estimated_duration_s=85.0, detected_steps=3,
#                 flags=['complex', 'slow-ops', 'multi-step', 'decomposition-recommended'],
#                 decompose=True)
```

Heuristics counted: slow-op keywords (`browse`, `scrape`, `search`, `analyz…`, `compile`, `research`, …), multi-step keywords (`then`, `and`, `after`, `next`, `finally`, …), explicit numbered/bullet lists, raw length, mentions of known tool names.

Agent-facing wrapper:

```python
from myclaw.tools import estimate_complexity
print(estimate_complexity("..."))  # JSON
```

---

## 4 · Auto-decomposition

```python
from myclaw.scheduler_features import decompose_task

decompose_task("1. pull repo\n2. run tests\n3. publish report")
# ["pull repo", "run tests", "publish report"]
```

Decomposition strategy (first match wins):
1. Numbered list (`1. … 2. …`)
2. Bullet list (`- … - …`)
3. Multi-step keyword split (` then `, ` and then `, `, followed by `, `;`)
4. Single task → return as-is

Agent-facing wrapper:

```python
from myclaw.tools import auto_schedule

auto_schedule(
    "1. fetch data\n2. transform\n3. publish",
    delay=60, user_id="u42",
)
# 📊 Complexity: 0.71 (complex, multi-step, explicit-list, decomposition-recommended)
#    Estimated: 6 tool calls · ~52s · 3 step(s)
# 🪓 Auto-split into 3 sub-task(s):
#    • auto_u42_…_sub0: fetch data
#    • auto_u42_…_sub1: transform
#    • auto_u42_…_sub2: publish
```

`auto_schedule(..., auto_split=False)` skips decomposition.

---

## 5 · Spawn sub-tasks from inside a running job

A job function opts in by declaring a `ctx` (or `context`) kwarg:

```python
from myclaw.scheduler_features import JobContext

async def parent(ctx: JobContext = None):
    ctx.progress("phase-1: planning", 1, 3)
    plan = make_plan()
    ctx.progress("phase-2: spawning workers", 2, 3, plan=len(plan))

    for i, item in enumerate(plan):
        ctx.spawn_subtask(child_job, item, sub_id=f"worker-{i}")

    ctx.progress("phase-3: parent done", 3, 3)
```

Sub-tasks are scheduled on the same `AsyncScheduler` with a `date` trigger, so they run once. `ctx.spawned` returns the list of child IDs.

The scheduler injects `ctx` only if the job function declares a parameter named `ctx`, `context`, or `job_ctx`. Existing functions without that parameter are unaffected.

---

## 6 · Checkpointing + progress reporting

```python
scheduler.add_job(my_job, "date", run_date=now, checkpoint_enabled=True)
```

Inside the job:

```python
async def my_job(ctx: JobContext = None):
    ctx.progress("step-1", 1, 4, fetched=120)
    ...
    ctx.progress("step-2", 2, 4)
    ...
```

Each `progress()` call appends to `~/.myclaw/checkpoints/<job_id>.json`:

```json
{
  "task_id": "my_job",
  "status": "completed",
  "last_step": "step-4",
  "last_update": "2026-05-05T17:42:01+00:00",
  "data": {"fetched": 120, "step_n": 4, "step_total": 4},
  "history": [
    {"step": "attempt_1_start", "at": "...", "status": "in_progress"},
    {"step": "step-1", "at": "...", "status": "in_progress"},
    {"step": "step-2", "at": "...", "status": "in_progress"},
    {"step": "step-3", "at": "...", "status": "in_progress"},
    {"step": "step-4", "at": "...", "status": "in_progress"},
    {"step": "completed", "at": "...", "status": "completed"}
  ],
  "total_steps": 4,
  "progress": 1.0
}
```

The scheduler also writes checkpoints automatically at attempt start, on retry, on validation failure, on timeout, and on fallback.

Agent-facing inspection tools:

```python
from myclaw.tools import get_checkpoint, list_checkpoints, clear_checkpoint

list_checkpoints()         # JSON array of all jobs with checkpoints
get_checkpoint("job_id")   # full state JSON
clear_checkpoint("job_id") # delete the file
```

> **Note** — checkpoints record progress; they do not yet support resume-from-step. A crash mid-job still requires the job to start over. Resume is a future extension.

---

## 7 · Smart time allocation

```python
from myclaw.scheduler_features import ComplexityAnalyzer, TaskBudget

c = ComplexityAnalyzer.analyze(task_string)
budget = TaskBudget.from_complexity(c, multiplier=1.5, floor_s=60, ceiling_s=1800)

scheduler.add_job(my_job, "date", run_date=now, budget=budget)
```

`budget.duration_s` becomes a wall-clock timeout enforced by `asyncio.wait_for`. A `TimeoutError` triggers retry if `RetryPolicy(retry_on=...)` includes it (which the default `(Exception,)` does).

Inside the job, `ctx.elapsed()`, `ctx.remaining_budget()`, `ctx.is_over_soft_budget()` (default 70 %), and `ctx.is_over_budget()` let the job adapt:

```python
async def adaptive(ctx: JobContext = None):
    while work_remaining():
        if ctx.is_over_soft_budget():
            ctx.progress("soft-budget hit; checkpointing partial result")
            return partial_result()
        do_work_chunk()
```

---

## Putting it together

```python
from datetime import datetime
from myclaw.async_scheduler import AsyncScheduler
from myclaw.scheduler_features import (
    ComplexityAnalyzer, RetryPolicy, TaskBudget,
    Chain, NotEmpty, JSONSchema,
)

task_text = "1. fetch latest CVEs\n2. correlate with installed packages\n3. write report"
c = ComplexityAnalyzer.analyze(task_text)

scheduler.add_job(
    run_security_audit,
    "interval", hours=6,
    id="sec-audit",
    complexity=c,
    budget=TaskBudget.from_complexity(c),
    retry_policy=RetryPolicy(max_attempts=3, backoff="exponential", base_delay=5.0),
    validator=Chain((NotEmpty(), JSONSchema(required_keys=("cves", "matches")))),
    checkpoint_enabled=True,
)
```

That single call gives you: validated output, automatic retry with exponential backoff, a duration budget derived from task complexity, and a JSON checkpoint trail for every run.

---

## Test coverage

`tests/test_scheduler_features.py` — 37 tests:

- 3 retry-policy backoff math
- 7 validator built-ins (each kind + Chain short-circuit + Custom exception swallow)
- 4 complexity-analyzer assertions
- 5 decomposition strategies (numbered, bullets, keywords, single, cap)
- 3 checkpoint cases (round-trip, clear, sanitisation against `..`)
- 1 budget bounds check
- 7 scheduler-integration tests (retry, fallback, validator-driven retry, budget timeout, ctx + checkpoint, sub-task spawn, no-ctx backwards-compat)

Run with:

```bash
python -m pytest tests/test_scheduler_features.py -v
```

---

*Phase 6.3 · Reliability layer · 2026-05-05*
