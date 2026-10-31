"""Tools page API (2026-10-31): your own tools — create, edit, delete, migrate —
and export / import of tools and skills as a file.

GET    /api/toolbox                       your tools + what "repair" would fix
GET    /api/toolbox/{name}                one tool with its code and README
POST   /api/toolbox                       create (create=true) or change a tool
DELETE /api/toolbox/{name}                delete (files go to TOOLBOX/.trash)
PUT    /api/toolbox/{name}/enabled        turn a tool on / off
GET    /api/toolbox/{name}/skill-preview  the skill this tool would become
POST   /api/toolbox/{name}/to-skill       make the tool a skill (tool disabled)
POST   /api/toolbox/repair                fix the registry / old formats
GET    /api/transfer/export               download a bundle (tools=…, skills=…)
POST   /api/transfer/import               import a bundle, a .py tool or a SKILL.md
GET    /api/skills/{name}/export          download one skill as SKILL.md

Changes need the Web UI (``ui_guard``): agents' code cannot reach them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from myclaw import agent_skills
from myclaw.tools import toolbox_manage as tm
from myclaw.web.settings_api import guard, ui_guard

router = APIRouter()


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except tm.ToolboxError as e:
        msg = str(e)
        raise HTTPException(status_code=404 if msg.startswith("No ") else 400, detail=msg)
    except ValueError as e:  # skill validation
        raise HTTPException(status_code=400, detail=str(e))


async def _skills_changed() -> None:
    from myclaw.web import skills_api

    await skills_api._reload_agents()


@router.get("/api/toolbox", dependencies=guard)
async def list_toolbox():
    return await _run(tm.list_tools)


@router.get("/api/toolbox/{name}", dependencies=guard)
async def get_tool(name: str):
    return await _run(tm.get_tool, name)


class ToolIn(BaseModel):
    name: str
    code: str
    description: str = ""
    documentation: str = ""
    create: bool = False
    tags: Optional[List[str]] = None


@router.post("/api/toolbox", dependencies=ui_guard)
async def save_tool(body: ToolIn):
    return await _run(tm.save_tool, body.name, body.code, body.description, body.documentation,
                      create=body.create, author="user", tags=body.tags)


@router.delete("/api/toolbox/{name}", dependencies=ui_guard)
async def delete_tool(name: str):
    return await _run(tm.delete_tool, name)


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/api/toolbox/{name}/enabled", dependencies=ui_guard)
async def set_enabled(name: str, body: EnabledIn):
    return await _run(tm.set_enabled, name, body.enabled)


@router.get("/api/toolbox/{name}/skill-preview", dependencies=guard)
async def skill_preview(name: str):
    return await _run(tm.skill_from_tool, name)


class ToSkillIn(BaseModel):
    skill_name: str = ""
    keep_tool: bool = False
    overwrite: bool = False


@router.post("/api/toolbox/{name}/to-skill", dependencies=ui_guard)
async def to_skill(name: str, body: ToSkillIn):
    out = await _run(tm.tool_to_skill, name, body.skill_name, keep_tool=body.keep_tool, overwrite=body.overwrite)
    await _skills_changed()
    return out


class RepairIn(BaseModel):
    dry_run: bool = False


@router.post("/api/toolbox/repair", dependencies=ui_guard)
async def repair(body: RepairIn):
    out = await _run(tm.repair, dry_run=body.dry_run)
    if not body.dry_run:
        await _skills_changed()
    return out


def _names(raw: Optional[str]) -> Optional[List[str]]:
    """'' → none, missing → all, 'a,b' → [a, b]."""
    if raw is None:
        return None
    return [n.strip() for n in raw.split(",") if n.strip()]


@router.get("/api/transfer/export", dependencies=guard)
async def export(tools: Optional[str] = None, skills: Optional[str] = None):
    bundle = await _run(tm.export_bundle, _names(tools), _names(skills))
    n_t, n_s = len(bundle["tools"]), len(bundle["skills"])
    if n_t + n_s == 1:
        only = (bundle["tools"] or bundle["skills"])[0]["name"]
        fname = f"zensynora-{'tool' if n_t else 'skill'}-{only}.json"
    else:
        fname = f"zensynora-export-{datetime.now():%Y%m%d-%H%M}.json"
    return Response(content=json.dumps(bundle, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


class ImportIn(BaseModel):
    text: str
    filename: str = ""
    overwrite: bool = False
    trust: bool = False


@router.post("/api/transfer/import", dependencies=ui_guard)
async def import_file(body: ImportIn):
    out = await _run(tm.import_file, body.text, body.filename, overwrite=body.overwrite, trust=body.trust)
    if any(i["kind"] == "skill" for i in out["imported"]):
        await _skills_changed()
    return out


@router.get("/api/skills/{name}/export", dependencies=guard)
async def export_skill(name: str):
    fname, text = await _run(agent_skills.export_skill, name)
    return Response(content=text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
