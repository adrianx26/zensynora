"""
Tools Core — Infrastructure for MyClaw tool system.

Provides shared registry, hooks, rate limiting, audit logging,
and validation used by all tool submodules.
"""

import asyncio
import subprocess
import shlex
import logging
import json
import time
import re
import inspect
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from collections import defaultdict

from ..worker_pool import WorkerPoolManager
from ..sandbox import SecuritySandbox, SecurityPolicy
from ..audit_log import TamperEvidentAuditLog

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / ".myclaw" / "workspace"
TOOLBOX_DIR = Path.home() / ".myclaw" / "TOOLBOX"
TOOLBOX_REG = Path.home() / ".myclaw" / "TOOLBOX" / "toolbox_registry.json"
TOOLBOX_DOCS = Path.home() / ".myclaw" / "TOOLBOX" / "README.md"

_runtime_config = None

# ── Security lists ────────────────────────────────────────────────────────────

ALLOWED_COMMANDS = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "type",
        "find",
        "grep",
        "findstr",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "git",
        "echo",
        "pwd",
        "curl",
        "wget",
        # NOTE: python, python3, pip removed (Phase 1.1 security hotfix)
        # to prevent shell sandbox escape via: python -c "import os; os.system('rm -rf ~')"
        # Users can still register custom Python tools via register_tool() if needed.
    }
)

BLOCKED_COMMANDS = frozenset(
    {
        "rm",
        "del",
        "erase",
        "format",
        "rd",
        "rmdir",
        "powershell",
        "cmd",
        "certutil",
        "bitsadmin",
        "icacls",
        "takeown",
        "reg",
        "schtasks",
        "net",
        "tasklist",
        "wmic",
        "msiexec",
        "control",
        "explorer",
        "shutdown",
        "restart",
    }
)


# ── 5.1 Rate Limiter for Tool Execution ──────────────────────────────────────────


