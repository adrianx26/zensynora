"""Model health, automatic fallback order and model replacement (2026-11-01).

Live case: nine agents sent ``cohere/north-mini-code:free`` to the Gemini
provider (404) and, having their own model, had no fallback at all.

* ``record`` / ``order`` — success rate and latency of every provider/model in
  this process; fallbacks are ordered by them (fully automatic, the given order
  only breaks ties). The agent's own model stays first while it is healthy.
* ``ATTEMPT_SECONDS`` / ``CALL_BUDGET_SECONDS`` — one model may take at most
  this long to start answering, a whole call (all fallbacks) at most this long,
  so no workflow waits longer than 180 s for a model.
* ``known_context`` — context sizes already known (provider answers, library),
  to prefer a bigger context after "context too long".
* ``is_permanent`` / ``replace_model`` — a model that will not come back
  (unknown, refused for this app/account) is replaced in every agent, the
  default and the rotation list by the model that answered instead; each change
  is written to ``~/.myclaw/model_changes.jsonl`` with the old value.
* ``model_mismatch`` — provider/model pairs that cannot work (Doctor, saving).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ATTEMPT_SECONDS = 90.0        # one model must start answering within this
CALL_BUDGET_SECONDS = 170.0   # all fallbacks of one call together (owner's limit: 180 s)
_ALPHA = 0.3                  # weight of the newest result in the moving averages

_lock = threading.Lock()


@dataclass
class Health:
    provider: str
    model: str
    calls: int = 0
    failures: int = 0
    fail_rate: float = 0.0        # moving average, 0..1
    latency: Optional[float] = None  # moving average of successful calls, seconds
    last_error: str = ""
    last_ok: float = 0.0
    last_fail: float = 0.0


_HEALTH: dict[tuple[str, str], Health] = {}


def reset() -> None:
    with _lock:
        _HEALTH.clear()


def record(provider: str, model: str, ok: bool, latency: float, error: str = "") -> None:
    with _lock:
        h = _HEALTH.setdefault((provider, model), Health(provider, model))
        h.calls += 1
        h.fail_rate = (1 - _ALPHA) * h.fail_rate + _ALPHA * (0.0 if ok else 1.0)
        if ok:
            h.latency = latency if h.latency is None else (1 - _ALPHA) * h.latency + _ALPHA * latency
            h.last_ok = time.time()
        else:
            h.failures += 1
            h.last_error = re.sub(r"\s+", " ", str(error))[:300]
            h.last_fail = time.time()


def snapshot() -> list[dict[str, Any]]:
    with _lock:
        rows = [asdict(h) for h in _HEALTH.values()]
    for r in rows:
        r["score"] = round(_score(r["provider"], r["model"]), 2)
    return sorted(rows, key=lambda r: r["score"])


def _score(provider: str, model: str) -> float:
    """Expected cost of trying this model: lower is better.

    Latency (seconds) times a penalty that grows with recent failures. A model
    without data gets a neutral score: better than a failing one, worse than
    one that is fast and works.
    """
    h = _HEALTH.get((provider, model))
    if h is None or h.calls == 0:
        return 8.0
    latency = h.latency if h.latency is not None else 8.0
    return latency * (1.0 + 6.0 * h.fail_rate) + 20.0 * h.fail_rate


def order(candidates: list[tuple], keep_first: bool = False) -> list[tuple]:
    """Candidates ((provider, model[, key])) by health; stable, so ties keep the given order.

    ``keep_first``: the first candidate (the agent's own model) stays first
    unless it has been failing (fail rate over 0.5).
    """
    with _lock:
        scores = {c: _score(c[0], c[1]) for c in candidates}
        first_ok = keep_first and candidates and (
            _HEALTH.get((candidates[0][0], candidates[0][1])) is None
            or _HEALTH[(candidates[0][0], candidates[0][1])].fail_rate <= 0.5)
    if first_ok:
        return [candidates[0]] + sorted(candidates[1:], key=lambda c: scores[c])
    return sorted(candidates, key=lambda c: scores[c])


def known_context(provider: str, model: str) -> Optional[int]:
    """The model's context size if already known (no network)."""
    from . import context_budget

    for (p, m, _k), (_t, value, _src) in list(context_budget._MAX_CACHE.items()):
        if p == provider and m == model and value:
            return int(value)
    return context_budget._library_max(model)


# ── permanent failures and replacing a model everywhere ─────────────────────
_PERMANENT_RE = re.compile(
    r"\b404\b|model not found|no such model|not a valid model|is not found|does not exist"
    r"|only available on agentic harnesses|\b403\b|forbidden|not supported for generatecontent")


def is_permanent(err: BaseException | str) -> bool:
    """True when the model will not work again by waiting (unknown, refused for this app)."""
    return bool(_PERMANENT_RE.search(str(err).lower()))


_TASKS: set = set()  # running background replacements (kept so they are not garbage-collected)


def start_replacement(old_provider: str, old_model: str, new_provider: str, new_model: str, reason: str) -> None:
    """Replace a model that is gone for good, in the background (the agent's turn goes on)."""
    import asyncio

    task = asyncio.get_running_loop().create_task(
        replace_in_background(old_provider, old_model, new_provider, new_model, reason))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def replace_in_background(old_provider: str, old_model: str, new_provider: str, new_model: str,
                                reason: str) -> None:
    """replace_model for the automatic switch: once per model at a time, never raises."""
    key = (old_provider, old_model)
    if key in _IN_PROGRESS or (old_provider, old_model) == (new_provider, new_model):
        return
    _IN_PROGRESS.add(key)
    try:
        await replace_model(old_provider, old_model, new_provider, new_model, reason=reason)
    except Exception as e:  # a failed settings write must not disturb the agent's turn
        logger.warning("Automatic switch from %s/%s to %s/%s failed: %s", old_provider, old_model,
                       new_provider, new_model, e)
    finally:
        _IN_PROGRESS.discard(key)


def changes_file() -> Path:
    return Path.home() / ".myclaw" / "model_changes.jsonl"


def history(limit: int = 50) -> list[dict[str, Any]]:
    p = changes_file()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(rows))


