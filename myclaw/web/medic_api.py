"""Medic self-update API (added in v0.7.1) — review and apply agents' code proposals.

GET    /api/medic/proposals                  list (?status=pending)
GET    /api/medic/proposals/{id}             one proposal with its diff and result
POST   /api/medic/proposals/{id}/approve     approve and install it ({"apply": true})
POST   /api/medic/proposals/{id}/reject      reject ({"note": "..."})
POST   /api/medic/proposals/{id}/apply       install an approved (or retry a failed) proposal
DELETE /api/medic/proposals/{id}             forget a proposal
GET    /api/medic/problems                   errors, failed/low-score jobs, open proposals
GET    /api/medic/lessons                    the medic-lessons knowledge note
POST   /api/medic/self-review                create (if needed) and run the "Medic self-review" job

Everything here can change the installed program, so every route uses the
settings guard (this computer or the admin API key).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw import selfupdate
from myclaw.web.settings_api import guard

router = APIRouter()


def _get(pid: str) -> dict:
    try:
        prop = selfupdate.get_proposal(pid)
    except ValueError:
        prop = None
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return prop


@router.get("/api/medic/proposals", dependencies=guard)
async def list_proposals(status: str | None = None):
    return {"proposals": await asyncio.to_thread(selfupdate.list_proposals, status)}


@router.get("/api/medic/proposals/{pid}", dependencies=guard)
async def get_proposal(pid: str):
    return _get(pid)


class ApproveIn(BaseModel):
    apply: bool = True


@router.post("/api/medic/proposals/{pid}/approve", dependencies=guard)
async def approve(pid: str, req: ApproveIn = ApproveIn()):
    _get(pid)
    try:
        prop = selfupdate.approve(pid, by="you")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if req.apply:
        selfupdate.apply_in_background(pid)
    return prop


class RejectIn(BaseModel):
    note: str = ""


@router.post("/api/medic/proposals/{pid}/reject", dependencies=guard)
async def reject(pid: str, req: RejectIn = RejectIn()):
    _get(pid)
    try:
        return selfupdate.reject(pid, by="you", note=req.note)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/medic/proposals/{pid}/apply", dependencies=guard)
async def apply(pid: str):
    prop = _get(pid)
    if prop["status"] == "failed":
        selfupdate.approve(pid, by="you (retry)")
    elif prop["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"Proposal is {prop['status']}; approve it first.")
    selfupdate.apply_in_background(pid)
    return {"ok": True, "status": "applying"}


@router.delete("/api/medic/proposals/{pid}", dependencies=guard)
async def delete(pid: str):
    _get(pid)
    if not selfupdate.delete_proposal(pid):
        raise HTTPException(status_code=409, detail="A proposal that is being installed cannot be deleted.")
    return {"ok": True}


@router.get("/api/medic/problems", dependencies=guard)
async def problems(hours: int = 24):
    return await asyncio.to_thread(selfupdate.recent_problems, max(1, min(hours, 24 * 30)))


@router.get("/api/medic/lessons", dependencies=guard)
async def lessons():
    from myclaw.knowledge.parser import parse_note
    from myclaw.knowledge.storage import get_knowledge_dir

    path = get_knowledge_dir("default") / f"{selfupdate.LESSONS_NOTE}.md"
    if not path.exists():
        return {"lessons": []}
    note = await asyncio.to_thread(parse_note, path)
    return {"lessons": [o.content for o in note.observations if o.category == "lesson"][::-1]}


SELF_REVIEW_JOB = "Medic self-review"


@router.post("/api/medic/self-review", dependencies=guard)
async def self_review():
    """Run the self-review now; the job is created (manual schedule) the first time."""
    from myclaw.jobs import store
    from myclaw.web import jobs_api

    job = next((j for j in await asyncio.to_thread(store.list_jobs) if j["name"] == SELF_REVIEW_JOB), None)
    if job is None:
        job = await asyncio.to_thread(store.create_job, {
            "name": SELF_REVIEW_JOB,
            "prompt": selfupdate.SELF_REVIEW_PROMPT,
            "agent": "default",
            "schedule": "manual",
            "success_criteria": "Names one concrete problem with its cause, and either files a proposal "
                                "or explains why not. Records a lesson.",
            "auto_evaluate": True,
            "timeout_s": 900,
            "created_by": "medic",
        })
    jobs_api.runner().run_in_background(job["id"], trigger="medic")
    return {"ok": True, "job_id": job["id"]}
