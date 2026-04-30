"""Observability primitives: tracing, structured spans.

Lazily depends on `opentelemetry-api` / `opentelemetry-sdk`. When the
packages are not installed, every helper degrades to a no-op so the rest
of the codebase can import unconditionally.
"""

from .tracing import (
    init_tracing,
    get_tracer,
    traced,
    traced_async,
    span,
    is_tracing_enabled,
)

__all__ = [
    "init_tracing",
    "get_tracer",
    "traced",
    "traced_async",
    "span",
    "is_tracing_enabled",
]
