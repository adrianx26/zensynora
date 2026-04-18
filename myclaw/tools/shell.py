"""
Tools — Shell Execution
"""

import asyncio
import subprocess
import shlex
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE, ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    _rate_limiter, _tool_audit_logger,
)

logger = logging.getLogger(__name__)

async def shell_async(cmd: str, timeout: int = 30) -> str:
    """Execute an allowed shell command asynchronously in the workspace directory.

    Async version of shell() for better async performance.
    Runs a command from the strict allowlist in ~/.myclaw/workspace.

    Args:
        cmd: Shell command string (e.g. 'ls -la', 'grep pattern file.txt')
        timeout: Timeout in seconds (default: 30)

    Returns:
        Combined stdout+stderr as a string on success.
        'Error: Empty command' if cmd is blank.
        'Error: Command X is blocked for security' if cmd is in BLOCKED_COMMANDS.
        'Error: X not allowed. Allowed: ...' if cmd is not in ALLOWED_COMMANDS.
        'Error: Command timed out after X seconds' on timeout.
        'Error: Rate limit exceeded for shell tool' if rate limited.
    """
    start_time = time.time()
    try:
        # 5.1: Rate limiting check
        if not _rate_limiter.check("shell", max_calls=10, window=60):
            _tool_audit_logger.log("shell_async", "", 0, False, "Rate limit exceeded")
            return "Error: Rate limit exceeded for shell tool (10 calls/minute)"

        # Security: Validate command doesn't contain dangerous characters
        dangerous = re.compile(r'[;&|`$(){}\[\]\\]')
        if dangerous.search(cmd):
            logger.warning(f"Blocked command with dangerous characters: {cmd}")
            _tool_audit_logger.log("shell_async", "", 0, False, "Dangerous characters detected")
            return "Error: Command contains dangerous characters"

        parts = shlex.split(cmd)
        if not parts:
            return "Error: Empty command"
        first_cmd = parts[0].lower()
        if first_cmd in BLOCKED_COMMANDS:
            logger.warning(f"Blocked command attempted: {first_cmd}")
            _tool_audit_logger.log("shell_async", "", 0, False, f"Blocked command: {first_cmd}")
            return f"Error: Command '{first_cmd}' is blocked for security"
        if first_cmd not in ALLOWED_COMMANDS:
            return f"Error: '{first_cmd}' not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"

        # Use the full command string for shell execution
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            duration_ms = (time.time() - start_time) * 1000
            # 5.4: Audit logging
            _tool_audit_logger.log("shell_async", "", duration_ms, True)
            return stdout.decode() + stderr.decode()
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration_ms = (time.time() - start_time) * 1000
            _tool_audit_logger.log("shell_async", "", duration_ms, False, "Command timed out")
            return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        _tool_audit_logger.log("shell_async", "", duration_ms, False, str(e))
        logger.error(f"Shell async error: {e}")
        return f"Error: {e}"



def shell(cmd: str) -> str:
    """Execute an allowed shell command in the workspace directory.

    Runs a command from the strict allowlist in ~/.myclaw/workspace.
    Commands not in the allowlist are rejected with a helpful message.
    Dangerous commands (rm, del, powershell, etc.) are blocked entirely.

    Args:
        cmd: Shell command string (e.g. 'ls -la', 'grep pattern file.txt')

    Returns:
        Combined stdout+stderr as a string on success.
        'Error: Empty command' if cmd is blank.
        'Error: Command X is blocked for security' if cmd is in BLOCKED_COMMANDS.
        'Error: X not allowed. Allowed: ...' if cmd is not in ALLOWED_COMMANDS.
        'Error: Command timed out after 30 seconds' on timeout.
        'Error: Rate limit exceeded for shell tool' if rate limited.

    Allowed commands: ls, dir, cat, type, find, grep, findstr, head, tail,
        wc, sort, uniq, cut, git, echo, pwd, python, python3, pip, curl, wget
    """
    start_time = time.time()
    try:
        # 5.1: Rate limiting check
        if not _rate_limiter.check("shell", max_calls=10, window=60):
            _tool_audit_logger.log("shell", "", 0, False, "Rate limit exceeded")
            return "Error: Rate limit exceeded for shell tool (10 calls/minute)"

        # Security: Validate command doesn't contain dangerous characters
        dangerous = re.compile(r'[;&|`$(){}\[\]\\]')
        if dangerous.search(cmd):
            logger.warning(f"Blocked command with dangerous characters: {cmd}")
            _tool_audit_logger.log("shell", "", 0, False, "Dangerous characters detected")
            return "Error: Command contains dangerous characters"

        parts = shlex.split(cmd)
        if not parts:
            return "Error: Empty command"
        first_cmd = parts[0].lower()
        if first_cmd in BLOCKED_COMMANDS:
            logger.warning(f"Blocked command attempted: {first_cmd}")
            _tool_audit_logger.log("shell", "", 0, False, f"Blocked command: {first_cmd}")
            return f"Error: Command '{first_cmd}' is blocked for security"
        if first_cmd not in ALLOWED_COMMANDS:
            return f"Error: '{first_cmd}' not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        result = subprocess.run(
            parts, shell=False, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=30
        )
        duration_ms = (time.time() - start_time) * 1000
        # 5.4: Audit logging
        _tool_audit_logger.log("shell", "", duration_ms, True)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        duration_ms = (time.time() - start_time) * 1000
        _tool_audit_logger.log("shell", "", duration_ms, False, "Command timed out")
        return "Error: Command timed out after 30 seconds"
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        _tool_audit_logger.log("shell", "", duration_ms, False, str(e))
        logger.error(f"Shell error: {e}")
        return f"Error: {e}"

