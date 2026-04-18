"""
Tools — SSH Remote Execution & Hardware Diagnostics
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

import getpass
import httpx
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── SSH & Hardware Features (Phase 2.0) ────────────────────────────────────────

def _prompt_ssh_password(host: str, user: str) -> str:
    """Prompt for SSH password using getpass. Never logs the password."""
    try:
        return getpass.getpass(f"SSH password for {user}@{host}: ")
    except Exception:
        # No TTY available (e.g., web UI, API calls)
        return ""


async def ssh_command(cmd: str, host: str = None, user: str = "root", key_path: str = "", timeout: int = 60) -> str:
    """Execute a command on a remote host via SSH.

    Key-based authentication is strongly preferred. If no key_path is provided,
    the user will be interactively prompted for a password via getpass.
    Plaintext passwords are NEVER accepted as parameters to prevent logging
    or caching of credentials.

    Args:
        cmd: Command to run
        host: Hostname or IP (if None, uses default from config)
        user: SSH user
        key_path: Path to SSH private key (recommended)
        timeout: Timeout in seconds
    """
    try:
        from .backends.ssh import SSHBackend
        from .config import SSHBackendConfig
        from pydantic import SecretStr

        # Load defaults if not provided
        effective_host = host or ""
        password = ""

        if not key_path:
            password = await asyncio.to_thread(_prompt_ssh_password, effective_host, user)
            if not password:
                return (
                    "Error: SSH key-based authentication is required when no TTY is available. "
                    "Please provide a key_path or run from an interactive terminal."
                )

        config = SSHBackendConfig(
            host=effective_host,
            user=user,
            password=SecretStr(password),
            key_path=key_path
        )

        backend = SSHBackend(config)
        result = await backend.run(cmd, timeout=timeout)
        return result
    except Exception as e:
        logger.error(f"SSH tool error: {e}")
        return f"Error: {e}"


async def ssh_put_file(local_path: str, remote_path: str, host: str, user: str = "root", key_path: str = "") -> str:
    """Upload a local file to a remote host via SFTP.

    Key-based authentication is strongly preferred. See ssh_command() for details.
    """
    try:
        from .backends.ssh import SSHBackend
        from .config import SSHBackendConfig
        from pydantic import SecretStr

        # Validate local path
        local_p = validate_path(local_path)

        password = ""
        if not key_path:
            password = await asyncio.to_thread(_prompt_ssh_password, host, user)
            if not password:
                return (
                    "Error: SSH key-based authentication is required when no TTY is available. "
                    "Please provide a key_path or run from an interactive terminal."
                )

        config = SSHBackendConfig(host=host, user=user, password=SecretStr(password), key_path=key_path)
        backend = SSHBackend(config)
        await backend.upload(str(local_p), remote_path)
        return f"✅ Successfully uploaded {local_path} to {host}:{remote_path}"
    except Exception as e:
        return f"Error: {e}"


async def ssh_get_file(remote_path: str, local_path: str, host: str, user: str = "root", key_path: str = "") -> str:
    """Download a remote file to the local workspace via SFTP.

    Key-based authentication is strongly preferred. See ssh_command() for details.
    """
    try:
        from .backends.ssh import SSHBackend
        from .config import SSHBackendConfig
        from pydantic import SecretStr

        # Validate local path
        local_p = validate_path(local_path)

        password = ""
        if not key_path:
            password = await asyncio.to_thread(_prompt_ssh_password, host, user)
            if not password:
                return (
                    "Error: SSH key-based authentication is required when no TTY is available. "
                    "Please provide a key_path or run from an interactive terminal."
                )

        config = SSHBackendConfig(host=host, user=user, password=SecretStr(password), key_path=key_path)
        backend = SSHBackend(config)
        await backend.download(remote_path, str(local_p))
        return f"✅ Successfully downloaded {remote_path} from {host} to {local_path}"
    except Exception as e:
        return f"Error: {e}"


def get_system_diagnostic() -> str:
    """Get a detailed diagnostic report of the current system hardware.
    Includes CPU, GPU, RAM, NPU, and Network metrics.
    """
    try:
        from .backends.hardware import get_system_metrics, get_optimization_suggestions
        metrics = get_system_metrics()
        suggestions = get_optimization_suggestions(metrics)

        lines = ["🖥️ System Diagnostic Report:", ""]
        lines.append(f"CPU: {metrics['cpu_model']} ({metrics['cpu_cores']} cores, {metrics['cpu_threads']} threads)")
        lines.append(f"RAM: {metrics['ram_total_gb']:.1f} GB ({metrics['ram_type']})")

        if metrics['gpus']:
            lines.append("GPUs:")
            for g in metrics['gpus']:
                lines.append(f"  - {g['model']} ({g['memory_gb']:.1f} GB VRAM, {g['temp']}°C)")

        lines.append(f"Network: {metrics['network_latency_ms']:.1f}ms latency")

        if suggestions:
            lines.append("\n💡 Optimization Suggestions:")
            for s in suggestions:
                lines.append(f"  - {s}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


