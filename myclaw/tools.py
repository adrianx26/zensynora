import asyncio
import subprocess
import shlex
import logging
import json
import time
import importlib.util
import re
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

from .exceptions import (
    ToolValidationError,
    ToolPermissionError,
    ToolNotFoundError,
    KnowledgeBaseError,
    KnowledgeNotFoundError,
)
from .knowledge import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_related_entities, build_context, sync_knowledge, get_all_tags,
    Observation, Relation
)
from .knowledge.storage import get_knowledge_dir

# Module-level config reference for timeout settings
_config = None

def set_config(config):
    """Called by gateway to provide config for timeout settings."""
    global _config
    _config = config

logger = logging.getLogger(__name__)

WORKSPACE         = Path.home() / ".myclaw" / "workspace"
TOOLBOX_DIR       = Path.home() / ".myclaw" / "TOOLBOX"
TOOLBOX_REG       = Path.home() / ".myclaw" / "TOOLBOX" / "toolbox_registry.json"
TOOLBOX_DOCS      = Path.home() / ".myclaw" / "TOOLBOX" / "README.md"

# ── Security lists ────────────────────────────────────────────────────────────

ALLOWED_COMMANDS = frozenset({
    'ls', 'dir', 'cat', 'type', 'find', 'grep', 'findstr',
    'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'git',
    'echo', 'pwd', 'python', 'python3', 'pip', 'curl', 'wget'
})

BLOCKED_COMMANDS = frozenset({
    'rm', 'del', 'erase', 'format', 'rd', 'rmdir',
    'powershell', 'cmd', 'certutil', 'bitsadmin', 'icacls',
    'takeown', 'reg', 'schtasks', 'net', 'tasklist',
    'wmic', 'msiexec', 'control', 'explorer', 'shutdown', 'restart'
})


# ── 5.1 Rate Limiter for Tool Execution ──────────────────────────────────────────

class RateLimiter:
    """Per-tool rate limiter using token bucket algorithm.
    
    Limits tool execution to prevent abuse. Default: 10 calls per minute per tool.
    """
    def __init__(self):
        # _limits: tool_name -> (timestamps list, max_calls, window_seconds)
        self._limits = defaultdict(lambda: ([], 10, 60))
    
    def check(self, tool_name: str, max_calls: int = 10, window: int = 60) -> bool:
        """Check if tool can be executed. Returns True if allowed, False if rate limited."""
        now = time.time()
        timestamps, _, _ = self._limits[tool_name]
        # Remove old timestamps outside the window
        self._limits[tool_name] = (
            [t for t in timestamps if now - t < window],
            max_calls,
            window
        )
        
        timestamps, max_calls, window = self._limits[tool_name]
        if len(timestamps) >= max_calls:
            return False
        timestamps.append(now)
        return True
    
    def get_remaining(self, tool_name: str) -> int:
        """Get remaining calls available for the tool in current window."""
        now = time.time()
        timestamps, max_calls, window = self._limits[tool_name]
        current_calls = len([t for t in timestamps if now - t < window])
        return max(0, max_calls - current_calls)

_rate_limiter = RateLimiter()


# ── 5.3 Runtime Allowlist Updates ────────────────────────────────────────────────

def update_allowlist(new_commands: List[str]) -> None:
    """Update the allowed commands list at runtime.
    
    Args:
        new_commands: List of command names to allow
    
    Note: This does NOT affect BLOCKED_COMMANDS which remain enforced.
    """
    global ALLOWED_COMMANDS
    ALLOWED_COMMANDS = frozenset(new_commands)
    logger.info(f"Updated ALLOWED_COMMANDS with {len(new_commands)} commands")


# ── 5.4 Tool Execution Audit Logging ─────────────────────────────────────────────

class ToolAuditLogger:
    """Structured audit logger for tool executions."""
    
    def __init__(self):
        self._logs: List[Dict] = []
        self._max_logs = 1000
    
    def log(self, tool_name: str, user: str, duration_ms: float, success: bool, 
            error: Optional[str] = None) -> None:
        """Log a tool execution event."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "tool": tool_name,
            "user": user or "system",
            "duration_ms": duration_ms,
            "success": success,
            "error": error
        }
        self._logs.append(entry)
        # Keep only recent logs in memory
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs:]
        
        # Log to standard logger
        if success:
            logger.info(f"AUDIT: {tool_name} executed by {user or 'system'} "
                       f"in {duration_ms:.2f}ms")
        else:
            logger.warning(f"AUDIT: {tool_name} failed for {user or 'system'} "
                          f"in {duration_ms:.2f}ms: {error}")
    
    def get_logs(self, limit: int = 100, tool_name: Optional[str] = None) -> List[Dict]:
        """Get recent audit logs, optionally filtered by tool name."""
        logs = self._logs[-limit:]
        if tool_name:
            logs = [l for l in logs if l["tool"] == tool_name]
        return logs

_tool_audit_logger = ToolAuditLogger()


# ── Module-level references injected by gateway / telegram / whatsapp ──────────

_agent_registry: dict = {}   # name -> Agent  (Feature 2 / 3)
_job_queue      = None        # python-telegram-bot JobQueue  (Feature 1 / 5)
_user_chat_ids: dict = {}     # user_id -> chat_id  (Feature 5 notifications)
_notification_callback = None  # Callback: async fn(user_id, message) for channel-agnostic notifications


def set_registry(registry: dict):
    """Called by gateway.py after building the agent registry."""
    global _agent_registry
    _agent_registry = registry


def set_job_queue(jq):
    """Called by telegram.py after the Application is built."""
    global _job_queue
    _job_queue = jq


def set_notification_callback(callback):
    """Set the notification callback: async fn(user_id, message).
    
    Used by WhatsApp (and future channels) so scheduled jobs can
    send results back without depending on Telegram's bot object.
    """
    global _notification_callback
    _notification_callback = callback


def register_chat_id(user_id: str, chat_id: int):
    """Store a user's Telegram chat_id so scheduled jobs can notify them."""
    _user_chat_ids[user_id] = chat_id


