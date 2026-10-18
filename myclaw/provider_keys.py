"""Several API keys per provider (v0.7.5).

Every provider with an API key keeps its plain ``api_key`` as the *default*
key (id ``"default"``) and may hold extra named keys::

    "providers": {"openrouter": {
        "api_key": "sk-main",
        "keys": [{"id": "work", "label": "Work account", "api_key": "sk-work"}],
        "rotate_keys": false}}

Agents pick a key by id (``agents.defaults.key``, ``agents.named[].key``,
``agents.rotation.entries[].key``); an empty id means the default key.
With ``rotate_keys`` on, a rate limit / quota / key error makes the agent try
the same model with the provider's other keys before moving to another model.

Secrets never leave this module except to build a provider client; logs and
messages only ever name key *ids*.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_KEY_ID = "default"
DEFAULT_KEY_LABEL = "Default key"


class UnknownKeyError(ValueError):
    """An agent or request names a key id the provider does not have."""


def normalize_key_id(key_id: Optional[str]) -> Optional[str]:
    """``None`` for the default key ("", None, "default"), else the lower-cased id."""
    k = (key_id or "").strip().lower()
    return None if k in ("", DEFAULT_KEY_ID) else k


def provider_settings(config: Any, provider: str):
    """The settings object holding a provider's keys (built-in or custom), or None."""
    name = (provider or "").strip()
    providers = getattr(config, "providers", None)
    if providers is None:
        return None
    builtin = getattr(type(providers), "model_fields", {})
    if name in builtin and name != "custom":
        pcfg = getattr(providers, name, None)
        return pcfg if pcfg is not None and hasattr(pcfg, "api_key") else None
    try:
        from .provider import find_custom_provider

        return find_custom_provider(config, name)
    except Exception:  # pragma: no cover - defensive
        return None


def supports_keys(config: Any, provider: str) -> bool:
    return provider_settings(config, provider) is not None


def extra_keys(config: Any, provider: str) -> list:
    pcfg = provider_settings(config, provider)
    return list(getattr(pcfg, "keys", None) or []) if pcfg is not None else []


def key_ids(config: Any, provider: str) -> List[str]:
    """All key ids of a provider, the default one first."""
    if not supports_keys(config, provider):
        return [DEFAULT_KEY_ID]
    return [DEFAULT_KEY_ID] + [k.id for k in extra_keys(config, provider)]


def has_key(config: Any, provider: str, key_id: Optional[str]) -> bool:
    kid = normalize_key_id(key_id)
    return kid is None or kid in key_ids(config, provider)


def key_secret(config: Any, provider: str, key_id: Optional[str]) -> str:
    """The secret of one key. Raises UnknownKeyError for a missing id."""
    kid = normalize_key_id(key_id)
    pcfg = provider_settings(config, provider)
    if kid is None:
        return pcfg.api_key.get_secret_value() if pcfg is not None else ""
    for k in extra_keys(config, provider):
        if k.id == kid:
            return k.api_key.get_secret_value()
    raise UnknownKeyError(f"{provider} has no key '{kid}' (Settings → Providers → Keys)")


def rotate_keys_enabled(config: Any, provider: str) -> bool:
    pcfg = provider_settings(config, provider)
    return bool(getattr(pcfg, "rotate_keys", False)) and bool(extra_keys(config, provider))


def key_chain(config: Any, provider: str, key_id: Optional[str]) -> List[Optional[str]]:
    """Keys to try for one request: the chosen one, then (with rotate_keys) the others.

    ``None`` stands for the default key. Keys without a secret are skipped.
    """
    first = normalize_key_id(key_id)
    chain: List[Optional[str]] = [first]
    if not rotate_keys_enabled(config, provider):
        return chain
    pcfg = provider_settings(config, provider)
    if first is not None and pcfg is not None and pcfg.api_key.get_secret_value():
        chain.append(None)
    for k in extra_keys(config, provider):
        if k.id != first and k.api_key.get_secret_value():
            chain.append(k.id)
    return chain


