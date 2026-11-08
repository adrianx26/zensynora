"""Readable explanations of model-server errors (added in v0.7.3).

Before 0.7.3 most failures reached the user as "the model returned an empty
answer twice" — the real reason (a model that refuses this kind of app, a
rate limit, a wrong URL, a bad key) was only in the log. `describe_llm_error`
turns the exception into one sentence with what to do, and `cooldown_for`
says how long rotation should skip a model after such an error.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# (provider, model) -> time until which it is skipped after a hard failure.
# Shared by agent rotation and utility calls (prompt writer, job judge). v0.7.3
# v0.7.5: a named API key adds a third part, (provider, model, key) — see cooldown_key.
MODEL_COOLDOWN: Dict[tuple, float] = {}

_RULES = [
    # (pattern in the lower-cased error, explanation, cooldown seconds)
    (r"only available on agentic harnesses",
     "this model is only offered to specific apps by OpenRouter and cannot be used by ZenSynora — pick another model on the Models page", 1800),
    (r"\b401\b|unauthori[sz]ed|invalid api key|incorrect api key|no auth credentials",
     "the API key was refused — check it in Settings → Providers", 1800),
    (r"\b402\b|insufficient credits|payment required|quota exceeded",
     "the account has no credits or quota left for this model", 1800),
    (r"\b403\b|forbidden",
     "the provider refused this model for this account", 1800),
    (r"\b404\b|model not found|no such model|does not exist|not a valid model",
     "the model name is unknown on this server — pick one from the list on the Models page", 1800),
    (r"unexpected endpoint or method",
     "the server URL looks wrong — for LM Studio and llama.cpp it must end in /v1 (Settings → Providers)", 600),
    # 2026-11-08: a daily limit does not pass in a minute — skip those models for an hour
    (r"per-day|per day|daily limit|requests per day",
     "the provider's daily limit for this model is used up (OpenRouter free models: a limit per day) — it resets "
     "within a day; until then rotation skips it — use another model or add credits", 3600),
    (r"\b429\b|rate.?limit|too many requests|temporarily rate-limited",
     "the model is rate-limited right now (free models often are) — try again in a minute or turn on rotation", 60),
    (r"non-empty content|invalid message provided",
     "the provider rejected the conversation format", 0),
    # 2026-11-08: also LM Studio / llama.cpp ("exceeds the available context size") and Anthropic
    (r"context length|maximum context|too many tokens|context_length_exceeded|context size|"
     r"exceed_context|prompt is too long",
     "the request is longer than the model's context — ZenSynora retries once with a shorter one; if it still "
     "fails, give the model a larger context (in LM Studio: the model's context length) or clear the channel", 0),
    (r"connect|connection refused|timed out|timeout|name or service not known|unreachable",
     "the model server cannot be reached — is it running, and is the URL right?", 30),
]


def _match(err: BaseException | str):
    text = str(err).lower()
    for pattern, explanation, cooldown in _RULES:
        if re.search(pattern, text):
            return explanation, cooldown
    return None, 0


def describe_llm_error(err: BaseException | str, model: Optional[str] = None) -> str:
    explanation, _ = _match(err)
    raw = re.sub(r"\s+", " ", str(err)).strip()
    prefix = f"{model}: " if model else ""
    if explanation:
        return f"{prefix}{explanation}. (Details: {raw[:200]})"
    return f"{prefix}{raw[:300]}"


def cooldown_for(err: BaseException | str) -> int:
    """Seconds rotation should skip the model after this error (0 = don't skip)."""
    return _match(err)[1]


# v0.7.5: errors that another API key of the same provider may not have
# (rate limit, no credits / quota, key refused).
_KEY_ERROR_RE = re.compile(
    r"\b429\b|rate.?limit|too many requests|temporarily rate-limited"
    r"|\b402\b|insufficient credits|payment required|quota exceeded|exceeded your current quota"
    r"|\b401\b|unauthori[sz]ed|invalid api key|incorrect api key|no auth credentials"
)


_DAILY_RE = re.compile(r"per-day|per day|daily limit|requests per day")
_RATE_LIMIT_RE = re.compile(r"\b429\b|rate.?limit|too many requests|temporarily rate-limited")


def is_rate_limit(err: BaseException | str) -> bool:
    """True for "slow down" errors that usually pass after a few seconds (not a daily limit)."""
    text = str(err).lower()
    return bool(_RATE_LIMIT_RE.search(text)) and not _DAILY_RE.search(text)


def is_provider_error(err: BaseException | str) -> bool:
    """True when the error comes from the model server (rate limit, timeout,
    refused key, unknown model …) — an expected outside problem, not a bug in
    ZenSynora, so no traceback and no medic diagnosis are needed (2026-10-18)."""
    return _match(err)[0] is not None


_CONTEXT_RE = re.compile(r"context length|maximum context|too many tokens|context_length_exceeded|context window|"
                         r"context size|exceed_context|prompt is too long")


def is_context_overflow(err: BaseException | str) -> bool:
    """True when the conversation is too long for the model (2026-11-01)."""
    return bool(_CONTEXT_RE.search(str(err).lower()))


def is_key_error(err: BaseException | str) -> bool:
    """True when trying the same model with another key of the provider may help."""
    return bool(_KEY_ERROR_RE.search(str(err).lower()))


def cooldown_key(provider: str, model: str, key: Optional[str] = None) -> tuple:
    """MODEL_COOLDOWN key: (provider, model) for the default key, else (provider, model, key)."""
    return (provider, model) if not key or key == "default" else (provider, model, key)


def log_failure(log, what: str, err: BaseException) -> None:
    """Log a failed turn/job (2026-11-08): a model-server problem (rate limit, context too
    small, server down …) is a WARNING with one line; anything else is a bug: ERROR + traceback."""
    from .exceptions import ProviderUnavailableError

    if isinstance(err, ProviderUnavailableError) or is_provider_error(err) or is_context_overflow(err):
        log.warning("%s: %s", what, describe_llm_error(err)[:600])
    else:
        log.error("%s", what, exc_info=err)
