"""
Tools — System Management
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,
    ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    _rate_limiter, _tool_audit_logger,
    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,
    _runtime_config,
    TOOLS, TOOL_SCHEMAS,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import json
from ..semantic_cache import get_semantic_cache, clear_semantic_cache as clear_global_semantic_cache

logger = logging.getLogger(__name__)

# ── Phase 5/6 Management Tools ─────────────────────────────────────────────────

def clear_semantic_cache() -> str:
    """Clear the global semantic cache instance."""
    clear_global_semantic_cache()
    return "✅ Semantic cache cleared."


def get_cache_stats() -> str:
    """Return semantic cache statistics."""
    cache_cfg = getattr(_runtime_config, "semantic_cache", None)
    cache = get_semantic_cache(
        max_size=getattr(cache_cfg, "max_size", 256),
        ttl=getattr(cache_cfg, "ttl", 3600),
        similarity_threshold=getattr(cache_cfg, "similarity_threshold", 0.92),
    )
    stats = cache.get_stats()
    return (
        f"Semantic Cache Stats:\n"
        f"  Entries: {stats['entries']}\n"
        f"  Hits: {stats['hits']}\n"
        f"  Misses: {stats['misses']}\n"
        f"  Hit Rate: {stats['hit_rate']}\n"
        f"  TTL: {stats['ttl']}s\n"
        f"  Similarity Threshold: {stats['similarity_threshold']}"
    )


def get_worker_pool_stats() -> str:
    """Return worker pool metrics."""
    stats = _get_worker_pool_manager().get_stats()
    return json.dumps(stats, indent=2)


async def resize_worker_pool(max_workers: int) -> str:
    """Resize worker pool at runtime."""
    result = await _get_worker_pool_manager().resize(max_workers=max_workers)
    return json.dumps(result, indent=2)


def get_sandbox_stats() -> str:
    """Return sandbox policy and runtime stats."""
    return json.dumps(_get_security_sandbox().get_stats(), indent=2)


def clear_sandbox_audit_log() -> str:
    """Clear persistent sandbox audit log."""
    _get_security_sandbox().clear_audit_log()
    return "✅ Sandbox audit log cleared."


def add_trusted_skill(skill_name: str) -> str:
    """Add a trusted skill that can bypass sandbox checks."""
    if not _runtime_config or not hasattr(_runtime_config, "sandbox"):
        return "Error: Sandbox config unavailable."
    trusted = list(getattr(_runtime_config.sandbox, "trusted_skills", []) or [])
    if skill_name not in trusted:
        trusted.append(skill_name)
        _runtime_config.sandbox.trusted_skills = trusted
    return f"✅ Added trusted skill: {skill_name}"


def verify_audit_log() -> str:
    """Verify integrity of persistent tool audit log."""
    return json.dumps(_tool_audit_logger.verify(), indent=2)


def get_audit_log_entries(limit: int = 100, tool_name: str = "") -> str:
    """Get recent tool audit log entries."""
    logs = _tool_audit_logger.get_logs(limit=limit, tool_name=tool_name or None)
    return json.dumps(logs, indent=2)


def export_audit_log(path: str) -> str:
    """Export persistent audit log file."""
    export_path = _tool_audit_logger.export(path)
    return f"✅ Audit log exported to {export_path}"


def rotate_audit_log() -> str:
    """Force log rotation for tool audit log."""
    result = _tool_audit_logger._persistent.rotate_now()
    return json.dumps(result, indent=2)


def get_log_rotation_status() -> str:
    """Get current log rotation configuration."""
    persistent = _tool_audit_logger._persistent
    return json.dumps(
        {
            "log_path": str(persistent.log_path),
            "max_size_bytes": persistent.max_size_bytes,
            "max_age_days": persistent.max_age_days,
            "max_files": persistent.max_files,
            "compress": persistent.compress,
        },
        indent=2,
    )


def cleanup_old_logs() -> str:
    """Apply retention cleanup to rotated logs."""
    _tool_audit_logger._persistent._enforce_retention()
    return "✅ Log retention cleanup complete."


