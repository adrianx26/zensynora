"""Distilled knowledge for the Web UI (KB phase 4, 2026-10-24).

Routes
------
GET  /api/knowledge/facts?base=&q=&limit=&offset=&category=   distilled facts (L1), best first
GET  /api/knowledge/scenarios?base=&q=&limit=                 scenarios (L2) with their first facts
GET  /api/knowledge/persona?base=&version=                    the persona (L3), newest or one version
GET  /api/knowledge/drill/{kind}/{id}?base=                   the notes and fact lines behind a fact,
                                                              scenario or persona (kind: fact |
                                                              scenario | persona; id: number, or
                                                              "latest" for the persona)
GET  /api/knowledge/distill/status?base=                      counts, last run, waiting notes, job
POST /api/knowledge/distill?base=&full=                       run it now in a background thread
                                                              (incremental; full = read every note)
DELETE /api/knowledge/distill?base=                           ask a running job to stop (keeps what
                                                              it did; the next run continues)

Everything is derived data in the index database; reading never changes a
note. Everything runs off the event loop. The notes are private, so every
route uses the settings guard.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from myclaw.web.knowledge_api import _base
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}          # base → distill job


def _job_view(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not job:
        return None
    return {k: v for k, v in job.items() if not k.startswith("_")}


@router.get("/api/knowledge/facts", dependencies=guard)
async def facts(base: str | None = None, q: str = "", limit: int = Query(50, ge=1, le=500),
                offset: int = Query(0, ge=0, le=1_000_000), category: str = "",
                about_user: Optional[bool] = None):
    from myclaw.knowledge import distill

    b = _base(base)
    return await asyncio.to_thread(distill.list_facts, b, q[:500], limit, offset, category[:64],
                                   about_user)


@router.get("/api/knowledge/scenarios", dependencies=guard)
async def scenarios(base: str | None = None, q: str = "", limit: int = Query(50, ge=1, le=500)):
    from myclaw.knowledge import distill

    b = _base(base)
    return await asyncio.to_thread(distill.list_scenarios, b, q[:500], limit)


@router.get("/api/knowledge/persona", dependencies=guard)
async def persona(base: str | None = None, version: Optional[int] = Query(None, ge=1, le=2**62)):
    from myclaw.knowledge import distill

    b = _base(base)
    p = await asyncio.to_thread(distill.get_persona, b, version)
    if p is None and version is not None:
        raise HTTPException(status_code=404, detail=f"There is no persona version {version}.")
    return {"base": b, "persona": p}


@router.get("/api/knowledge/drill/{kind}/{item_id}", dependencies=guard)
async def drill(kind: str, item_id: str, base: str | None = None):
    from myclaw.knowledge import distill

    b = _base(base)
    kind = kind.strip().lower()
    if kind not in ("fact", "scenario", "persona"):
        raise HTTPException(status_code=400, detail="kind must be fact, scenario or persona.")
    if kind == "persona" and item_id in ("latest", "current", ""):
        ref = "persona"
    elif item_id.isdigit() and len(item_id) <= 18:
        ref = f"{kind}:{item_id}"
    else:
        raise HTTPException(status_code=400, detail="The id must be a number (or 'latest' for the persona).")
    try:
        return await asyncio.to_thread(distill.drill_down, ref, b)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/knowledge/distill/status", dependencies=guard)
async def distill_status(base: str | None = None):
    from myclaw.knowledge import distill

    b = _base(base)
    st = await asyncio.to_thread(distill.status, b)
    with _lock:
        st["job"] = _job_view(_jobs.get(b))
    return st


def _run(b: str, job: Dict[str, Any], full: bool) -> None:
    from myclaw.knowledge import distill, sync_knowledge

    def progress(done: int, total: int) -> None:
        job["done"], job["total"] = done, total

    try:
        job["phase"] = "re-indexing"
        try:
            sync_knowledge(b)          # notes edited by hand are seen too
        except Exception as e:
            logger.info("Knowledge: sync before distilling %s skipped: %s", b, e)
        job["phase"] = "distilling"
        r = distill.distill(b, full=full, progress=progress, stop=lambda: job["_stop"],
                            wait_lock=60.0)
        job["result"] = r
        job["status"] = ("stopped" if job["_stop"] else "done") if r.get("ok") else "error"
        if not r.get("ok"):
            job["error"] = r.get("message")
    except Exception as e:  # a fault in ZenSynora, not in the user's input
        logger.error("Knowledge: distilling %s failed: %s", b, e, exc_info=True)
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = time.time()
        job["phase"] = "finished"


@router.post("/api/knowledge/distill", dependencies=guard)
async def distill_now(base: str | None = None, full: bool = False):
    b = _base(base)
    with _lock:
        cur = _jobs.get(b)
        if cur and cur["status"] == "running":
            raise HTTPException(status_code=409, detail="Distillation of this knowledge base is already running.")
        job: Dict[str, Any] = {"base": b, "status": "running", "phase": "starting", "done": 0,
                               "total": 0, "started": time.time(), "finished": None,
                               "error": None, "result": None, "full": bool(full), "_stop": False}
        _jobs[b] = job
    threading.Thread(target=_run, args=(b, job, bool(full)), name=f"kb-distill-{b}",
                     daemon=True).start()
    return {"ok": True, "job": _job_view(job)}


@router.delete("/api/knowledge/distill", dependencies=guard)
async def distill_stop(base: str | None = None):
    """2026-10-24 (KB phase 4 review): stop a running job between batches."""
    b = _base(base)
    with _lock:
        job = _jobs.get(b)
        if not job or job["status"] != "running":
            raise HTTPException(status_code=404, detail="No distillation is running for this knowledge base.")
        job["_stop"] = True
    return {"ok": True, "job": _job_view(job)}
