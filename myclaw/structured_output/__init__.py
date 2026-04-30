"""Structured-output validation + repair for LLM responses.

Public surface:

* ``schema_for(model)``: turn a Pydantic ``BaseModel`` subclass into a JSON
  schema dict ready to feed providers that support `response_format` /
  `tools`.
* ``validate_json(text, schema)``: parse ``text`` as JSON and validate
  against ``schema``. Returns ``ValidationResult``.
* ``repair_json(text, schema, llm_call)``: ask an LLM to fix invalid JSON.
"""

from .validator import (
    ValidationResult,
    schema_for,
    validate_json,
    extract_json,
)
from .repair import repair_json

__all__ = [
    "ValidationResult",
    "schema_for",
    "validate_json",
    "extract_json",
    "repair_json",
]