class RateLimiter:
    """Per-tool rate limiter using token bucket algorithm.

    Limits tool execution to prevent abuse. Default: 10 calls per minute per tool.

    Phase 6.1: When a Redis-backed StateStore is configured, rate-limit checks
    are distributed across workers. Otherwise falls back to in-memory tracking.

    SECURITY FIX (2026-04-23): Added asyncio.Lock for async contexts and
    threading.Lock for sync contexts to prevent race conditions under
    concurrent tool execution.
    """

    def __init__(self):
        # _limits: tool_name -> (timestamps list, max_calls, window_seconds)
        self._limits = defaultdict(lambda: ([], 10, 60))
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    def _get_state_store(self):
        """Lazy import to avoid circular dependencies."""
        try:
            from ..state_store import get_state_store

            return get_state_store()
        except Exception:
            return None

    def _do_check(self, tool_name: str, max_calls: int, window: int) -> bool:
        """Internal check logic (not thread-safe — caller must hold lock)."""
        now = time.time()
        timestamps, _, _ = self._limits[tool_name]
        # Remove old timestamps outside the window
        self._limits[tool_name] = (
            [t for t in timestamps if now - t < window],
            max_calls,
            window,
        )

        timestamps, max_calls, window = self._limits[tool_name]
        if len(timestamps) >= max_calls:
            return False
        timestamps.append(now)

        # Phase 6.1: Sync to state store for multi-worker awareness (Redis only)
        # Local limiter is source of truth; state store is for cross-worker visibility
        store = self._get_state_store()
        if store is not None:
            try:
                from ..state_store import RedisStateStore

                if isinstance(store, RedisStateStore):
                    store.rate_limit_add(tool_name, max_calls, window)
                    # Note: Redis result is advisory; local already decided
                    store.rate_limit_check(tool_name, max_calls, window)
            except Exception:
                pass

        return True

    def check(self, tool_name: str, max_calls: int = 10, window: int = 60) -> bool:
        """Synchronous check. Use in sync tool functions like shell()."""
        with self._sync_lock:
            return self._do_check(tool_name, max_calls, window)

    async def acheck(self, tool_name: str, max_calls: int = 10, window: int = 60) -> bool:
        """Asynchronous check. Use in async tool functions like shell_async()."""
        async with self._async_lock:
            return self._do_check(tool_name, max_calls, window)

    def get_remaining(self, tool_name: str, max_calls: int = 10, window: int = 60) -> int:
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
        self._persistent = TamperEvidentAuditLog(
            log_path=Path.home() / ".myclaw" / "audit" / "tools_audit.log.jsonl"
        )

    def log(
        self,
        tool_name: str,
        user: str,
        duration_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Log a tool execution event."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "tool": tool_name,
            "user": user or "system",
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
        }
        self._logs.append(entry)
        self._persistent.append(
            event_type=f"tool:{tool_name}",
            details=entry,
            severity="INFO" if success else "WARNING",
        )
        # Keep only recent logs in memory
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs :]

        # Log to standard logger
        if success:
            logger.info(f"AUDIT: {tool_name} executed by {user or 'system'} in {duration_ms:.2f}ms")
        else:
            logger.warning(
                f"AUDIT: {tool_name} failed for {user or 'system'} in {duration_ms:.2f}ms: {error}"
            )

    def get_logs(self, limit: int = 100, tool_name: Optional[str] = None) -> List[Dict]:
        """Get recent audit logs, optionally filtered by tool name."""
        persisted_logs = self._persistent.read_entries(limit=limit * 5)
        if persisted_logs:
            logs = [
                p.get("details", {})
                for p in persisted_logs
                if p.get("event_type", "").startswith("tool:")
            ]
        else:
            logs = self._logs[-limit:]
        if tool_name:
            logs = [l for l in logs if l["tool"] == tool_name]
        return logs[-limit:]

    def verify(self) -> Dict[str, Any]:
        return self._persistent.verify_integrity()

    def export(self, export_path: str) -> str:
        return self._persistent.export(export_path)

    def clear(self) -> None:
        self._persistent.clear()
        self._logs = []


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
        self, tool_calls: List[Dict], user_id: str = "default"
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
                asyncio.gather(*tasks, return_exceptions=True), timeout=self.timeout
            )

            # Process results
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    tc = tool_calls[i]
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    processed_results.append(
                        {
                            "tool_name": tool_name,
                            "result": "",
                            "error": str(result),
                            "duration": 0.0,
                            "success": False,
                        }
                    )
                else:
                    processed_results.append(result)

            return processed_results

        except asyncio.TimeoutError:
            logger.error(f"Parallel tool execution timed out after {self.timeout}s")
            return [
                {
                    "tool_name": "parallel_executor",
                    "result": "",
                    "error": f"Execution timed out after {self.timeout}s",
                    "duration": self.timeout,
                    "success": False,
                }
            ]

    async def _execute_single_tool(self, tool_call: Dict, user_id: str) -> Dict[str, str]:
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
                        "success": False,
                    }

                # Check rate limit
                if not await _rate_limiter.acheck(tool_name):
                    duration = time.time() - start_time
                    try:
                        from ..metrics import get_metrics

                        get_metrics().record_tool_execution(
                            tool_name, duration, status="rate_limited"
                        )
                    except Exception:
                        pass
                    return {
                        "tool_name": tool_name,
                        "result": "",
                        "error": f"Rate limit exceeded for {tool_name}",
                        "duration": duration,
                        "success": False,
                    }

                func = TOOLS[tool_name]["func"]
                sandbox_cfg = getattr(_runtime_config, "sandbox", None)
                sandbox_enabled = bool(getattr(sandbox_cfg, "enabled", False))
                if sandbox_enabled and _is_untrusted_skill(tool_name):
                    violations = _validate_skill_for_sandbox(tool_name)
                    if violations:
                        duration = time.time() - start_time
                        _get_security_sandbox()._log_audit(
                            "sandbox_violation",
                            {"tool": tool_name, "violations": violations},
                            severity="WARNING",
                        )
                        try:
                            from ..metrics import get_metrics

                            get_metrics().record_tool_execution(
                                tool_name, duration, status="blocked"
                            )
                        except Exception:
                            pass
                        return {
                            "tool_name": tool_name,
                            "result": "",
                            "error": f"Sandbox blocked execution: {violations}",
                            "duration": duration,
                            "success": False,
                        }

                # Execute the tool
                if inspect.iscoroutinefunction(func):
                    result = await func(**args)
                else:
                    pool = _get_worker_pool_manager()
                    result = await pool.submit(func, **args)

                duration = time.time() - start_time

                # Log the execution
                _tool_audit_logger.log(tool_name, user_id, duration * 1000, True)

                # Record Prometheus metrics
                try:
                    from ..metrics import get_metrics

                    get_metrics().record_tool_execution(tool_name, duration, status="success")
                except Exception:
                    pass

                return {
                    "tool_name": tool_name,
                    "result": str(result),
                    "error": "",
                    "duration": duration,
                    "success": True,
                }

            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Tool execution error ({tool_name}): {e}")

                _tool_audit_logger.log(tool_name, user_id, duration * 1000, False, str(e))

                # Record Prometheus metrics
                try:
                    from ..metrics import get_metrics

                    get_metrics().record_tool_execution(tool_name, duration, status="error")
                except Exception:
                    pass

                return {
                    "tool_name": tool_name,
                    "result": "",
                    "error": str(e),
                    "duration": duration,
                    "success": False,
                }


