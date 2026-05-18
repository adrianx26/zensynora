"""Deprecated — use ``myclaw.logging_config.configure_logging()`` instead.

This module is retained for backward-compatibility only.  New code must
import from ``myclaw.logging_config`` which provides the structured
formatter, PII scrubbing, JSON/console dual output, and file logging.

Usage (old, still works but deprecated):
    from myclaw.logging import configure_logging
    configure_logging(level=logging.INFO)

Usage (preferred):
    from myclaw.logging_config import configure_logging
    configure_logging(level=logging.INFO, use_json=False, log_file=None)

The ``init_app()`` function in ``myclaw.__init__`` already uses the
preferred path.  If you must import this module, you will get a
forwarding wrapper that delegates to ``logging_config`` and emits a
``DeprecationWarning``.
"""

from __future__ import annotations

import logging
import warnings
from typing import Optional


def configure_logging(
    level: int = logging.INFO,
    fmt: Optional[str] = None,
    datefmt: Optional[str] = None,
) -> None:
    """DEPRECATED — delegates to ``logging_config.configure_logging``.

    The *fmt* and *datefmt* arguments are ignored; the structured
    formatter is always used.  Pass ``use_json=True`` to the real
    function if you need JSON output.

    This wrapper exists so legacy callers don't crash.  It will emit a
    warning exactly once per process.
    """
    warnings.warn(
        "myclaw.logging is deprecated. Use myclaw.logging_config.configure_logging() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from .logging_config import configure_logging as _configure

    _configure(level=level)
