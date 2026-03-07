import subprocess
import shlex
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)
WORKSPACE = Path.home() / ".myclaw" / "workspace"

# Security: Allowlist of permitted shell commands
ALLOWED_COMMANDS = frozenset({
    'ls', 'dir', 'cat', 'type', 'find', 'grep', 'findstr',
    'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'awk',
    'git', 'python', 'python3', 'pip', 'pip3', 'node', 'npm'
})

# Security: Blocklist of dangerous commands
BLOCKED_COMMANDS = frozenset({
    'rm', 'del', 'erase', 'format', 'rd', 'rmdir', 'rm -rf',
    'powershell', 'cmd', 'certutil', 'bitsadmin', 'icacls',
    'takeown', 'reg', 'schtasks', 'net', 'netstat', 'tasklist',
    'wmic', 'msiexec', 'control', 'explorer', 'shutdown', 'restart'
})


def validate_path(path: str) -> Path:
    """Validate that path stays within workspace directory."""
    workspace = WORKSPACE.resolve()
    try:
        target = (workspace / path).resolve()
        # Check if resolved path starts with workspace (prevents path traversal)
        if not str(target).startswith(str(workspace)):
            raise ValueError(f"Path traversal detected: {path}")
        return target
    except Exception as e:
        raise ValueError(f"Invalid path: {path}") from e


def shell(cmd: str) -> str:
    """Execute shell command in workspace with security restrictions."""
    try:
        # Split safely — prevents shell injection via ; | && $() etc.
        parts = shlex.split(cmd)
        if not parts:
            return "Error: Empty command"

        first_cmd = parts[0].lower()

        # Check blocklist first (most dangerous)
        if first_cmd in BLOCKED_COMMANDS:
            logger.warning(f"Blocked command attempted: {first_cmd}")
            return f"Error: Command '{first_cmd}' is blocked for security"

        # Check allowlist
        if first_cmd not in ALLOWED_COMMANDS:
            return f"Error: Command '{first_cmd}' not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"

        # Execute without shell=True to prevent injection
        result = subprocess.run(
            parts,            # list of args, not a string
            shell=False,      # no shell expansion — semicolons, pipes, $() are all inert
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds"
    except Exception as e:
        logger.error(f"Shell command error: {e}")
        return f"Error: {e}"

def read_file(path: str) -> str:
    """Read file from workspace with path validation."""
    try:
        validated_path = validate_path(path)
        return validated_path.read_text()
    except ValueError as e:
        logger.warning(f"Path validation failed: {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File read error: {e}")
        return f"Error: {e}"

def write_file(path: str, content: str) -> str:
    """Write file to workspace with path validation."""
    try:
        validated_path = validate_path(path)
        # Create parent directories if needed
        validated_path.parent.mkdir(parents=True, exist_ok=True)
        validated_path.write_text(content)
        return f"File written: {path}"
    except ValueError as e:
        logger.warning(f"Path validation failed: {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File write error: {e}")
        return f"Error: {e}"

TOOLS = {
    "shell": {"func": shell, "desc": "Execute shell command"},
    "read_file": {"func": read_file, "desc": "Read file from workspace"},
    "write_file": {"func": write_file, "desc": "Write file to workspace"},
}