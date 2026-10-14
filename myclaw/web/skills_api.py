"""Skills page API (v0.7.5): list, read, write, import and assign agent skills.

GET    /api/skills                  all skills + which agents use each
GET    /api/skills/{name}           one skill with its full text
POST   /api/skills                  create or update one of your skills
POST   /api/skills/import           add a SKILL.md (text) made elsewhere
POST   /api/skills/{name}/copy      copy a built-in skill to your skills to change it
DELETE /api/skills/{name}           delete one of your skills
PUT    /api/skills/{name}/agents    which agents have this skill

Assigning goes through the normal settings save (validation, agents reload).
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw import agent_skills
from myclaw.config import load_config
from myclaw.web import settings_api
from myclaw.web.settings_api import guard

router = APIRouter()


def _usage(config: Any) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for agent in ["default"] + [a.name for a in config.agents.named]:
        for s in agent_skills.skills_for(config, agent):
            used.setdefault(s, []).append(agent)
    return used


async def _reload_agents() -> None:
    """Agents read their skills when they are created — rebuild them."""
    if settings_api._on_config_changed is not None:
        await settings_api._on_config_changed()


@router.get("/api/skills", dependencies=guard)
async def list_skills():
    cfg = load_config()
    used = _usage(cfg)
    skills = await asyncio.to_thread(agent_skills.list_skills)
    for s in skills:
        s["agents"] = used.get(s["name"], [])
        s.pop("path", None)
    known = {s["name"] for s in skills}
    from myclaw.tools import TOOLS

    return {
        "skills": skills,
        "agents": ["default"] + [a.name for a in cfg.agents.named],
        "missing": sorted(n for n in used if n not in known),  # assigned but deleted
        "tools": sorted(TOOLS.keys()),  # what /api/skills returned before 0.7.5
    }


@router.get("/api/skills/{name}", dependencies=guard)
async def get_skill(name: str):
    sk = await asyncio.to_thread(agent_skills.get_skill, name)
    if sk is None:
        raise HTTPException(status_code=404, detail="No such skill")
    sk.pop("path", None)
    sk["agents"] = _usage(load_config()).get(sk["name"], [])
    return sk


class SkillIn(BaseModel):
    name: str
    description: str = ""
    body: str
    always: bool = False


@router.post("/api/skills", dependencies=guard)
async def save_skill(body: SkillIn):
    try:
        sk = await asyncio.to_thread(agent_skills.save_skill, body.name, body.description, body.body, body.always)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload_agents()
    return sk


class ImportIn(BaseModel):
    text: str
    name: str = ""


@router.post("/api/skills/import", dependencies=guard)
async def import_skill(body: ImportIn):
    try:
        sk = await asyncio.to_thread(agent_skills.import_skill, body.text, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload_agents()
    return sk


@router.post("/api/skills/{name}/copy", dependencies=guard)
async def copy_skill(name: str):
    sk = await asyncio.to_thread(agent_skills.get_skill, name)
    if sk is None:
        raise HTTPException(status_code=404, detail="No such skill")
    if sk["source"] == "yours":
        return sk
    return await asyncio.to_thread(agent_skills.save_skill, sk["name"], sk["description"], sk["body"], sk["always"])


@router.delete("/api/skills/{name}", dependencies=guard)
async def delete_skill(name: str):
    if not await asyncio.to_thread(agent_skills.delete_skill, name):
        raise HTTPException(status_code=404, detail="Only your own skills can be deleted (built-in ones stay).")
    await _reload_agents()
    return {"status": "deleted", "still_available": agent_skills.get_skill(name) is not None}


class AgentsIn(BaseModel):
    agents: list[str]


@router.put("/api/skills/{name}/agents", dependencies=guard)
async def assign(name: str, body: AgentsIn):
    """Give this skill to exactly these agents (others lose it)."""
    key = agent_skills.slug(name)
    if agent_skills.get_skill(key) is None:
        raise HTTPException(status_code=404, detail="No such skill")
    raw = copy.deepcopy(settings_api._read_raw_config() or {})
    cfg = load_config()
    wanted = set(body.agents)
    unknown = wanted - {"default"} - {a.name for a in cfg.agents.named}
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown agent(s): {', '.join(sorted(unknown))}")

    def toggled(current: list[str], on: bool) -> list[str]:
        current = [s for s in (current or []) if s != key]
        return current + [key] if on else current

    default_skills = toggled(list(cfg.agents.defaults.skills), "default" in wanted)
    named = settings_api._get_path(raw, "agents.named") if raw else None
    if named is None:
        named = [a.model_dump(mode="json") for a in cfg.agents.named]
    named = [{**a, "skills": toggled(a.get("skills") or [], a.get("name") in wanted)} for a in named]
    await settings_api.update_settings(settings_api.SettingsUpdate(
        changes={"agents.defaults.skills": default_skills, "agents.named": named}))
    return {"name": key, "agents": _usage(load_config()).get(key, [])}
