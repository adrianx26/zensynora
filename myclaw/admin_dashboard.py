"""Admin dashboard data provider for ZenSynora.

Aggregates metrics from multiple sources into a single dashboard payload:
    - Active WebSocket sessions
    - Model routing decisions
    - Average response times
    - Knowledge base growth
    - Tool execution stats
    - LLM provider health
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ── In-memory session tracking ──────────────────────────────────────────────
_active_websocket_sessions: Dict[str, float] = {}


def register_websocket_session(session_id: str) -> None:
    """Track a new WebSocket session."""
    _active_websocket_sessions[session_id] = time.time()
    logger.debug(f"WS session registered: {session_id}")


def unregister_websocket_session(session_id: str) -> None:
    """Remove a WebSocket session."""
    _active_websocket_sessions.pop(session_id, None)
    logger.debug(f"WS session unregistered: {session_id}")


def get_active_session_count() -> int:
    """Get number of active WebSocket sessions."""
    # Clean stale sessions (inactive for > 5 minutes)
    cutoff = time.time() - 300
    stale = [sid for sid, last in _active_websocket_sessions.items() if last < cutoff]
    for sid in stale:
        _active_websocket_sessions.pop(sid, None)
    return len(_active_websocket_sessions)


def update_session_activity(session_id: str) -> None:
    """Update last activity timestamp for a session."""
    if session_id in _active_websocket_sessions:
        _active_websocket_sessions[session_id] = time.time()


# ── Response time tracking ──────────────────────────────────────────────────
_response_times: List[float] = []  # Circular buffer of recent response times
_MAX_RESPONSE_SAMPLES = 100


def record_response_time(duration_seconds: float) -> None:
    """Record a response time sample."""
    _response_times.append(duration_seconds)
    if len(_response_times) > _MAX_RESPONSE_SAMPLES:
        _response_times.pop(0)


def get_avg_response_time() -> float:
    """Get average response time in seconds."""
    if not _response_times:
        return 0.0
    return sum(_response_times) / len(_response_times)


# ── Routing decision log ────────────────────────────────────────────────────
_routing_decisions: List[Dict[str, Any]] = []
_MAX_ROUTING_LOG = 50


def log_routing_decision(user_message: str, from_model: str, to_model: str, reason: str = "") -> None:
    """Log a model routing decision."""
    _routing_decisions.append({
        "timestamp": datetime.utcnow().isoformat(),
        "message_preview": user_message[:60],
        "from_model": from_model,
        "to_model": to_model,
        "reason": reason,
    })
    if len(_routing_decisions) > _MAX_ROUTING_LOG:
        _routing_decisions.pop(0)


def get_recent_routing_decisions(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent routing decisions."""
    return _routing_decisions[-limit:]


# ── KB growth tracking ──────────────────────────────────────────────────────
def get_kb_stats(user_id: str = "default") -> Dict[str, Any]:
    """Get knowledge base statistics."""
    from ..knowledge.db import KnowledgeDB

    try:
        with KnowledgeDB(user_id) as db:
            stats = db.get_stats()
            return {
                "entities": stats.get("entities", 0),
                "observations": stats.get("observations", 0),
                "relations": stats.get("relations", 0),
            }
    except Exception as e:
        logger.warning(f"Could not get KB stats: {e}")
        return {"entities": 0, "observations": 0, "relations": 0}


# ── Provider health ─────────────────────────────────────────────────────────
def get_provider_health() -> List[Dict[str, Any]]:
    """Check health of configured LLM providers."""
    from ..provider import SUPPORTED_PROVIDERS
    from ..config import load_config
    import httpx

    config = load_config()
    health = []

    # Local provider endpoints to check
    health_checks = {
        "ollama": ("http://localhost:11434/api/tags", 2.0),
        "lmstudio": ("http://localhost:1234/v1/models", 2.0),
        "llamacpp": ("http://localhost:8080/v1/models", 2.0),
    }

    for provider in SUPPORTED_PROVIDERS:
        status = "unknown"
        latency = 0.0

        if provider in health_checks:
            url, timeout = health_checks[provider]
            start = time.time()
            try:
                r = httpx.get(url, timeout=timeout)
                latency = time.time() - start
                status = "healthy" if r.status_code < 400 else "error"
            except Exception:
                latency = time.time() - start
                status = "unreachable"
        else:
            # Cloud providers — check if API key is configured
            try:
                cfg = getattr(config.providers, provider, None)
                if cfg and getattr(cfg, "api_key", None):
                    key = cfg.api_key.get_secret_value()
                    status = "configured" if key else "no_key"
                else:
                    status = "not_configured"
            except Exception:
                status = "not_configured"

        health.append({
            "provider": provider,
            "status": status,
            "latency_ms": round(latency * 1000, 1),
        })

    return health


# ── Tool execution stats (from Prometheus metrics if available) ─────────────
def get_tool_stats() -> List[Dict[str, Any]]:
    """Get recent tool execution statistics."""
    try:
        from ..metrics import get_metrics
        metrics = get_metrics()
        # We can't easily read back from Prometheus counters, so return placeholder
        # In a real deployment, this would query Prometheus or a local stats DB
        return []
    except Exception:
        return []


# ── Dashboard payload builder ───────────────────────────────────────────────
def build_dashboard_data() -> Dict[str, Any]:
    """Build the complete dashboard payload."""
    from ..config import load_config
    from ..provider import SUPPORTED_PROVIDERS

    config = load_config()
    kb_stats = get_kb_stats()

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.4.1",
        "sessions": {
            "active_websocket_count": get_active_session_count(),
            "avg_response_time_ms": round(get_avg_response_time() * 1000, 1),
        },
        "routing": {
            "enabled": getattr(config.intelligence.routing, "enabled", False),
            "recent_decisions": get_recent_routing_decisions(10),
        },
        "knowledge_base": kb_stats,
        "providers": get_provider_health(),
        "agents": [
            {"name": "default", "model": config.agents.defaults.model}
        ] + [{"name": n.name, "model": n.model} for n in config.agents.named],
        "tools": {
            "available_count": len([t for t in dir(__import__("myclaw.tools", fromlist=["TOOLS"]))]),
        },
    }
