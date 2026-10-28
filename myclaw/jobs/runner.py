"""Runs jobs: on schedule, on demand, evaluated and benchmarked (v0.7.1).

The runner lives in the Web UI process (``zensynora webui``): a background
task wakes up every few seconds, starts due jobs, and also takes automatic
backups when they are enabled. Jobs whose time passed while the Web UI was
closed run once when it starts again.

A run executes ``agent.think(prompt)`` with:
* its own conversation memory (``MEMORY_SCOPE = job-<id>``), cleared first
  when ``fresh_context`` is on (default);
* a timeout and optional retries;
* ``CURRENT_CHANNEL`` set when the job reports to a chat channel, so the
  agent's tool calls/delegations appear there live, followed by the result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from .. import events
from . import store

logger = logging.getLogger(__name__)

TICK_SECONDS = 5
MAX_CONCURRENT = 3

JUDGE_PROMPT = """You evaluate the result of an automated AI task.
Score how well the RESULT fulfils the TASK (and the SUCCESS CRITERIA, if given)
from 0 (useless/failed) to 10 (perfect). Be strict and concrete.
Reply with JSON only: {"score": <0-10>, "verdict": "pass" | "fail", "reasons": "<one or two sentences>"}"""


class JobRunner:
    def __init__(self, registry: Callable[[], dict[str, Any]], chat_engine: Callable[[], Any] | None = None):
        self._registry = registry
        self._chat = chat_engine
        self._task: asyncio.Task | None = None
        self._running: dict[str, asyncio.Task] = {}  # job_id -> task
        self._run_tasks: dict[int, asyncio.Task] = {}  # run_id -> task
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self.on_tick: list[Callable[[], Any]] = []  # extra periodic work (auto backups)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        n = store.recover_interrupted()
        if n:
            logger.info("Marked %d interrupted job run(s)", n)
        self._task = asyncio.create_task(self._loop(), name="zensynora-job-runner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for t in list(self._running.values()):
            t.cancel()

    def running_job_ids(self) -> list[str]:
        return [j for j, t in self._running.items() if not t.done()]

    async def _loop(self) -> None:
        while True:
            try:
                for job in await asyncio.to_thread(store.due_jobs):
                    if job["id"] not in self.running_job_ids():
                        self.run_in_background(job["id"], trigger="schedule")
                for fn in self.on_tick:
                    try:
                        res = fn()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.warning("periodic task failed: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Job runner tick failed: %s", e)
            await asyncio.sleep(TICK_SECONDS)

    # ── running ──────────────────────────────────────────────────────────────
    def run_in_background(self, job_id: str, trigger: str = "manual") -> None:
        task = asyncio.create_task(self.run_job(job_id, trigger))
        self._running[job_id] = task

    def cancel_run(self, run_id: int) -> bool:
        t = self._run_tasks.get(run_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def run_job(self, job_id: str, trigger: str = "manual") -> int | None:
        job = await asyncio.to_thread(store.get_job, job_id)
        if job is None:
            return None
        run_id: int | None = None
        status = "error"
        async with self._sem:
            attempts = 1 + max(0, int(job.get("retries") or 0))
            for attempt in range(1, attempts + 1):
                run_id = await asyncio.to_thread(store.start_run, job_id, "run", trigger, "", "", attempt)
                self._run_tasks[run_id] = asyncio.current_task()  # type: ignore[assignment]
                status = await self._execute(job, run_id)
                if status in ("ok", "cancelled"):
                    break
        await asyncio.to_thread(store.mark_ran, job_id, status, time.time())
        # v0.7.5: the medic looks at failed runs when the job asks for it.
        if run_id and status not in ("ok", "cancelled") and job.get("on_failure") and trigger != "medic":
            return await self._medic_after_failure(job, run_id)
        if run_id and status == "ok" and job.get("auto_evaluate"):
            try:
                await self.evaluate_run(run_id)
            except Exception as e:
                logger.warning("auto-evaluate of run %s failed: %s", run_id, e)
        return run_id

    async def _medic_after_failure(self, job: dict[str, Any], run_id: int) -> int:
        """Diagnose; with on_failure="fix" apply the job changes and run once more."""
        from . import medic

        try:
            diag = await medic.diagnose(run_id)
        except Exception as e:
            logger.warning("medic could not diagnose run %s: %s", run_id, e)
            return run_id
        if job.get("on_failure") != "fix" or not diag.get("job_changes"):
            return run_id
        try:
            await medic.apply_fix(run_id)
        except Exception as e:
            logger.warning("medic could not apply the fix for run %s: %s", run_id, e)
            return run_id
        logger.info("medic changed job %s (%s) and runs it again", job["id"], ", ".join(diag["job_changes"]))
        rerun = await self.run_job(job["id"], trigger="medic")  # trigger "medic": no second round
        if rerun:
            d = (await asyncio.to_thread(store.get_run, run_id) or {}).get("diagnosis") or {}
            d["rerun_id"] = rerun
            await asyncio.to_thread(store.set_diagnosis, run_id, d)
        return rerun or run_id

    async def _execute(self, job: dict[str, Any], run_id: int, agent: Any = None,
                       kind: str = "run") -> str:
        from ..agent import MEMORY_SCOPE
        from ..agent_internals.tool_executor import MAX_ROUNDS

        agent = agent or self._registry().get(job["agent"]) or self._registry().get("default")
        if agent is None:
            await asyncio.to_thread(store.finish_run, run_id, "error", "", f"Agent '{job['agent']}' not found")
            return "error"
        channel = (job.get("channel_id") or None) if kind == "run" else None
        scope = f"job-{job['id']}" if kind == "run" else f"bench-{job['id']}-{run_id}"
        tokens = (events.CURRENT_CHANNEL.set(channel), events.CURRENT_AGENT.set(agent.name),
                  MEMORY_SCOPE.set(scope))
        steps_token = MAX_ROUNDS.set(int(job.get("max_steps") or 8))  # v0.7.5
        from ..task_timer import TASK_MAX_SECONDS

        # the agent's task timer must not stop a job before its own timeout (2026-10-21)
        tmax_token = TASK_MAX_SECONDS.set(int(job.get("timeout_s") or 300) + 30)
        status, output, error = "error", "", ""
        try:
            if job.get("fresh_context", True) or kind != "run":
                try:
                    mem = await agent._get_memory("webui")
                    await mem.clear()
                except Exception as e:
                    logger.debug("could not clear job memory: %s", e)
            if channel:
                events.emit({"type": "job_run", "status": "started", "job_id": job["id"],
                             "job": job["name"], "run_id": run_id, "task": events.short(job["prompt"], 500)})
            output = await asyncio.wait_for(agent.think(job["prompt"], user_id="webui"),
                                            timeout=int(job.get("timeout_s") or 300))
            status = "ok"
            # v0.7.3: failure texts are runs that failed, not "ok" (the job page
            # showed every failed run as successful).
            if output.startswith(("Sorry, this task took too long", "[Error", "I'm sorry — the model",
                                  "I needed more tool steps", "Tool executed but error")) \
                    or not output.strip():
                status, error = "error", output[:500]
        except TimeoutError:
            status, error = "timeout", f"No answer within {job.get('timeout_s')} s"
        except asyncio.CancelledError:
            status, error = "cancelled", "Cancelled"
        except Exception as e:
            logger.exception("Job %s failed", job["id"])
            status, error = "error", str(e)[:1000]
        finally:
            used = getattr(agent, "last_model_used", None) or (getattr(agent, "_provider_name", ""), agent.model)
            await asyncio.to_thread(store.finish_run, run_id, status, output, error, used[0], used[1])
            if channel:
                events.emit({"type": "job_run", "status": status, "job_id": job["id"], "job": job["name"],
                             "run_id": run_id, "result": events.short(output or error, 4000)})
            for var, tok in zip((events.CURRENT_CHANNEL, events.CURRENT_AGENT, MEMORY_SCOPE), tokens):
                var.reset(tok)
            MAX_ROUNDS.reset(steps_token)
            TASK_MAX_SECONDS.reset(tmax_token)
        if channel and status == "ok" and self._chat:
            try:
                await self._chat().post_as_agent(channel, agent.name, output,
                                                 {"job_id": job["id"], "job": job["name"], "run_id": run_id})
            except Exception as e:
                logger.debug("could not post job result: %s", e)
        return status

    # ── evaluation ───────────────────────────────────────────────────────────
    async def evaluate_run(self, run_id: int, provider: str | None = None,
                           model: str | None = None) -> dict[str, Any]:
        """Ask an LLM (default: the main model) to score a run from 0 to 10."""
        from ..config import load_config
        run = await asyncio.to_thread(store.get_run, run_id)
        if run is None:
            raise KeyError(run_id)
        job = await asyncio.to_thread(store.get_job, run["job_id"])
        if job is None:
            raise KeyError(run["job_id"])
        if run["status"] != "ok":
            result = {"score": 0, "verdict": "fail", "reasons": f"The run ended with status '{run['status']}'.",
                      "judge": "rule"}
            await asyncio.to_thread(store.set_evaluation, run_id, 0, result)
            return result
        cfg = load_config()
        content = (f"TASK:\n{job['prompt']}\n\nSUCCESS CRITERIA:\n{job.get('success_criteria') or '(none given)'}"
                   f"\n\nRESULT:\n{run['output'][:12000]}")
        # v0.7.4: main model, then the rotation list (see complete_with_fallback).
        from ..provider import complete_with_fallback

        text, provider, model = await complete_with_fallback(
            cfg, [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": content}],
            provider, model)
        result = parse_judgement(text)
        result["judge"] = f"{provider}/{model}"
        await asyncio.to_thread(store.set_evaluation, run_id, result.get("score"), result)
        return result

    # ── benchmark ────────────────────────────────────────────────────────────
    async def benchmark(self, job_id: str, models: list[dict[str, str]], evaluate: bool = True) -> list[int]:
        """Run the job's prompt once per provider/model; returns the run ids."""
        from ..agent import Agent
        from ..config import load_config

        job = await asyncio.to_thread(store.get_job, job_id)
        if job is None:
            raise KeyError(job_id)
        cfg = load_config()
        base = self._registry().get(job["agent"])
        prompt = getattr(base, "_custom_system_prompt", None) if base else None
        run_ids: list[int] = []

        async def one(pair: dict[str, str]) -> None:
            run_id = await asyncio.to_thread(store.start_run, job_id, "bench", "benchmark",
                                             pair["provider"], pair["model"])
            run_ids.append(run_id)
            agent = Agent(cfg, name=f"bench-{job['agent']}", model=pair["model"],
                          provider_name=pair["provider"], system_prompt=prompt)
            agent._rotation_candidates = lambda: []  # benchmark exactly this model, no rotation
            agent.rules_name = job["agent"]  # 2026-10-27: the real agent's rules apply
            try:
                async with self._sem:
                    status = await self._execute(job, run_id, agent=agent, kind="bench")
                if evaluate and status == "ok":
                    try:
                        await self.evaluate_run(run_id)
                    except Exception as e:
                        await asyncio.to_thread(store.set_evaluation, run_id, None,
                                                {"error": f"evaluation failed: {str(e)[:200]}"})
            finally:
                try:
                    await agent.close()
                except Exception:
                    pass

        await asyncio.gather(*(one(p) for p in models if p.get("provider") and p.get("model")))
        return sorted(run_ids)


def parse_judgement(text: str) -> dict[str, Any]:
    """Pull {"score", "verdict", "reasons"} out of a judge reply (tolerates prose around the JSON)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    data: dict[str, Any] = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = {}
    score = data.get("score")
    if score is None:
        n = re.search(r"\b(10|[0-9](?:\.[0-9])?)\s*/\s*10\b", text or "")
        score = float(n.group(1)) if n else None
    try:
        score = max(0.0, min(10.0, float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    verdict = data.get("verdict") or ("pass" if (score or 0) >= 6 else "fail")
    return {"score": score, "verdict": verdict,
            "reasons": str(data.get("reasons") or (text or "").strip()[:500])}
