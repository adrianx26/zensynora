"""Context length per agent: estimate what a prompt needs, find the model's limit,
and keep a conversation inside the chosen size.

* :func:`estimate_context` — rough token counts of everything an agent sends on
  every turn (its system prompt, skills list, team section, the tool
  definitions) and from that a **minimum** context (the fixed part + room for
  one exchange and the reply) and a **recommended** one (plus knowledge-base
  results, the recent conversation and a longer reply). Shown as markers on
  the context slider in Settings → Agents.
* :func:`model_context_max` — the model's own maximum, asked from the provider
  (Ollama ``/api/show``, LM Studio ``/api/v0/models``, llama.cpp ``/props``,
  OpenRouter / Groq / vLLM ``/models``, Gemini, Anthropic) or taken from the
  built-in model library.
* :func:`agent_context_length` / :func:`fit_messages` — the agent's setting
  (0 = the model's default) and trimming the oldest messages so a turn fits.
  Ollama also receives it as ``num_ctx`` (:data:`CONTEXT_LENGTH`).
* 2026-11-08 :func:`call_fitted` / :func:`learn_from_error` — every model call
  is fitted to the model's known limit (learned from an "exceeds the context"
  error, LM Studio's *loaded* context, or the setting): the oldest messages are
  left out and, when the tool list alone is too big, the tools get short
  descriptions and the most useful come first (:data:`TOOL_BUDGET`).

Token counts are estimates (about 3.8 characters per token for English and
code); real tokenizers differ by ±20 %.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from contextvars import ContextVar
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3.8
REPLY_MIN = 1024          # room for a short reply
REPLY_RECOMMENDED = 2048  # room for a longer reply / tool calls
EXCHANGE_TOKENS = 512     # one user message + one short answer
KB_TOKENS = 1500          # knowledge-base results added to the system prompt
MSG_TOKENS = 150          # average message in the recent conversation
SLIDER_FLOOR = 1024
FALLBACK_MAX = 131072     # when the model's limit is unknown

# Context length of the current agent turn (0 = model default); read by the
# Ollama provider to send num_ctx. Set in Agent._provider_chat.
CONTEXT_LENGTH: ContextVar[int] = ContextVar("context_length", default=0)

# 2026-11-08: tokens the tool list of this call may use (0 = all tools as they are, -1 = none).
TOOL_BUDGET: ContextVar[int] = ContextVar("tool_budget", default=0)
# Sent first when the tool list must be shortened.
ESSENTIAL_TOOLS = ("shell", "read_file", "write_file", "browse", "web_search", "delegate", "search_knowledge",
                   "read_knowledge", "write_to_knowledge", "list_tools", "get_tool_documentation",
                   "download_file", "schedule", "list_schedules")
# (provider, model) -> context size learned from an "exceeds the context" error
_LEARNED: dict[tuple[str, str], int] = {}
_LIMIT_RE = re.compile(
    r'n_ctx"?\s*[:=]\s*(\d{3,7})|available context size \((\d{3,7})|maximum context length is (\d{3,7})|'
    r"context length of (\d{3,7})|context window of (\d{3,7})|>\s*(\d{3,7}) maximum", re.I)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _round_up(n: int, step: int) -> int:
    return int(math.ceil(n / step) * step)


def _tool_schema_tokens() -> tuple[int, int]:
    try:
        from .provider import _get_tool_schemas

        schemas = _get_tool_schemas() or []
        return estimate_tokens(json.dumps(schemas, separators=(",", ":"))), len(schemas)
    except Exception as e:  # never block the estimate
        logger.debug("tool schemas unavailable for the estimate: %s", e)
        return 0, 0


def estimate_context(config: Any, agent_name: str, system_prompt: str = "",
                     skills: Optional[list[str]] = None) -> dict[str, Any]:
    """Token estimate for one agent; ``system_prompt``/``skills`` may be unsaved edits."""
    from .agent import SYSTEM_PROMPT

    parts: dict[str, int] = {}
    parts["prompt"] = estimate_tokens((system_prompt or "").strip() or SYSTEM_PROMPT)
    try:
        from .agent_skills import prompt_section

        parts["skills"] = estimate_tokens(prompt_section(list(skills or [])))
    except Exception:
        parts["skills"] = 0
    try:
        from .agent_team import team_section

        parts["team"] = estimate_tokens(team_section(config, agent_name))
    except Exception:
        parts["team"] = 0
    parts["tools"], tool_count = _tool_schema_tokens()
    fixed = sum(parts.values())
    threshold = int(getattr(config.agents, "summarization_threshold", 10) or 10)
    history = threshold * MSG_TOKENS
    minimum = _round_up(fixed + EXCHANGE_TOKENS + REPLY_MIN, 512)
    recommended = _round_up(fixed + KB_TOKENS + history + REPLY_RECOMMENDED, 1024)
    return {
        "parts": parts, "tool_count": tool_count, "fixed": fixed,
        "history": history, "knowledge": KB_TOKENS,
        "reply_min": REPLY_MIN, "reply_recommended": REPLY_RECOMMENDED,
        "min": max(SLIDER_FLOOR, minimum), "recommended": max(SLIDER_FLOOR, recommended),
        "chars_per_token": CHARS_PER_TOKEN,
    }


# ── The model's maximum ──────────────────────────────────────────────────────
_MAX_CACHE: dict[tuple, tuple[float, Optional[int], str]] = {}
_CACHE_SECONDS = 3600
# 2026-11-08: LM Studio's loaded_context_length (what the model runs with now) before its maximum
_CTX_KEYS = ("loaded_context_length", "context_length", "context_window", "max_context_length", "max_model_len",
             "max_input_tokens", "input_token_limit", "inputTokenLimit", "n_ctx")


def _find_ctx(obj: Any) -> Optional[int]:
    """First positive context-size field in a model description (nested ok)."""
    if isinstance(obj, dict):
        for k in _CTX_KEYS:
            v = obj.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
        for v in obj.values():
            if isinstance(v, dict):
                found = _find_ctx(v)
                if found:
                    return found
    return None


def _root(url: str) -> str:
    return re.sub(r"/v1/?$", "", url.rstrip("/"))


async def _ask_provider(ep: dict[str, Any], model: str) -> Optional[int]:
    import httpx

    kind, url, key = ep.get("kind"), (ep.get("base_url") or "").rstrip("/"), ep.get("api_key") or ""
    if not url:
        return None
    async with httpx.AsyncClient(timeout=8) as c:
        if kind == "ollama":
            r = await c.post(f"{url}/api/show", json={"model": model})
            r.raise_for_status()
            info = r.json().get("model_info") or {}
            vals = [v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)]
            return max(vals) if vals else None
        if kind == "gemini":
            r = await c.get(f"{url}/models/{model.removeprefix('models/')}", params={"key": key})
            r.raise_for_status()
            return _find_ctx(r.json())
        if kind == "anthropic":
            r = await c.get(f"{url}/models/{model}", headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return _find_ctx(r.json())
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # LM Studio and llama.cpp tell the size through their own endpoints.
        if ep.get("local"):
            for path in (f"/api/v0/models/{model}", "/props"):
                try:
                    r = await c.get(_root(url) + path, headers=headers)
                    if r.status_code == 200:
                        found = _find_ctx(r.json())
                        if found:
                            return found
                except httpx.HTTPError:
                    continue
        r = await c.get(f"{url}/models", headers=headers)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        for item in items or []:
            if isinstance(item, dict) and model in (item.get("id"), item.get("name"), item.get("model")):
                return _find_ctx(item)
    return None


def _library_max(model: str) -> Optional[int]:
    try:
        from .llm_library import get_model_metadata

        meta = get_model_metadata(model) or {}
        v = meta.get("context_window")
        return int(v) if v else None
    except Exception:
        return None


async def model_context_max(config: Any, provider: str, model: str, key: str = "") -> dict[str, Any]:
    """{"max": int | None, "source": "provider" | "library" | "unknown", "error"?}"""
    from .provider import provider_endpoint

    model = (model or "").strip()
    if not model:
        return {"max": None, "source": "unknown"}
    ck = (provider, model, key or "")
    hit = _MAX_CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return {"max": hit[1], "source": hit[2]}
    error = ""
    value: Optional[int] = None
    source = "unknown"
    try:
        ep = provider_endpoint(config, provider, key or None)
        if ep:
            value = await _ask_provider(ep, model)
            source = "provider" if value else "unknown"
    except Exception as e:
        error = str(e)[:200]
        logger.debug("context size of %s/%s not available: %s", provider, model, e)
    if not value:
        value = _library_max(model)
        source = "library" if value else "unknown"
    if value or not error:  # don't cache "server down"
        _MAX_CACHE[ck] = (time.monotonic(), value, source)
    out: dict[str, Any] = {"max": value, "source": source}
    if error and not value:
        out["error"] = error
    return out


# ── Using the setting ────────────────────────────────────────────────────────
def agent_context_length(config: Any, agent_name: str) -> int:
    """The agent's context length (0 = use the model's default)."""
    try:
        if agent_name == "default":
            return int(getattr(config.agents.defaults, "context_length", 0) or 0)
        for a in config.agents.named:
            if a.name == agent_name:
                return int(getattr(a, "context_length", 0) or 0)
    except Exception:
        pass
    return 0


def message_tokens(m: dict[str, Any]) -> int:
    content = m.get("content") or ""
    if not isinstance(content, str):
        content = json.dumps(content)
    extra = json.dumps(m.get("tool_calls")) if m.get("tool_calls") else ""
    return estimate_tokens(content) + estimate_tokens(extra) + 4


def fit_messages(messages: list[dict[str, Any]], limit: int,
                 reserve: int = REPLY_MIN) -> tuple[list[dict[str, Any]], int]:
    """Drop the oldest conversation messages until the turn fits ``limit`` tokens.

    Keeps the system message(s) and the newest message. Tool results whose
    tool call was dropped are dropped too (providers reject them).
    Returns (messages, number dropped). ``limit`` 0 = no change.
    """
    if limit <= 0 or not messages:
        return messages, 0
    tools, _ = _tool_schema_tokens()
    budget = limit - reserve - tools
    head = [m for m in messages if m.get("role") == "system"]
    body = [m for m in messages if m.get("role") != "system"]
    used = sum(message_tokens(m) for m in head) + sum(message_tokens(m) for m in body)
    dropped = 0
    while body[:-1] and used > budget:
        used -= message_tokens(body.pop(0))
        dropped += 1
        while body[:-1] and body[0].get("role") == "tool":
            used -= message_tokens(body.pop(0))
            dropped += 1
    if dropped:
        logger.info("Context %d tokens: left out the %d oldest message(s)", limit, dropped)
    return head + body, dropped


# ── Fitting every call (2026-11-08) ─────────────────────────────────────────
def learn_from_error(provider: str, model: str, err: BaseException | str) -> Optional[int]:
    """The context size an "exceeds the context" error names; remembered for that model."""
    from .llm_errors import is_context_overflow

    if not is_context_overflow(err):
        return None
    m = _LIMIT_RE.search(str(err))
    if not m:
        return None
    value = int(next(g for g in m.groups() if g))
    _LEARNED[(provider, model)] = value
    return value


def limit_for(provider: str, model: str) -> int:
    """The smallest known limit for this call: learned, the agent's setting (0 = unknown)."""
    limits = [v for v in (_LEARNED.get((provider, model)), CONTEXT_LENGTH.get()) if v]
    return min(limits) if limits else 0


