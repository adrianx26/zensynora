"""LLM cost tracking — per-provider token usage and monthly cost accumulator.

Stores usage in SQLite for persistence and aggregation.

Usage:
    from myclaw.cost_tracker import record_usage, get_monthly_costs

    record_usage("openai", "gpt-4o", prompt_tokens=100, completion_tokens=50)
    costs = get_monthly_costs()
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COST_DB_PATH = Path.home() / ".myclaw" / "cost_tracking.db"

# Provider pricing per 1K tokens (USD)
_PRICING = {
    "openai": {
        "gpt-4o": {"prompt": 0.005, "completion": 0.015},
        "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
        "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
        "gpt-4": {"prompt": 0.03, "completion": 0.06},
    },
    "anthropic": {
        "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
        "claude-3-opus-20240229": {"prompt": 0.015, "completion": 0.075},
        "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
    },
    "gemini": {
        "gemini-1.5-pro": {"prompt": 0.0035, "completion": 0.0105},
        "gemini-1.5-flash": {"prompt": 0.00035, "completion": 0.00105},
        "gemini-2.0-flash": {"prompt": 0.00035, "completion": 0.00105},
    },
    "groq": {
        "llama3-70b-8192": {"prompt": 0.00059, "completion": 0.00079},
        "mixtral-8x7b-32768": {"prompt": 0.00024, "completion": 0.00024},
    },
    "ollama": {},
    "lmstudio": {},
    "llamacpp": {},
    "openrouter": {},
}


def _get_db() -> sqlite3.Connection:
    """Get or create the cost tracking database."""
    COST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(COST_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            timestamp TEXT NOT NULL,
            month_key TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_month ON usage_records(month_key)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_records(provider)
    """)
    conn.commit()
    return conn


def _lookup_price(provider: str, model: str, token_type: str) -> float:
    """Look up price per 1K tokens."""
    provider_prices = _PRICING.get(provider, {})
    # Exact match
    if model in provider_prices:
        return provider_prices[model].get(token_type, 0.0)
    # Prefix match
    for k, v in provider_prices.items():
        if model.startswith(k):
            return v.get(token_type, 0.0)
    return 0.0


def record_usage(
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Dict[str, float]:
    """Record LLM usage and compute cost.

    Returns:
        Dict with cost_usd and token counts.
    """
    prompt_price = _lookup_price(provider, model, "prompt")
    completion_price = _lookup_price(provider, model, "completion")

    cost = (
        prompt_tokens * prompt_price +
        completion_tokens * completion_price
    ) / 1000.0

    now = datetime.utcnow()
    month_key = now.strftime("%Y-%m")

    try:
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO usage_records
            (provider, model, prompt_tokens, completion_tokens, cost_usd, timestamp, month_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, model, prompt_tokens, completion_tokens, cost, now.isoformat(), month_key),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to record cost: {e}")

    return {
        "cost_usd": cost,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def get_monthly_costs(month: Optional[str] = None) -> List[Dict[str, any]]:
    """Get aggregated costs by provider for a month.

    Args:
        month: Month in YYYY-MM format (default: current month)

    Returns:
        List of dicts with provider, total_cost, total_prompt, total_completion
    """
    if month is None:
        month = datetime.utcnow().strftime("%Y-%m")

    try:
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT
                provider,
                SUM(cost_usd) as total_cost,
                SUM(prompt_tokens) as total_prompt,
                SUM(completion_tokens) as total_completion,
                COUNT(*) as request_count
            FROM usage_records
            WHERE month_key = ?
            GROUP BY provider
            ORDER BY total_cost DESC
            """,
            (month,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to get monthly costs: {e}")
        return []


def get_costs_by_model(month: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Aggregate spend by (provider, model) for one month.

    Args:
        month: ``YYYY-MM`` (defaults to current UTC month).
        limit: Cap on rows returned.

    Returns the top ``limit`` rows by total cost, descending.
    """
    if month is None:
        month = datetime.utcnow().strftime("%Y-%m")
    try:
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT
                provider,
                model,
                SUM(cost_usd) AS total_cost,
                SUM(prompt_tokens) AS total_prompt,
                SUM(completion_tokens) AS total_completion,
                COUNT(*) AS request_count
            FROM usage_records
            WHERE month_key = ?
            GROUP BY provider, model
            ORDER BY total_cost DESC
            LIMIT ?
            """,
            (month, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to get costs by model: {e}")
        return []


def get_daily_timeline(days: int = 30) -> List[Dict[str, Any]]:
    """Daily cost series for the last ``days`` days, oldest-first.

    Days with zero spend are omitted (the chart layer can fill gaps if it
    cares; most charting libs handle sparse series natively).
    """
    if days < 1:
        days = 1
    try:
        conn = _get_db()
        # SQLite: substr(timestamp, 1, 10) → "YYYY-MM-DD"
        rows = conn.execute(
            """
            SELECT
                substr(timestamp, 1, 10) AS day,
                SUM(cost_usd) AS total_cost,
                SUM(prompt_tokens + completion_tokens) AS total_tokens,
                COUNT(*) AS request_count
            FROM usage_records
            WHERE datetime(timestamp) >= datetime('now', ?)
            GROUP BY day
            ORDER BY day ASC
            """,
            (f"-{int(days)} days",),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to get daily timeline: {e}")
        return []


def get_cost_summary() -> Dict[str, any]:
    """Get overall cost summary."""
    try:
        conn = _get_db()
        total = conn.execute(
            "SELECT SUM(cost_usd) as total_cost, SUM(prompt_tokens) as total_prompt, SUM(completion_tokens) as total_completion FROM usage_records"
        ).fetchone()
        months = conn.execute(
            "SELECT DISTINCT month_key FROM usage_records ORDER BY month_key DESC"
        ).fetchall()
        conn.close()
        return {
            "total_cost_usd": total["total_cost"] or 0.0,
            "total_prompt_tokens": total["total_prompt"] or 0,
            "total_completion_tokens": total["total_completion"] or 0,
            "tracked_months": [r["month_key"] for r in months],
        }
    except Exception as e:
        logger.error(f"Failed to get cost summary: {e}")
        return {"total_cost_usd": 0.0, "total_prompt_tokens": 0, "total_completion_tokens": 0, "tracked_months": []}


# ── Public API surface ───────────────────────────────────────────────
# Listing __all__ explicitly so `from this_module import *` doesn't leak
# internal helpers (e.g. _profile_cache, _LAST_ACTIVE_TIME). Names that
# aren't here are still importable by direct attribute access — they
# just don't participate in star imports.
__all__ = ['record_usage', 'get_monthly_costs', 'get_costs_by_model', 'get_daily_timeline', 'get_cost_summary']
