"""Evolve page API (2026-11-03) — see myclaw/evolve.py.

GET  /api/evolve/overview               agents (level), change proposals, requests, agent trash, models
PUT  /api/evolve/agents/{name}/level    {"level": 1-9}
POST /api/evolve/request                {action, target_kind, target, goal, agent} → a one-off job
POST /api/evolve/fuse/preview           {a, b} → the merged agent to review (changes nothing)
POST /api/evolve/fuse                   {a, b, name, description, system_prompt, model_from}
POST /api/evolve/trash/restore          {name}

Code proposals are approved / applied / rejected with the existing
/api/medic/proposals routes. Every route uses the settings guard.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from myclaw import evolve
from myclaw.web.settings_api import guard

router = APIRouter()


@router.get("/api/evolve/overview", dependencies=guard)
async def overview():
    from myclaw import model_health, selfupdate
    from myclaw.config import load_config

    cfg = load_config()
    d = cfg.agents.defaults
    agents = [{"name": "default", "level": d.level, "provider": d.provider, "model": d.model,
               "description": "The main agent", "skills": []}]
    agents += [{"name": a.name, "level": a.level, "provider": a.provider or d.provider, "model": a.model,
                "description": a.description, "skills": a.skills} for a in cfg.agents.named]
    return {
        "agents": agents,
        "proposals": await asyncio.to_thread(selfupdate.list_proposals, None),
        "requests": await asyncio.to_thread(evolve.change_requests),
        "trash": evolve.agent_trash(),
        "models": model_health.snapshot(),
        "self_update": cfg.medic.self_update.model_dump(),
    }


class LevelIn(BaseModel):
    level: int = Field(..., ge=1, le=9)


@router.put("/api/evolve/agents/{name}/level", dependencies=guard)
async def set_level(name: str, body: LevelIn):
    try:
        return {"name": name, "level": await evolve.set_level(name, body.level)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RequestIn(BaseModel):
    action: Literal["optimise", "edit", "add", "move", "remove"]
    target_kind: Literal["module", "function", "script", "code", "llm"]
    target: str = Field("", max_length=300)
    goal: str = Field(..., max_length=4000)
    agent: str = "default"


@router.post("/api/evolve/request", dependencies=guard)
async def request_change(body: RequestIn):
    try:
        # in the event loop: starting the job schedules it on this loop (a quick SQLite insert)
        return evolve.request_change(body.action, body.target_kind, body.target, body.goal, body.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PairIn(BaseModel):
    a: str
    b: str


@router.post("/api/evolve/fuse/preview", dependencies=guard)
async def fuse_preview(body: PairIn):
    try:
        return await evolve.fusion_preview(body.a, body.b)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class FuseIn(PairIn):
    name: str
    description: str = ""
    system_prompt: str
    model_from: str


@router.post("/api/evolve/fuse", dependencies=guard)
async def fuse(body: FuseIn):
    try:
        return await evolve.fuse(body.a, body.b, body.name, body.description, body.system_prompt, body.model_from)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class NameIn(BaseModel):
    name: str


@router.post("/api/evolve/trash/restore", dependencies=guard)
async def restore(body: NameIn):
    try:
        return await evolve.restore_agent(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
