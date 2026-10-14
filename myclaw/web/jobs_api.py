"""Jobs API (added in v0.7.1) — add, monitor, modify, evaluate, benchmark.

GET    /api/jobs                       all jobs with stats and running state
POST   /api/jobs                       create
GET    /api/jobs/{id}                  one job + recent runs
PATCH  /api/jobs/{id}                  modify (also enable/disable)
DELETE /api/jobs/{id}                  delete job and its history
POST   /api/jobs/{id}/run              run now (in the background)
GET    /api/jobs/{id}/runs             run history (?kind=run|bench&limit=50)
POST   /api/jobs/{id}/benchmark        {models: [{provider, model}], evaluate}
POST   /api/jobs/runs/{run_id}/evaluate  score a run with the main (or given) LLM
POST   /api/jobs/runs/{run_id}/cancel    stop a running run

Jobs run agents and can be scheduled, so every route uses the settings guard
(this computer or the admin API key).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.jobs import store
from myclaw.jobs.runner import JobRunner
from myclaw.web.settings_api import guard

router = APIRouter()
_runner: JobRunner | None = None


def set_registry_getter(registry: Callable[[], dict[str, Any]], chat_engine: Callable[[], Any]) -> None:
    global _runner
    _runner = JobRunner(registry, chat_engine)
    from myclaw.web import backup_api
    _runner.on_tick.append(backup_api.auto_backup_tick)


def runner() -> JobRunner:
    if _runner is None:
        raise HTTPException(status_code=503, detail="Job runner not ready")
    return _runner


def start_runner() -> None:
    if _runner is not None:
        _runner.start()


async def stop_runner() -> None:
    if _runner is not None:
        await _runner.stop()


class JobIn(BaseModel):
    name: str | None = None
    prompt: str | None = None
    agent: str | None = None
    schedule: str | None = None
    every_minutes: int | None = None
    at_time: str | None = None
    days: str | None = None
    run_at: float | None = None
    enabled: bool | None = None
    channel_id: str | None = None
    success_criteria: str | None = None
    auto_evaluate: bool | None = None
    fresh_context: bool | None = None
    timeout_s: int | None = None
    retries: int | None = None
    on_failure: str | None = None   # v0.7.5: "" | "diagnose" | "fix"
    max_steps: int | None = None    # v0.7.5: tool rounds per run


class BenchIn(BaseModel):
    models: list[dict[str, str]]
    evaluate: bool = True


class EvalIn(BaseModel):
    provider: str | None = None
    model: str | None = None


def _with_state(job: dict[str, Any]) -> dict[str, Any]:
    job["running"] = job["id"] in runner().running_job_ids()
    return job


def _check_agent(name: str | None) -> None:
    if name and name not in runner()._registry():
        raise HTTPException(status_code=400, detail=f"Unknown agent '{name}'")


@router.get("/api/jobs", dependencies=guard)
async def list_jobs():
    jobs = await asyncio.to_thread(store.list_jobs)
    return {"jobs": [_with_state(j) for j in jobs], "schedules": list(store.SCHEDULES),
            "agents": list(runner()._registry().keys())}


@router.post("/api/jobs", dependencies=guard)
async def create_job(body: JobIn):
    _check_agent(body.agent)
    try:
        job = await asyncio.to_thread(store.create_job, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_state(job)


@router.get("/api/jobs/{job_id}", dependencies=guard)
async def get_job(job_id: str):
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    runs = await asyncio.to_thread(store.list_runs, job_id, 30)
    return {"job": _with_state(job), "runs": runs}


@router.patch("/api/jobs/{job_id}", dependencies=guard)
async def update_job(job_id: str, body: JobIn):
    _check_agent(body.agent)
    try:
        job = await asyncio.to_thread(store.update_job, job_id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return _with_state(job)


@router.delete("/api/jobs/{job_id}", dependencies=guard)
async def delete_job(job_id: str):
    if not await asyncio.to_thread(store.delete_job, job_id):
        raise HTTPException(status_code=404, detail="No such job")
    return {"status": "deleted"}


@router.post("/api/jobs/{job_id}/run", dependencies=guard)
async def run_job(job_id: str):
    if await asyncio.to_thread(store.get_job, job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    if job_id in runner().running_job_ids():
        raise HTTPException(status_code=409, detail="This job is already running.")
    runner().run_in_background(job_id, trigger="manual")
    return {"status": "started"}


@router.get("/api/jobs/{job_id}/runs", dependencies=guard)
async def job_runs(job_id: str, kind: str | None = None, limit: int = 50):
    return {"runs": await asyncio.to_thread(store.list_runs, job_id, limit, kind)}


@router.post("/api/jobs/{job_id}/benchmark", dependencies=guard)
async def benchmark(job_id: str, body: BenchIn):
    if await asyncio.to_thread(store.get_job, job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    models = [m for m in body.models if m.get("provider") and m.get("model")]
    if not models:
        raise HTTPException(status_code=400, detail="Pick at least one provider + model.")
    if len(models) > 8:
        raise HTTPException(status_code=400, detail="Benchmark at most 8 models at a time.")
    asyncio.create_task(runner().benchmark(job_id, models, body.evaluate))
    return {"status": "started", "models": models}


@router.post("/api/jobs/runs/{run_id}/evaluate", dependencies=guard)
async def evaluate(run_id: int, body: EvalIn = EvalIn()):
    try:
        return await runner().evaluate_run(run_id, body.provider, body.model)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such run")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Evaluation failed: {str(e)[:300]}")


@router.post("/api/jobs/runs/{run_id}/cancel", dependencies=guard)
async def cancel(run_id: int):
    return {"cancelled": runner().cancel_run(run_id)}


# ── v0.7.5: the medic for failed runs ───────────────────────────────────────
@router.post("/api/jobs/runs/{run_id}/diagnose", dependencies=guard)
async def diagnose(run_id: int):
    """Why did this run fail, and what would fix it?"""
    from myclaw.jobs import medic

    try:
        return await medic.diagnose(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such run")


class FixIn(BaseModel):
    run_again: bool = True


@router.post("/api/jobs/runs/{run_id}/fix", dependencies=guard)
async def apply_fix(run_id: int, body: FixIn = FixIn()):
    """Apply the medic's suggested job changes (and run the job again)."""
    from myclaw.jobs import medic

    try:
        job = await medic.apply_fix(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such run")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.run_again:
        runner().run_in_background(job["id"], trigger="medic")
    return {"job": _with_state(job), "running": body.run_again}


@router.post("/api/jobs/runs/{run_id}/code-fix", dependencies=guard)
async def code_fix(run_id: int):
    """Let the medic look for a bug in ZenSynora's code and propose a fix."""
    from myclaw.jobs import medic

    try:
        job = await medic.code_fix_job(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such run")
    runner().run_in_background(job["id"], trigger="medic")
    return {"job_id": job["id"], "name": job["name"]}
