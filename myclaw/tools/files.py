"""
Tools — File I/O
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

from pathlib import Path

logger = logging.getLogger(__name__)

def read_file(path: str) -> str:
    """Read a file from the workspace directory (~/.myclaw/workspace).

    All paths are validated against directory traversal before reading.
    Only files within the workspace boundary are accessible.

    Args:
        path: Relative path to the file within the workspace
              (e.g. 'notes.txt', 'subdir/data.json')

    Returns:
        File contents as a string on success.
        'Error: Invalid path: ...' if path escapes the workspace.
        'Error: ...' on any other failure (file not found, permission denied).
    """
    try:
        return validate_path(path).read_text()
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File read error: {e}")
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file in the workspace directory (~/.myclaw/workspace).

    Creates parent directories automatically. Overwrites existing files.
    All paths are validated against directory traversal.

    Args:
        path: Relative path within the workspace (e.g. 'output.txt', 'data/result.json').
              Supports nested paths — parent directories are created automatically.
        content: String content to write to the file.

    Returns:
        'File written: {path}' on success.
        'Error: Invalid path: ...' if path escapes the workspace.
        'Error: ...' on any other failure.
    """
    try:
        p = validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"File written: {path}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File write error: {e}")
        return f"Error: {e}"

