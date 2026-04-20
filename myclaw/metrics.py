"""Prometheus metrics for ZenSynora.

Optional dependency: pip install prometheus-client

When prometheus-client is not installed, all metrics operations become no-ops
(zero overhead in production when metrics are not needed).

Usage:
    from myclaw.metrics import get_metrics
    metrics = get_metrics()
    metrics.record_llm_request("openai", "gpt-4o", prompt_tokens=100, completion_tokens=50)
    metrics.record_tool_execution("browse", 0.5, status="success")
    metrics.record_cache("semantic", hit=True)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional, Dict

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Info,
        generate_latest, CONTENT_TYPE_LATEST,
        CollectorRegistry
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.info("prometheus-client not installed. Metrics collection disabled.")

# ── Registry ──────────────────────────────────────────────────────────────
_METRICS_REGISTRY = CollectorRegistry() if _PROMETHEUS_AVAILABLE else None


# ── No-op metrics class for when prometheus-client is not installed ───────
class _NoopMetrics:
    """No-op metrics collector. All methods do nothing."""

    def record_llm_request(self, *args, **kwargs) -> None:
        pass

    def record_tool_execution(self, *args, **kwargs) -> None:
        pass

    def record_cache(self, *args, **kwargs) -> None:
        pass

    def set_active_sessions(self, *args, **kwargs) -> None:
        pass

    def set_knowledge_entries(self, *args, **kwargs) -> None:
        pass

    def record_error(self, *args, **kwargs) -> None:
        pass


# ── Real Prometheus metrics class ─────────────────────────────────────────
class PrometheusMetrics:
    """All Prometheus metrics for ZenSynora."""

    # Provider-specific approximate pricing per 1K tokens (USD) for cost tracking
    _PRICE_TABLE: Dict[str, Dict[str, float]] = {
        "openai": {
            "gpt-4o": 0.005,
            "gpt-4o-mini": 0.00015,
            "gpt-4-turbo": 0.01,
            "gpt-4": 0.03,
        },
        "anthropic": {
            "claude-3-5-sonnet-20241022": 0.003,
            "claude-3-opus-20240229": 0.015,
            "claude-3-haiku-20240307": 0.00025,
        },
        "gemini": {
            "gemini-1.5-pro": 0.0035,
            "gemini-1.5-flash": 0.00035,
        },
        "groq": {
            "llama3-70b-8192": 0.00059,
            "mixtral-8x7b-32768": 0.00024,
        },
        "ollama": {},
        "lmstudio": {},
        "llamacpp": {},
        "openrouter": {},
    }

    def __init__(self):
        if not _PROMETHEUS_AVAILABLE:
            raise RuntimeError("prometheus-client is not installed")

        # Request latency (HTTP + WebSocket)
        self.request_duration = Histogram(
            "zensynora_request_duration_seconds",
            "Request latency in seconds",
            ["method", "endpoint", "status"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=_METRICS_REGISTRY,
        )

        # LLM token usage per provider
        self.llm_tokens_total = Counter(
            "zensynora_llm_tokens_total",
            "Total LLM tokens used",
            ["provider", "model", "token_type"],  # token_type: prompt|completion
            registry=_METRICS_REGISTRY,
        )

        self.llm_requests_total = Counter(
            "zensynora_llm_requests_total",
            "Total LLM API calls",
            ["provider", "model", "status"],  # status: success|error|cached
            registry=_METRICS_REGISTRY,
        )

        self.llm_request_duration = Histogram(
            "zensynora_llm_request_duration_seconds",
            "LLM API call latency",
            ["provider", "model"],
            buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
            registry=_METRICS_REGISTRY,
        )

        # Tool execution metrics
        self.tool_executions_total = Counter(
            "zensynora_tool_executions_total",
            "Total tool executions",
            ["tool_name", "status"],  # status: success|error|rate_limited|blocked
            registry=_METRICS_REGISTRY,
        )

        self.tool_execution_duration = Histogram(
            "zensynora_tool_execution_duration_seconds",
            "Tool execution latency",
            ["tool_name"],
            buckets=[0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0],
            registry=_METRICS_REGISTRY,
        )

        # Cache metrics
        self.cache_hits_total = Counter(
            "zensynora_cache_hits_total",
            "Cache hits",
            ["cache_type"],  # semantic|provider|profile
            registry=_METRICS_REGISTRY,
        )

        self.cache_misses_total = Counter(
            "zensynora_cache_misses_total",
            "Cache misses",
            ["cache_type"],
            registry=_METRICS_REGISTRY,
        )

        # Error tracking
        self.errors_total = Counter(
            "zensynora_errors_total",
            "Total errors",
            ["component", "error_type"],
            registry=_METRICS_REGISTRY,
        )

        # Active sessions gauge
        self.active_sessions = Gauge(
            "zensynora_active_sessions",
            "Number of active user sessions",
            registry=_METRICS_REGISTRY,
        )

        # Knowledge base size gauge
        self.knowledge_entries = Gauge(
            "zensynora_knowledge_entries",
            "Number of knowledge base entries",
            registry=_METRICS_REGISTRY,
        )

        # Cost tracking (optional)
        self.llm_cost_usd = Counter(
            "zensynora_llm_cost_usd_total",
            "Estimated LLM cost in USD",
            ["provider", "model"],
            registry=_METRICS_REGISTRY,
        )

        # App info
        self.app_info = Info(
            "zensynora_app",
            "ZenSynora application information",
            registry=_METRICS_REGISTRY,
        )
        self.app_info.info({"version": "0.4.1"})

    def record_llm_request(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration: float = 0.0,
        status: str = "success",
    ) -> None:
        """Record an LLM request with token usage and cost estimation."""
        self.llm_requests_total.labels(
            provider=provider, model=model, status=status
        ).inc()
        self.llm_request_duration.labels(
            provider=provider, model=model
        ).observe(duration)

        if prompt_tokens:
            self.llm_tokens_total.labels(
                provider=provider, model=model, token_type="prompt"
            ).inc(prompt_tokens)
        if completion_tokens:
            self.llm_tokens_total.labels(
                provider=provider, model=model, token_type="completion"
            ).inc(completion_tokens)

        # Cost estimation
        cost = self._estimate_cost(provider, model, prompt_tokens, completion_tokens)
        if cost > 0:
            self.llm_cost_usd.labels(provider=provider, model=model).inc(cost)

        if status == "error":
            self.errors_total.labels(
                component="llm_provider", error_type="api_error"
            ).inc()

    def _estimate_cost(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Estimate cost in USD based on provider pricing table."""
        provider_prices = self._PRICE_TABLE.get(provider, {})
        # Try exact model match, then prefix match
        price = provider_prices.get(model)
        if price is None:
            for k, v in provider_prices.items():
                if model.startswith(k):
                    price = v
                    break
        if price is None:
            return 0.0
        return price * (prompt_tokens + completion_tokens) / 1000.0

    def record_tool_execution(
        self, tool_name: str, duration: float, status: str = "success"
    ) -> None:
        """Record a tool execution."""
        self.tool_executions_total.labels(
            tool_name=tool_name, status=status
        ).inc()
        self.tool_execution_duration.labels(
            tool_name=tool_name
        ).observe(duration)

        if status == "error":
            self.errors_total.labels(
                component="tool_executor", error_type="execution_error"
            ).inc()

    def record_cache(self, cache_type: str, hit: bool) -> None:
        """Record a cache hit or miss."""
        if hit:
            self.cache_hits_total.labels(cache_type=cache_type).inc()
        else:
            self.cache_misses_total.labels(cache_type=cache_type).inc()

    def set_active_sessions(self, count: int) -> None:
        """Update active session count."""
        self.active_sessions.set(count)

    def set_knowledge_entries(self, count: int) -> None:
        """Update knowledge base entry count."""
        self.knowledge_entries.set(count)

    def record_error(self, component: str, error_type: str) -> None:
        """Record a generic error."""
        self.errors_total.labels(
            component=component, error_type=error_type
        ).inc()


