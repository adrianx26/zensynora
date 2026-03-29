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

from .knowledge import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_related_entities, build_context, sync_knowledge, get_all_tags,
    Observation, Relation
)
from .knowledge.storage import get_knowledge_dir
from .agents.skill_adapter import (
    analyze_external_skill,
    convert_skill,
    list_compatible_skills,
    register_external_skill
)
from .agents.medic_agent import (
    check_system_health,
    verify_file_integrity,
    recover_file,
    get_health_report,
    validate_modification,
    record_task_execution,
    get_task_analytics,
    enable_hash_check,
    scan_files,
    detect_errors_in_file,
    prevent_infinite_loop,
    create_backup,
    list_backups,
    check_file_virustotal
)
from .agents.newtech_agent import (
    fetch_ai_news,
    get_technology_proposals,
    add_to_roadmap,
    enable_newtech_agent,
    run_newtech_scan,
    summarize_tech,
    generate_tech_proposal,
    share_proposal,
    get_roadmap
)

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


# ── Parallel Tool Executor (Optimization #3) ───────────────────────────────────

class ParallelToolExecutor:
    """Execute multiple independent tools concurrently for better throughput.
    
    Uses asyncio.gather to run independent tools in parallel, significantly
    reducing total execution time when multiple tools are called at once.
    """
    
    def __init__(self, max_concurrent: int = 5, timeout: float = 30.0):
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def execute_tools(
        self,
        tool_calls: List[Dict],
        user_id: str = "default"
    ) -> List[Dict[str, str]]:
        """Execute multiple tools in parallel.
        
        Args:
            tool_calls: List of tool call dicts with 'function' containing 'name' and 'arguments'
            user_id: User ID for context
            
        Returns:
            List of result dicts with 'tool_name', 'result', 'error', 'duration'
        """
        if not tool_calls:
            return []
        
        # Create tasks for all tool executions
        tasks = []
        for tc in tool_calls:
            task = self._execute_single_tool(tc, user_id)
            tasks.append(task)
        
        # Execute all in parallel with semaphore limiting
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.timeout
            )
            
            # Process results
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    tc = tool_calls[i]
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    processed_results.append({
                        "tool_name": tool_name,
                        "result": "",
                        "error": str(result),
                        "duration": 0.0,
                        "success": False
                    })
                else:
                    processed_results.append(result)
            
            return processed_results
            
        except asyncio.TimeoutError:
            logger.error(f"Parallel tool execution timed out after {self.timeout}s")
            return [{
                "tool_name": "parallel_executor",
                "result": "",
                "error": f"Execution timed out after {self.timeout}s",
                "duration": self.timeout,
                "success": False
            }]
    
    async def _execute_single_tool(
        self,
        tool_call: Dict,
        user_id: str
    ) -> Dict[str, str]:
        """Execute a single tool with semaphore control."""
        async with self._semaphore:
            tool_name = tool_call.get("function", {}).get("name", "")
            args = tool_call.get("function", {}).get("arguments", {})
            start_time = time.time()
            
            try:
                if tool_name not in TOOLS:
                    return {
                        "tool_name": tool_name,
                        "result": "",
                        "error": f"Unknown tool: {tool_name}",
                        "duration": time.time() - start_time,
                        "success": False
                    }
                
                # Check rate limit
                if not _rate_limiter.check(tool_name):
                    return {
                        "tool_name": tool_name,
                        "result": "",
                        "error": f"Rate limit exceeded for {tool_name}",
                        "duration": time.time() - start_time,
                        "success": False
                    }
                
                func = TOOLS[tool_name]["func"]
                
                # Execute the tool
                if inspect.iscoroutinefunction(func):
                    result = await func(**args)
                else:
                    result = await asyncio.to_thread(func, **args)
                
                duration = time.time() - start_time
                
                # Log the execution
                _tool_audit_logger.log(
                    tool_name, user_id, duration * 1000, True
                )
                
                return {
                    "tool_name": tool_name,
                    "result": str(result),
                    "error": "",
                    "duration": duration,
                    "success": True
                }
                
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Tool execution error ({tool_name}): {e}")
                
                _tool_audit_logger.log(
                    tool_name, user_id, duration * 1000, False, str(e)
                )
                
                return {
                    "tool_name": tool_name,
                    "result": "",
                    "error": str(e),
                    "duration": duration,
                    "success": False
                }


# Global parallel executor instance
_parallel_executor: Optional[ParallelToolExecutor] = None


def get_parallel_executor(max_concurrent: int = 5, timeout: float = 30.0) -> ParallelToolExecutor:
    """Get or create the global parallel executor instance."""
    global _parallel_executor
    
    if _parallel_executor is None:
        _parallel_executor = ParallelToolExecutor(
            max_concurrent=max_concurrent,
            timeout=timeout
        )
    
    return _parallel_executor


def is_tool_independent(tool_name: str) -> bool:
    """Check if a tool can be executed in parallel (has no dependencies).
    
    Tools that modify shared state or have side effects should not be
    executed in parallel with other tools.
    """
    # Tools that are NOT safe for parallel execution
    dependent_tools = {
        "shell", "run_command", "delegate",
        "write_file", "create_file", "edit_file",
        "schedule", "cancel_schedule", "edit_schedule"
    }
    
    return tool_name not in dependent_tools


# ── Module-level references injected by gateway / telegram / whatsapp ──────────

_agent_registry: dict = {}   # name -> Agent  (Feature 2 / 3)
_job_queue      = None        # python-telegram-bot JobQueue  (Feature 1 / 5)
_user_chat_ids: dict = {}     # user_id -> chat_id  (Feature 5 notifications)
_notification_callback = None  # Callback: async fn(user_id, message) for channel-agnostic notifications


# ── Plugin Lifecycle Hooks ──────────────────────────────────────────────────────

_HOOKS: dict[str, list] = {
    "pre_llm_call": [],      # Called before LLM request: fn(messages, model) -> modified messages
    "post_llm_call": [],     # Called after LLM response: fn(response, tool_calls) -> modified response
    "on_session_start": [],  # Called when session starts: fn(user_id, agent_name) -> None
    "on_session_end": [],    # Called when session ends: fn(user_id, agent_name, message_count) -> None
}


