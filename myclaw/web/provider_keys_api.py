"""Several API keys per provider (v0.7.5) — manage them from the Web UI.

Routes (served through models_api's router, same guard as the settings API)
------
GET    /api/provider-keys                         every provider's keys (no secrets) + who uses each
POST   /api/provider-keys/{provider}              add a key {label, api_key, id?}
PATCH  /api/provider-keys/{provider}/{key_id}     rename {label} and/or replace the secret {api_key}
DELETE /api/provider-keys/{provider}/{key_id}     remove a key (refused while agents use it)
PUT    /api/provider-keys/{provider}/rotate       {"enabled": bool} try other keys on rate limits
POST   /api/provider-keys/{provider}/{key_id}/test   test connection + list models with that key

The default key ("default") is the provider's plain ``api_key``: it can be
replaced but not renamed or deleted here. Secrets are never returned.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.config import load_config
from myclaw.provider_keys import (
    DEFAULT_KEY_ID,
    agents_using_key,
    keys_overview,
    normalize_key_id,
    provider_settings,
)
from myclaw.web import settings_api
from myclaw.web.settings_api import SettingsUpdate, guard

router = APIRouter()


class NewKey(BaseModel):
    label: str = ""
    api_key: str
    id: Optional[str] = None


class KeyChange(BaseModel):
    label: Optional[str] = None
    api_key: Optional[str] = None


class RotateKeys(BaseModel):
    enabled: bool


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (text or "").strip().lower()).strip("-_")
    return s[:32]


def _require_provider(provider: str):
    cfg = load_config()
    if provider_settings(cfg, provider) is None:
        raise HTTPException(status_code=404, detail=f"'{provider}' does not use API keys (or is unknown).")
    return cfg


def _stored(provider: str) -> Dict[str, Any]:
    """The provider's settings as stored on disk (secrets in plain text, server side only)."""
    raw = settings_api._read_raw_config()
    if provider in _builtin_names():
        return copy.deepcopy((raw.get("providers") or {}).get(provider) or {})
    for c in (raw.get("providers") or {}).get("custom") or []:
        if isinstance(c, dict) and c.get("name") == provider:
            return copy.deepcopy(c)
    return {}


def _builtin_names() -> List[str]:
    from myclaw.provider import SUPPORTED_PROVIDERS

    return list(SUPPORTED_PROVIDERS)


async def _save(provider: str, changes: Dict[str, Any]) -> None:
    """Write some fields of one provider (built-in path or its custom-list item)."""
    if provider in _builtin_names():
        await settings_api.update_settings(SettingsUpdate(changes={
            f"providers.{provider}.{field}": value for field, value in changes.items()}))
        return
    raw = settings_api._read_raw_config()
    items = copy.deepcopy((raw.get("providers") or {}).get("custom") or [])
    for item in items:
        if isinstance(item, dict) and item.get("name") == provider:
            item.update(changes)
    await settings_api.update_settings(SettingsUpdate(changes={"providers.custom": items}))


def _overview() -> Dict[str, Any]:
    return {"providers": keys_overview(load_config(force_reload=True))}


@router.get("/api/provider-keys", dependencies=guard)
async def list_provider_keys():
    return _overview()


@router.post("/api/provider-keys/{provider}", dependencies=guard)
async def add_provider_key(provider: str, req: NewKey):
    _require_provider(provider)
    secret = "".join((req.api_key or "").split())
    if not secret:
        raise HTTPException(status_code=400, detail="Paste the API key.")
    keys = list(_stored(provider).get("keys") or [])
    ids = {DEFAULT_KEY_ID} | {k.get("id") for k in keys}
    kid = _slug(req.id or req.label) or "key"
    if req.id and kid in ids:
        raise HTTPException(status_code=400, detail=f"{provider} already has a key '{kid}'.")
    base, n = kid, 2
    while kid in ids:  # "work" → "work-2" when the id came from the label
        kid = f"{base[:29]}-{n}"
        n += 1
    keys.append({"id": kid, "label": (req.label or "").strip()[:80], "api_key": secret})
    await _save(provider, {"keys": keys})
    return {**_overview(), "added": kid}


@router.patch("/api/provider-keys/{provider}/{key_id}", dependencies=guard)
async def change_provider_key(provider: str, key_id: str, req: KeyChange):
    _require_provider(provider)
    kid = normalize_key_id(key_id)
    secret = "".join((req.api_key or "").split())
    if kid is None:
        if req.label is not None and req.api_key is None:
            raise HTTPException(status_code=400, detail="The default key cannot be renamed.")
        if secret:
            await _save(provider, {"api_key": secret})
        return _overview()
    keys = list(_stored(provider).get("keys") or [])
    for k in keys:
        if k.get("id") == kid:
            if req.label is not None:
                k["label"] = req.label.strip()[:80]
            if secret:
                k["api_key"] = secret
            break
    else:
        raise HTTPException(status_code=404, detail=f"{provider} has no key '{kid}'.")
    await _save(provider, {"keys": keys})
    return _overview()


@router.delete("/api/provider-keys/{provider}/{key_id}", dependencies=guard)
async def delete_provider_key(provider: str, key_id: str):
    cfg = _require_provider(provider)
    kid = normalize_key_id(key_id)
    if kid is None:
        raise HTTPException(status_code=400, detail="The default key cannot be deleted (remove it in Settings → Providers).")
    users = agents_using_key(cfg, provider, kid)
    if users:
        raise HTTPException(status_code=409, detail=f"Key '{kid}' is used by: {', '.join(users)}. "
                            "Pick another key for them first.")
    keys = list(_stored(provider).get("keys") or [])
    left = [k for k in keys if k.get("id") != kid]
    if len(left) == len(keys):
        raise HTTPException(status_code=404, detail=f"{provider} has no key '{kid}'.")
    await _save(provider, {"keys": left})
    return _overview()


@router.put("/api/provider-keys/{provider}/rotate", dependencies=guard)
async def set_rotate_keys(provider: str, req: RotateKeys):
    _require_provider(provider)
    await _save(provider, {"rotate_keys": bool(req.enabled)})
    return _overview()


@router.post("/api/provider-keys/{provider}/{key_id}/test", dependencies=guard)
async def test_provider_key(provider: str, key_id: str):
    _require_provider(provider)
    return await settings_api._probe(provider, None, None, None, key_id)