# ── Singleton factory ─────────────────────────────────────────────────────
_metrics_instance: Optional[PrometheusMetrics] = None


def get_metrics() -> PrometheusMetrics | _NoopMetrics:
    """Get the global metrics instance (lazy init)."""
    global _metrics_instance
    if _metrics_instance is None:
        if _PROMETHEUS_AVAILABLE:
            _metrics_instance = PrometheusMetrics()
        else:
            _metrics_instance = _NoopMetrics()
    return _metrics_instance


def reset_metrics() -> None:
    """Reset the metrics singleton (useful for testing)."""
    global _metrics_instance
    _metrics_instance = None


# ── Context managers ──────────────────────────────────────────────────────
@contextmanager
def timed_llm_request(provider: str, model: str):
    """Context manager to time an LLM request and auto-record metrics."""
    metrics = get_metrics()
    start = time.time()
    try:
        yield
        metrics.record_llm_request(
            provider, model, duration=time.time() - start, status="success"
        )
    except Exception as exc:
        metrics.record_llm_request(
            provider, model, duration=time.time() - start, status="error"
        )
        raise


@contextmanager
def timed_tool_execution(tool_name: str):
    """Context manager to time a tool execution and auto-record metrics."""
    metrics = get_metrics()
    start = time.time()
    try:
        yield
        metrics.record_tool_execution(
            tool_name, time.time() - start, status="success"
        )
    except Exception:
        metrics.record_tool_execution(
            tool_name, time.time() - start, status="error"
        )
        raise


# ── FastAPI helpers ───────────────────────────────────────────────────────
def setup_metrics_endpoint(app) -> None:
    """Add /metrics endpoint to FastAPI app."""
    from fastapi import Response

    @app.get("/metrics")
    async def metrics():
        if not _PROMETHEUS_AVAILABLE:
            return Response(
                "# prometheus-client not installed\n",
                status_code=503,
                media_type="text/plain",
            )
        return Response(
            generate_latest(_METRICS_REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )


class MetricsMiddleware:
    """ASGI middleware to track request latency and errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        metrics = get_metrics()
        start = time.time()
        status_code = 200

        async def wrapped_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception:
            status_code = 500
            metrics.record_error("http", "unhandled_exception")
            raise
        finally:
            duration = time.time() - start
            method = scope.get("method", "UNKNOWN")
            path = scope.get("path", "/")
            # Bucket status codes to avoid cardinality explosion
            status_bucket = "2xx"
            if 400 <= status_code < 500:
                status_bucket = "4xx"
            elif status_code >= 500:
                status_bucket = "5xx"
            metrics.request_duration.labels(
                method=method, endpoint=path, status=status_bucket
            ).observe(duration)