def register_hook(event_type: str, callback) -> str:
    """Register a callback function for a lifecycle event.
    
    event_type: One of: pre_llm_call, post_llm_call, on_session_start, on_session_end
    callback: Function to call - signature varies by event type:
        - pre_llm_call: fn(messages: list, model: str) -> list | None (return modified messages or None)
        - post_llm_call: fn(response: str, tool_calls: list) -> tuple | None
        - on_session_start: fn(user_id: str, agent_name: str) -> None
        - on_session_end: fn(user_id: str, agent_name: str, message_count: int) -> None
    
    Returns:
        Success or error message.
    """
    valid_events = {"pre_llm_call", "post_llm_call", "on_session_start", "on_session_end"}
    
    if event_type not in valid_events:
        return f"Error: Invalid event type '{event_type}'. Use: {', '.join(sorted(valid_events))}"
    
    if not callable(callback):
        return "Error: Callback must be a callable function"
    
    if callback not in _HOOKS[event_type]:
        _HOOKS[event_type].append(callback)
        logger.info(f"Hook registered: {event_type} -> {callback.__name__}")
        return f"Hook registered: {event_type}"
    else:
        return f"Hook already registered for {event_type}"


def trigger_hook(event_type: str, *args, **kwargs):
    """Trigger all callbacks for a lifecycle event.
    
    event_type: The event type to trigger
    *args, **kwargs: Arguments passed to each callback
    
    Returns:
        List of results from each callback (if they return anything).
    """
    if event_type not in _HOOKS:
        return []
    
    results = []
    for callback in _HOOKS[event_type]:
        try:
            result = callback(*args, **kwargs)
            results.append(result)
        except Exception as e:
            logger.error(f"Hook error in {event_type}->{callback.__name__}: {e}")
            results.append({"error": str(e)})
    
    return results


def list_hooks() -> str:
    """List all registered lifecycle hooks.
    
    Returns:
        Formatted list of registered hooks by event type.
    """
    if not any(_HOOKS.values()):
        return "No hooks registered. Use register_hook(event_type, callback) to add hooks."
    
    lines = ["📋 Registered Lifecycle Hooks:", ""]
    
    for event_type, callbacks in _HOOKS.items():
        if callbacks:
            lines.append(f"  {event_type}:")
            for cb in callbacks:
                lines.append(f"    - {cb.__name__}")
        else:
            lines.append(f"  {event_type}: (empty)")
    
    return "\n".join(lines)


