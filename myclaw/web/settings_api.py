"""Web UI management API: settings, live system health, costs, history.

All routes here can change configuration or reveal usage data, so they are
only served to requests from this machine (loopback) or to callers that send
the admin API key in ``X-API-Key``. The chat UI served on localhost therefore
works without extra setup, while other machines on the LAN are refused.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, SecretStr, ValidationError

from myclaw.config import (
    CONFIG_FILE,
    AppConfig,
    load_config,
    save_config,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

# Providers that speak the OpenAI-compatible /models endpoint.
_OPENAI_COMPATIBLE = {"lmstudio", "llamacpp", "openai", "groq", "openrouter"}

_CLOUD_DEFAULT_URLS = {
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

# Python SDK each provider needs at chat time (the probe below uses plain
# HTTP, so without this check "Test connection" could pass while chat fails).
_PROVIDER_SDK = {
    "lmstudio": ("openai", "openai"),
    "llamacpp": ("openai", "openai"),
    "openai": ("openai", "openai"),
    "groq": ("openai", "openai"),
    "openrouter": ("openai", "openai"),
    "anthropic": ("anthropic", "anthropic"),
    "gemini": ("google.generativeai", "google-generativeai"),
}


def _missing_sdk(provider: str) -> Optional[str]:
    """Return the pip package to install if the provider's SDK is missing."""
    spec = _PROVIDER_SDK.get(provider)
    if not spec:
        return None
    import importlib.util

    module, pip_name = spec
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return None if found else pip_name


# Callback set by web.api so a settings change rebuilds the agent registry.
_on_config_changed = None


def set_config_changed_hook(fn) -> None:
    global _on_config_changed
    _on_config_changed = fn


