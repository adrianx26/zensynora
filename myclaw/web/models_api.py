"""Model switching, rotation and agent-prompt generation (added in v0.7.1).

Routes
------
GET  /api/models                 current default, rotation list, every provider
GET  /api/models/{provider}      models offered by one provider (live probe)
POST /api/models/default         switch the default provider + model now
POST /api/models/rotate          make the next rotation entry the default
POST /api/agents/generate-prompt write a system prompt from a name/description

All routes change settings or call the LLM, so they use the same guard as
the settings API (this computer, or the admin API key).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.config import load_config
from myclaw.web import settings_api
from myclaw.web.settings_api import SettingsUpdate, guard

logger = logging.getLogger(__name__)
router = APIRouter()


def _provider_list(cfg) -> list[dict[str, Any]]:
    from myclaw.provider import provider_endpoint, provider_names

    out = []
    for name in provider_names(cfg):
        ep = provider_endpoint(cfg, name) or {}
        out.append({
            "name": name,
            "label": ep.get("label", name),
            "local": bool(ep.get("local")),
            "custom": bool(ep.get("custom")),
            "configured": bool(ep.get("local") or ep.get("api_key") or ep.get("custom")),
        })
    return out


@router.get("/api/models", dependencies=guard)
async def models_overview():
    cfg = load_config()
    rot = cfg.agents.rotation
    return {
        "default": {"provider": cfg.agents.defaults.provider, "model": cfg.agents.defaults.model},
        "rotation": {
            "enabled": rot.enabled,
            "strategy": rot.strategy,
            "entries": [e.model_dump() for e in rot.entries],
        },
        "providers": _provider_list(cfg),
    }


@router.get("/api/models/{provider}", dependencies=guard)
async def provider_models(provider: str):
    return await settings_api._probe(provider, None, None)


class DefaultModel(BaseModel):
    provider: str
    model: str
    # Keep the previous default in the rotation list so you can switch back.
    remember_previous: bool = True


@router.post("/api/models/default", dependencies=guard)
async def set_default_model(req: DefaultModel):
    cfg = load_config()
    if not req.model.strip():
        raise HTTPException(status_code=400, detail="Pick a model.")
    changes: dict[str, Any] = {
        "agents.defaults.provider": req.provider,
        "agents.defaults.model": req.model.strip(),
    }
    if req.remember_previous:
        prev = {"provider": cfg.agents.defaults.provider, "model": cfg.agents.defaults.model}
        entries = [e.model_dump() for e in cfg.agents.rotation.entries]
        new = {"provider": req.provider, "model": req.model.strip()}
        entries = [e for e in entries if e != new]
        if prev != new and prev not in entries:
            entries.append(prev)
        changes["agents.rotation.entries"] = entries
    await settings_api.update_settings(SettingsUpdate(changes=changes))
    return await models_overview()


@router.post("/api/models/rotate", dependencies=guard)
async def rotate_default_model():
    """Next model in the rotation list becomes the default; the old default moves to the end."""
    cfg = load_config()
    entries = [e.model_dump() for e in cfg.agents.rotation.entries]
    if not entries:
        raise HTTPException(status_code=400, detail="The rotation list is empty — add models in Settings → Model.")
    nxt = entries.pop(0)
    entries.append({"provider": cfg.agents.defaults.provider, "model": cfg.agents.defaults.model})
    await settings_api.update_settings(SettingsUpdate(changes={
        "agents.defaults.provider": nxt["provider"],
        "agents.defaults.model": nxt["model"],
        "agents.rotation.entries": entries,
    }))
    return await models_overview()


# ── Agent prompt generator ───────────────────────────────────────────────────
PROMPT_WRITER = """You write system prompts for AI agents inside ZenSynora, a personal \
multi-agent assistant. Agents can use tools (files in a workspace, web search, \
knowledge base, scheduling) and can delegate tasks to other agents.

Write the system prompt for the agent described below. Requirements:
- Start with "You are <name>," and state the role in one sentence.
- Then 4-8 short bullet points: responsibilities, how to work step by step, \
which tools to prefer, when to ask a question or delegate, and the output style.
- Add one line on what the agent must NOT do (stay in scope, no invented facts).
- Plain text only, under 220 words, in the language of the description.
Return ONLY the prompt text, no preface and no code fences."""


class PromptRequest(BaseModel):
    name: str
    description: str = ""
    # Optional: use a specific provider/model instead of the default ("main") LLM.
    provider: str | None = None
    model: str | None = None


def _clean_prompt(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    for prefix in ("Here is", "Here's", "Sure"):
        if text.startswith(prefix) and "\n" in text:
            text = text.split("\n", 1)[1]
    return text.strip()


@router.post("/api/agents/generate-prompt", dependencies=guard)
async def generate_agent_prompt(req: PromptRequest):
    from myclaw.provider import complete_with_fallback

    name = req.name.strip()
    if not name and not req.description.strip():
        raise HTTPException(status_code=400, detail="Give the agent a name or a short description first.")
    cfg = load_config()
    messages = [
        {"role": "system", "content": PROMPT_WRITER},
        {"role": "user", "content": f"Agent name: {name or '(none)'}\nDescription: {req.description.strip() or '(none — infer the role from the name)'}"},
    ]
    # v0.7.4: the main model first, then the rotation list (it only ever used
    # the main model — a refused or rate-limited one made the button fail).
    try:
        text, provider, model = await complete_with_fallback(cfg, messages, req.provider, req.model)
    except Exception as e:
        logger.warning("Prompt generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"No model could write the prompt — {str(e)[:600]}. "
                            "Test your models on the Models page.")
    text = _clean_prompt(text)
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty answer. Try again or pick another model.")
    return {"prompt": text, "provider": provider, "model": model}


# ── Real test of a model (v0.7.3) ────────────────────────────────────────────
class TestRequest(BaseModel):
    provider: str
    model: str


@router.post("/api/models/test", dependencies=guard)
async def test_model(req: TestRequest):
    """Send a tiny real chat request and report whether the model answers.

    Listing a provider's models only proves the server is reachable; a model
    can still refuse ZenSynora (e.g. OpenRouter's "only available on agentic
    harnesses"), need credits, be rate-limited or not be loaded.
    """
    import asyncio
    import time

    from myclaw.llm_errors import describe_llm_error
    from myclaw.provider import complete_text, get_provider

    cfg = load_config()
    started = time.monotonic()
    try:
        text = await asyncio.wait_for(complete_text(
            get_provider(cfg, req.provider),
            [{"role": "user", "content": "Reply with the single word: OK"}], req.model), timeout=60)
    except Exception as e:
        return {"ok": False, "error": describe_llm_error(e), "ms": int((time.monotonic() - started) * 1000)}
    ms = int((time.monotonic() - started) * 1000)
    if not (text or "").strip():
        return {"ok": False, "error": "The model answered with nothing. It may not be loaded, or it "
                "only works through other apps.", "ms": ms}
    return {"ok": True, "reply": text.strip()[:200], "ms": ms}