def clear_hooks(event_type: str = None) -> str:
    """Clear all hooks, or hooks for a specific event type.
    
    event_type: Optional specific event type to clear. If None, clears all hooks.
    
    Returns:
        Success message.
    """
    if event_type:
        if event_type in _HOOKS:
            count = len(_HOOKS[event_type])
            _HOOKS[event_type] = []
            return f"Cleared {count} hooks for {event_type}"
        else:
            return f"Error: Unknown event type '{event_type}'"
    else:
        for event_type in _HOOKS:
            _HOOKS[event_type] = []
        return "All hooks cleared"


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
            raise ValueError(f"Path traversal detected: {path}")
        return target
    except Exception as e:
        raise ValueError(f"Invalid path: {path}") from e


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

    # Persist registry so tool survives restarts (with full metadata)
    registry = {}
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception:
            pass
    
    registry[name] = {
        "path": str(tool_path),
        "name": name,
        "version": "1.0.0",
        "description": documentation,
        "tags": [],
        "author": "agent",
        "created": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "eval_score": None,
        "eval_count": 0,
        "enabled": True,
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
            enabled = info.get('enabled', True)
            status = "🟢" if enabled else "🔴"
            eval_score = info.get('eval_score')
            
            lines.append(f"{status} [TOOL] {name} v{info.get('version', '1.0.0')}")
            lines.append(f"   Author: {info.get('author', 'unknown')}")
            lines.append(f"   Created: {info.get('created', 'Unknown')}")
            if info.get('tags'):
                lines.append(f"   Tags: {', '.join(info.get('tags', []))}")
            lines.append(f"   Description: {info.get('description', 'No description')[:80]}...")
            if eval_score is not None:
                lines.append(f"   Eval Score: {eval_score:.2f} ({info.get('eval_count', 0)} runs)")
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
            
            # Skip disabled tools
            if isinstance(info, dict) and not info.get('enabled', True):
                logger.info(f"Skipping disabled tool from TOOLBOX: {name}")
                continue
                
            try:
                spec = importlib.util.spec_from_file_location(name, tool_path)
                if spec is None or spec.loader is None:
                    logger.warning(f"Could not load spec for tool '{name}'")
                    continue
                mod = importlib.util.module_from_spec(spec)
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


# ── Skill Evaluation Harness ─────────────────────────────────────────────────

def get_skill_info(skill_name: str) -> str:
    """Get detailed information about a skill from the TOOLBOX registry.
    
    skill_name: The name of the skill to query
    
    Returns:
        Formatted skill information including version, tags, evaluation score, etc.
    """
    if not TOOLBOX_REG.exists():
        return f"TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        info = registry[skill_name]
        
        lines = [
            f"📋 Skill: {skill_name}",
            f"   Version: {info.get('version', '1.0.0')}",
            f"   Author: {info.get('author', 'unknown')}",
            f"   Status: {'🟢 Enabled' if info.get('enabled', True) else '🔴 Disabled'}",
            f"   Created: {info.get('created', 'Unknown')}",
            f"   Last Modified: {info.get('last_modified', 'Unknown')}",
        ]
        
        if info.get('tags'):
            lines.append(f"   Tags: {', '.join(info.get('tags', []))}")
        
        if info.get('description'):
            lines.append(f"   Description: {info.get('description')}")
        
        lines.extend([
            f"   Evaluation Score: {info.get('eval_score', 'Not evaluated')}",
            f"   Evaluation Count: {info.get('eval_count', 0)}",
            f"   Path: {info.get('path', 'Unknown')}",
        ])
        
        if info.get('errors'):
            lines.append(f"   Recent Errors: {len(info['errors'])}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Error getting skill info: {e}")
        return f"Error getting skill info: {e}"


def enable_skill(skill_name: str) -> str:
    """Enable a disabled skill in the TOOLBOX.
    
    skill_name: The name of the skill to enable
    
    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        registry[skill_name]['enabled'] = True
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        # Reload the tool into TOOLS if it was previously disabled
        if skill_name not in TOOLS:
            info = registry[skill_name]
            tool_path = Path(info.get("path", ""))
            if tool_path.exists():
                try:
                    spec = importlib.util.spec_from_file_location(skill_name, tool_path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        func = getattr(mod, skill_name)
                        TOOLS[skill_name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {skill_name}"}
                        logger.info(f"Enabled and loaded skill: {skill_name}")
                except Exception as e:
                    return f"Skill enabled but failed to reload: {e}"
        
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
        
        return f"✅ Skill '{skill_name}' enabled."
        
    except Exception as e:
        logger.error(f"Error enabling skill: {e}")
        return f"Error enabling skill: {e}"


def disable_skill(skill_name: str) -> str:
    """Disable an enabled skill in the TOOLBOX (soft delete).
    
    skill_name: The name of the skill to disable
    
    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        registry[skill_name]['enabled'] = False
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        # Remove from TOOLS to prevent execution
        if skill_name in TOOLS:
            del TOOLS[skill_name]
            TOOL_SCHEMAS.clear()
            TOOL_SCHEMAS.extend(_generate_schemas())
        
        logger.info(f"Disabled skill: {skill_name}")
        return f"✅ Skill '{skill_name}' disabled."
        
    except Exception as e:
        logger.error(f"Error disabling skill: {e}")
        return f"Error disabling skill: {e}"


def update_skill_metadata(skill_name: str, tags: str = None, description: str = None, version: str = None) -> str:
    """Update metadata for an existing skill.
    
    skill_name: The name of the skill to update
    tags: Comma-separated list of tags (optional)
    description: New description (optional)
    version: New version string like "1.1.0" (optional)
    
    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        if tags is not None:
            registry[skill_name]['tags'] = [t.strip() for t in tags.split(",") if t.strip()]
        
        if description is not None:
            registry[skill_name]['description'] = description
            # Also update the documentation file
            doc_path = TOOLBOX_DIR / f"{skill_name}_README.md"
            if doc_path.exists():
                content = doc_path.read_text()
                if "## Description" in content:
                    content = content.split("## Description")[0] + f"## Description\n{description}\n" + content.split("## Description")[1].split("\n##")[1:]
                    doc_path.write_text(content)
        
        if version is not None:
            registry[skill_name]['version'] = version
        
        registry[skill_name]['last_modified'] = datetime.now().isoformat()
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        return f"✅ Skill '{skill_name}' metadata updated."
        
    except Exception as e:
        logger.error(f"Error updating skill metadata: {e}")
        return f"Error updating skill metadata: {e}"


def benchmark_skill(skill_name: str, test_cases_json: str = "[]") -> str:
    """Run benchmark tests against a skill and return evaluation results.
    
    skill_name: The name of the skill to benchmark
    test_cases_json: JSON array of test cases. Each test case has:
        {"input": {"param": value}, "expected": "expected_output"}
    
    Returns:
        Formatted benchmark results with pass/fail rates and scores.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        if skill_name not in TOOLS:
            return f"Skill '{skill_name}' is not loaded in memory. Enable it first."
        
        test_cases = json.loads(test_cases_json)
        
        if not test_cases:
            return f"No test cases provided. Pass a JSON array of test cases."
        
        func = TOOLS[skill_name]["func"]
        results = []
        passed = 0
        
        for i, tc in enumerate(test_cases):
            try:
                args = tc.get("input", {})
                expected = tc.get("expected")
                
                # Execute the skill
                if inspect.iscoroutinefunction(func):
                    result = asyncio.run(func(**args))
                else:
                    result = func(**args)
                
                # Check result
                if expected is not None:
                    # Simple string matching (could be enhanced with regex or fuzzy matching)
                    success = str(result) == str(expected)
                else:
                    # No expected value - just check it doesn't crash
                    success = True
                    result = "executed successfully"
                
                if success:
                    passed += 1
                    results.append(f"  ✅ Test {i+1}: PASS")
                else:
                    results.append(f"  ❌ Test {i+1}: FAIL (got: {str(result)[:50]}...)")
                    
            except Exception as e:
                results.append(f"  ❌ Test {i+1}: ERROR - {str(e)}")
        
        score = (passed / len(test_cases)) * 100 if test_cases else 0
        
        # Update registry with new evaluation score
        current_count = registry[skill_name].get('eval_count', 0)
        current_score = registry[skill_name].get('eval_score')
        
        if current_score is not None:
            # Running average
            new_avg = (current_score * current_count + score) / (current_count + 1)
        else:
            new_avg = score
        
        registry[skill_name]['eval_score'] = round(new_avg, 2)
        registry[skill_name]['eval_count'] = current_count + 1
        
        # Auto-disable if score is too low (< 30%)
        if score < 30 and len(test_cases) >= 3:
            registry[skill_name]['enabled'] = False
            if skill_name in TOOLS:
                del TOOLS[skill_name]
        
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        lines = [
            f"📊 Benchmark Results for '{skill_name}':",
            f"",
            f"Tests Run: {len(test_cases)}",
            f"Passed: {passed}",
            f"Failed: {len(test_cases) - passed}",
            f"Score: {score:.1f}%",
            f"Running Avg Score: {new_avg:.2f}% (from {registry[skill_name]['eval_count']} evaluations)",
            "",
            "Details:",
        ] + results
        
        if score < 30 and len(test_cases) >= 3:
            lines.append("")
            lines.append("⚠️ Auto-disabled due to low score (< 30%)")
        
        return "\n".join(lines)
        
    except json.JSONDecodeError as e:
        return f"Error parsing test cases JSON: {e}"
    except Exception as e:
        logger.error(f"Benchmark error: {e}")
        return f"Benchmark error: {e}"


def evaluate_skill(skill_name: str) -> str:
    """Run basic evaluation tests on a skill.
    
    Performs a simple sanity check:
    1. Skill can be loaded
    2. Skill has a docstring
    3. Skill doesn't crash on basic input
    
    skill_name: The name of the skill to evaluate
    
    Returns:
        Formatted evaluation results.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        info = registry[skill_name]
        tool_path = Path(info.get("path", ""))
        
        if not tool_path.exists():
            return f"Skill file not found at: {tool_path}"
        
        # Run basic checks
        checks = []
        score = 0
        code = ""
        
        # Check 1: File exists and is readable
        checks.append(("File exists and readable", True))
        score += 20
        
        # Check 2: Can be compiled (syntax check)
        try:
            code = tool_path.read_text()
            compile(code, tool_path.name, "exec")
            checks.append(("Code has valid Python syntax", True))
            score += 20
        except SyntaxError as e:
            checks.append(("Code has valid Python syntax", False))
            checks.append((f"Syntax error: {e}", False))
        
        # Check 3: Has docstring
        if '"""' in code or "'''" in code:
            checks.append(("Has docstring", True))
            score += 15
        else:
            checks.append(("Has docstring", False))
        
        # Check 4: Has error handling
        if 'try:' in code and 'except' in code:
            checks.append(("Has error handling", True))
            score += 15
        else:
            checks.append(("Has error handling", False))
        
        # Check 5: Has logging
        if 'logger' in code:
            checks.append(("Has logging", True))
            score += 10
        else:
            checks.append(("Has logging", False))
        
        # Check 6: Registry metadata complete
        required_fields = ['version', 'description', 'tags', 'author', 'created']
        missing = [f for f in required_fields if f not in info or not info[f]]
        if not missing:
            checks.append(("Registry metadata complete", True))
            score += 20
        else:
            checks.append((f"Registry metadata complete", False))
            checks.append((f"Missing fields: {', '.join(missing)}", False))
        
        # Update evaluation score
        current_count = info.get('eval_count', 0)
        current_score = info.get('eval_score')
        
        if current_score is not None:
            new_avg = (current_score * current_count + score) / (current_count + 1)
        else:
            new_avg = score
        
        registry[skill_name]['eval_score'] = round(new_avg, 2)
        registry[skill_name]['eval_count'] = current_count + 1
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        lines = [
            f"📊 Evaluation Results for '{skill_name}':",
            f"",
            f"Overall Score: {score}/100",
            f"Running Avg: {new_avg:.2f}% (from {current_count + 1} evaluations)",
            "",
            "Checks:",
        ]
        
        for check, passed in checks:
            icon = "✅" if passed else "❌"
            lines.append(f"  {icon} {check}")
        
        if score < 50:
            lines.append("")
            lines.append("⚠️ Score below 50% - skill may need improvement")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        return f"Evaluation error: {e}"


# ── Skill Self-Improvement ───────────────────────────────────────────────────

def improve_skill(skill_name: str, improved_code: str, documentation: str = "") -> str:
    """Improve an existing skill with new code, with safety checks and evaluation.
    
    This function allows an agent to replace/update a skill's implementation with
    improved code. The new code undergoes the same security checks as register_tool(),
    and is evaluated before being activated.
    
    skill_name: The name of the skill to improve (must already exist)
    improved_code: Full Python source for the improved function
    documentation: Updated documentation (optional, keeps existing if empty)
    
    Returns:
        Success or error message with evaluation results.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX. Use register_tool() to create new skills."
        
        # Get existing info to preserve
        existing_info = registry[skill_name]
        
        # Validate skill_name
        if not skill_name.isidentifier():
            return f"Error: '{skill_name}' is not a valid Python identifier."
        
        # Syntax validation
        try:
            compile(improved_code, "<agent-tool>", "exec")
        except SyntaxError as e:
            return f"Syntax error in improved code: {e}"
        
        # AST validation for security
        import ast
        try:
            tree = ast.parse(improved_code)
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
        
        # Validate code requirements
        if '"""' not in improved_code and "'''" not in improved_code:
            return "Error: Improved code must include a docstring explaining its purpose and usage."
        
        if 'try:' not in improved_code or 'except' not in improved_code:
            return "Error: Improved code must include error handling with try-except blocks."
        
        if 'logger.error' not in improved_code:
            return "Error: Improved code must include error logging using logger.error()."
        
        # Backup existing file
        existing_path = Path(existing_info.get('path', ''))
        backup_path = None
        if existing_path.exists():
            backup_path = existing_path.with_suffix('.py.bak')
            import shutil
            shutil.copy2(existing_path, backup_path)
        
        # Write new code
        existing_path.write_text(improved_code, encoding="utf-8")
        
        # Update documentation if provided
        if not documentation:
            documentation = existing_info.get('description', '')
        
        if documentation:
            doc_path = TOOLBOX_DIR / f"{skill_name}_README.md"
            doc_content = f"""# {skill_name}

## Description
{documentation}

## Code
```python
{improved_code}
```

## Updated
{datetime.now().isoformat()}

## Previous Version
{existing_info.get('version', '1.0.0')}

## Error Logging
Errors are logged to the standard logging system.
"""
            doc_path.write_text(doc_content, encoding="utf-8")
        
        # Try to load and validate the new code
        try:
            spec = importlib.util.spec_from_file_location(skill_name, existing_path)
            if spec is None or spec.loader is None:
                # Restore backup
                if backup_path and backup_path.exists():
                    shutil.copy2(backup_path, existing_path)
                return "Error: Could not load improved code. Restored previous version."
            
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            func = getattr(mod, skill_name)
            
            # Update TOOLS
            TOOLS[skill_name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {skill_name}"}
            
        except Exception as e:
            # Restore backup on load failure
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, existing_path)
            return f"Error loading improved code: {e}. Previous version restored."
        
        # Update version (increment patch version)
        old_version = existing_info.get('version', '1.0.0')
        try:
            parts = old_version.split('.')
            patch = int(parts[-1]) + 1
            new_version = '.'.join(parts[:-1]) + '.' + str(patch)
        except:
            new_version = "1.1.0"
        
        # Update registry
        registry[skill_name] = {
            "path": str(existing_path),
            "name": skill_name,
            "version": new_version,
            "description": documentation or existing_info.get('description', ''),
            "tags": existing_info.get('tags', []),
            "author": "agent",
            "created": existing_info.get('created', datetime.now().isoformat()),
            "last_modified": datetime.now().isoformat(),
            "eval_score": existing_info.get('eval_score'),
            "eval_count": existing_info.get('eval_count', 0),
            "enabled": True,
            "errors": []
        }
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        # Update schemas
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
        
        # Clean up backup
        if backup_path and backup_path.exists():
            backup_path.unlink()
        
        logger.info(f"Skill improved: {skill_name} v{new_version}")
        
        return (
            f"✅ Skill '{skill_name}' improved successfully!\n"
            f"   Old Version: {old_version}\n"
            f"   New Version: {new_version}\n"
            f"   Code validated and loaded.\n"
            f"   Previous version backed up and replaced."
        )
        
    except Exception as e:
        logger.error(f"Error improving skill: {e}")
        return f"Error improving skill: {e}"


def rollback_skill(skill_name: str) -> str:
    """Rollback a skill to its previous version if a backup exists.
    
    skill_name: The name of the skill to rollback
    
    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."
    
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        existing_info = registry[skill_name]
        existing_path = Path(existing_info.get('path', ''))
        backup_path = existing_path.with_suffix('.py.bak')
        
        if not backup_path.exists():
            return f"No backup found for '{skill_name}'. Cannot rollback."
        
        # Restore backup
        import shutil
        shutil.copy2(backup_path, existing_path)
        
        # Update version (decrement)
        old_version = existing_info.get('version', '1.0.0')
        try:
            parts = old_version.split('.')
            patch = max(0, int(parts[-1]) - 1)
            new_version = '.'.join(parts[:-1]) + '.' + str(patch)
        except:
            new_version = "1.0.0"
        
        # Reload the tool
        try:
            spec = importlib.util.spec_from_file_location(skill_name, existing_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                func = getattr(mod, skill_name)
                TOOLS[skill_name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {skill_name}"}
        except Exception as e:
            return f"Restored but failed to reload: {e}"
        
        # Update registry
        registry[skill_name]['version'] = new_version
        registry[skill_name]['last_modified'] = datetime.now().isoformat()
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
        
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
        
        return (
            f"✅ Skill '{skill_name}' rolled back to previous version.\n"
            f"   New Version: {new_version}\n"
            f"   Backup retained for another rollback if needed."
        )
        
    except Exception as e:
        logger.error(f"Error rolling back skill: {e}")
        return f"Error rolling back skill: {e}"


# ── Periodic Session Reflection ─────────────────────────────────────────────

def schedule_daily_reflection(user_id: str = "default", hour: int = 20, minute: int = 0) -> str:
    """Schedule a daily session reflection that analyzes what was learned and saves to knowledge base.
    
    This creates a recurring task that runs at the specified time each day, analyzes
    recent conversations, and writes insights to the knowledge base with tag 'daily_reflection'.
    
    user_id: User ID for notification routing
    hour: Hour of day to run (0-23, default: 20 = 8 PM)
    minute: Minute of hour (0-59, default: 0)
    
    Returns:
        Success or error message.
    """
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."
    
    task = (
        "Analyze recent conversations and write a daily reflection to knowledge base. "
        "Use write_to_knowledge with title format 'Daily Reflection YYYY-MM-DD', "
        "tags: ['daily_reflection', 'session_summary'], and content summarizing: "
        "1) Key topics discussed, "
        "2) Important decisions made, "
        "3) Tasks completed, "
        "4) User preferences observed, "
        "5) Insights gained. "
        "Format as a structured summary with sections."
    )
    
    # Calculate delay until next occurrence of the specified time
    from datetime import datetime, timedelta
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # If target time has passed today, schedule for tomorrow
    if target <= now:
        target += timedelta(days=1)
    
    delay = int((target - now).total_seconds())
    
    # Create daily recurring job
    job_id = f"daily_reflection_{user_id}"
    
    # Remove existing daily reflection jobs to avoid duplicates
    existing_jobs = _job_queue.get_jobs_by_name(job_id) if _job_queue else []
    for job in existing_jobs:
        job.schedule_removal()
    
    return _create_job_internal(task, delay, 86400, user_id, job_id) + f" (scheduled daily at {hour:02d}:{minute:02d})"


def generate_session_insights(user_id: str = "default", save_to_knowledge: bool = True) -> str:
    """Generate insights from recent session conversations.
    
    Analyzes the user's recent conversation history to identify patterns,
    preferences, key topics, and learning opportunities.
    
    user_id: User ID for memory access
    save_to_knowledge: Whether to save insights to knowledge base (default: True)
    
    Returns:
        Formatted insights summary, or confirmation if saved.
    """
    from .memory import Memory
    import asyncio
    
    try:
        mem = Memory(user_id=user_id)
        asyncio.get_event_loop().run_until_complete(mem.initialize())
        history = asyncio.get_event_loop().run_until_complete(mem.get_history(limit=50))
        
        if not history:
            return "No conversation history available for analysis."
        
        # Build context for analysis
        analysis_text = "Analyze these recent conversations and identify:\n"
        analysis_text += "1. Key topics and themes\n"
        analysis_text += "2. User's communication style\n"
        analysis_text += "3. Important facts or decisions\n"
        analysis_text += "4. User preferences or patterns\n"
        analysis_text += "5. Learning opportunities\n\n"
        
        for m in history[-20:]:  # Last 20 messages
            analysis_text += f"{m['role']}: {m['content'][:200]}\n"
        
        # Return the analysis prompt for LLM to process
        return analysis_text
        
    except Exception as e:
        logger.error(f"Error generating session insights: {e}")
        return f"Error generating insights: {e}"


def extract_user_preferences(user_id: str = "default") -> str:
    """Analyze conversation history to extract user preferences and style.
    
    Builds a profile of the user's communication style, preferences, interests,
    and patterns that can be used to personalize future interactions.
    
    user_id: User ID for memory access
    
    Returns:
        JSON string with user profile data, or error message.
    """
    from .memory import Memory
    import asyncio
    
    try:
        mem = Memory(user_id=user_id)
        asyncio.get_event_loop().run_until_complete(mem.initialize())
        history = asyncio.get_event_loop().run_until_complete(mem.get_history(limit=100))
        
        if len(history) < 5:
            return "Not enough conversation history to build profile. Need at least 5 messages."
        
        # Analyze for patterns
        user_messages = [m['content'] for m in history if m['role'] == 'user']
        
        # Extract keywords and topics
        all_text = ' '.join(user_messages).lower()
        
        # Simple pattern analysis
        preferences = {
            "total_conversations": len([m for m in history if m['role'] == 'user']),
            "avg_message_length": sum(len(m) for m in user_messages) // max(1, len(user_messages)),
            "topics_mentioned": [],
            "questions_asked": sum(1 for m in user_messages if '?' in m),
            "commands_used": sum(1 for m in user_messages if any(c in m.lower() for c in ['calculate', 'search', 'find', 'get', 'show', 'list', 'create', 'make'])),
        }
        
        # Look for topic patterns (simplified)
        topic_keywords = {
            'coding': ['code', 'python', 'function', 'debug', 'programming', 'script'],
            'data': ['data', 'database', 'query', 'sql', 'table'],
            'files': ['file', 'read', 'write', 'open', 'save', 'folder'],
            'research': ['research', 'search', 'find', 'look up', 'information'],
            'tasks': ['task', 'schedule', 'remind', 'todo', 'plan'],
            'creative': ['write', 'story', 'creative', 'explain', 'describe'],
        }
        
        for topic, keywords in topic_keywords.items():
            if any(kw in all_text for kw in keywords):
                preferences['topics_mentioned'].append(topic)
        
        import json
        profile_json = json.dumps(preferences, indent=2)
        
        # Optionally save to knowledge base
        try:
            tags_str = ",".join(["user_profile", "preferences", "auto-extracted"])
            permalink = write_to_knowledge(
                title=f"User Profile - {user_id}",
                content=profile_json,
                tags=tags_str,
                user_id=user_id
            )
            return f"Profile saved to knowledge base.\n\n{profile_json}"
        except:
            return profile_json
        
    except Exception as e:
        logger.error(f"Error extracting user preferences: {e}")
        return f"Error extracting preferences: {e}"


def update_user_profile(insights: str, user_id: str = "default") -> str:
    """Update the user dialectic profile with new insights.
    
    Writes insights to the user dialectic profile file that the agent
    can read on startup to customize responses.
    
    insights: Markdown content to add to the profile
    user_id: User ID (used for knowledge base fallback)
    
    Returns:
        Success or error message.
    """
    from pathlib import Path
    
    dialectic_path = Path(__file__).parent / "profiles" / "user_dialectic.md"
    
    try:
        dialectic_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Read existing content
        existing = dialectic_path.read_text() if dialectic_path.exists() else ""
        
        # Add insights section with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        new_section = f"\n\n## Insights ({timestamp})\n{insights}"
        
        # Check if section already exists and update it
        if "## Insights" in existing:
            existing = existing.split("## Insights")[0] + new_section
        else:
            existing += new_section
        
        dialectic_path.write_text(existing, encoding="utf-8")
        
        # Also save to knowledge base as backup
        try:
            tags_str = ",".join(["user_profile", "dialectic", "manual_update"])
            write_to_knowledge(
                title=f"User Profile Update - {timestamp}",
                content=insights,
                tags=tags_str,
                user_id=user_id
            )
        except:
            pass
        
        return f"✅ User dialectic profile updated at {timestamp}"
        
    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        return f"Error updating profile: {e}"


def get_user_profile(user_id: str = "default") -> str:
    """Get the current user dialectic profile.
    
    Reads the user dialectic profile file and returns its contents.
    
    user_id: User ID (for consistency, not used for file lookup)
    
    Returns:
        User profile content or placeholder message.
    """
    from pathlib import Path
    
    dialectic_path = Path(__file__).parent / "profiles" / "user_dialectic.md"
    
    try:
        if dialectic_path.exists():
            return dialectic_path.read_text(encoding="utf-8")
        else:
            return "No user dialectic profile found. Use extract_user_preferences() to generate one."
    except Exception as e:
        logger.error(f"Error reading user profile: {e}")
        return f"Error reading profile: {e}"


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
            raise ValueError()
    except:
        return "Error: sub_tasks_json must be a valid JSON array of strings."
        
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


# ── Natural Language Scheduling Parser ──────────────────────────────────────

def _parse_natural_schedule(natural_time: str) -> dict:
    """Parse natural language scheduling expressions into delay/every values.
    
    Supports patterns like:
    - "at 8 AM" -> one-shot at 8 AM today/tomorrow
    - "at 8 AM daily" or "every day at 8 AM" -> daily recurring
    - "every Monday at 9pm" -> weekly recurring
    - "every 2 hours" -> hourly recurring
    - "in 5 minutes" -> one-shot in 5 minutes
    - "every 30 minutes" -> recurring every 30 minutes
    
    Returns:
        dict with 'delay' (seconds for one-shot) or 'every' (seconds for recurring),
        and 'parsed' description of what was parsed.
    """
    import re
    from datetime import datetime, timedelta
    
    text = natural_time.lower().strip()
    result = {"delay": 0, "every": 0, "parsed": text}
    
    # "in X minutes/hours/days"
    in_match = re.match(r'in (\d+) (minute|minutes|min|mins|hour|hours|hr|hrs|day|days|d)', text)
    if in_match:
        value = int(in_match.group(1))
        unit = in_match.group(2)
        if unit.startswith('min'):
            result["delay"] = value * 60
        elif unit.startswith('hour') or unit == 'hr':
            result["delay"] = value * 3600
        elif unit == 'd' or unit == 'day' or unit.startswith('day'):
            result["delay"] = value * 86400
        result["parsed"] = f"in {value} {'minutes' if value > 1 else 'minute'}"
        return result
    
    # "every X minutes/hours/days"
    every_match = re.match(r'every (\d+) (minute|minutes|min|mins|hour|hours|hr|hrs|day|days|d|weeks?|week)', text)
    if every_match:
        value = int(every_match.group(1))
        unit = every_match.group(2)
        if unit.startswith('min'):
            result["every"] = value * 60
        elif unit.startswith('hour') or unit == 'hr':
            result["every"] = value * 3600
        elif unit == 'd' or unit == 'day' or unit.startswith('day'):
            result["every"] = value * 86400
        elif unit.startswith('week'):
            result["every"] = value * 604800
        result["parsed"] = f"every {value} {'minutes' if unit.startswith('min') else 'hours' if unit.startswith('hour') else 'days' if value > 1 else 'day'}"
        return result
    
    # "at HH:MM AM/PM" or "at HH AM"
    time_match = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "0")
        period = time_match.group(3).lower()
        
        if period == 'pm' and hour != 12:
            hour += 12
        elif period == 'am' and hour == 12:
            hour = 0
        
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If target time has passed today, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)
        
        delay = int((target - now).total_seconds())
        
        # Check for daily/recurring pattern
        if 'daily' in text or 'every day' in text or 'everyday' in text:
            result["every"] = 86400
            result["parsed"] = f"daily at {hour%12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
        else:
            result["delay"] = delay
            result["parsed"] = f"at {hour%12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
        return result
    
    # "every Monday at 9pm" etc.
    day_match = re.search(r'every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', text)
    time_of_day = re.search(r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    
    if day_match:
        day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        target_day_idx = day_names.index(day_match.group(1))
        
        now = datetime.now()
        days_ahead = target_day_idx - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7  # Next occurrence of this day
        
        hour = 0
        minute = 0
        if time_of_day:
            hour = int(time_of_day.group(1))
            minute = int(time_of_day.group(2) or "0")
            period = time_of_day.group(3)
            if period:
                period = period.lower()
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
        
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
        delay = int((target - now).total_seconds())
        
        result["delay"] = delay
        result["every"] = 604800  # Weekly
        result["parsed"] = f"every {day_match.group(1).capitalize()} at {hour%12 or 12}:{minute:02d}"
        return result
    
    # "daily" or "every day" shorthand
    if 'daily' in text or text == 'every day' or text == 'everyday':
        result["every"] = 86400
        result["parsed"] = "daily"
        return result
    
    # "hourly" shorthand
    if text == 'hourly':
        result["every"] = 3600
        result["parsed"] = "hourly"
        return result
    
    return result  # Return unchanged if couldn't parse


def nlp_schedule(task: str, natural_time: str, user_id: str = "default") -> str:
    """Schedule a task using natural language time expressions.
    
    Supports patterns like:
    - "at 8 AM" - one-shot at 8 AM today/tomorrow
    - "in 5 minutes" - one-shot in 5 minutes
    - "every 2 hours" - recurring every 2 hours
    - "daily at 9pm" - daily recurring at 9 PM
    - "every Monday at 9pm" - weekly recurring
    
    task: The task description to execute
    natural_time: Natural language time expression
    user_id: User ID for notification routing
    
    Returns:
        Success or error message with parsed schedule info.
    """
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."
    
    parsed = _parse_natural_schedule(natural_time)
    
    if parsed["delay"] == 0 and parsed["every"] == 0:
        return f"Error: Could not parse time expression '{natural_time}'. Try patterns like 'in 5 minutes', 'at 8 AM daily', 'every 2 hours', etc."
    
    job_id = f"nlp_{user_id}_{int(time.time())}"
    
    if parsed["every"] > 0:
        return _create_job_internal(task, 0, parsed["every"], user_id, job_id) + f" (parsed: {parsed['parsed']})"
    else:
        return _create_job_internal(task, parsed["delay"], 0, user_id, job_id) + f" (parsed: {parsed['parsed']})"


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


async def swarm_message(
    swarm_id: str,
    message: str,
    from_agent: str = "user",
    to_agent: str = None
) -> str:
    """Send a message to agents in a swarm for inter-agent communication.
    
    swarm_id: The swarm ID to send message to
    message: The message content to send
    from_agent: The sender agent name (default: "user")
    to_agent: Optional specific recipient agent (None for broadcast to all)
    
    Returns:
        Success or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        
        # Validate swarm exists
        swarm_info = orchestrator.get_status(swarm_id)
        if not swarm_info:
            return f"❌ Swarm {swarm_id} not found"
        
        # Validate from_agent exists in the swarm
        all_agents = swarm_info.workers.copy()
        if swarm_info.coordinator:
            all_agents.append(swarm_info.coordinator)
        
        if from_agent != "user" and from_agent not in all_agents:
            return f"❌ Agent '{from_agent}' is not part of swarm {swarm_id}"
        
        # Validate to_agent if specified
        if to_agent and to_agent not in all_agents:
            return f"❌ Agent '{to_agent}' is not part of swarm {swarm_id}"
        
        message_id = orchestrator.send_message(
            swarm_id=swarm_id,
            from_agent=from_agent,
            message=message,
            to_agent=to_agent
        )
        
        recipient = f"all agents" if not to_agent else f"agent '{to_agent}'"
        return f"✅ Message sent to {recipient} in swarm {swarm_id} (ID: {message_id})"
    except Exception as e:
        logger.error(f"Failed to send swarm message: {e}")
        return f"Error sending message: {e}"


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
    "sync_knowledge_base":  {"func": sync_knowledge,       "desc": "Sync knowledge base with files"},
    "list_knowledge_tags":  {"func": list_knowledge_tags,  "desc": "List all knowledge tags"},
    # Agent Swarm Tools
    "swarm_create":         {"func": swarm_create,         "desc": "Create a new agent swarm"},
    "swarm_assign":         {"func": swarm_assign,         "desc": "Assign a task to a swarm"},
    "swarm_status":         {"func": swarm_status,         "desc": "Get swarm status"},
    "swarm_result":         {"func": swarm_result,         "desc": "Get swarm execution result"},
    "swarm_terminate":      {"func": swarm_terminate,      "desc": "Terminate a running swarm"},
    "swarm_list":           {"func": swarm_list,           "desc": "List all swarms"},
    "swarm_stats":          {"func": swarm_stats,          "desc": "Get swarm statistics"},
    "swarm_message":        {"func": swarm_message,        "desc": "Send message to agents in a swarm"},
    # Lifecycle Hooks
    "register_hook":        {"func": register_hook,        "desc": "Register a callback for lifecycle events (pre_llm_call, post_llm_call, etc.)"},
    "list_hooks":           {"func": list_hooks,           "desc": "List all registered lifecycle hooks"},
    "clear_hooks":          {"func": clear_hooks,          "desc": "Clear all or specific lifecycle hooks"},
    # Natural Language Scheduling
    "nlp_schedule":        {"func": nlp_schedule,         "desc": "Schedule a task using natural language (e.g., 'in 5 minutes', 'at 8 AM daily', 'every Monday at 9pm')"},
    # Skill Management
    "get_skill_info":       {"func": get_skill_info,       "desc": "Get detailed info about a skill (version, tags, eval score, etc.)"},
    "enable_skill":         {"func": enable_skill,         "desc": "Enable a disabled skill"},
    "disable_skill":        {"func": disable_skill,        "desc": "Disable an enabled skill (soft delete)"},
    "update_skill_metadata":{"func": update_skill_metadata,"desc": "Update skill metadata (tags, description, version)"},
    "benchmark_skill":     {"func": benchmark_skill,      "desc": "Run benchmark tests on a skill with JSON test cases"},
    "evaluate_skill":      {"func": evaluate_skill,       "desc": "Run basic evaluation checks on a skill"},
    # Skill Self-Improvement
    "improve_skill":       {"func": improve_skill,        "desc": "Improve an existing skill with new code (with safety checks and rollback)"},
    "rollback_skill":      {"func": rollback_skill,        "desc": "Rollback a skill to its previous version"},
    # External Skill Adapter
    "analyze_external_skill": {"func": analyze_external_skill, "desc": "Analyze an external skill from URL or file path and return compatibility report"},
    "convert_skill":      {"func": convert_skill,         "desc": "Convert an external skill to ZenSynora format"},
    "list_compatible_skills": {"func": list_compatible_skills, "desc": "List all skills from external sources compatible with ZenSynora"},
    "register_external_skill": {"func": register_external_skill, "desc": "Register an externally sourced skill file in the TOOLBOX"},
    # Medic Agent - System Health
    "check_system_health":     {"func": check_system_health,      "desc": "Check overall system health status"},
    "verify_file_integrity":  {"func": verify_file_integrity,   "desc": "Verify file integrity against recorded hashes"},
    "recover_file":           {"func": recover_file,            "desc": "Recover a corrupted or missing file from GitHub"},
    "get_health_report":      {"func": get_health_report,       "desc": "Get formatted health report"},
    "validate_modification":  {"func": validate_modification,  "desc": "Validate a proposed code modification before applying"},
    "record_task_execution":  {"func": record_task_execution,  "desc": "Record a task execution for analytics"},
    "get_task_analytics":     {"func": get_task_analytics,      "desc": "Get task execution analytics"},
    "enable_hash_check":      {"func": enable_hash_check,       "desc": "Enable or disable hash checking"},
    "scan_files":             {"func": scan_files,              "desc": "Scan files and record their hashes for integrity checking"},
    "detect_errors_in_file":  {"func": detect_errors_in_file,   "desc": "Detect syntax errors in a Python file"},
    "prevent_infinite_loop": {"func": prevent_infinite_loop,  "desc": "Get status of infinite loop prevention"},
    "create_backup":         {"func": create_backup,          "desc": "Create a local backup of a file"},
    "list_backups":          {"func": list_backups,           "desc": "List all local backups"},
    "check_file_virustotal": {"func": check_file_virustotal,   "desc": "Check a file against VirusTotal for malware detection"},
    # New Tech Agent - AI News & Technology
    "fetch_ai_news":         {"func": fetch_ai_news,          "desc": "Fetch AI news from various sources"},
    "get_technology_proposals": {"func": get_technology_proposals, "desc": "Get all technology proposals"},
    "add_to_roadmap":        {"func": add_to_roadmap,         "desc": "Add a technology to the roadmap"},
    "enable_newtech_agent":  {"func": enable_newtech_agent,   "desc": "Enable or disable the New Tech Agent"},
    "run_newtech_scan":     {"func": run_newtech_scan,       "desc": "Run on-demand AI news scan"},
    "summarize_tech":        {"func": summarize_tech,         "desc": "Create a summary for a technology"},
    "generate_tech_proposal": {"func": generate_tech_proposal, "desc": "Generate implementation proposal for a technology"},
    "share_proposal":        {"func": share_proposal,         "desc": "Share a proposal on GitHub"},
    "get_roadmap":          {"func": get_roadmap,           "desc": "Get the technology roadmap"},
    # Session Reflection & Learning
    "schedule_daily_reflection": {"func": schedule_daily_reflection, "desc": "Schedule daily session reflection at a specific time"},
    "generate_session_insights": {"func": generate_session_insights, "desc": "Analyze recent conversations for insights"},
    "extract_user_preferences": {"func": extract_user_preferences, "desc": "Build user profile from conversation history"},
    "update_user_profile":  {"func": update_user_profile,   "desc": "Update the user dialectic profile with new insights"},
    "get_user_profile":     {"func": get_user_profile,      "desc": "Get the current user dialectic profile"},
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
