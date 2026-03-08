import subprocess
import shlex
import logging
import json
import time
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

from .knowledge import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_related_entities, build_context, sync_knowledge, get_all_tags,
    Observation, Relation
)
from .knowledge.storage import get_knowledge_dir

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
        if jd["chat_id"]:
            await context.bot.send_message(
                chat_id=jd["chat_id"],
                text=f"⏰ Scheduled task '{jd['task']}' result:\n{result}"
            )

    if every > 0:
        _job_queue.run_repeating(_job_fn, interval=every, first=every, name=job_id, data=job_data)
        return f"Recurring job '{job_id}' scheduled — runs every {every}s."
    else:
        _job_queue.run_once(_job_fn, when=delay, name=job_id, data=job_data)
        return f"One-shot job '{job_id}' scheduled — fires in {delay}s."


def schedule(task: str, delay: int = 0, every: int = 0, user_id: str = "default") -> str:
    """Schedule a task to run in the future, executed by the default agent."""
    if _job_queue is None:
        return "Error: Scheduler not available (no Telegram gateway running)."
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


# ── Tool Registry ─────────────────────────────────────────────────────────────
# NOTE: new custom tools are added to this dict at runtime by register_tool()

TOOLS: Dict[str, dict] = {
    # Core
    "shell":                {"func": shell,                "desc": "Execute a shell command"},
    "read_file":            {"func": read_file,            "desc": "Read a file from workspace"},
    "write_file":           {"func": write_file,           "desc": "Write a file to workspace"},
    # Feature 3
    "delegate":             {"func": delegate,             "desc": "Delegate a task to a named agent"},
    # Feature 4
    "list_tools":           {"func": list_tools,           "desc": "List all available tools"},
    "register_tool":        {"func": register_tool,        "desc": "Build and register a new Python tool"},
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
}