def _compact(schema: dict[str, Any]) -> dict[str, Any]:
    """A tool definition with short descriptions (first sentence, ≤ 140 characters)."""
    s = json.loads(json.dumps(schema))
    fn = s.get("function", s)
    desc = (fn.get("description") or "").strip()
    fn["description"] = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0][:140]
    for p in ((fn.get("parameters") or {}).get("properties") or {}).values():
        if isinstance(p, dict) and p.get("description"):
            p["description"] = str(p["description"])[:60]
    return s


def fit_tool_list(schemas: list) -> list:
    """The tool list for this call within :data:`TOOL_BUDGET` (most useful tools first)."""
    budget = TOOL_BUDGET.get()
    if budget == 0:
        return schemas
    if budget < 0:
        return []

    def name(s: dict) -> str:
        return (s.get("function") or s).get("name", "")

    rank = {n: i for i, n in enumerate(ESSENTIAL_TOOLS)}
    ordered = sorted(schemas, key=lambda s: rank.get(name(s), len(rank)))  # stable: others keep their order
    out, used = [], 0
    for s in ordered:
        c = _compact(s)
        t = estimate_tokens(json.dumps(c, separators=(",", ":")))
        if used + t <= budget:
            out.append(c)
            used += t
    return out


def _plan_tools(messages: list[dict[str, Any]], limit: int, reserve: int = REPLY_MIN) -> int:
    """The TOOL_BUDGET for a call to a model with ``limit`` tokens (0 = all tools fit)."""
    token = TOOL_BUDGET.set(0)
    try:
        full, _ = _tool_schema_tokens()
    finally:
        TOOL_BUDGET.reset(token)
    head = sum(message_tokens(m) for m in messages if m.get("role") == "system")
    last = message_tokens(messages[-1]) if messages else 0
    conversation = min(1024, limit // 8)  # room for the recent messages
    room = limit - reserve - head - last - conversation
    if full <= room:
        return 0
    return room if room >= 400 else -1


async def call_fitted(messages: list[dict[str, Any]], limit: int, send: Any) -> Any:
    """``await send(messages)`` with the request fitted to ``limit`` tokens (0 = as it is)."""
    if limit <= 0:
        return await send(messages)
    token = TOOL_BUDGET.set(_plan_tools(messages, limit))
    try:
        fitted, _ = fit_messages(messages, limit)
        if TOOL_BUDGET.get():
            logger.info("Context %d tokens: tool list shortened to %s", limit,
                        "none" if TOOL_BUDGET.get() < 0 else f"{TOOL_BUDGET.get()} tokens")
        return await send(fitted)
    finally:
        TOOL_BUDGET.reset(token)
