"""Rules page API (2026-10-27): the user's rules and the agents' proposals.

GET    /api/rules                          all rules, agent names, pending count
GET    /api/rules/proposals?status=        proposals (newest first) with a diff vs the rule now
POST   /api/rules/proposals/{id}/approve   apply it ({"note": "..."} optional)
POST   /api/rules/proposals/{id}/reject    keep it as rejected ({"note": "..."} optional)
GET    /api/rules/{slug}                   one rule with its text
POST   /api/rules                          create a rule (yours, active at once)
PUT    /api/rules/{slug}                   change a rule (any of the fields; e.g. {"enabled": false})
DELETE /api/rules/{slug}                   delete a rule (moved to ~/.myclaw/rules/.trash)

Rules are instructions to every agent, so every route uses the settings guard
(this computer or the admin API key). Agents never come through here: their
tools can only create proposals (myclaw/rules.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw import rules
from myclaw.config import load_config
from myclaw.web.settings_api import guard

router = APIRouter()


def _agents() -> list[str]:
    cfg = load_config()
    return ["default"] + [a.name for a in cfg.agents.named]


def _with_diff(p: dict[str, Any]) -> dict[str, Any]:
    out = dict(p)
    if p.get("status") == "pending":
        try:
            out["diff"] = rules.proposal_diff(p)
        except ValueError:
            out["diff"] = ""
        cur = None
        try:
            cur = rules.get_rule(p["slug"])
        except ValueError:
            pass
        out["current_exists"] = cur is not None
    return out


@router.get("/api/rules", dependencies=guard)
async def list_rules():
    items = await asyncio.to_thread(rules.list_rules)
    pending = await asyncio.to_thread(rules.list_proposals, "pending")
    return {"rules": items, "agents": _agents(), "pending": len(pending),
            "priorities": list(rules.PRIORITIES), "max_text": rules.MAX_TEXT}


@router.get("/api/rules/proposals", dependencies=guard)
async def list_proposals(status: str | None = None):
    if status and status not in rules.STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(rules.STATUSES)}")
    props = await asyncio.to_thread(rules.list_proposals, status)
    return {"proposals": [_with_diff(p) for p in props]}


class DecisionIn(BaseModel):
    note: str = ""


def _decide(fn, pid: str, note: str) -> dict[str, Any]:
    try:
        return fn(pid, by="user (web)", note=note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No proposal #{pid}")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/rules/proposals/{pid}/approve", dependencies=guard)
async def approve(pid: str, req: DecisionIn = DecisionIn()):
    return await asyncio.to_thread(_decide, rules.approve, pid, req.note)


@router.post("/api/rules/proposals/{pid}/reject", dependencies=guard)
async def reject(pid: str, req: DecisionIn = DecisionIn()):
    return await asyncio.to_thread(_decide, rules.reject, pid, req.note)


def _get(slug: str) -> dict[str, Any]:
    try:
        r = rules.get_rule(slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if r is None:
        raise HTTPException(status_code=404, detail=f"No rule '{slug}'")
    return r


@router.get("/api/rules/{slug}", dependencies=guard)
async def get_rule(slug: str):
    return _get(slug)


class RuleIn(BaseModel):
    title: str
    text: str
    scope: list[str] | str = "all"
    priority: str = "normal"
    enabled: bool = True


@router.post("/api/rules", dependencies=guard)
async def create_rule(req: RuleIn):
    try:
        return await asyncio.to_thread(rules.save_rule, req.title, req.text, req.scope, req.priority,
                                       req.enabled, "", "user", True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RulePatch(BaseModel):
    title: str | None = None
    text: str | None = None
    scope: list[str] | str | None = None
    priority: str | None = None
    enabled: bool | None = None


@router.put("/api/rules/{slug}", dependencies=guard)
async def update_rule(slug: str, req: RulePatch):
    _get(slug)
    try:
        return await asyncio.to_thread(lambda: rules.update_rule(slug, **req.model_dump()))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No rule '{slug}'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/rules/{slug}", dependencies=guard)
async def delete_rule(slug: str):
    _get(slug)
    try:
        trash = await asyncio.to_thread(rules.delete_rule, slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No rule '{slug}'")
    return {"ok": True, "trash": trash}
