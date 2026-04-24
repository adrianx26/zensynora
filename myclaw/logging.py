"""Central logging configuration for the ZenSynora codebase.

All modules should obtain a logger via ``logging.getLogger(__name__)`` and rely
on the configuration defined here.  This eliminates scattered ``basicConfig``
calls and guarantees a consistent format across the project.
"""

import logging
from typing import Optional

# Default format – timestamp, logger name, level, message
_DEFAULT_FORMAT = "% (asctime)s - %(name)s - %(levelname)s - %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO, fmt: Optional[str] = None, datefmt: Optional[str] = None) -> None:
    """Configure the root logger once.

    This should be called at application start‑up (e.g., in the CLI entry point).
    Subsequent imports only retrieve child loggers via ``logging.getLogger``.
    """
    if logging.getLogger().handlers:
        # Logging already configured – avoid duplicate configuration.
        return
    logging.basicConfig(
        level=level,
        format=fmt or _DEFAULT_FORMAT,
        datefmt=datefmt or _DEFAULT_DATEFMT,
    )