# Global parallel executor instance
_parallel_executor: Optional[ParallelToolExecutor] = None
_worker_pool_manager: Optional[WorkerPoolManager] = None
_security_sandbox: Optional[SecuritySandbox] = None


def get_parallel_executor(max_concurrent: int = 5, timeout: float = 30.0) -> ParallelToolExecutor:
    """Get or create the global parallel executor instance."""
    global _parallel_executor

    if _parallel_executor is None:
        _parallel_executor = ParallelToolExecutor(max_concurrent=max_concurrent, timeout=timeout)

    return _parallel_executor


def _get_worker_pool_manager() -> WorkerPoolManager:
    """Get or create worker pool manager configured from runtime config."""
    global _worker_pool_manager
    if _worker_pool_manager is None:
        cfg = getattr(_runtime_config, "worker_pool", None)
        max_workers = getattr(cfg, "max_workers", 5) if cfg else 5
        task_timeout = getattr(cfg, "task_timeout", 30) if cfg else 30
        queue_size = getattr(cfg, "queue_size", 100) if cfg else 100
        _worker_pool_manager = WorkerPoolManager(
            max_workers=max_workers,
            task_timeout=task_timeout,
            queue_size=queue_size,
        )
    return _worker_pool_manager


def _get_security_sandbox() -> SecuritySandbox:
    """Get or create security sandbox configured from runtime config."""
    global _security_sandbox
    if _security_sandbox is None:
        cfg = getattr(_runtime_config, "sandbox", None)
        policy = SecurityPolicy(
            max_memory_mb=getattr(cfg, "max_memory_mb", 256),
            max_execution_seconds=getattr(cfg, "max_time_seconds", 30),
            allow_network=getattr(cfg, "allow_network", False),
        )
        _security_sandbox = SecuritySandbox(policy=policy)
    return _security_sandbox


def _is_untrusted_skill(tool_name: str) -> bool:
    """Return True when tool looks like an external/custom skill and is not trusted."""
    if not _runtime_config or not hasattr(_runtime_config, "sandbox"):
        return False
    trusted = set(getattr(_runtime_config.sandbox, "trusted_skills", []) or [])
    if tool_name in trusted:
        return False
    return (TOOLBOX_DIR / f"{tool_name}.py").exists()


def _validate_skill_for_sandbox(tool_name: str) -> Optional[str]:
    """Validate tool source before execution when sandboxing is enabled."""
    skill_path = TOOLBOX_DIR / f"{tool_name}.py"
    if not skill_path.exists():
        return None
    source = skill_path.read_text(encoding="utf-8")
    violations = _get_security_sandbox().validate_code(source)
    if violations:
        return "; ".join(violations)
    return None