# ── Core Tools ────────────────────────────────────────────────────────────────

def validate_path(path: str) -> Path:
    """Validate that path stays within workspace — prevents traversal attacks."""
    workspace = WORKSPACE.resolve()
    try:
        target = (workspace / path).resolve()
        if not str(target).startswith(str(workspace)):
            raise ToolPermissionError(
                f"Path traversal detected: {path}",
                reason="Path traversal attempt blocked",
                tool_name="validate_path"
            )
        return target
    except ToolPermissionError:
        raise
    except Exception as e:
        raise ToolValidationError(
            f"Invalid path: {path}",
            tool_name="validate_path",
            validation_errors={"path": str(e)}
        ) from e


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
        # Get timeout from config (default 30 seconds)
        timeout = 30
        if _config and hasattr(_config, 'timeouts'):
            timeout = _config.timeouts.shell_seconds
        result = subprocess.run(
            parts, shell=False, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=timeout
        )
        duration_ms = (time.time() - start_time) * 1000
        # 5.4: Audit logging
        _tool_audit_logger.log("shell", "", duration_ms, True)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        duration_ms = (time.time() - start_time) * 1000
        _tool_audit_logger.log("shell", "", duration_ms, False, "Command timed out")
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        _tool_audit_logger.log("shell", "", duration_ms, False, str(e))
        logger.error(f"Shell error: {e}")
        return f"Error: {e}"


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


