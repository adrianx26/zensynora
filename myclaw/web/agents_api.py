"""Agent fusion API (2026-10-27): propose, preview, approve, reject and undo.

GET    /api/agents/fusions                 list (?status=pending); "interrupted" marks a dead apply
POST   /api/agents/fusions                 propose {agent_a, agent_b, new_name, reason, use_model, allow_default}
GET    /api/agents/fusions/{id}            preview: fused agent, prompt diffs, what moves, undo check
POST   /api/agents/fusions/{id}/approve    apply it (two agents become one)
POST   /api/agents/fusions/{id}/reject     reject it ({"note": "..."})
POST   /api/agents/fusions/{id}/undo       put both agents back ({"force": true} loses later edits)

An agent's level is saved with the normal settings save (agents.named[].level,
agents.defaults.level). Everything here changes the agents, so every route
uses the settings guard (this computer or the admin API key).

2026-10-28: only these routes (and the CLI) can apply, reject or undo — they
pass a private origin object to myclaw.agent_fusion; an agent's tool cannot.
Every failure is a 409 with a readable message, never a 500.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from myclaw import agent_fusion
from myclaw.web import settings_api
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

_ORIGIN = object()  # private: identifies "you, in the Web UI" to agent_fusion
agent_fusion.register_origin(_ORIGIN, "web")


async def _agents_changed() -> None:
    """Rebuild the agents (registry, channels) like a settings save does."""
    try:
        from myclaw.provider import clear_provider_cache

        clear_provider_cache()
    except Exception:  # pragma: no cover - defensive
        pass
    if settings_api._on_config_changed is not None:
        try:
            await settings_api._on_config_changed()
        except Exception as e:  # pragma: no cover - the change itself is done
            logger.warning("Agents not reloaded after the fusion change: %s", e)


def _get(fid: str) -> dict:
    try:
        return agent_fusion.preview(fid)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Fusion proposal not found")


def _conflict(e: Exception, what: str) -> HTTPException | JSONResponse:
    if isinstance(e, agent_fusion.FusionConflict):
        return JSONResponse(status_code=409, content={"detail": str(e), "conflicts": e.details})
    if isinstance(e, ValueError):
        return HTTPException(status_code=409, detail=str(e))
    logger.warning("Fusion %s failed: %s: %s", what, e.__class__.__name__, e)
    return HTTPException(status_code=409, detail=f"Could not {what} the fusion ({e.__class__.__name__}: {e}).")


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
        prop = await asyncio.to_thread(agent_fusion.apply_fusion, fid, _ORIGIN)
    except Exception as e:
        err = _conflict(e, "apply")
        await _agents_changed()  # a rolled-back attempt may have touched the settings
        if isinstance(err, JSONResponse):
            return err
        raise err
    await _agents_changed()
    return prop


class RejectIn(BaseModel):
    note: str = ""


@router.post("/api/agents/fusions/{fid}/reject", dependencies=guard)
async def reject(fid: str, body: RejectIn = RejectIn()):
    _get(fid)
    try:
        return await asyncio.to_thread(agent_fusion.reject_fusion, fid, _ORIGIN, body.note)
    except Exception as e:
        err = _conflict(e, "reject")
        if isinstance(err, JSONResponse):
            return err
        raise err


class UndoIn(BaseModel):
    force: bool = False


@router.post("/api/agents/fusions/{fid}/undo", dependencies=guard)
async def undo(fid: str, body: UndoIn = UndoIn()):
    _get(fid)
    try:
        prop = await asyncio.to_thread(agent_fusion.undo_fusion, fid, _ORIGIN, body.force)
    except Exception as e:
        err = _conflict(e, "undo")
        if isinstance(err, JSONResponse):
            return err
        raise err
    await _agents_changed()
    return prop
