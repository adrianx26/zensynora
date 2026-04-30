"""Single source of truth for tunable defaults.

Constants that used to live as module-level globals in three different
files (agent.py, provider.py, memory.py, etc.) are consolidated here.
Modules can still re-export their canonical name for backward
compatibility, but the value lives in this one place — so changing a
default doesn't require hunting through 50 files.

The module is import-cheap and dependency-free on purpose. **Do not**
add anything that imports from ``myclaw.config`` or any logger here;
this file is sometimes loaded before logging is configured.

When in doubt, prefer environment-variable overrides for operator
convenience — every default has a parallel ``MYCLAW_*`` variable.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Filesystem layout ─────────────────────────────────────────────────────

#: Root of the user's MyClaw state. Every storage layer derives its own
#: subdirectory from this.
MYCLAW_HOME: Path = Path(os.environ.get("MYCLAW_HOME", str(Path.home() / ".myclaw")))

#: JSONL log of detected knowledge gaps. Used by the gap researcher.
GAP_FILE: Path = MYCLAW_HOME / "knowledge_gaps.jsonl"

#: Per-user memory database template. ``user_id`` substituted at runtime.
MEMORY_DB_TEMPLATE: str = str(MYCLAW_HOME / "memory_{user_id}.db")

#: Cost-tracking SQLite database.
COST_DB_PATH: Path = MYCLAW_HOME / "cost_tracking.db"

#: Default vector-store path used by ``vector.factory.make_backend("sqlite")``.
VECTORS_DB_PATH: Path = MYCLAW_HOME / "vectors.db"

#: JSONL prompt-template registry path.
PROMPTS_PATH: Path = MYCLAW_HOME / "prompts.jsonl"

#: Plugin-install root used by ``marketplace.MarketplaceClient``.
PLUGINS_INSTALL_DIR: Path = MYCLAW_HOME / "plugins" / "installed"


# ── Network / provider defaults ──────────────────────────────────────────

#: Per-request HTTP timeout for LLM providers (seconds).
DEFAULT_TIMEOUT: float = float(os.environ.get("MYCLAW_HTTP_TIMEOUT", "60"))

#: Retry policy for transient LLM provider errors.
DEFAULT_MAX_RETRIES: int = int(os.environ.get("MYCLAW_HTTP_MAX_RETRIES", "3"))
DEFAULT_BACKOFF_BASE: float = float(os.environ.get("MYCLAW_HTTP_BACKOFF_BASE", "1.0"))
DEFAULT_BACKOFF_MAX: float = float(os.environ.get("MYCLAW_HTTP_BACKOFF_MAX", "30.0"))
DEFAULT_BACKOFF_EXPONENTIAL: float = float(
    os.environ.get("MYCLAW_HTTP_BACKOFF_EXPONENTIAL", "2.0")
)


# ── Memory defaults ──────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE: int = int(os.environ.get("MYCLAW_MEMORY_BATCH_SIZE", "10"))
DEFAULT_CACHE_SIZE: int = int(os.environ.get("MYCLAW_MEMORY_CACHE_SIZE", "100"))
CACHE_TTL_SECONDS: float = float(os.environ.get("MYCLAW_MEMORY_BATCH_TIMEOUT", "1.0"))
VACUUM_INTERVAL: int = int(os.environ.get("MYCLAW_MEMORY_VACUUM_INTERVAL", "100"))
CLEANUP_CHUNK_SIZE: int = int(os.environ.get("MYCLAW_MEMORY_CLEANUP_CHUNK", "1000"))
DEFAULT_CLEANUP_DAYS: int = int(os.environ.get("MYCLAW_MEMORY_CLEANUP_DAYS", "90"))


# ── Resilience defaults ──────────────────────────────────────────────────

#: Failures in CLOSED state that trip the circuit breaker. ``0`` disables
#: the breaker entirely (matches historical behavior).
PROVIDER_CB_FAILURE_THRESHOLD: int = int(
    os.environ.get("MYCLAW_PROVIDER_CB_FAILURE_THRESHOLD", "5")
)
PROVIDER_CB_RESET_TIMEOUT: float = float(
    os.environ.get("MYCLAW_PROVIDER_CB_RESET_TIMEOUT", "60.0")
)


# ── Knowledge / search defaults ──────────────────────────────────────────

KB_SEARCH_EXECUTOR_WORKERS: int = int(
    os.environ.get("MYCLAW_KB_SEARCH_WORKERS", "8")
)


# ── Public surface ───────────────────────────────────────────────────────

__all__ = [
    "MYCLAW_HOME",
    "GAP_FILE",
    "MEMORY_DB_TEMPLATE",
    "COST_DB_PATH",
    "VECTORS_DB_PATH",
    "PROMPTS_PATH",
    "PLUGINS_INSTALL_DIR",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_BACKOFF_MAX",
    "DEFAULT_BACKOFF_EXPONENTIAL",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CACHE_SIZE",
    "CACHE_TTL_SECONDS",
    "VACUUM_INTERVAL",
    "CLEANUP_CHUNK_SIZE",
    "DEFAULT_CLEANUP_DAYS",
    "PROVIDER_CB_FAILURE_THRESHOLD",
    "PROVIDER_CB_RESET_TIMEOUT",
    "KB_SEARCH_EXECUTOR_WORKERS",
]
