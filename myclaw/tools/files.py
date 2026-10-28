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

from . import core as _core  # noqa: E402  (WORKSPACE read at call time)

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
    # 2026-10-21: folders are listed instead of failing ("Is a directory"),
    # a missing file shows what is there, huge/binary files are not dumped,
    # and these agent mistakes are no longer logged as ERRORs (they filled the
    # Doctor's error list).
    try:
        p = validate_path(path)
    except ValueError as e:
        return (f"Error: {e}. read_file only reads files inside the workspace ({_core.WORKSPACE}). "
                "ZenSynora's settings and logs are not readable by agents — use check_system_health, "
                "get_health_report or get_recent_problems for diagnostics.")
    try:
        if p.is_dir():
            return _listing(p, path)
        if not p.exists():
            logger.info(f"read_file: no such file: {path}")
            parent = p.parent if p.parent.is_dir() else _core.WORKSPACE
            near = sorted(x.name + ("/" if x.is_dir() else "") for x in parent.iterdir())[:40]
            rel = parent.relative_to(_core.WORKSPACE.resolve()) if parent.resolve().is_relative_to(_core.WORKSPACE.resolve()) else parent
            return (f"Error: No file '{path}' in the workspace. In '{rel or '.'}' there is: "
                    f"{', '.join(near) or '(nothing)'}. Paths are relative to {_core.WORKSPACE}.")
        size = p.stat().st_size
        with open(p, "rb") as f:
            head = f.read(MAX_READ_BYTES)
        if b"\x00" in head[:8192]:
            return (f"Error: '{path}' is a binary file ({size:,} bytes) and cannot be shown as text.")
        text = head.decode("utf-8", errors="replace")
        if size > MAX_READ_BYTES:
            text += (f"\n\n[… only the first {MAX_READ_BYTES:,} of {size:,} bytes are shown — "
                     "use shell 'head', 'tail' or 'grep' for other parts]")
        return text
    except Exception as e:
        logger.warning(f"File read error: {e}")
        return f"Error: {e}"


MAX_READ_BYTES = 200_000


def _listing(p, path: str) -> str:
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    lines = []
    for x in items[:200]:
        if x.is_dir():
            lines.append(f"{x.name}/")
        else:
            try:
                lines.append(f"{x.name}  ({x.stat().st_size:,} bytes)")
            except OSError:
                lines.append(x.name)
    more = f"\n… and {len(items) - 200} more" if len(items) > 200 else ""
    return (f"'{path}' is a folder ({len(items)} entries) — read a file inside it with read_file('<folder>/<name>'):\n"
            + ("\n".join(lines) or "(empty)") + more)


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
        # 2026-10-27 (rules review): a hand-written .git/config (aliases, hooks,
        # filters) or a '.git' file pointing elsewhere makes git run commands or
        # write outside the workspace. Use git itself for its own files.
        from .core import WORKSPACE as _ws

        if ".git" in p.relative_to(_ws.resolve()).parts:
            return ("Error: files inside .git (or a file named .git) cannot be written with write_file — "
                    "use git commands for the repository's own files.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"File written: {path}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File write error: {e}")
        return f"Error: {e}"