# ── Access guard ─────────────────────────────────────────────────────────────
async def require_local_or_admin(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host in _LOOPBACK:
        return
    supplied = request.headers.get("x-api-key", "")
    cfg = load_config()
    expected = cfg.security.admin_api_key.get_secret_value()
    if expected and supplied and secrets.compare_digest(supplied, expected):
        return
    raise HTTPException(
        status_code=403,
        detail="Settings are only available from this computer (localhost) "
        "or with the admin API key (X-API-Key).",
    )


guard = [Depends(require_local_or_admin)]


# ── Helpers: secrets & dotted paths ──────────────────────────────────────────
def _secret_paths(model: BaseModel, prefix: str = "") -> List[str]:
    """Dotted paths of every SecretStr field in a (nested) pydantic model."""
    out: List[str] = []
    for name in type(model).model_fields:
        val = getattr(model, name)
        path = f"{prefix}{name}"
        if isinstance(val, SecretStr):
            out.append(path)
        elif isinstance(val, BaseModel):
            out.extend(_secret_paths(val, path + "."))
    return out


def _get_path(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(d: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _path_exists_in_schema(path: str) -> bool:
    """True when every segment of ``path`` is a declared AppConfig field."""
    model_cls: Any = AppConfig
    parts = path.split(".")
    for i, part in enumerate(parts):
        fields = getattr(model_cls, "model_fields", None)
        if fields is None or part not in fields:
            return False
        ann = fields[part].annotation
        if i < len(parts) - 1:
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                model_cls = ann
            else:
                return False
    return True


def _public_config(cfg: AppConfig) -> Tuple[Dict[str, Any], List[str]]:
    """Config as JSON with secrets blanked, plus the list of secrets that are set."""
    data = cfg.model_dump(mode="json")
    set_secrets = []
    for path in _secret_paths(cfg):
        obj: Any = cfg
        for part in path.split("."):
            obj = getattr(obj, part)
        if obj.get_secret_value():
            set_secrets.append(path)
        _set_path(data, path, "")
    return data, set_secrets


def _read_raw_config() -> Dict[str, Any]:
    """The on-disk config (decrypted, no env overrides) as a plain dict."""
    if not CONFIG_FILE.exists():
        return {}
    from myclaw.config_encryption import load_encrypted_or_plain

    return load_encrypted_or_plain(CONFIG_FILE) or {}


# ── Settings ─────────────────────────────────────────────────────────────────
class SettingsUpdate(BaseModel):
    # {"agents.defaults.model": "qwen3", "providers.openai.api_key": "sk-…"}
    changes: Dict[str, Any]
    # Secret paths to blank out explicitly (an empty secret input means "keep").
    clear_secrets: List[str] = []


@router.get("/api/settings", dependencies=guard)
async def get_settings():
    from myclaw.provider import SUPPORTED_PROVIDERS

    cfg = load_config(force_reload=True)
    data, set_secrets = _public_config(cfg)
    return {
        "config": data,
        "secrets_set": set_secrets,
        "secret_paths": _secret_paths(cfg),
        "providers": SUPPORTED_PROVIDERS,
        "config_file": str(CONFIG_FILE),
        "config_file_exists": CONFIG_FILE.exists(),
    }


@router.put("/api/settings", dependencies=guard)
async def update_settings(update: SettingsUpdate):
    cfg = load_config()
    secret_paths = set(_secret_paths(cfg))
    raw = copy.deepcopy(_read_raw_config())

    if not raw:
        # First save (no onboarding yet): start from the full defaults,
        # with secrets in plain text so they round-trip.
        base = AppConfig()
        raw = base.model_dump(mode="json")
        for path in secret_paths:
            _set_path(raw, path, "")

    for path, value in update.changes.items():
        if not _path_exists_in_schema(path):
            raise HTTPException(status_code=400, detail=f"Unknown setting: {path}")
        if path in secret_paths and (value is None or value == ""):
            continue  # empty secret field = keep the stored value
        _set_path(raw, path, value)

    for path in update.clear_secrets:
        if path not in secret_paths:
            raise HTTPException(status_code=400, detail=f"Not a secret setting: {path}")
        _set_path(raw, path, "")

    provider = _get_path(raw, "agents.defaults.provider")
    from myclaw.provider import SUPPORTED_PROVIDERS

    if provider and provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    for named in _get_path(raw, "agents.named") or []:
        p = (named or {}).get("provider") or ""
        if p and p not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider for agent {named.get('name')}: {p}")
        if not (named or {}).get("name", "").strip():
            raise HTTPException(status_code=400, detail="Every named agent needs a name.")
        if named["name"] == "default":
            raise HTTPException(status_code=400, detail="'default' is reserved for the main agent.")

    try:
        AppConfig(**raw)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {e.errors()[:3]}")

    save_config(raw)
    load_config(force_reload=True)
    try:
        from myclaw.provider import clear_provider_cache

        clear_provider_cache()
    except Exception:  # pragma: no cover - defensive
        pass
    if _on_config_changed is not None:
        await _on_config_changed()

    logger.info("Settings updated from Web UI: %s", sorted(update.changes.keys()))
    return {
        "status": "saved",
        "changed": sorted(update.changes.keys()),
        "restart_needed_for": [
            p for p in update.changes if p.startswith(("channels.", "mcp.", "security.cors_origins"))
        ],
    }


# ── Provider probe (test connection + list models) ───────────────────────────
class ProbeRequest(BaseModel):
    provider: str
    base_url: Optional[str] = None  # unsaved value from the form, optional
    api_key: Optional[str] = None  # unsaved value from the form, optional


async def _probe(provider: str, base_url: Optional[str], api_key: Optional[str]) -> Dict[str, Any]:
    cfg = load_config()
    pcfg = getattr(cfg.providers, provider, None)
    if pcfg is None:
        return {"ok": False, "error": f"Unknown provider '{provider}'", "models": []}

    url = (base_url or getattr(pcfg, "base_url", "") or _CLOUD_DEFAULT_URLS.get(provider, "")).rstrip("/")
    key = api_key
    if not key and hasattr(pcfg, "api_key"):
        key = pcfg.api_key.get_secret_value()

    headers: Dict[str, str] = {}
    params: Dict[str, str] = {}
    if provider == "ollama":
        endpoint = f"{url}/api/tags"
    elif provider == "anthropic":
        endpoint = f"{url}/models"
        headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
    elif provider == "gemini":
        endpoint = f"{url}/models"
        params = {"key": key or ""}
    else:
        endpoint = f"{url}/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"

    missing = _missing_sdk(provider)
    if missing:
        return {
            "ok": False,
            "error": (
                f"Python package '{missing}' is not installed, so chat cannot use "
                f"this provider. Run: pip install {missing} (or re-run install.sh)."
            ),
            "models": [],
            "url": url,
        }

    if provider not in ("ollama", "lmstudio", "llamacpp") and not key:
        return {"ok": False, "error": "API key is not set.", "models": [], "url": url}

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(endpoint, headers=headers, params=params)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code} from {endpoint}",
                "models": [],
                "url": url,
            }
        body = resp.json()
        if provider == "ollama":
            models = [m.get("name", "") for m in body.get("models", [])]
        elif provider == "gemini":
            models = [m.get("name", "").removeprefix("models/") for m in body.get("models", [])]
        else:
            models = [m.get("id", "") for m in body.get("data", [])]
        return {
            "ok": True,
            "models": sorted(m for m in models if m),
            "latency_ms": latency_ms,
            "url": url,
        }
    except Exception as e:
        return {"ok": False, "error": f"Cannot reach {url}: {e.__class__.__name__}", "models": [], "url": url}


@router.post("/api/settings/probe", dependencies=guard)
async def probe_provider(req: ProbeRequest):
    return await _probe(req.provider, req.base_url, req.api_key)


# ── Live system health (replaces the hard-coded sidebar values) ──────────────
_health_cache: Dict[str, Any] = {"at": 0.0, "data": None}
_active_ws = {"count": 0}


def ws_opened() -> None:
    _active_ws["count"] += 1


def ws_closed() -> None:
    _active_ws["count"] = max(0, _active_ws["count"] - 1)


@router.get("/api/system/health")
async def system_health():
    # Cheap and read-only (no secrets), so it is served to every UI client.
    now = time.monotonic()
    if _health_cache["data"] and now - _health_cache["at"] < 10:
        data = dict(_health_cache["data"])
        data["websocket_clients"] = _active_ws["count"]
        return data

    cfg = load_config()
    provider = cfg.agents.defaults.provider
    probe = await _probe(provider, None, None)

    kb: Dict[str, Any] = {"ok": False}
    try:
        from myclaw.knowledge.db import KnowledgeDB

        def _kb_stats():
            with KnowledgeDB("default") as db:
                return db.get_stats()

        stats = await asyncio.to_thread(_kb_stats)
        kb = {"ok": True, **stats}
    except Exception as e:
        kb = {"ok": False, "error": str(e)}

    try:
        from myclaw.tools import TOOLS

        tool_count = len(TOOLS)
    except Exception:
        tool_count = 0

    data = {
        "llm": {
            "provider": provider,
            "model": cfg.agents.defaults.model,
            "ok": probe["ok"],
            "error": probe.get("error"),
            "latency_ms": probe.get("latency_ms"),
            "model_available": (cfg.agents.defaults.model in probe["models"]) if probe["ok"] and probe["models"] else None,
        },
        "knowledge": kb,
        "mcp": {"enabled": cfg.mcp.enabled, "servers": len(cfg.mcp.servers)},
        "swarm": {"enabled": cfg.swarm.enabled},
        "channels": {
            "telegram": cfg.channels.telegram.enabled,
            "whatsapp": cfg.channels.whatsapp.enabled,
        },
        "tools": tool_count,
        "config_file_exists": CONFIG_FILE.exists(),
        "version": __import__("myclaw").__version__,
    }
    _health_cache.update(at=now, data=data)
    out = dict(data)
    out["websocket_clients"] = _active_ws["count"]
    return out


# ── Conversation history (server side) ───────────────────────────────────────
@router.delete("/api/history", dependencies=guard)
async def clear_history():
    from myclaw.memory import Memory

    mem = Memory(user_id="webui")
    try:
        removed = await mem.clear()
    finally:
        await mem.close()
    if _on_config_changed is not None:
        # Agents cache Memory objects; rebuild so nothing stale is served.
        await _on_config_changed()
    return {"status": "cleared", "removed": removed}


# ── Tools list ───────────────────────────────────────────────────────────────
@router.get("/api/tools")
async def list_tools():
    try:
        from myclaw.tools import TOOL_SCHEMAS, ensure_tool_schemas

        ensure_tool_schemas()
        tools = []
        for schema in TOOL_SCHEMAS:
            fn = schema.get("function", schema)
            tools.append({"name": fn.get("name", ""), "description": (fn.get("description") or "").strip()})
        return {"tools": sorted(tools, key=lambda t: t["name"])}
    except Exception as e:
        from myclaw.tools import TOOLS

        return {"tools": [{"name": n, "description": ""} for n in sorted(TOOLS)], "note": str(e)}


# ── Cost dashboard (same shapes as api_server's /api/v1/costs/*) ─────────────
@router.get("/api/v1/costs/summary", dependencies=guard)
async def costs_summary():
    from myclaw.cost_tracker import get_cost_summary

    return get_cost_summary()


@router.get("/api/v1/costs/by-provider", dependencies=guard)
async def costs_by_provider(month: Optional[str] = None):
    from myclaw.cost_tracker import get_monthly_costs

    return {"month": month, "rows": get_monthly_costs(month)}


@router.get("/api/v1/costs/by-model", dependencies=guard)
async def costs_by_model(month: Optional[str] = None, limit: int = 20):
    from myclaw.cost_tracker import get_costs_by_model

    return {"month": month, "rows": get_costs_by_model(month, limit=limit)}


@router.get("/api/v1/costs/timeline", dependencies=guard)
async def costs_timeline(days: int = 30):
    from myclaw.cost_tracker import get_daily_timeline

    return {"days": days, "rows": get_daily_timeline(days)}
