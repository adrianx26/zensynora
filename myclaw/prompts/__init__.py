"""Prompt template management — versioned, registry-backed."""

from .registry import (
    PromptTemplate,
    PromptRegistry,
    get_registry,
)

__all__ = ["PromptTemplate", "PromptRegistry", "get_registry"]