# ── Internet & Download Tools ────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace to produce clean plain text."""
    # Remove <script> and <style> blocks entirely
    html = re.sub(r'<(script|style)[^>]*>.*?</(\1)>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    html = re.sub(r'<[^>]+>', ' ', html)
    # Decode common HTML entities
    html = html.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    # Collapse whitespace
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def browse(url: str, max_length: int = 5000) -> str:
    """Browse a URL and return its plain-text content (HTML is stripped).

    Fetches a web page, strips HTML tags, script/style blocks, and HTML entities,
    then returns clean readable text. Truncates to max_length characters.

    Args:
        url: Full URL to fetch (e.g. 'https://example.com')
        max_length: Maximum characters to return (default: 5000).
                    Pages longer than this are truncated with a notice.

    Returns:
        'URL: {url}\nStatus: {code}\n\nContent:\n{plain_text}' on success.
        'Error browsing {url}: ...' on HTTP or network errors.
        'Error: ...' on unexpected failures.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Strip HTML to plain text
        text = _strip_html(response.text)

        # Limit length
        if len(text) > max_length:
            text = text[:max_length] + "\n\n[Content truncated - reached max_length limit]"

        return f"URL: {url}\nStatus: {response.status_code}\n\nContent:\n{text}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Browse error for {url}: {e}")
        return f"Error browsing {url}: {e}"
    except Exception as e:
        logger.error(f"Unexpected browse error: {e}")
        return f"Error: {e}"


def download_file(url: str, path: str) -> str:
    """
    Download a file from a URL and save it to the workspace.
    
    url: The URL to download from
    path: The path (relative to workspace) to save the file
    """
    try:
        # Validate the path
        target = validate_path(path)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()
        
        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # Download and save
        with open(target, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Get file size
        size = target.stat().st_size
        
        logger.info(f"Downloaded file from {url} to {path} ({size} bytes)")
        return f"[OK] Downloaded file from {url} to {path} ({size} bytes)"
        
    except ValueError as e:
        return f"Error: {e}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Download error for {url}: {e}")
        return f"Error downloading from {url}: {e}"
    except Exception as e:
        logger.error(f"Unexpected download error: {e}")
        return f"Error: {e}"


# ── Feature 3: Sub-Agent Delegation ──────────────────────────────────────────

async def delegate(agent_name: str, task: str, _depth: int = 0) -> str:
    """Delegate a task to another named agent and return its response.

    agent_name: name of the agent (see /agents for available names)
    task: the instruction to send to that agent
    """
    if _depth >= 2:
        return "Error: Maximum delegation depth (2) reached — cannot delegate further."
    if not _agent_registry:
        return "Error: Agent registry not initialized."
    if agent_name not in _agent_registry:
        available = ", ".join(_agent_registry.keys())
        return f"Error: Unknown agent '{agent_name}'. Available: {available}"
    try:
        return await _agent_registry[agent_name].think(task, user_id="__delegate__", _depth=_depth)
    except Exception as e:
        logger.error(f"Delegation error: {e}")
        return f"Delegation failed: {e}"


# ── Feature 4: Agent Builds Its Own Tools ────────────────────────────────────

def list_tools() -> str:
    """Return the names of all currently registered tools."""
    return "Available tools: " + ", ".join(sorted(TOOLS.keys()))


def register_tool(name: str, code: str, documentation: str = "") -> str:
    """Dynamically create a new tool from Python source code and store it in TOOLBOX.

    name: valid Python identifier — must match the function name defined in code
    code: full Python source for the function (use \\n for newlines)
    documentation: detailed documentation explaining what the tool does, its parameters, return values, and usage examples

    IMPORTANT: Before creating a tool, you must check if a similar tool already exists in TOOLBOX.
    Use list_toolbox() to see existing tools first.

    The tool must include:
    1. A proper docstring explaining its purpose and usage
    2. Error handling with try-except blocks
    3. Logging of errors using logger.error()
    
    Example:
        register_tool("greet", "def greet(who='world'):\\n    \\"\\"\\"Greet someone.\\"\\"\\"\\n    try:\\n        return f'Hello {who}!'\\n    except Exception as e:\\n        logger.error(f'Error in greet: {e}')\\n        return f'Error: {e}'\\n", "Tool to greet someone with their name")
    """
    if not name.isidentifier():
        return f"Error: '{name}' is not a valid Python identifier."

    # Check if tool already exists in TOOLBOX or is a core tool
    if name in TOOLS or name in ["shell", "read_file", "write_file", "browse", "download_file",
                                       "delegate", "list_tools", "register_tool", "schedule",
                                       "edit_schedule", "split_schedule", "suspend_schedule",
                                       "resume_schedule", "cancel_schedule", "list_schedules",
                                       "write_to_knowledge", "search_knowledge", "read_knowledge",
                                       "list_knowledge", "get_knowledge_context", "get_related_knowledge",
                                       "sync_knowledge_base", "list_knowledge_tags",
                                       "swarm_create", "swarm_assign", "swarm_status", "swarm_result",
                                       "swarm_terminate", "swarm_list", "swarm_stats"]:
        return f"Error: Tool '{name}' already exists or is a protected core tool. Use list_tools() to see all available tools."

    # Check if file already exists in TOOLBOX directory
    TOOLBOX_DIR.mkdir(parents=True, exist_ok=True)
    tool_path = TOOLBOX_DIR / f"{name}.py"
    if tool_path.exists():
        return f"Error: Tool file '{name}.py' already exists in TOOLBOX. Please choose a different name or modify the existing tool."

    # Check for similar tools based on name similarity
    similar_tools = [t for t in TOOLS.keys() if name.lower() in t.lower() or t.lower() in name.lower()]
    if similar_tools:
        return f"Error: Similar tool(s) already exist in TOOLBOX: {', '.join(similar_tools)}. Please check if an existing tool meets your needs using list_tools() or choose a more specific name."

    # Syntax validation before anything hits disk
    try:
        compile(code, "<agent-tool>", "exec")
    except SyntaxError as e:
        return f"Syntax error in tool code: {e}"

    # AST validation to prevent dangerous operations
    import ast
    try:
        tree = ast.parse(code)
        forbidden_imports = {"os", "sys", "subprocess", "shutil", "socket", "urllib", "http", "pty", "commands"}
        forbidden_calls = {"eval", "exec", "open", "__import__", "globals", "locals", "compile"}
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split('.')[0] in forbidden_imports:
                        return f"Error: Importing '{alias.name}' is forbidden for security reasons."
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split('.')[0] in forbidden_imports:
                    return f"Error: Importing from '{node.module}' is forbidden for security reasons."
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    return f"Error: Calling '{node.func.id}' is forbidden for security reasons."
    except Exception as e:
        return f"AST validation error: {e}"

    # Validate that the code has a docstring and error handling
    if '"""' not in code and "'''" not in code:
        return "Error: Tool code must include a docstring explaining its purpose and usage."
    
    if 'try:' not in code or 'except' not in code:
        return "Error: Tool code must include error handling with try-except blocks."
    
    if 'logger.error' not in code:
        return "Error: Tool code must include error logging using logger.error()."

    # Write to disk
    tool_path.write_text(code, encoding="utf-8")

    # Create documentation file
    if documentation:
        doc_path = TOOLBOX_DIR / f"{name}_README.md"
        doc_content = f"""# {name}

## Description
{documentation}

## Code
```python
{code}
```

## Created
{datetime.now().isoformat()}

## Error Logging
Errors are logged to the standard logging system and can be found in the application logs.
"""
        doc_path.write_text(doc_content, encoding="utf-8")

    # Update main TOOLBOX README
    _update_toolbox_readme()

    # Dynamic load
    try:
        spec = importlib.util.spec_from_file_location(name, tool_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        func = getattr(mod, name)
    except AttributeError:
        tool_path.unlink(missing_ok=True)
        return f"Error: code must define a function named '{name}'."
    except Exception as e:
        tool_path.unlink(missing_ok=True)
        return f"Error loading tool: {e}"

    TOOLS[name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {name}"}

    # Update dynamic schemas for LLMs
    TOOL_SCHEMAS.clear()
    TOOL_SCHEMAS.extend(_generate_schemas())

    # Persist registry so tool survives restarts
    registry = {}
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception:
            pass
    registry[name] = {
        "path": str(tool_path),
        "documentation": documentation,
        "created": datetime.now().isoformat(),
        "errors": []
    }
    TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

    logger.info(f"Tool registered in TOOLBOX: {name}")
    return f"Tool '{name}' registered in TOOLBOX and available immediately. Documentation saved to {name}_README.md"


def _update_toolbox_readme():
    """Update the main TOOLBOX README with a list of all tools."""
    readme_content = """# TOOLBOX

This directory contains custom tools created by agents.

## Tools

"""
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
            for name, info in sorted(registry.items()):
                readme_content += f"### {name}\n"
                readme_content += f"- Created: {info.get('created', 'Unknown')}\n"
                readme_content += f"- Documentation: {name}_README.md\n"
                readme_content += f"- Description: {info.get('documentation', 'No documentation provided')[:100]}...\n\n"
        except Exception:
            readme_content += "No tools registered yet.\n"
    else:
        readme_content += "No tools registered yet.\n"

    readme_content += """
## Creating New Tools

When creating a new tool, the agent must:
1. Check if a similar tool already exists (use list_tools())
2. Provide comprehensive documentation
3. Include error handling with try-except blocks
4. Log errors using logger.error()
5. Include a proper docstring explaining usage

## Error Logging

All tools in the TOOLBOX use the standard Python logging system. Errors are logged and can be reviewed to improve tools.
"""
    
    TOOLBOX_DOCS.write_text(readme_content, encoding="utf-8")


def list_toolbox() -> str:
    """List all custom tools stored in the TOOLBOX with metadata.

    Reads the TOOLBOX registry and returns a formatted list of all
    agent-created tools, including creation date and documentation preview.
    Use this before register_tool() to check for existing similar tools.

    Returns:
        Formatted list of tool names, creation dates, and doc previews.
        'TOOLBOX is empty.' if no custom tools have been created.
        'Error listing TOOLBOX: ...' on registry read failure.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX is empty. No custom tools have been created yet."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        if not registry:
            return "TOOLBOX is empty."
        
        lines = ["[TOOLBOX] Contents:", ""]
        for name, info in sorted(registry.items()):
            lines.append(f"[TOOL] {name}")
            lines.append(f"   Created: {info.get('created', 'Unknown')}")
            lines.append(f"   Docs: {info.get('documentation', 'No documentation')[:80]}...")
            lines.append("")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error listing TOOLBOX: {e}")
        return f"Error listing TOOLBOX: {e}"


def get_tool_documentation(name: str) -> str:
    """Get the full documentation for a specific TOOLBOX tool by name.

    Reads and returns the {name}_README.md documentation file for the given tool.
    Documentation is created automatically when a tool is registered with
    register_tool(name, code, documentation).

    Args:
        name: Tool name as registered in TOOLBOX (e.g. 'calculate_sum')

    Returns:
        Full Markdown documentation string on success.
        'No documentation found for tool {name}.' if not in TOOLBOX.
        'Error reading documentation: ...' on read failure.
    """
    doc_path = TOOLBOX_DIR / f"{name}_README.md"
    if not doc_path.exists():
        return f"No documentation found for tool '{name}'. Create documentation when registering the tool."
    
    try:
        return doc_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error reading documentation for {name}: {e}")
        return f"Error reading documentation: {e}"


def load_custom_tools():
    """Load persisted custom tools from TOOLBOX at startup — called by gateway.py / cli.py."""
    if not TOOLBOX_REG.exists():
        return
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        for name, info in registry.items():
            if isinstance(info, dict):
                tool_path = Path(info.get("path", ""))
            else:
                # Handle old format where registry was just path strings
                tool_path = Path(info)
            
            if not tool_path.exists():
                logger.warning(f"Tool file missing from TOOLBOX: {tool_path}")
                continue
            try:
                spec = importlib.util.spec_from_file_location(name, tool_path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                func = getattr(mod, name)
                TOOLS[name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {name}"}
                logger.info(f"Loaded tool from TOOLBOX: {name}")
            except Exception as e:
                logger.warning(f"Failed to load tool '{name}' from TOOLBOX: {e}")
        
        # Update the TOOLBOX README
        _update_toolbox_readme()
        
        # Sync dynamic schemas for LLMs after loading all tools
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
    except Exception as e:
        logger.error(f"Error loading TOOLBOX registry: {e}")


# ── Feature 5: Agent-Initiated Scheduling ────────────────────────────────────

def _create_job_internal(task: str, delay: int, every: int, user_id: str, job_id: str) -> str:
    chat_id = _user_chat_ids.get(user_id)
    job_data = {
        "task": task,
        "user_id": user_id,
        "chat_id": chat_id,
        "delay": delay,
        "every": every
    }

    async def _job_fn(context):
        jd = context.job.data
        agent = _agent_registry.get("default")
        if not agent:
            return
        result = await agent.think(jd["task"], user_id=jd["user_id"])
        msg = f"⏰ Scheduled task '{jd['task']}' result:\n{result}"
        # Try channel-agnostic callback first (WhatsApp, future channels)
        if _notification_callback:
            try:
                import asyncio
                asyncio.ensure_future(_notification_callback(jd["user_id"], msg))
            except Exception as e:
                logger.error(f"Notification callback error: {e}")
        # Fall back to Telegram bot.send_message
        elif jd["chat_id"] and hasattr(context, 'bot'):
            await context.bot.send_message(
                chat_id=jd["chat_id"],
                text=msg
            )

    if every > 0:
        _job_queue.run_repeating(_job_fn, interval=every, first=every, name=job_id, data=job_data)
        return f"Recurring job '{job_id}' scheduled — runs every {every}s."
    else:
        _job_queue.run_once(_job_fn, when=delay, name=job_id, data=job_data)
        return f"One-shot job '{job_id}' scheduled — fires in {delay}s."


def schedule(task: str, delay: int = 0, every: int = 0, user_id: str = "default") -> str:
    """Schedule a task to run in the future, executed by the default agent."""
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."
    if delay <= 0 and every <= 0:
        return "Error: Specify 'delay' (one-shot) or 'every' (recurring) in seconds."

    job_id  = f"agent_{user_id}_{int(time.time())}"
    return _create_job_internal(task, delay, every, user_id, job_id)


def edit_schedule(job_id: str, new_task: str = "", delay: int = -1, every: int = -1) -> str:
    """Edit an active scheduled job. 
    new_task specifies the new action. delay/every > 0 reschedules it.
    """
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    
    job = jobs[0]
    data = job.data or {}
    
    final_task = new_task if new_task else data.get("task", "")
    final_delay = delay if delay > 0 else data.get("delay", 0)
    final_every = every if every > 0 else data.get("every", 0)
    user_id = data.get("user_id", "default")
    
    if delay > 0 or every > 0:
        job.schedule_removal()
        return _create_job_internal(final_task, final_delay, final_every, user_id, job_id)
    else:
        data["task"] = final_task
        return f"Job '{job_id}' updated with new task: {final_task}"


def split_schedule(job_id: str, sub_tasks_json: str) -> str:
    """Split an existing job into multiple sub-jobs.
    sub_tasks_json: A JSON array of strings, each being a new task.
    They will inherit the delay/every settings of the original job.
    """
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    
    import json
    try:
        tasks = json.loads(sub_tasks_json)
        if not isinstance(tasks, list):
            raise ToolValidationError(
                "sub_tasks_json must be a valid JSON array of strings",
                tool_name="schedule_complex_task",
                validation_errors={"sub_tasks_json": "Must be a JSON array"}
            )
    except json.JSONDecodeError as e:
        raise ToolValidationError(
            "Invalid JSON in sub_tasks_json",
            tool_name="schedule_complex_task",
            validation_errors={"sub_tasks_json": str(e)}
        )
    except ToolValidationError:
        raise
        
    job = jobs[0]
    data = job.data or {}
    delay = data.get("delay", 0)
    every = data.get("every", 0)
    user_id = data.get("user_id", "default")
    
    job.schedule_removal()
    
    results = [f"Original job '{job_id}' removed and split into {len(tasks)} tasks:"]
    for i, t in enumerate(tasks):
        new_id = f"{job_id}_sub{i}"
        res = _create_job_internal(str(t), delay, every, user_id, new_id)
        results.append(res)
        
    return "\n".join(results)


def suspend_schedule(job_id: str) -> str:
    """Suspend (pause) an active scheduled job without cancelling it."""
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    for job in jobs:
        job.enabled = False
    return f"Job '{job_id}' suspended."


def resume_schedule(job_id: str) -> str:
    """Resume a suspended scheduled job."""
    if _job_queue is None: return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs: return f"No job found with ID: {job_id}"
    for job in jobs:
        job.enabled = True
    return f"Job '{job_id}' resumed."


def cancel_schedule(job_id: str) -> str:
    """Cancel an active scheduled job by its ID."""
    if _job_queue is None:
        return "Error: Scheduler not available."
    jobs = _job_queue.get_jobs_by_name(job_id)
    if not jobs:
        return f"No job found with ID: {job_id}"
    for job in jobs:
        job.schedule_removal()
    return f"Job '{job_id}' cancelled."


def list_schedules() -> str:
    """List all currently active scheduled jobs."""
    if _job_queue is None:
        return "Error: Scheduler not available."
    jobs = _job_queue.jobs()
    if not jobs:
        return "No scheduled jobs active."
    lines = []
    for j in jobs:
        status = "🟢 Active" if j.enabled else "⏸️ Suspended"
        task_name = j.data.get("task", "Unknown Task") if j.data else "Unknown Task"
        lines.append(f"- {j.name} ({status}) | task: {task_name} | next: {j.next_t}")
    return "Active jobs:\n" + "\n".join(lines)


# ── Knowledge Tools ───────────────────────────────────────────────────────────

def write_to_knowledge(
    title: str,
    content: str,
    tags: str = "",
    observations: str = "",
    relations: str = "",
    user_id: str = "default"
) -> str:
    """
    Write a new note to the knowledge base.
    
    title: The title/name of the note (becomes permalink)
    content: Main content/description
    tags: Comma-separated list of tags (optional)
    observations: One observation per line, format: "category | content" (optional)
    relations: One relation per line, format: "relation_type | target_entity" (optional)
    user_id: User ID for multi-user isolation
    """
    try:
        # Parse tags
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        
        # Parse observations
        obs_list = []
        if observations:
            for line in observations.strip().split("\n"):
                if "|" in line:
                    category, obs_content = line.split("|", 1)
                    obs_list.append(Observation(
                        category=category.strip(),
                        content=obs_content.strip(),
                        tags=[]
                    ))
        
        # Parse relations
        rel_list = []
        if relations:
            for line in relations.strip().split("\n"):
                if "|" in line:
                    rel_type, target = line.split("|", 1)
                    rel_list.append(Relation(
                        relation_type=rel_type.strip(),
                        target=target.strip()
                    ))
        
        # Create note
        permalink = write_note(
            name=title,
            title=title,
            content=content,
            observations=obs_list,
            relations=rel_list,
            tags=tag_list,
            user_id=user_id
        )
        
        return f"✅ Knowledge note created: [{title}](memory://{permalink})"
    except Exception as e:
        logger.error(f"Failed to write knowledge: {e}")
        return f"Error writing knowledge: {e}"


def search_knowledge(query: str, limit: int = 5, user_id: str = "default") -> str:
    """
    Search the knowledge base using full-text search.
    
    query: Search query (supports FTS5 syntax: AND, OR, NOT, *)
    limit: Maximum number of results (default: 5)
    user_id: User ID for multi-user isolation
    """
    try:
        notes = search_notes(query, user_id, limit)
        
        if not notes:
            return f"No results found for: '{query}'"
        
        lines = [f"🔍 Search results for '{query}':", ""]
        
        for i, note in enumerate(notes, 1):
            lines.append(f"{i}. **{note.title}** ([{note.permalink}](memory://{note.permalink}))")
            if note.observations:
                for obs in note.observations[:2]:  # Show first 2 observations
                    lines.append(f"   - [{obs.category}] {obs.content[:80]}...")
            if note.tags:
                lines.append(f"   Tags: {', '.join(f'#{tag}' for tag in note.tags)}")
            lines.append("")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to search knowledge: {e}")
        return f"Error searching knowledge: {e}"


def read_knowledge(permalink: str, user_id: str = "default") -> str:
    """
    Read a specific knowledge note by permalink.
    
    permalink: The note's permalink/identifier
    user_id: User ID for multi-user isolation
    """
    try:
        note = read_note(permalink, user_id)
        
        if not note:
            return f"Note not found: {permalink}"
        
        lines = [
            f"# {note.title}",
            f"Permalink: {note.permalink}",
            ""
        ]
        
        if note.observations:
            lines.append("## Observations")
            for obs in note.observations:
                lines.append(f"- [{obs.category}] {obs.content}")
            lines.append("")
        
        if note.relations:
            lines.append("## Relations")
            for rel in note.relations:
                lines.append(f"- {rel.relation_type} → [[{rel.target}]]")
            lines.append("")
        
        if note.tags:
            lines.append(f"Tags: {', '.join(f'#{tag}' for tag in note.tags)}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to read knowledge: {e}")
        return f"Error reading knowledge: {e}"


def get_knowledge_context(permalink: str, depth: int = 2, user_id: str = "default") -> str:
    """
    Build context for a knowledge entity including related entities.
    
    permalink: The starting entity's permalink
    depth: How many relationship hops to include (default: 2)
    user_id: User ID for multi-user isolation
    """
    try:
        context = build_context(permalink, user_id, depth)
        return context
    except Exception as e:
        logger.error(f"Failed to build context: {e}")
        return f"Error building context: {e}"


def list_knowledge(user_id: str = "default", limit: int = 20) -> str:
    """
    List recent knowledge notes.
    
    user_id: User ID for multi-user isolation
    limit: Maximum number of notes to list
    """
    try:
        notes = list_notes(user_id)
        notes = notes[:limit]
        
        if not notes:
            return "Knowledge base is empty."
        
        lines = [f"📚 Knowledge Notes ({len(notes)} shown):", ""]
        
        for note in notes:
            lines.append(f"- **{note.title}** ([{note.permalink}](memory://{note.permalink}))")
            if note.tags:
                lines.append(f"  Tags: {', '.join(f'#{tag}' for tag in note.tags)}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to list knowledge: {e}")
        return f"Error listing knowledge: {e}"


def sync_knowledge_base(user_id: str = "default") -> str:
    """
    Synchronize the knowledge base (re-index all files).
    
    user_id: User ID for multi-user isolation
    """
    try:
        result = sync_knowledge(user_id)
        total = result['added'] + result['updated'] + result['deleted']
        return (
            f"✅ Sync complete: {total} changes\n"
            f"  Added: {result['added']}\n"
            f"  Updated: {result['updated']}\n"
            f"  Deleted: {result['deleted']}"
        )
    except Exception as e:
        logger.error(f"Failed to sync knowledge: {e}")
        return f"Error syncing knowledge: {e}"


def get_related_knowledge(permalink: str, user_id: str = "default", depth: int = 1) -> str:
    """
    Get entities related to a knowledge note.
    
    permalink: The note's permalink
    depth: Relationship depth to traverse (default: 1)
    user_id: User ID for multi-user isolation
    """
    try:
        related = get_related_entities(permalink, user_id, depth)
        
        if not related:
            return f"No related entities found for: {permalink}"
        
        lines = [f"🔗 Related to [{permalink}]:", ""]
        
        for r in related:
            lines.append(f"- {r['relation_type']} → **{r['name']}** (depth: {r['depth']})")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get related knowledge: {e}")
        return f"Error getting related knowledge: {e}"


def list_knowledge_tags(user_id: str = "default") -> str:
    """
    List all tags used in the knowledge base.
    
    user_id: User ID for multi-user isolation
    """
    try:
        tags = get_all_tags(user_id)
        
        if not tags:
            return "No tags found in knowledge base."
        
        return "🏷️ Tags:\n" + " ".join(f"#{tag}" for tag in tags)
    except Exception as e:
        logger.error(f"Failed to list tags: {e}")
        return f"Error listing tags: {e}"


# ── Agent Swarm Tools ──────────────────────────────────────────────────────────

_swarm_orchestrator = None  # Lazy-initialized orchestrator instance


def _get_swarm_orchestrator():
    """Get or create the swarm orchestrator instance."""
    global _swarm_orchestrator
    if _swarm_orchestrator is None:
        from .swarm import SwarmOrchestrator
        from .config import load_config
        config = load_config()
        _swarm_orchestrator = SwarmOrchestrator(_agent_registry, config)
    return _swarm_orchestrator


async def swarm_create(
    name: str,
    strategy: str,
    workers: str,
    coordinator: str = None,
    aggregation: str = "synthesis",
    user_id: str = "default"
) -> str:
    """Create a new agent swarm for collaborative task execution.
    
    name: A descriptive name for the swarm (e.g., "research_team", "code_reviewers")
    strategy: Execution strategy - one of: parallel, sequential, hierarchical, voting
        - parallel: All agents work simultaneously, results aggregated
        - sequential: Agents work in pipeline (output feeds to next input)
        - hierarchical: Coordinator delegates tasks to workers
        - voting: Multiple agents solve same problem, consensus wins
    workers: Comma-separated list of agent names (e.g., "agent1,agent2,agent3")
    coordinator: Coordinator agent name (required for hierarchical strategy)
    aggregation: How to combine results - one of: consensus, best_pick, concatenation, synthesis
    user_id: User identifier for multi-user isolation
    
    Returns:
        Success message with swarm ID, or error message.
    
    Example:
        swarm_create("research_team", "parallel", "agent1,agent2,agent3")
    """
    try:
        from .swarm import SwarmConfig, SwarmStrategy, AggregationMethod
        
        orchestrator = _get_swarm_orchestrator()
        
        # Parse workers list
        worker_list = [w.strip() for w in workers.split(",") if w.strip()]
        if not worker_list:
            return "Error: At least one worker agent is required"
        
        # Validate strategy
        try:
            strategy_enum = SwarmStrategy(strategy.lower())
        except ValueError:
            return f"Error: Invalid strategy '{strategy}'. Use: parallel, sequential, hierarchical, voting"
        
        # Validate aggregation
        try:
            aggregation_enum = AggregationMethod(aggregation.lower())
        except ValueError:
            return f"Error: Invalid aggregation '{aggregation}'. Use: consensus, best_pick, concatenation, synthesis"
        
        # Create config
        config = SwarmConfig(
            name=name,
            strategy=strategy_enum,
            workers=worker_list,
            coordinator=coordinator,
            aggregation_method=aggregation_enum
        )
        
        # Create swarm
        swarm_id = await orchestrator.create_swarm(config, user_id)
        
        return (
            f"✅ Swarm created successfully!\n"
            f"   ID: {swarm_id}\n"
            f"   Name: {name}\n"
            f"   Strategy: {strategy}\n"
            f"   Workers: {', '.join(worker_list)}\n"
            f"   Coordinator: {coordinator or 'N/A'}\n"
            f"   Aggregation: {aggregation}"
        )
    except ValueError as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Failed to create swarm: {e}")
        return f"Error creating swarm: {e}"


async def swarm_assign(swarm_id: str, task: str, user_id: str = "default") -> str:
    """Assign a task to a swarm for execution.
    
    swarm_id: The swarm ID returned by swarm_create()
    task: The task description/prompt for the swarm
    user_id: User identifier for multi-user isolation
    
    Returns:
        The aggregated result from all swarm agents.
    
    Example:
        swarm_assign("swarm_abc123", "Research the latest AI developments in 2024")
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        
        # Execute task
        result = await orchestrator.execute_task(swarm_id, task)
        
        # Format response
        lines = [
            f"🐝 Swarm Execution Complete",
            f"   Swarm: {result.swarm_id}",
            f"   Aggregation: {result.aggregation_method.value}",
            f"   Confidence: {result.confidence_score:.2f}",
            f"   Execution Time: {result.execution_time_seconds:.2f}s",
            f"",
            f"📊 Individual Results:",
        ]
        
        for agent_name, agent_result in result.individual_results.items():
            status = "✅" if agent_result.success else "❌"
            lines.append(f"   {status} {agent_name}: {len(agent_result.result)} chars")
        
        lines.extend([
            f"",
            f"🎯 Final Result:",
            f"{result.final_result}"
        ])
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to assign task to swarm: {e}")
        return f"Error assigning task: {e}"


def swarm_status(swarm_id: str) -> str:
    """Get the current status of a swarm.
    
    swarm_id: The swarm ID
    
    Returns:
        Status information including current state and configuration.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        info = orchestrator.get_status(swarm_id)
        
        if not info:
            return f"Swarm not found: {swarm_id}"
        
        lines = [
            f"🐝 Swarm Status: {info.name}",
            f"   ID: {info.id}",
            f"   Status: {info.status.value}",
            f"   Strategy: {info.strategy.value}",
            f"   Workers: {', '.join(info.workers)}",
            f"   Coordinator: {info.coordinator or 'N/A'}",
            f"   Aggregation: {info.aggregation_method.value}",
            f"   Created: {info.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        
        if info.completed_at:
            lines.append(f"   Completed: {info.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm status: {e}")
        return f"Error getting status: {e}"


def swarm_result(swarm_id: str) -> str:
    """Get the final result of a completed swarm execution.
    
    swarm_id: The swarm ID
    
    Returns:
        The aggregated result or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        result = orchestrator.get_result(swarm_id)
        
        if not result:
            return f"No result found for swarm: {swarm_id}"
        
        lines = [
            f"🎯 Swarm Result: {result.swarm_id}",
            f"   Aggregation Method: {result.aggregation_method.value}",
            f"   Confidence Score: {result.confidence_score:.2f}",
            f"   Execution Time: {result.execution_time_seconds:.2f}s",
            f"",
            f"📊 Individual Agent Results:",
        ]
        
        for agent_name, agent_result in result.individual_results.items():
            status = "✅" if agent_result.success else "❌"
            lines.append(f"   {status} {agent_name} ({agent_result.execution_time_seconds:.2f}s):")
            preview = agent_result.result[:200].replace('\n', ' ')
            lines.append(f"      {preview}...")
        
        lines.extend([
            f"",
            f"🎯 Final Aggregated Result:",
            f"{result.final_result}"
        ])
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm result: {e}")
        return f"Error getting result: {e}"


async def swarm_terminate(swarm_id: str) -> str:
    """Terminate a running swarm.
    
    swarm_id: The swarm ID to terminate
    
    Returns:
        Success or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        success = await orchestrator.terminate_swarm(swarm_id)
        
        if success:
            return f"✅ Swarm {swarm_id} terminated successfully"
        else:
            return f"❌ Could not terminate swarm {swarm_id} (may not be running or doesn't exist)"
    except Exception as e:
        logger.error(f"Failed to terminate swarm: {e}")
        return f"Error terminating swarm: {e}"


def swarm_list(status: str = None, user_id: str = "default") -> str:
    """List all swarms for a user.
    
    status: Optional filter - pending, running, completed, failed, terminated
    user_id: User identifier
    
    Returns:
        List of swarms with their status.
    """
    try:
        from .swarm import TaskStatus
        
        orchestrator = _get_swarm_orchestrator()
        
        # Parse status filter
        status_filter = None
        if status:
            try:
                status_filter = TaskStatus(status.lower())
            except ValueError:
                return f"Error: Invalid status '{status}'. Use: pending, running, completed, failed, terminated"
        
        swarms = orchestrator.list_swarms(user_id, status_filter)
        
        if not swarms:
            filter_str = f" with status '{status}'" if status else ""
            return f"No swarms found{filter_str}."
        
        lines = [f"🐝 Swarms ({len(swarms)} total):", ""]
        
        for swarm in swarms:
            status_icon = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "terminated": "🛑"
            }.get(swarm.status.value, "❓")
            
            lines.append(
                f"{status_icon} {swarm.name} ({swarm.id})"
            )
            lines.append(f"   Strategy: {swarm.strategy.value} | Workers: {len(swarm.workers)}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to list swarms: {e}")
        return f"Error listing swarms: {e}"


def swarm_stats(user_id: str = "default") -> str:
    """Get swarm statistics for a user.
    
    user_id: User identifier
    
    Returns:
        Statistics about swarm usage.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        stats = orchestrator.get_stats(user_id)
        
        lines = [
            f"📊 Swarm Statistics",
            f"",
            f"Total Swarms: {stats['total_swarms']}",
            f"  ⏳ Pending: {stats['pending']}",
            f"  🔄 Running: {stats['running']}",
            f"  ✅ Completed: {stats['completed']}",
            f"  ❌ Failed: {stats['failed']}",
            f"",
            f"Concurrent Slots: {stats['active_slots']}/{stats['max_concurrent']} used",
            f"Available Slots: {stats['available_slots']}",
            f"",
            f"Average Confidence: {stats['avg_confidence']:.2f}",
            f"Average Execution Time: {stats['avg_execution_time']:.2f}s"
        ]
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm stats: {e}")
        return f"Error getting stats: {e}"


# ── Tool Registry ─────────────────────────────────────────────────────────────
# NOTE: new custom tools are added to this dict at runtime by register_tool()

TOOLS: Dict[str, dict] = {
    # Core
    "shell":                {"func": shell,                "desc": "Execute a shell command"},
    "read_file":            {"func": read_file,            "desc": "Read a file from workspace"},
    "write_file":           {"func": write_file,           "desc": "Write a file to workspace"},
    # Internet & Download
    "browse":               {"func": browse,               "desc": "Browse a URL and return text content"},
    "download_file":        {"func": download_file,        "desc": "Download a file from URL to workspace"},
    # Feature 3
    "delegate":             {"func": delegate,             "desc": "Delegate a task to a named agent"},
    # Feature 4
    "list_tools":           {"func": list_tools,           "desc": "List all available tools"},
    "register_tool":        {"func": register_tool,        "desc": "Build and register a new Python tool in TOOLBOX"},
    "list_toolbox":         {"func": list_toolbox,         "desc": "List all tools stored in TOOLBOX"},
    "get_tool_documentation":{"func": get_tool_documentation,"desc": "Get documentation for a specific TOOLBOX tool"},
    # Feature 5
    "schedule":             {"func": schedule,             "desc": "Schedule a future task (delay= or every=)"},
    "edit_schedule":        {"func": edit_schedule,        "desc": "Edit an active scheduled job (new task, delay, or every)"},
    "split_schedule":       {"func": split_schedule,       "desc": "Split an existing job into multiple tasks (JSON array)"},
    "suspend_schedule":     {"func": suspend_schedule,     "desc": "Suspend (pause) an active scheduled job"},
    "resume_schedule":      {"func": resume_schedule,      "desc": "Resume a suspended scheduled job"},
    "cancel_schedule":      {"func": cancel_schedule,      "desc": "Cancel a scheduled job by ID"},
    "list_schedules":       {"func": list_schedules,       "desc": "List all active scheduled jobs"},
    # Knowledge
    "write_to_knowledge":   {"func": write_to_knowledge,   "desc": "Write a note to the knowledge base"},
    "search_knowledge":     {"func": search_knowledge,     "desc": "Search knowledge base with FTS5"},
    "read_knowledge":       {"func": read_knowledge,       "desc": "Read a specific knowledge note"},
    "list_knowledge":       {"func": list_knowledge,       "desc": "List all knowledge notes"},
    "get_knowledge_context":{"func": get_knowledge_context,"desc": "Build context from knowledge graph"},
    "get_related_knowledge":{"func": get_related_knowledge,"desc": "Get related entities from knowledge base"},
    "sync_knowledge_base":  {"func": sync_knowledge_base,  "desc": "Sync knowledge base with files"},
    "list_knowledge_tags":  {"func": list_knowledge_tags,  "desc": "List all knowledge tags"},
    # Agent Swarm Tools
    "swarm_create":         {"func": swarm_create,         "desc": "Create a new agent swarm"},
    "swarm_assign":         {"func": swarm_assign,         "desc": "Assign a task to a swarm"},
    "swarm_status":         {"func": swarm_status,         "desc": "Get swarm status"},
    "swarm_result":         {"func": swarm_result,         "desc": "Get swarm execution result"},
    "swarm_terminate":      {"func": swarm_terminate,      "desc": "Terminate a running swarm"},
    "swarm_list":           {"func": swarm_list,           "desc": "List all swarms"},
    "swarm_stats":          {"func": swarm_stats,          "desc": "Get swarm statistics"},
}

import inspect

def _generate_schemas() -> list[dict]:
    schemas = []
    for name, info in TOOLS.items():
        func = info["func"]
        try:
            sig = inspect.signature(func)
        except ValueError:
            continue
            
        params = {}
        required = []
        for param_name, param in sig.parameters.items():
            if param_name in ("user_id", "_depth", "context"):
                continue
                
            ptype = "string"
            if param.annotation == int: ptype = "integer"
            elif param.annotation == bool: ptype = "boolean"
            elif param.annotation == float: ptype = "number"
            
            params[param_name] = {"type": ptype, "description": ""}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
                
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": info["desc"] or "",
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required
                }
            }
        })
    return schemas

TOOL_SCHEMAS = _generate_schemas()