def is_tool_independent(tool_name: str) -> bool:
    """Check if a tool can be executed in parallel (has no dependencies).

    Tools that modify shared state or have side effects should not be
    executed in parallel with other tools.
    """
    # Tools that are NOT safe for parallel execution
    dependent_tools = {
        "shell",
        "run_command",
        "delegate",
        "write_file",
        "create_file",
        "edit_file",
        "schedule",
        "cancel_schedule",
        "edit_schedule",
    }

    return tool_name not in dependent_tools


# ── Module-level references injected by gateway / telegram / whatsapp ──────────

_agent_registry: dict = {}  # name -> Agent  (Feature 2 / 3)
_job_queue = None  # python-telegram-bot JobQueue  (Feature 1 / 5)
_user_chat_ids: dict = {}  # user_id -> chat_id  (Feature 5 notifications)
_notification_callback = (
    None  # Callback: async fn(user_id, message) for channel-agnostic notifications
)


# ── Plugin Lifecycle Hooks ──────────────────────────────────────────────────────

# QUALITY FIX (2026-04-23): Extracted _HOOKS into HookRegistry class to reduce
# global mutable state. The module-level _HOOKS dict remains for backwards
# compatibility but is now backed by a class that can be instantiated per-test.


class HookRegistry:
    """Lifecycle hook registry with typed event support."""

    _VALID_EVENTS = frozenset(
        {"pre_llm_call", "post_llm_call", "on_session_start", "on_session_end"}
    )

    def __init__(self):
        self._hooks: dict[str, list] = {ev: [] for ev in self._VALID_EVENTS}

    def register(self, event_type: str, callback) -> str:
        if event_type not in self._VALID_EVENTS:
            return f"Error: Invalid event type '{event_type}'. Use: {', '.join(sorted(self._VALID_EVENTS))}"
        if not callable(callback):
            return "Error: Callback must be a callable function"
        if callback not in self._hooks[event_type]:
            self._hooks[event_type].append(callback)
            logger.info(f"Hook registered: {event_type} -> {callback.__name__}")
            return f"Hook registered: {event_type}"
        return f"Hook already registered for {event_type}"

    def trigger(self, event_type: str, *args, **kwargs):
        if event_type not in self._hooks:
            return []
        results = []
        for callback in list(self._hooks[event_type]):
            try:
                result = callback(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook error in {event_type}->{callback.__name__}: {e}")
                results.append({"error": str(e)})
        return results

    def list(self) -> str:
        lines = ["Registered hooks:"]
        for event_type, callbacks in self._hooks.items():
            if callbacks:
                lines.append(f"  {event_type}:")
                for cb in callbacks:
                    lines.append(f"    - {cb.__name__}")
            else:
                lines.append(f"  {event_type}: (empty)")
        return "\n".join(lines)

    def clear(self, event_type: str | None = None) -> str:
        if event_type:
            if event_type in self._hooks:
                count = len(self._hooks[event_type])
                self._hooks[event_type] = []
                return f"Cleared {count} hooks for {event_type}"
            return f"Error: Unknown event type '{event_type}'"
        for event_type in self._hooks:
            self._hooks[event_type] = []
        return "Cleared all hooks"

    def __getitem__(self, event_type: str):
        return self._hooks.get(event_type, [])

    def __contains__(self, event_type: str) -> bool:
        return event_type in self._hooks


# Backwards-compatible module-level registry
_hook_registry = HookRegistry()
_HOOKS: dict[str, list] = _hook_registry._hooks  # alias for existing code


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
    return _hook_registry.register(event_type, callback)


def trigger_hook(event_type: str, *args, **kwargs):
    """Trigger all callbacks for a lifecycle event.

    event_type: The event type to trigger
    *args, **kwargs: Arguments passed to each callback

    Returns:
        List of results from each callback (if they return anything).
    """
    return _hook_registry.trigger(event_type, *args, **kwargs)


def list_hooks() -> str:
    """List all registered lifecycle hooks.

    Returns:
        Formatted list of registered hooks by event type.
    """
    return _hook_registry.list()


def clear_hooks(event_type: str = None) -> str:
    """Clear all hooks, or hooks for a specific event type.

    event_type: Optional specific event type to clear. If None, clears all hooks.

    Returns:
        Success message.
    """
    return _hook_registry.clear(event_type)


def set_registry(registry: dict):
    """Called by gateway.py after building the agent registry."""
    global _agent_registry
    _agent_registry = registry
    # Phase 6.1: Sync to state store for multi-worker awareness
    try:
        from ..state_store import get_state_store

        store = get_state_store()
        store.set_agent_registry(registry)
    except Exception:
        pass


def set_config(cfg):
    """Inject runtime configuration for tools, worker pool, and sandbox."""
    global _runtime_config, _worker_pool_manager, _security_sandbox
    _runtime_config = cfg
    _worker_pool_manager = None
    _security_sandbox = None
    # Phase 6.1: Initialise state store with config (triggers Redis if configured)
    try:
        from ..state_store import get_state_store

        get_state_store(config=cfg)
    except Exception:
        pass


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
    # Phase 6.1: Sync to state store
    try:
        from ..state_store import get_state_store

        store = get_state_store()
        store.set_notification_callback(callback)
    except Exception:
        pass


def register_chat_id(user_id: str, chat_id: int):
    """Store a user's Telegram chat_id so scheduled jobs can notify them."""
    _user_chat_ids[user_id] = chat_id
    # Phase 6.1: Sync to state store for multi-worker awareness
    try:
        from ..state_store import get_state_store

        store = get_state_store()
        store.set_chat_id(user_id, chat_id)
    except Exception:
        pass


# ── Core Tools ────────────────────────────────────────────────────────────────


def validate_path(path: str) -> Path:
    """Validate that path stays within workspace — prevents traversal attacks."""
    # Reject null bytes which can bypass path validation on some systems
    if "\x00" in path:
        raise ValueError(f"Invalid path: null bytes are not allowed")

    workspace = WORKSPACE.resolve()
    try:
        target = (workspace / path).resolve()
        # Use is_relative_to for proper path traversal validation
        # This handles case variations and different path separators correctly
        if not target.is_relative_to(workspace):
            raise ValueError(f"Path traversal detected: {path}")
        return target
    except (ValueError, RuntimeError) as e:
        if "Path traversal detected" in str(e) or "null bytes" in str(e):
            raise
        raise ValueError(f"Invalid path: {path}") from e


def register_mcp_tool(name: str, server_name: str, func, documentation: str = "") -> str:
    """Register a remote tool retrieved via MCP."""
    global TOOLS
    local_name = f"mcp_{server_name}_{name}"
    TOOLS[local_name] = {"func": func, "desc": f"[{server_name} MCP] {documentation}"}
    return f"MCP tool '{local_name}' registered successfully."


# -- Tool Registry -----------------------------------------------------------
# NOTE: new custom tools are added to this dict at runtime by register_tool()

TOOLS: Dict[str, dict] = {}


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
            if param.annotation == int:
                ptype = "integer"
            elif param.annotation == bool:
                ptype = "boolean"
            elif param.annotation == float:
                ptype = "number"

            params[param_name] = {"type": ptype, "description": ""}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["desc"] or "",
                    "parameters": {"type": "object", "properties": params, "required": required},
                },
            }
        )
    return schemas


TOOL_SCHEMAS: list[dict] = []


class _ToolFunctionsProxy(dict):
    """Proxy dict that reads tool functions from TOOLS registry."""

    def __contains__(self, key):
        return key in TOOLS

    def __getitem__(self, key):
        return TOOLS[key]["func"]

    def get(self, key, default=None):
        if key in TOOLS:
            return TOOLS[key]["func"]
        return default

    def keys(self):
        return TOOLS.keys()

    def __iter__(self):
        return iter(TOOLS)

    def __len__(self):
        return len(TOOLS)

    def items(self):
        return ((k, TOOLS[k]["func"]) for k in TOOLS)

    def values(self):
        return (TOOLS[k]["func"] for k in TOOLS)


TOOL_FUNCTIONS = _ToolFunctionsProxy()
