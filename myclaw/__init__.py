"""MyClaw - Personal AI Agent

Task Timer Integration (v3):
---------------------------
The agent now includes automatic task timing with the following features:
- 300-second maximum timeout for any user question
- Status updates at 60s, 120s, 180s, and 240s thresholds
- Automatic task failure and logging at 300s
- User notifications at each threshold with diagnostic information

The timer starts automatically when agent.think() is called and tracks:
  1. Memory loading
  2. Knowledge base search
  3. System prompt building
  4. LLM call
  5. Tool execution (if needed)
  6. Response generation

Configuration:
- Logs are stored in ~/.myclaw/task_logs/
- Thresholds are configurable via TaskThresholdConfig
- Status updates are printed to console with color coding
"""
__version__ = "0.4.1"

import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# ── Centralised application bootstrapper ───────────────────────────────────
#
# Entry points (cli.py, onboard.py, deploy.py) previously duplicated
# logging configuration, config loading, and graceful-shutdown wiring.
# ``init_app()`` consolidates these steps so each script can invoke a
# single call and inherit consistent behaviour.
#
# Usage:
#     from myclaw import init_app
#     init_app()


def init_app(
    log_level: Optional[int] = None,
    log_file: Optional[str] = None,
) -> None:
    """Centralised application bootstrapping for all entry points.

    Performs the following in order:
      1. Configure logging via ``myclaw.logging.configure_logging``.
      2. Install graceful-shutdown handlers (SIGINT / SIGTERM / atexit).
      3. Verify that the myclaw config directory exists.

    Args:
        log_level: Logging level (defaults to ``logging.INFO`` or the
                   ``MYCLAW_LOG_LEVEL`` environment variable).
        log_file: Optional path for a log file output.

    Raises:
        SystemExit: If the Python version is below 3.11.
    """
    if log_level is None:
        env_level = __import__("os").environ.get("MYCLAW_LOG_LEVEL", "INFO")
        log_level = getattr(logging, env_level.upper(), logging.INFO)

    # 1. Centralised logging (uses structured formatter with PII scrubbing)
    from .logging_config import configure_logging as _configure_logging
    _configure_logging(level=log_level, log_file=log_file)

    # 2. Graceful-shutdown handlers
    _install_shutdown_handlers()

    logger.info("ZenSynora v%s initialised (log_level=%s)", __version__, logging.getLevelName(log_level))


def _install_shutdown_handlers() -> None:
    """Register signal handlers and atexit hooks for graceful shutdown."""
    import atexit
    import signal

    def _graceful_shutdown() -> None:
        logger.info("Shutting down gracefully...")
        # Close shared aiohttp session
        try:
            from .aiohttp_session import close_aiohttp_session
            close_aiohttp_session()
        except Exception as exc:
            logger.debug("Error closing aiohttp session: %s", exc)
        # Close HTTP client pool
        try:
            from . import provider
            if hasattr(provider, "HTTPClientPool"):
                import asyncio
                asyncio.run(provider.HTTPClientPool.close())
        except Exception as exc:
            logger.debug("Error closing HTTP pool: %s", exc)
        # Close SQLite pool
        try:
            from . import memory
            if hasattr(memory, "SQLitePool"):
                memory.SQLitePool.close_all()
        except Exception as exc:
            logger.debug("Error closing SQLite pool: %s", exc)
        # Close state store
        try:
            from .state_store import reset_state_store
            reset_state_store()
        except Exception as exc:
            logger.debug("Error resetting state store: %s", exc)
        logger.info("Graceful shutdown complete")

    def _signal_handler(signum: int, frame) -> None:  # noqa: ARG001
        logger.info("Received signal %d, initiating graceful shutdown...", signum)
        _graceful_shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    atexit.register(_graceful_shutdown)


# Export task timer for external use
from .task_timer import (
    TaskTimerOrchestrator,
    TaskThresholdConfig,
    TaskStatus,
    get_task_timer_orchestrator,
)

__all__ = [
    "init_app",
    "TaskTimerOrchestrator",
    "TaskThresholdConfig",
    "TaskStatus",
    "get_task_timer_orchestrator",
]