_IN_PROGRESS: set[tuple[str, str]] = set()  # models being replaced right now


async def replace_model(old_provider: str, old_model: str, new_provider: str, new_model: str,
                        reason: str = "") -> dict[str, Any]:
    """Use new_provider/new_model wherever old_provider/old_model is set.

    The default model, every named agent (an empty provider means the default
    provider) and the rotation list. Goes through the normal settings save
    (validation, agents reload). Returns {"changed": [names]}.
    """
    from .config import load_config
    from .provider import provider_names
    from .web import settings_api

    cfg = load_config()
    if new_provider not in provider_names(cfg) or not new_model:
        raise ValueError(f"Unknown provider or empty model: {new_provider}/{new_model}")
    d = cfg.agents.defaults
    changes: dict[str, Any] = {}
    changed: list[str] = []
    if (d.provider, d.model) == (old_provider, old_model):
        changes["agents.defaults.provider"], changes["agents.defaults.model"] = new_provider, new_model
        changed.append("default")
    named = []
    for a in cfg.agents.named:
        row = a.model_dump(mode="json")
        if ((a.provider or d.provider), a.model) == (old_provider, old_model):
            row["provider"], row["model"] = new_provider, new_model
            changed.append(a.name)
        named.append(row)
    if any(n in changed for n in (a.name for a in cfg.agents.named)):
        changes["agents.named"] = named
    entries, seen, rot_changed = [], set(), False
    for e in cfg.agents.rotation.entries:
        row = e.model_dump(mode="json")
        if (e.provider, e.model) == (old_provider, old_model):
            row["provider"], row["model"] = new_provider, new_model
            rot_changed = True
        k = (row["provider"], row["model"], row.get("key") or "")
        if k not in seen:
            seen.add(k)
            entries.append(row)
    if rot_changed:
        changes["agents.rotation.entries"] = entries
        changed.append("rotation")
    if not changes:
        return {"changed": []}
    await settings_api.update_settings(settings_api.SettingsUpdate(changes=changes))
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "old": f"{old_provider}/{old_model}",
             "new": f"{new_provider}/{new_model}", "changed": changed, "reason": reason[:300]}
    with changes_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    logger.warning("Model %s/%s replaced by %s/%s in %s (%s)", old_provider, old_model, new_provider,
                   new_model, ", ".join(changed), reason[:200] or "by hand")
    return {"changed": changed}


# ── provider/model pairs that cannot work ───────────────────────────────────
_HOSTS = {"generativelanguage.googleapis.com": "gemini", "openrouter.ai": "openrouter",
          "api.openai.com": "openai", "api.groq.com": "groq", "api.anthropic.com": "anthropic"}


def _service(provider: str, config: Any = None) -> str:
    """Which hosted service a provider is (built-in name, or a custom provider's URL/name)."""
    name = (provider or "").lower()
    if name in ("gemini", "openrouter", "openai", "groq", "anthropic"):
        return name
    url = ""
    if config is not None:
        for cp in getattr(getattr(config, "providers", None), "custom", []) or []:
            if getattr(cp, "name", "") == provider:
                url = (getattr(cp, "base_url", "") or "").lower()
    for host, svc in _HOSTS.items():
        if host in url:
            return svc
    for svc in ("gemini", "openrouter"):
        if svc in name:
            return svc
    return ""


def model_mismatch(provider: str, model: str, config: Any = None) -> Optional[str]:
    """Why this provider cannot serve this model, or None (unknown services are not checked)."""
    svc, m = _service(provider, config), (model or "").strip()
    if not m or not svc:
        return None
    if svc == "gemini":
        bare = m[len("models/"):] if m.startswith("models/") else m
        if "/" in bare:
            return f"'{m}' is not a Gemini model (Gemini models look like models/gemini-…)"
    elif svc == "openrouter":
        if m.startswith("openrouter/") and "/" in m[len("openrouter/"):]:
            return f"'{m}' has an extra 'openrouter/' prefix — use '{m.split('/', 1)[1].removeprefix('openrouter/')}'"
        if "/" not in m:
            return f"OpenRouter models are named vendor/model (e.g. openai/{m})"
    elif svc in ("openai", "anthropic") and "/" in m:
        return f"'{m}' looks like an OpenRouter model name, not a {svc} one"
    return None


def mismatches(config: Any) -> list[tuple[str, str, str, str]]:
    """(who, provider, model, why) for the default model, agents and rotation entries."""
    d = config.agents.defaults
    rows = [("default", d.provider, d.model)]
    rows += [(a.name, a.provider or d.provider, a.model or d.model) for a in config.agents.named]
    rows += [(f"rotation {e.model}", e.provider, e.model) for e in config.agents.rotation.entries]
    out = []
    for who, p, m in rows:
        why = model_mismatch(p, m, config)
        if why:
            out.append((who, p, m, why))
    return out
