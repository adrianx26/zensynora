"""JSON-schema validation for LLM tool-call payloads and structured outputs.

Why this exists: providers that don't support native structured output
return free-form text where a JSON object is expected. The agent loop has
to (1) extract JSON from possibly-wrapped output, (2) validate it against
a schema, and (3) optionally repair it. This module owns steps 1 and 2.
Step 3 lives in ``repair.py`` because it depends on a live LLM call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Union

logger = logging.getLogger(__name__)

try:
    from pydantic import BaseModel, ValidationError as PydanticValidationError

    _PYDANTIC_AVAILABLE = True
except Exception:
    BaseModel = None  # type: ignore[assignment]
    PydanticValidationError = Exception  # type: ignore[assignment]
    _PYDANTIC_AVAILABLE = False

try:
    import jsonschema

    _JSONSCHEMA_AVAILABLE = True
except Exception:
    jsonschema = None  # type: ignore[assignment]
    _JSONSCHEMA_AVAILABLE = False


# ── Result type ──────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """Outcome of a validate_json() call.

    ``data`` is set on success; ``errors`` lists human-readable failure
    descriptions otherwise. Use ``ok`` for boolean checks.
    """
    ok: bool
    data: Optional[Any] = None
    errors: List[str] = field(default_factory=list)
    raw: Optional[str] = None  # the input that was checked (for debugging)


# ── Schema generation ────────────────────────────────────────────────────


def schema_for(model: Type[Any]) -> Dict[str, Any]:
    """Return a JSON-schema dict for a Pydantic v2 BaseModel subclass.

    For non-Pydantic types this raises TypeError — there's no universal
    way to derive a JSON schema from an arbitrary Python class.
    """
    if not _PYDANTIC_AVAILABLE:
        raise RuntimeError("Pydantic 2.x is required for schema_for()")
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"Expected a Pydantic BaseModel subclass, got {type(model)}")
    # Pydantic 2 exposes model_json_schema; fall back to v1 compatibility.
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()  # type: ignore[attr-defined]


# ── JSON extraction ──────────────────────────────────────────────────────

# Greedy match of the first balanced JSON object/array in a string.
# Brace counting beats regex for nested structures.

def extract_json(text: str) -> Optional[str]:
    """Pull the first balanced JSON object or array out of a free-form
    response. Returns the substring, or None if no balanced structure exists.

    Handles common LLM patterns:
        - JSON wrapped in ```json ...``` fences
        - JSON preceded by chatty prose ("Sure, here you go: { ... }")
        - JSON with trailing commentary
    """
    if not text:
        return None

    # Strip code-fence wrappers first.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)

    # Find first { or [ and walk forward counting braces, ignoring chars
    # inside string literals.
    start = -1
    open_char = ""
    close_char = ""
    for i, ch in enumerate(text):
        if ch == "{":
            start, open_char, close_char = i, "{", "}"
            break
        if ch == "[":
            start, open_char, close_char = i, "[", "]"
            break
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Validation ───────────────────────────────────────────────────────────


def validate_json(
    text: str,
    schema: Union[Dict[str, Any], Type[Any]],
) -> ValidationResult:
    """Parse and validate ``text`` against ``schema``.

    ``schema`` may be either a JSON-schema dict or a Pydantic BaseModel
    subclass. Pydantic gives richer error messages and coerces types
    (strings to ints where the schema declares int), so prefer it for
    new code.
    """
    raw = text
    extracted = extract_json(text) if text else None
    if extracted is None:
        return ValidationResult(
            ok=False,
            errors=["No balanced JSON object or array found in input"],
            raw=raw,
        )

    try:
        data = json.loads(extracted)
    except json.JSONDecodeError as e:
        return ValidationResult(
            ok=False,
            errors=[f"JSON parse error: {e.msg} at line {e.lineno} col {e.colno}"],
            raw=raw,
        )

    # Branch: Pydantic model vs raw schema dict.
    if (
        _PYDANTIC_AVAILABLE
        and isinstance(schema, type)
        and issubclass(schema, BaseModel)
    ):
        try:
            instance = schema.model_validate(data) if hasattr(schema, "model_validate") else schema(**data)  # type: ignore[misc]
            return ValidationResult(ok=True, data=instance, raw=raw)
        except PydanticValidationError as e:
            errors = [f"{'/'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
            return ValidationResult(ok=False, errors=errors, raw=raw)
        except Exception as e:
            return ValidationResult(ok=False, errors=[str(e)], raw=raw)

    if isinstance(schema, dict):
        if not _JSONSCHEMA_AVAILABLE:
            return ValidationResult(
                ok=False,
                errors=[
                    "jsonschema is not installed; install it or pass a Pydantic model"
                ],
                raw=raw,
            )
        try:
            jsonschema.validate(data, schema)
            return ValidationResult(ok=True, data=data, raw=raw)
        except jsonschema.ValidationError as e:
            return ValidationResult(
                ok=False,
                errors=[f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"],
                raw=raw,
            )

    return ValidationResult(
        ok=False,
        errors=[f"Unsupported schema type: {type(schema).__name__}"],
        raw=raw,
    )
