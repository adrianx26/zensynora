"""LLM-driven repair for invalid JSON.

When ``validate_json`` reports failure, the simplest recovery is to ask
the model to fix its own output. ``repair_json`` builds a focused
prompt — original output + schema + error list — and runs a single
extra completion. Caller supplies the LLM call as a coroutine, so this
module stays free of any provider-specific code.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Type, Union

from .validator import ValidationResult, schema_for, validate_json

logger = logging.getLogger(__name__)


# Type of the LLM call the caller injects:
# accepts a list of {role, content} messages, returns the assistant text.
LLMCall = Callable[[List[Dict[str, str]]], Awaitable[str]]


def _format_schema(schema: Union[Dict[str, Any], Type[Any]]) -> str:
    """Pretty-print the schema for prompt embedding."""
    if isinstance(schema, dict):
        return json.dumps(schema, indent=2)
    try:
        return json.dumps(schema_for(schema), indent=2)
    except Exception:
        return repr(schema)


def _build_repair_messages(
    original: str,
    schema: Union[Dict[str, Any], Type[Any]],
    errors: List[str],
) -> List[Dict[str, str]]:
    schema_text = _format_schema(schema)
    error_text = "\n".join(f"- {e}" for e in errors)
    system = (
        "You are a JSON repair tool. The user will give you a JSON-shaped "
        "string that failed schema validation, the schema, and the "
        "validation errors. Output ONLY valid JSON conforming to the "
        "schema — no prose, no code fences, no commentary."
    )
    user = (
        f"## Schema\n```json\n{schema_text}\n```\n\n"
        f"## Validation errors\n{error_text}\n\n"
        f"## Original output\n```\n{original}\n```\n\n"
        f"Return the corrected JSON object only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def repair_json(
    text: str,
    schema: Union[Dict[str, Any], Type[Any]],
    llm_call: LLMCall,
    max_attempts: int = 1,
) -> ValidationResult:
    """Validate ``text``; if it fails, ask the model to fix it.

    Args:
        text: The original assistant output.
        schema: JSON-schema dict OR Pydantic BaseModel subclass.
        llm_call: Async callable that takes ``messages`` and returns the
            assistant's reply.
        max_attempts: Maximum repair rounds. Default 1; raise to 2 for
            stubborn models. Each attempt costs one LLM call.

    Returns the final ``ValidationResult``. ``ok=True`` indicates a valid
    payload was obtained (originally or after repair).
    """
    result = validate_json(text, schema)
    if result.ok:
        return result

    last_text = text
    last_errors = list(result.errors)

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "JSON validation failed (attempt %d/%d); requesting repair",
            attempt, max_attempts,
        )
        try:
            repaired_text = await llm_call(
                _build_repair_messages(last_text, schema, last_errors)
            )
        except Exception as call_err:
            logger.warning("Repair LLM call failed", exc_info=call_err)
            return ValidationResult(
                ok=False,
                errors=last_errors + [f"Repair call raised: {call_err}"],
                raw=last_text,
            )

        result = validate_json(repaired_text, schema)
        if result.ok:
            return result

        last_text = repaired_text
        last_errors = list(result.errors)

    return result
