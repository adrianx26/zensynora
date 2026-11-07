"""Customize page API (2026-11-07): customization profiles, who uses which, agents'
proposals and the AI review.

GET    /api/customize                        profiles, use per situation, per-agent choices (choices),
                                             proposals, agents, tools (name + what it does)
POST   /api/customize/profiles               create a profile
PUT    /api/customize/profiles/{slug}        change a profile (any field)
DELETE /api/customize/profiles/{slug}        delete one of yours (kept in ~/.myclaw/.trash)
POST   /api/customize/profiles/{slug}/reset  a built-in profile back to ZenSynora's text
POST   /api/customize/profiles/{slug}/undo   the version before the last change
GET    /api/customize/profiles/{slug}/preview   the text this profile adds to a prompt
PUT    /api/customize/use                    {"use": {...}, "agents": {...}, "agents_may_propose": bool}
POST   /api/customize/proposals/{id}/approve|reject
POST   /api/customize/review                 {"profile": slug} → issues + improved text (not saved)
POST   /api/customize/adapt                  {"profile": slug, "scenario": "..."} → draft (not saved)

Profiles are instructions to every agent, so every route uses the settings guard and
every change also needs the Web UI header (``ui_guard``); agents only reach
``customize.propose`` through their tool.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw import customize
from myclaw.web.settings_api import guard, ui_guard

router = APIRouter()


def _tools() -> List[Dict[str, str]]:
    try:
        from myclaw.tools import TOOLS

        return sorted(({"name": n, "desc": str((t or {}).get("desc") or "")[:160]} for n, t in TOOLS.items()),
                      key=lambda x: x["name"])
    except Exception:  # pragma: no cover
        return []


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/api/customize", dependencies=guard)
def get_all() -> Dict[str, Any]:
    use = customize.get_use()
    return {"profiles": customize.list_profiles(), "use": use["use"], "choices": use["agents"],
            "agents_may_propose": use["agents_may_propose"], "proposals": customize.list_proposals()[:100],
            "pending": len(customize.list_proposals("pending")), "agents": customize._known_agents(),
            "tools": _tools(), "situations": [{"id": s, "label": customize.SITUATION_LABELS[s]}
                                              for s in customize.SITUATIONS],
            "fields": [{"id": f, "label": customize.FIELD_LABELS[f]} for f in customize.FIELDS],
            "max_field": customize.MAX_FIELD}


class ProfileIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    style: Optional[str] = None
    structure: Optional[str] = None
    behavior: Optional[str] = None
    tools_off: Optional[List[str]] = None


def _data(p: ProfileIn) -> Dict[str, Any]:
    return {k: v for k, v in p.model_dump().items() if v is not None}


@router.post("/api/customize/profiles", dependencies=ui_guard)
def create(p: ProfileIn) -> Dict[str, Any]:
    try:
        return customize.save_profile(None, _data(p))
    except ValueError as e:
        raise _bad(e)


@router.put("/api/customize/profiles/{slug}", dependencies=ui_guard)
def change(slug: str, p: ProfileIn) -> Dict[str, Any]:
    try:
        return customize.save_profile(slug, _data(p))
    except ValueError as e:
        raise _bad(e)


@router.delete("/api/customize/profiles/{slug}", dependencies=ui_guard)
def delete(slug: str) -> Dict[str, Any]:
    try:
        return {"trash": str(customize.delete_profile(slug))}
    except ValueError as e:
        raise _bad(e)


@router.post("/api/customize/profiles/{slug}/reset", dependencies=ui_guard)
def reset(slug: str) -> Dict[str, Any]:
    try:
        return customize.reset_profile(slug)
    except ValueError as e:
        raise _bad(e)


@router.post("/api/customize/profiles/{slug}/undo", dependencies=ui_guard)
def undo(slug: str) -> Dict[str, Any]:
    try:
        return customize.undo(slug)
    except ValueError as e:
        raise _bad(e)


@router.get("/api/customize/profiles/{slug}/preview", dependencies=guard)
def preview(slug: str) -> Dict[str, Any]:
    p = customize.get_profile(slug)
    if p is None:
        raise HTTPException(status_code=404, detail=f"There is no profile '{slug}'.")
    text = customize.render(p)  # exactly what an agent using it gets
    return {"text": text, "chars": len(text)}


class UseIn(BaseModel):
    use: Optional[Dict[str, str]] = None
    agents: Optional[Dict[str, Dict[str, str]]] = None
    agents_may_propose: Optional[bool] = None


@router.put("/api/customize/use", dependencies=ui_guard)
def set_use(u: UseIn) -> Dict[str, Any]:
    try:
        return customize.set_use(u.use, u.agents, u.agents_may_propose)
    except ValueError as e:
        raise _bad(e)


@router.post("/api/customize/proposals/{pid}/{verdict}", dependencies=ui_guard)
def decide(pid: int, verdict: str) -> Dict[str, Any]:
    if verdict not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="approve or reject")
    try:
        return customize.decide(pid, verdict == "approve")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"There is no proposal #{pid}.")
    except ValueError as e:
        raise _bad(e)


class ReviewIn(BaseModel):
    profile: str
    scenario: str = ""


@router.post("/api/customize/review", dependencies=ui_guard)
async def review(r: ReviewIn) -> Dict[str, Any]:
    try:
        return await customize.review(r.profile)
    except ValueError as e:
        raise _bad(e)
    except customize.AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/customize/adapt", dependencies=ui_guard)
async def adapt(r: ReviewIn) -> Dict[str, Any]:
    try:
        return await customize.adapt(r.profile, r.scenario)
    except ValueError as e:
        raise _bad(e)
    except customize.AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
