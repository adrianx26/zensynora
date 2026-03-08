import subprocess
import shlex
import logging
import json
import time
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict

logger = logging.getLogger(__name__)

WORKSPACE         = Path.home() / ".myclaw" / "workspace"
CUSTOM_TOOLS_DIR  = Path.home() / ".myclaw" / "tools"
CUSTOM_TOOLS_REG  = Path.home() / ".myclaw" / "custom_tools.json"

# ── Security lists ────────────────────────────────────────────────────────────

ALLOWED_COMMANDS = frozenset({
    'ls', 'dir', 'cat', 'type', 'find', 'grep', 'findstr',
    'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'git'
})

BLOCKED_COMMANDS = frozenset({
    'rm', 'del', 'erase', 'format', 'rd', 'rmdir',
    'powershell', 'cmd', 'certutil', 'bitsadmin', 'icacls',
    'takeown', 'reg', 'schtasks', 'net', 'tasklist',
    'wmic', 'msiexec', 'control', 'explorer', 'shutdown', 'restart'
})

# ── Module-level references injected by gateway / telegram ───────────────────

_agent_registry: dict = {}   # name -> Agent  (Feature 2 / 3)
_job_queue      = None        # python-telegram-bot JobQueue  (Feature 1 / 5)
_user_chat_ids: dict = {}     # user_id -> chat_id  (Feature 5 notifications)


def set_registry(registry: dict):
    """Called by gateway.py after building the agent registry."""
    global _agent_registry
    _agent_registry = registry


def set_job_queue(jq):
    """Called by telegram.py after the Application is built."""
    global _job_queue
    _job_queue = jq


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


def shell(cmd: str) -> str:
    """Execute an allowed shell command in the workspace directory."""
    try:
        parts = shlex.split(cmd)
        if not parts:
            return "Error: Empty command"
        first_cmd = parts[0].lower()
        if first_cmd in BLOCKED_COMMANDS:
            logger.warning(f"Blocked command attempted: {first_cmd}")
            return f"Error: Command '{first_cmd}' is blocked for security"
        if first_cmd not in ALLOWED_COMMANDS:
            return f"Error: '{first_cmd}' not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        result = subprocess.run(
            parts, shell=False, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds"
    except Exception as e:
        logger.error(f"Shell error: {e}")
        return f"Error: {e}"


def read_file(path: str) -> str:
    """Read a file from the workspace directory."""
    try:
        return validate_path(path).read_text()
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"File read error: {e}")
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file in the workspace directory."""
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


def register_tool(name: str, code: str) -> str:
    """Dynamically create a new tool from Python source code.

    name: valid Python identifier — must match the function name defined in code
    code: full Python source for the function (use \\n for newlines)

    Example:
        register_tool("greet", "def greet(who='world'):\\n    return f'Hello {who}!'")
    """
    if not name.isidentifier():
        return f"Error: '{name}' is not a valid Python identifier."

    # Syntax validation before anything hits disk
    try:
        compile(code, "<agent-tool>", "exec")
    except SyntaxError as e:
        return f"Syntax error in tool code: {e}"

    # Write to disk
    CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    tool_path = CUSTOM_TOOLS_DIR / f"{name}.py"
    tool_path.write_text(code, encoding="utf-8")

    # Dynamic load
    try:
        spec = importlib.util.spec_from_file_location(name, tool_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        func = getattr(mod, name)
    except AttributeError:
        return f"Error: code must define a function named '{name}'."
    except Exception as e:
        tool_path.unlink(missing_ok=True)
        return f"Error loading tool: {e}"

    TOOLS[name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {name}"}

    # Persist registry so tool survives restarts
    registry = {}
    if CUSTOM_TOOLS_REG.exists():
        try:
            registry = json.loads(CUSTOM_TOOLS_REG.read_text())
        except Exception:
            pass
    registry[name] = str(tool_path)
    CUSTOM_TOOLS_REG.write_text(json.dumps(registry, indent=2))

    logger.info(f"Custom tool registered: {name}")
    return f"Tool '{name}' registered and available immediately."


def load_custom_tools():
    """Load persisted custom tools at startup — called by gateway.py / cli.py."""
    if not CUSTOM_TOOLS_REG.exists():
        return
    try:
        registry = json.loads(CUSTOM_TOOLS_REG.read_text())
        for name, path in registry.items():
            tool_path = Path(path)
            if not tool_path.exists():
                logger.warning(f"Custom tool file missing: {path}")
                continue
            try:
                spec = importlib.util.spec_from_file_location(name, tool_path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                func = getattr(mod, name)
                TOOLS[name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {name}"}
                logger.info(f"Loaded custom tool: {name}")
            except Exception as e:
                logger.warning(f"Failed to load custom tool '{name}': {e}")
    except Exception as e:
        logger.error(f"Error loading custom tools registry: {e}")


# ── Feature 5: Agent-Initiated Scheduling ────────────────────────────────────

def schedule(task: str, delay: int = 0, every: int = 0, user_id: str = "default") -> str:
    """Schedule a task to run in the future, executed by the default agent.

    task:    natural-language instruction or tool call to execute at trigger time
    delay:   run once after this many seconds (one-shot)
    every:   run repeatedly every N seconds (recurring)
    user_id: whose memory context to use when the task fires
    """
    if _job_queue is None:
        return "Error: Scheduler not available (no Telegram gateway running)."
    if delay <= 0 and every <= 0:
        return "Error: Specify 'delay' (one-shot) or 'every' (recurring) in seconds."

    job_id  = f"agent_{user_id}_{int(time.time())}"
    chat_id = _user_chat_ids.get(user_id)

    async def _job_fn(context):
        agent = _agent_registry.get("default")
        if not agent:
            return
        result = await agent.think(task, user_id=user_id)
        if chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Scheduled task result:\n{result}"
            )

    if every > 0:
        _job_queue.run_repeating(_job_fn, interval=every, first=every, name=job_id)
        return f"Recurring job '{job_id}' scheduled — runs every {every}s."
    else:
        _job_queue.run_once(_job_fn, when=delay, name=job_id)
        return f"One-shot job '{job_id}' scheduled — fires in {delay}s."


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
    lines = [f"- {j.name}  next: {j.next_t}" for j in jobs]
    return "Active jobs:\n" + "\n".join(lines)


# ── Tool Registry ─────────────────────────────────────────────────────────────
# NOTE: new custom tools are added to this dict at runtime by register_tool()

TOOLS: Dict[str, dict] = {
    # Core
    "shell":           {"func": shell,           "desc": "Execute a shell command"},
    "read_file":       {"func": read_file,        "desc": "Read a file from workspace"},
    "write_file":      {"func": write_file,       "desc": "Write a file to workspace"},
    # Feature 3
    "delegate":        {"func": delegate,         "desc": "Delegate a task to a named agent"},
    # Feature 4
    "list_tools":      {"func": list_tools,       "desc": "List all available tools"},
    "register_tool":   {"func": register_tool,    "desc": "Build and register a new Python tool"},
    # Feature 5
    "schedule":        {"func": schedule,         "desc": "Schedule a future task (delay= or every=)"},
    "cancel_schedule": {"func": cancel_schedule,  "desc": "Cancel a scheduled job by ID"},
    "list_schedules":  {"func": list_schedules,   "desc": "List all active scheduled jobs"},
}