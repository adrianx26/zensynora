"""Agent fusion API (2026-10-27): propose, preview, approve, reject and undo.

GET    /api/agents/fusions                 list (?status=pending)
POST   /api/agents/fusions                 propose {agent_a, agent_b, new_name, reason, use_model, allow_default}
GET    /api/agents/fusions/{id}            preview: fused agent, prompt diffs, what moves
POST   /api/agents/fusions/{id}/approve    apply it (two agents become one)
POST   /api/agents/fusions/{id}/reject     reject it ({"note": "..."})
POST   /api/agents/fusions/{id}/undo       put both agents back

An agent's level is saved with the normal settings save (agents.named[].level,
agents.defaults.level). Everything here changes the agents, so every route
uses the settings guard (this computer or the admin API key).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw import agent_fusion
from myclaw.web import settings_api
from myclaw.web.settings_api import guard

router = APIRouter()


async def _agents_changed() -> None:
    """Rebuild the agents (registry, channels) like a settings save does."""
    try:
        from myclaw.provider import clear_provider_cache

        clear_provider_cache()
    except Exception:  # pragma: no cover - defensive
        pass
    if settings_api._on_config_changed is not None:
        await settings_api._on_config_changed()


def _get(fid: str) -> dict:
    try:
        return agent_fusion.preview(fid)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Fusion proposal not found")


@router.get("/api/agents/fusions", dependencies=guard)
async def list_fusions(status: str | None = None):
    return {"fusions": await asyncio.to_thread(agent_fusion.list_fusions, status)}


class FusionIn(BaseModel):
    agent_a: str
    agent_b: str
    new_name: str = ""
    reason: str = ""
    use_model: bool = True
    allow_default: bool = False


@router.post("/api/agents/fusions", dependencies=guard)
async def propose(body: FusionIn):
    try:
        prop = await asyncio.to_thread(agent_fusion.create_fusion, body.agent_a, body.agent_b, body.new_name,
                                       body.reason, "you", False, body.allow_default)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.use_model:  # in this event loop, where the model clients live
        await agent_fusion.write_merged_prompt(prop["id"])
    return await asyncio.to_thread(agent_fusion.preview, prop["id"])


@router.get("/api/agents/fusions/{fid}", dependencies=guard)
async def get_fusion(fid: str):
    return await asyncio.to_thread(_get, fid)


@router.post("/api/agents/fusions/{fid}/approve", dependencies=guard)
async def approve(fid: str):
    _get(fid)
    try:
        prop = await asyncio.to_thread(agent_fusion.apply_fusion, fid, "you")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _agents_changed()
    return prop


class RejectIn(BaseModel):
    note: str = ""


@router.post("/api/agents/fusions/{fid}/reject", dependencies=guard)
async def reject(fid: str, body: RejectIn = RejectIn()):
    _get(fid)
    try:
        return await asyncio.to_thread(agent_fusion.reject_fusion, fid, "you", body.note)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/agents/fusions/{fid}/undo", dependencies=guard)
async def undo(fid: str):
    _get(fid)
    try:
        prop = await asyncio.to_thread(agent_fusion.undo_fusion, fid, "you")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _agents_changed()
    return prop
