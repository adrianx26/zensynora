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


# ── Ready-made custom providers (v0.7.1) ────────────────────────────────────
# Shown in Settings → Providers → "Add provider". All are OpenAI-compatible
# (or Ollama). "free" means the service offers a free tier or free models;
# limits change, so the UI says "check the provider's page".
PROVIDER_PRESETS: List[Dict[str, Any]] = [
    # Local servers (run on your computer or network)
    {"name": "vllm", "label": "vLLM", "kind": "openai", "base_url": "http://localhost:8000/v1", "local": True},
    {"name": "localai", "label": "LocalAI", "kind": "openai", "base_url": "http://localhost:8080/v1", "local": True},
    {"name": "jan", "label": "Jan", "kind": "openai", "base_url": "http://localhost:1337/v1", "local": True},
    {"name": "koboldcpp", "label": "KoboldCpp", "kind": "openai", "base_url": "http://localhost:5001/v1", "local": True},
    {"name": "textgen", "label": "text-generation-webui", "kind": "openai", "base_url": "http://localhost:5000/v1", "local": True},
    {"name": "ollama-remote", "label": "Ollama on another machine", "kind": "ollama", "base_url": "http://192.168.1.10:11434", "local": True},
    # Online services
    {"name": "deepseek", "label": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1", "local": False},
    {"name": "mistral", "label": "Mistral AI", "kind": "openai", "base_url": "https://api.mistral.ai/v1", "local": False, "free": True},
    {"name": "cerebras", "label": "Cerebras", "kind": "openai", "base_url": "https://api.cerebras.ai/v1", "local": False, "free": True},
    {"name": "together", "label": "Together AI", "kind": "openai", "base_url": "https://api.together.xyz/v1", "local": False},
    {"name": "github-models", "label": "GitHub Models", "kind": "openai", "base_url": "https://models.inference.ai.azure.com", "local": False, "free": True},
    {"name": "huggingface", "label": "Hugging Face router", "kind": "openai", "base_url": "https://router.huggingface.co/v1", "local": False, "free": True},
    {"name": "nvidia", "label": "NVIDIA NIM", "kind": "openai", "base_url": "https://integrate.api.nvidia.com/v1", "local": False, "free": True},
    {"name": "gemini-openai", "label": "Google Gemini (OpenAI-compatible)", "kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "local": False, "free": True},
    {"name": "xai", "label": "xAI (Grok)", "kind": "openai", "base_url": "https://api.x.ai/v1", "local": False},
    {"name": "fireworks", "label": "Fireworks AI", "kind": "openai", "base_url": "https://api.fireworks.ai/inference/v1", "local": False},
]


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
    # v0.7.1: custom providers keep their API key inside a list item.
    for i, cp in enumerate(cfg.providers.custom):
        if cp.api_key.get_secret_value():
            set_secrets.append(f"providers.custom.{cp.name}.api_key")
        data["providers"]["custom"][i]["api_key"] = ""
    return data, set_secrets


_HELPER_KEYS = ("_previous_name", "clear_api_key")


def _merge_custom_providers(new_items: Any, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate the custom-provider list from the form and keep stored API keys.

    The browser never receives API keys, so an empty ``api_key`` means "keep
    the saved one" (matched by name, or by ``_previous_name`` after a rename);
    ``clear_api_key: true`` removes it.
    """
    from myclaw.provider import CUSTOM_PROVIDER_KINDS, SUPPORTED_PROVIDERS

    if not isinstance(new_items, list):
        raise HTTPException(status_code=400, detail="providers.custom must be a list")
    stored = {c.get("name"): c for c in (_get_path(raw, "providers.custom") or []) if isinstance(c, dict)}
    out, seen = [], set()
    for item in new_items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="Invalid custom provider entry")
        name = str(item.get("name", "")).strip()
        if not name or not all(ch.isalnum() or ch in "-_." for ch in name):
            raise HTTPException(status_code=400, detail=f"Custom provider name '{name}' must use letters, digits, - _ .")
        if name in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"'{name}' is a built-in provider; pick another name.")
        if name in seen:
            raise HTTPException(status_code=400, detail=f"Two custom providers are called '{name}'.")
        seen.add(name)
        if item.get("kind", "openai") not in CUSTOM_PROVIDER_KINDS:
            raise HTTPException(status_code=400, detail=f"{name}: kind must be one of {', '.join(CUSTOM_PROVIDER_KINDS)}")
        if not str(item.get("base_url", "")).strip():
            raise HTTPException(status_code=400, detail=f"{name}: server URL is required.")
        clean = {k: v for k, v in item.items() if k not in _HELPER_KEYS}
        if item.get("clear_api_key"):
            clean["api_key"] = ""
        elif not clean.get("api_key"):
            prev = stored.get(item.get("_previous_name") or name) or {}
            clean["api_key"] = prev.get("api_key", "")
        out.append(clean)
    return out


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
    from myclaw.provider import provider_names

    cfg = load_config(force_reload=True)
    data, set_secrets = _public_config(cfg)
    return {
        "config": data,
        "secrets_set": set_secrets,
        "secret_paths": _secret_paths(cfg),
        "providers": provider_names(cfg),
        "provider_presets": PROVIDER_PRESETS,
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
        if path == "providers.custom":
            value = _merge_custom_providers(value, raw)
        _set_path(raw, path, value)

    for path in update.clear_secrets:
        if path not in secret_paths:
            raise HTTPException(status_code=400, detail=f"Not a secret setting: {path}")
        _set_path(raw, path, "")

    provider = _get_path(raw, "agents.defaults.provider")
    from myclaw.provider import SUPPORTED_PROVIDERS

    # Built-in providers plus the user's custom ones (v0.7.1).
    known = set(SUPPORTED_PROVIDERS) | {
        c.get("name") for c in (_get_path(raw, "providers.custom") or []) if isinstance(c, dict)
    }
    if provider and provider not in known:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    for entry in _get_path(raw, "agents.rotation.entries") or []:
        if (entry or {}).get("provider") not in known:
            raise HTTPException(status_code=400, detail=f"Unknown provider in rotation: {(entry or {}).get('provider')}")
    if (_get_path(raw, "agents.rotation.strategy") or "fallback") not in ("fallback", "round_robin"):
        raise HTTPException(status_code=400, detail="Rotation strategy must be 'fallback' or 'round_robin'.")
    for named in _get_path(raw, "agents.named") or []:
        p = (named or {}).get("provider") or ""
        if p and p not in known:
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
    kind: Optional[str] = None  # for an unsaved custom provider: "openai" | "ollama"


async def _probe(provider: str, base_url: Optional[str], api_key: Optional[str],
                 kind: Optional[str] = None) -> Dict[str, Any]:
    """Test a provider and list its models.

    v0.7.1: works for custom providers too (via provider_endpoint), and for an
    unsaved custom provider when ``kind`` + ``base_url`` come from the form.
    """
    from myclaw.provider import provider_endpoint

    cfg = load_config()
    ep = provider_endpoint(cfg, provider)
    if ep is None:
        if not (kind and base_url):
            return {"ok": False, "error": f"Unknown provider '{provider}'", "models": []}
        ep = {"kind": kind, "base_url": "", "api_key": "", "local": False}

    kind = kind or ep["kind"]
    url = (base_url or ep["base_url"]).rstrip("/")
    key = api_key or ep["api_key"]
    if not url:
        return {"ok": False, "error": "No server URL set.", "models": []}

    headers: Dict[str, str] = {}
    params: Dict[str, str] = {}
    if kind == "ollama":
        endpoint = f"{url}/api/tags"
    elif kind == "anthropic":
        endpoint = f"{url}/models"
        headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
    elif kind == "gemini":
        endpoint = f"{url}/models"
        params = {"key": key or ""}
    else:
        endpoint = f"{url}/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"

    missing = _missing_sdk(provider if provider in _PROVIDER_SDK else ("openai" if kind == "openai" else ""))
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

    # Built-in cloud providers always need a key. Custom ones may not (local
    # servers); a cloud server without a key answers 401, reported below.
    if provider in ("openai", "groq", "openrouter", "anthropic", "gemini") and not key:
        return {"ok": False, "error": "API key is not set.", "models": [], "url": url}

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(endpoint, headers=headers, params=params)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            hint = " (check the API key)" if resp.status_code in (401, 403) else ""
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code} from {endpoint}{hint}",
                "models": list(ep.get("models") or []),
                "url": url,
            }
        body = resp.json()
        if kind == "ollama":
            models = [m.get("name", "") for m in body.get("models", [])]
        elif kind == "gemini":
            models = [m.get("name", "").removeprefix("models/") for m in body.get("models", [])]
        else:
            models = [m.get("id", "") for m in body.get("data", [])]
        models = sorted({m for m in models if m} | set(ep.get("models") or []))
        return {"ok": True, "models": models, "latency_ms": latency_ms, "url": url}
    except Exception as e:
        return {"ok": False, "error": f"Cannot reach {url}: {e.__class__.__name__}",
                "models": list(ep.get("models") or []), "url": url}


@router.post("/api/settings/probe", dependencies=guard)
async def probe_provider(req: ProbeRequest):
    return await _probe(req.provider, req.base_url, req.api_key, req.kind)


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


# ── Doctor (full diagnostics, same checks as `zensynora doctor`) ────────────
@router.get("/api/system/doctor", dependencies=guard)
async def doctor_report(offline: bool = False):
    from myclaw.doctor import run_checks

    report = await asyncio.to_thread(run_checks, None, offline)
    return report.to_dict()


@router.post("/api/system/doctor/fix", dependencies=guard)
async def doctor_fix():
    from myclaw.doctor import apply_fixes, run_checks

    report = await asyncio.to_thread(run_checks)
    done = await asyncio.to_thread(apply_fixes, report)
    after = await asyncio.to_thread(run_checks)
    return {"applied": done, "report": after.to_dict()}


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
