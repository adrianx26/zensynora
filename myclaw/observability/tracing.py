"""OpenTelemetry-based distributed tracing.

Design goals:

* **Optional dependency.** If `opentelemetry-api` is not installed, every
  helper here is a no-op. Importing this module never fails.
* **Decorator + context-manager API.** Hot paths use `@traced_async`;
  ad-hoc spans use `with span("name"): ...`.
* **Single init point.** `init_tracing()` reads env vars and configures the
  global provider exactly once. Subsequent calls are idempotent.

Environment variables:

* ``ZENSYNORA_TRACING_ENABLED`` — ``"true"`` to turn tracing on (default off)
* ``OTEL_SERVICE_NAME`` — service name (default ``"zensynora"``)
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — collector endpoint
  (default ``http://localhost:4317``). When unset and the OTLP exporter is
  not installed, falls back to a console exporter.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import wraps
from typing import Any, Awaitable, Callable, Iterator, Optional, TypeVar

logger = logging.getLogger(__name__)

# ── Optional dependency probing ──────────────────────────────────────────

try:  # pragma: no cover - import guard
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except Exception:  # ImportError or any setup error
    _otel_trace = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False


_INITIALIZED = False
_TRACING_ENABLED = False


def is_tracing_enabled() -> bool:
    """True when tracing is both requested and the SDK is importable."""
    return _TRACING_ENABLED and _OTEL_AVAILABLE


def init_tracing(
    service_name: Optional[str] = None,
    endpoint: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> bool:
    """Configure the global tracer provider.

    Returns True if tracing is now active, False otherwise. Safe to call
    repeatedly; only the first call has effect.
    """
    global _INITIALIZED, _TRACING_ENABLED
    if _INITIALIZED:
        return _TRACING_ENABLED

    _INITIALIZED = True

    if enabled is None:
        enabled = os.environ.get("ZENSYNORA_TRACING_ENABLED", "").lower() == "true"
    if not enabled:
        return False

    if not _OTEL_AVAILABLE:
        logger.info(
            "Tracing requested but opentelemetry-api is not installed. "
            "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp"
        )
        return False

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        resource = Resource.create({
            "service.name": service_name or os.environ.get("OTEL_SERVICE_NAME", "zensynora"),
        })
        provider = TracerProvider(resource=resource)

        exporter: Any
        ep = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=ep) if ep else OTLPSpanExporter()
        except Exception:
            logger.info("OTLP exporter unavailable; falling back to console exporter")
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        _otel_trace.set_tracer_provider(provider)
        _TRACING_ENABLED = True
        logger.info("OpenTelemetry tracing initialized (service=%s)", resource.attributes.get("service.name"))
        return True
    except Exception as init_err:
        logger.warning("Failed to initialize tracing", exc_info=init_err)
        return False


def get_tracer(name: str = "zensynora"):
    """Return a tracer (no-op proxy when tracing is disabled)."""
    if is_tracing_enabled():
        return _otel_trace.get_tracer(name)
    return _NoopTracer()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Context-manager span. No-op when tracing is disabled.

        with span("agent.think", user_id=user_id):
            ...
    """
    if not is_tracing_enabled():
        yield None
        return

    tracer = _otel_trace.get_tracer("zensynora")
    with tracer.start_as_current_span(name) as sp:
        for k, v in attributes.items():
            try:
                sp.set_attribute(k, v)
            except Exception:
                pass
        try:
            yield sp
        except Exception as e:
            try:
                sp.record_exception(e)
                sp.set_status(Status(StatusCode.ERROR, str(e)))
            except Exception:
                pass
            raise


F = TypeVar("F", bound=Callable[..., Any])


def traced(name: Optional[str] = None) -> Callable[[F], F]:
    """Decorator for sync functions. No-op when tracing is disabled."""
    def decorator(func: F) -> F:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return func(*args, **kwargs)
            with span(span_name):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


AF = TypeVar("AF", bound=Callable[..., Awaitable[Any]])


def traced_async(name: Optional[str] = None) -> Callable[[AF], AF]:
    """Decorator for coroutines. No-op when tracing is disabled."""
    def decorator(func: AF) -> AF:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return await func(*args, **kwargs)
            with span(span_name):
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# ── No-op fallback used when the SDK is absent ───────────────────────────


class _NoopSpan:
    def set_attribute(self, *a: Any, **k: Any) -> None: ...
    def record_exception(self, *a: Any, **k: Any) -> None: ...
    def set_status(self, *a: Any, **k: Any) -> None: ...
    def add_event(self, *a: Any, **k: Any) -> None: ...
    def end(self) -> None: ...
    def __enter__(self) -> "_NoopSpan":
        return self
    def __exit__(self, *exc: Any) -> None: ...


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, *a: Any, **k: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()
