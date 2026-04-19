"""Usage-based metering — per-user quota tracking and enforcement.

Tracks LLM API calls and tool executions per user, enforcing quotas
via configurable limits.

Dependencies: None (uses SQLite from stdlib)

Usage:
    from myclaw.metering import record_call, check_quota

    # Record a call
    record_call("alice", "llm_request", {"provider": "openai", "model": "gpt-4o"})

    # Check if user is within quota
    ok, remaining = check_quota("alice", "llm_requests_daily", 100)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_METERING_DB_PATH = Path.home() / ".myclaw" / "metering.db"

# Default quotas
DEFAULT_QUOTAS = {
    "llm_requests_daily": 500,
    "llm_tokens_daily": 1_000_000,
    "tool_executions_daily": 200,
    "web_requests_daily": 100,
}


def _get_db() -> sqlite3.Connection:
    """Get or create the metering database."""
    _METERING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_METERING_DB_PATH))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            resource TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL,
            period_key TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_quotas (
            user_id TEXT NOT NULL,
            quota_name TEXT NOT NULL,
            limit_value INTEGER NOT NULL,
            PRIMARY KEY (user_id, quota_name)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_user_period ON usage_events(user_id, event_type, period_key)
    """)

    conn.commit()
    return conn


def record_call(
    user_id: str,
    event_type: str,
    metadata: Optional[Dict[str, str]] = None,
    quantity: int = 1,
) -> None:
    """Record a usage event for a user.

    Args:
        user_id: The user identifier
        event_type: Type of event (e.g. "llm_request", "tool_execution")
        metadata: Optional metadata dict (stored as JSON in resource field)
        quantity: Amount consumed (default 1)
    """
    now = datetime.utcnow()
    period_key = now.strftime("%Y-%m-%d")
    resource = ""
    if metadata:
        resource = str(metadata)

    try:
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_type, resource, quantity, timestamp, period_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, event_type, resource, quantity, now.isoformat(), period_key),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to record usage: {e}")


def get_usage(user_id: str, event_type: str, period: Optional[str] = None) -> int:
    """Get total usage for a user/event type in a period.

    Args:
        user_id: User identifier
        event_type: Event type
        period: Period key (default: today, format YYYY-MM-DD)

    Returns:
        Total quantity consumed
    """
    if period is None:
        period = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        conn = _get_db()
        row = conn.execute(
            """
            SELECT SUM(quantity) as total FROM usage_events
            WHERE user_id = ? AND event_type = ? AND period_key = ?
            """,
            (user_id, event_type, period),
        ).fetchone()
        conn.close()
        return row["total"] or 0
    except Exception as e:
        logger.error(f"Failed to get usage: {e}")
        return 0


def set_quota(user_id: str, quota_name: str, limit_value: int) -> None:
    """Set a quota limit for a user."""
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO user_quotas (user_id, quota_name, limit_value)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, quota_name) DO UPDATE SET limit_value = excluded.limit_value
        """,
        (user_id, quota_name, limit_value),
    )
    conn.commit()
    conn.close()


def get_quota(user_id: str, quota_name: str) -> int:
    """Get quota limit for a user. Falls back to DEFAULT_QUOTAS."""
    conn = _get_db()
    row = conn.execute(
        "SELECT limit_value FROM user_quotas WHERE user_id = ? AND quota_name = ?",
        (user_id, quota_name),
    ).fetchone()
    conn.close()
    if row:
        return row["limit_value"]
    return DEFAULT_QUOTAS.get(quota_name, 0)


def check_quota(user_id: str, quota_name: str, requested: int = 1) -> Tuple[bool, int]:
    """Check if a user is within quota.

    Returns:
        (allowed, remaining) — allowed is True if within quota, False if exceeded
    """
    limit = get_quota(user_id, quota_name)
    if limit <= 0:
        return True, -1  # No limit

    # Map quota names to event types
    event_type_map = {
        "llm_requests_daily": "llm_request",
        "llm_tokens_daily": "llm_tokens",
        "tool_executions_daily": "tool_execution",
        "web_requests_daily": "web_request",
    }
    event_type = event_type_map.get(quota_name, quota_name)
    used = get_usage(user_id, event_type)
    remaining = limit - used - requested
    return remaining >= 0, remaining


def get_user_summary(user_id: str) -> Dict[str, any]:
    """Get usage summary for a user."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT event_type, SUM(quantity) as total
        FROM usage_events
        WHERE user_id = ? AND period_key = ?
        GROUP BY event_type
        """,
        (user_id, today),
    ).fetchall()
    conn.close()

    usage = {r["event_type"]: r["total"] for r in rows}
    quotas = {}
    for qname in DEFAULT_QUOTAS:
        limit = get_quota(user_id, qname)
        event = qname.replace("_daily", "").replace("llm_requests", "llm_request").replace("llm_tokens", "llm_tokens")
        used = usage.get(event, 0)
        quotas[qname] = {"limit": limit, "used": used, "remaining": max(0, limit - used)}

    return {
        "user_id": user_id,
        "period": today,
        "usage": usage,
        "quotas": quotas,
    }
