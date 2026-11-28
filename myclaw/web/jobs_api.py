"""Jobs API (added in v0.7.1) — add, monitor, modify, evaluate, benchmark.

GET    /api/jobs                       all jobs with stats and running state
POST   /api/jobs                       create
GET    /api/jobs/{id}                  one job + recent runs
PATCH  /api/jobs/{id}                  modify (also enable/disable)
DELETE /api/jobs/{id}                  delete job and its history
POST   /api/jobs/{id}/run              run now (in the background)
POST   /api/jobs/{id}/ai-ask           ask for an agent/run recommendation
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
from pydantic import BaseModel, Field

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
    context_runs: int | None = Field(None, ge=0, le=20)  # prior finished runs included as prompt context (0 = off)
    insight_notes: bool | None = None    # v0.8.5: save knowledge-base notes from run insights (opt-in)
    insight_profile: bool | None = None  # v0.8.5: propose profile updates from run insights (opt-in)
    doctor_managed: bool | None = None
    doctor_recovery: bool | None = None
    doctor_auto_workflow: bool | None = None
    recovery_attempts: int | None = Field(None, ge=1)
    recovery_timeout_s: int | None = Field(None, ge=10)
    team_agents: list[str] | None = None


class BenchIn(BaseModel):
    models: list[dict[str, str]]
    evaluate: bool = True


class EvalIn(BaseModel):
    provider: str | None = None
    model: str | None = None


class AIAskIn(BaseModel):
    run_id: int | None = None
    question: str = Field("", max_length=2000)


def _with_state(job: dict[str, Any]) -> dict[str, Any]:
    job["running"] = runner().job_busy(job["id"])
    return job


def _check_agent(name: str | None) -> None:
    if name and name not in runner()._registry():
        raise HTTPException(status_code=400, detail=f"Unknown agent '{name}'")
    from myclaw.evolve import fusion_blocks_agent

    if name and fusion_blocks_agent(name):
        raise HTTPException(status_code=409, detail=f"Agent '{name}' is inactive. Ask Doctor to reassign this job.")


@router.get("/api/jobs", dependencies=guard)
async def list_jobs():
    from myclaw.jobs import planning

    jobs = await asyncio.to_thread(store.list_jobs)
    return {"jobs": [_with_state(j) for j in jobs], "schedules": list(store.SCHEDULES),
            "agents": list(planning.available(runner()._registry()))}


@router.post("/api/jobs", dependencies=guard)
async def create_job(body: JobIn):
    _check_agent(body.agent)
    for name in body.team_agents or []:
        _check_agent(name)
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
    if runner().job_busy(job_id):
        raise HTTPException(status_code=409, detail="Stop this job before editing its workflow or recovery settings.")
    _check_agent(body.agent)
    for name in body.team_agents or []:
        _check_agent(name)
    try:
        # Keep the runner reservation check and mutation in the same event-loop turn.
        job = store.update_job(job_id, body.model_dump(exclude_none=True), require_idle=True)
    except store.JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return _with_state(job)


@router.delete("/api/jobs/{job_id}", dependencies=guard)
async def delete_job(job_id: str):
    if runner().job_busy(job_id):
        raise HTTPException(status_code=409, detail="Stop the active run before deleting this job.")
    result = store.delete_job_if_idle(job_id)
    if result == "running":
        raise HTTPException(status_code=409, detail="Stop the active run before deleting this job.")
    if result == "missing":
        raise HTTPException(status_code=404, detail="No such job")
    return {"status": "deleted"}


@router.post("/api/jobs/{job_id}/run", dependencies=guard)
async def run_job(job_id: str):
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    if runner().job_busy(job_id):
        raise HTTPException(status_code=409, detail="This job is already running.")
    if job["planning_state"] == "needs_approval":
        raise HTTPException(status_code=409, detail="Review and approve the Doctor plan before running.")
    runner().run_in_background(job_id, trigger="manual")
    return {"status": "started"}


@router.post("/api/jobs/{job_id}/plan", dependencies=guard)
async def plan_job(job_id: str):
    from myclaw.jobs import planning

    if runner().job_busy(job_id):
        raise HTTPException(status_code=409, detail="Stop the job before revising its Doctor plan.")
    try:
        store.request_planning(job_id, "Explicit user analysis request.")
        return _with_state(await planning.plan_job(job_id, runner()._registry()))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such job")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PlanApprovalIn(BaseModel):
    plan_id: str


@router.post("/api/jobs/{job_id}/plan/approve", dependencies=guard)
async def approve_plan(job_id: str, body: PlanApprovalIn):
    from myclaw.jobs import planning

    if runner().job_busy(job_id):
        raise HTTPException(status_code=409, detail="Stop the job before applying a plan.")
    try:
        return _with_state(await planning.approve(job_id, body.plan_id, runner()._registry()))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such job")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/api/jobs/{job_id}/audit", dependencies=guard)
async def audit(job_id: str):
    return {"job_id": job_id, "events": await asyncio.to_thread(store.job_audit, job_id),
            "subtasks": store._safe_value(await asyncio.to_thread(store.children, job_id), preserve_ids=True)}


@router.post("/api/jobs/{job_id}/stop", dependencies=guard)
async def stop_job(job_id: str):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    task = runner()._running.get(job_id)
    stopped = bool(task and not task.done())
    if stopped:
        store.recovery_state(job_id, "cancelled", "Stop requested by the user.")
        task.cancel()
    return {"stopped": stopped}


@router.post("/api/jobs/runs/{run_id}/retry-assisted", dependencies=guard)
async def retry_assisted(run_id: int):
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such run")
    if run["status"] not in {"error", "timeout", "interrupted", "blocked"}:
        raise HTTPException(status_code=409, detail="Assisted retry requires a failed or blocked run.")
    if store.get_job(run["job_id"]) is None:
        raise HTTPException(status_code=404, detail="The job for this run no longer exists.")
    if runner().job_busy(run["job_id"]):
        raise HTTPException(status_code=409, detail="This job is already running.")
    root = await asyncio.to_thread(store.recovery_root, run_id)
    current_job = await asyncio.to_thread(store.get_job, run["job_id"])
    if root is None or current_job is None:
        raise HTTPException(status_code=404, detail="The job or run was deleted. Reload its history.")
    completed = await asyncio.to_thread(store.completed_recovery, root, current_job)
    if completed:
        return {"status": "already_completed", "run_id": completed["id"], "uid": completed["uid"]}
    if runner().job_busy(run["job_id"]):
        raise HTTPException(status_code=409, detail="This job is already running.")
    runner().run_in_background(run["job_id"], trigger="doctor", failure_run_id=run_id)
    return {"status": "started", "job_id": run["job_id"], "failure_uid": run["uid"]}


@router.post("/api/jobs/{job_id}/ai-ask", dependencies=guard)
async def ai_ask(job_id: str, body: AIAskIn = AIAskIn()):
    """Analyze a job and run, recommending an available agent without applying it."""
    from myclaw.jobs import medic

    try:
        return await medic.ai_ask(
            job_id,
            run_id=body.run_id,
            question=body.question,
            registry=runner()._registry(),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="No such job or run")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    runner().benchmark_in_background(job_id, models, body.evaluate)
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

    saved = await asyncio.to_thread(store.get_run, run_id)
    if saved and runner().job_busy(saved["job_id"]):
        raise HTTPException(status_code=409, detail="Stop this job before applying the Medic fix.")
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