def key_label(config: Any, provider: str, key_id: Optional[str]) -> str:
    kid = normalize_key_id(key_id)
    if kid is None:
        return DEFAULT_KEY_LABEL
    for k in extra_keys(config, provider):
        if k.id == kid:
            return k.label or k.id
    return kid


def config_with_key(config: Any, provider: str, secret: str):
    """A copy of *config* whose built-in provider uses *secret* as its api_key."""
    from pydantic import SecretStr

    pcfg = getattr(config.providers, provider)
    new_p = pcfg.model_copy(update={"api_key": SecretStr(secret)})
    new_providers = config.providers.model_copy(update={provider: new_p})
    return config.model_copy(update={"providers": new_providers})


# ── Who uses which key ───────────────────────────────────────────────────────
def key_references(config: Any) -> List[Tuple[str, str, str]]:
    """(who, provider, key_id) for every agent / rotation entry that names a key."""
    d = config.agents.defaults
    refs: List[Tuple[str, str, str]] = []
    if normalize_key_id(getattr(d, "key", "")):
        refs.append(("default", d.provider, normalize_key_id(d.key)))
    for a in config.agents.named:
        kid = normalize_key_id(getattr(a, "key", ""))
        if kid:
            refs.append((a.name, a.provider or d.provider, kid))
    rot = getattr(config.agents, "rotation", None)
    for e in getattr(rot, "entries", []) or []:
        kid = normalize_key_id(getattr(e, "key", ""))
        if kid:
            refs.append((f"rotation {e.provider}/{e.model}", e.provider, kid))
    return refs


def agents_using_key(config: Any, provider: str, key_id: Optional[str]) -> List[str]:
    """Agent names (and rotation entries) that use this key of *provider*."""
    kid = normalize_key_id(key_id)
    d = config.agents.defaults
    out: List[str] = []
    if d.provider == provider and normalize_key_id(getattr(d, "key", "")) == kid:
        out.append("default")
    for a in config.agents.named:
        prov = a.provider or d.provider
        own = normalize_key_id(getattr(a, "key", ""))
        if not a.provider and own is None:
            own = normalize_key_id(getattr(d, "key", ""))  # inherits the default agent's key
        if prov == provider and own == kid:
            out.append(a.name)
    rot = getattr(config.agents, "rotation", None)
    for e in getattr(rot, "entries", []) or []:
        if e.provider == provider and normalize_key_id(getattr(e, "key", "")) == kid:
            out.append(f"rotation: {e.model}")
    return out


def missing_key_refs(config: Any) -> List[str]:
    """Readable problems: agents that use a key which no longer exists (for the doctor)."""
    out = []
    for who, provider, kid in key_references(config):
        if not has_key(config, provider, kid):
            what = f"Agent {who}" if not who.startswith("rotation") else who.capitalize()
            out.append(f"{what} uses key '{kid}' of {provider}, which no longer exists")
    return out


def keys_overview(config: Any) -> Dict[str, Dict[str, Any]]:
    """Every provider that can hold keys: its keys (no secrets) and who uses them."""
    from .provider import provider_names

    out: Dict[str, Dict[str, Any]] = {}
    for name in provider_names(config):
        pcfg = provider_settings(config, name)
        if pcfg is None:
            continue
        keys = [{"id": DEFAULT_KEY_ID, "label": DEFAULT_KEY_LABEL,
                 "set": bool(pcfg.api_key.get_secret_value()),
                 "used_by": agents_using_key(config, name, None)}]
        for k in extra_keys(config, name):
            keys.append({"id": k.id, "label": k.label or k.id, "set": bool(k.api_key.get_secret_value()),
                         "used_by": agents_using_key(config, name, k.id)})
        out[name] = {"rotate_keys": bool(getattr(pcfg, "rotate_keys", False)), "keys": keys}
    return out
