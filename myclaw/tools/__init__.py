"""
MyClaw Tools Package

Decomposed from the original monolithic tools.py into focused submodules:
    - core:      Registry, hooks, rate limiting, validation
    - shell:     Shell execution (sync + async)
    - files:     File I/O (read_file, write_file)
    - web:       Web browsing & download
    - ssh:       SSH remote execution & hardware diagnostics
    - swarm:     Agent swarm & delegation
    - kb:        Knowledge base tools
    - scheduler: Task scheduling
    - toolbox:   TOOLBOX skill management
    - session:   Session insights & user profiles
    - management: System management tools
"""

# -- Re-export all public APIs from submodules --------------------------------

from .core import (
    # Infrastructure
    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,
    ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    RateLimiter, _rate_limiter,
    ToolAuditLogger, _tool_audit_logger,
    update_allowlist,
    ParallelToolExecutor, get_parallel_executor,
    _get_worker_pool_manager, _get_security_sandbox,
    _is_untrusted_skill, _validate_skill_for_sandbox,
    is_tool_independent,
    # Module refs
    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,
    _runtime_config,
    # Hooks
    _HOOKS, register_hook, trigger_hook, list_hooks, clear_hooks,
    # Registry setters
    set_registry, set_config, set_job_queue, set_notification_callback,
    register_chat_id,
    # Validation
    validate_path,
    # Registry
    TOOLS, TOOL_SCHEMAS, TOOL_FUNCTIONS,
    register_mcp_tool,
    _generate_schemas,
)

# Phase 6.1: State store for multi-worker deployments
from ..state_store import get_state_store

from .shell import shell, shell_async
from .files import read_file, write_file
from .web import browse, download_file
from .ssh import ssh_command, ssh_put_file, ssh_get_file, get_system_diagnostic
from .swarm import (
    delegate,
    swarm_create, swarm_assign, swarm_status, swarm_result,
    swarm_terminate, swarm_list, swarm_stats, swarm_message,
)
from .kb import (
    write_to_knowledge, search_knowledge, read_knowledge,
    get_knowledge_context, list_knowledge, sync_knowledge_base,
    get_related_knowledge, list_knowledge_tags,
    _extract_search_terms,
)
from .scheduler import (
    schedule, edit_schedule, split_schedule, suspend_schedule,
    resume_schedule, cancel_schedule, list_schedules,
    nlp_schedule, _parse_natural_schedule,
    _create_job_internal,
)
from .session import (
    generate_session_insights, extract_user_preferences,
    update_user_profile, get_user_profile, schedule_daily_reflection,
)
from .toolbox import (
    list_tools, register_tool, list_toolbox, get_tool_documentation,
    load_custom_tools, _update_toolbox_readme,
    get_skill_info, enable_skill, disable_skill, update_skill_metadata,
    benchmark_skill, evaluate_skill, improve_skill, rollback_skill,
)
from .management import (
    clear_semantic_cache, get_cache_stats,
    get_worker_pool_stats, resize_worker_pool,
    get_sandbox_stats, clear_sandbox_audit_log, add_trusted_skill,
    verify_audit_log, get_audit_log_entries, export_audit_log,
    rotate_audit_log, get_log_rotation_status, cleanup_old_logs,
)

import inspect

class _LazyCallable:
    """Defer importing heavy agent modules until the tool is actually invoked.

    Preserves ``inspect.signature()`` compatibility via a dynamic ``__signature__``
    property so schema generation works without eager imports.
    """

    def __init__(self, module: str, name: str):
        self._module = module
        self._name = name
        self._func = None

    def _load(self):
        if self._func is None:
            mod = __import__(self._module, fromlist=[self._name])
            self._func = getattr(mod, self._name)
        return self._func

    def __call__(self, *args, **kwargs):
        return self._load()(*args, **kwargs)

    @property
    def __signature__(self):
        return inspect.signature(self._load())


# Mapping of agent tools that should be loaded lazily.
_LAZY_AGENT_TOOLS = {
    # External Skill Adapter
    "analyze_external_skill":    ("myclaw.agents.skill_adapter", "analyze_external_skill",    "Analyze an external skill from URL or file path and return compatibility report"),
    "convert_skill":             ("myclaw.agents.skill_adapter", "convert_skill",             "Convert an external skill to ZenSynora format"),
    "list_compatible_skills":    ("myclaw.agents.skill_adapter", "list_compatible_skills",    "List all skills from external sources compatible with ZenSynora"),
    "register_external_skill":   ("myclaw.agents.skill_adapter", "register_external_skill",   "Register an externally sourced skill file in the TOOLBOX"),
    # Medic Agent
    "check_system_health":       ("myclaw.agents.medic_agent",   "check_system_health",       "Check overall system health status"),
    "verify_file_integrity":     ("myclaw.agents.medic_agent",   "verify_file_integrity",     "Verify file integrity against recorded hashes"),
    "recover_file":              ("myclaw.agents.medic_agent",   "recover_file",              "Recover a corrupted or missing file from GitHub"),
    "get_health_report":         ("myclaw.agents.medic_agent",   "get_health_report",         "Get formatted health report"),
    "validate_modification":     ("myclaw.agents.medic_agent",   "validate_modification",     "Validate a proposed code modification before applying"),
    "record_task_execution":     ("myclaw.agents.medic_agent",   "record_task_execution",     "Record a task execution for analytics"),
    "get_task_analytics":        ("myclaw.agents.medic_agent",   "get_task_analytics",        "Get task execution analytics"),
    "enable_hash_check":         ("myclaw.agents.medic_agent",   "enable_hash_check",         "Enable or disable hash checking"),
    "scan_files":                ("myclaw.agents.medic_agent",   "scan_files",                "Scan files and record their hashes for integrity checking"),
    "detect_errors_in_file":     ("myclaw.agents.medic_agent",   "detect_errors_in_file",     "Detect syntax errors in a Python file"),
    "prevent_infinite_loop":     ("myclaw.agents.medic_agent",   "prevent_infinite_loop",     "Get status of infinite loop prevention"),
    "create_backup":             ("myclaw.agents.medic_agent",   "create_backup",             "Create a local backup of a file"),
    "list_backups":              ("myclaw.agents.medic_agent",   "list_backups",              "List all local backups"),
    "check_file_virustotal":     ("myclaw.agents.medic_agent",   "check_file_virustotal",     "Check a file against VirusTotal for malware detection"),
    # New Tech Agent
    "fetch_ai_news":             ("myclaw.agents.newtech_agent", "fetch_ai_news",             "Fetch AI news from various sources"),
    "get_technology_proposals":  ("myclaw.agents.newtech_agent", "get_technology_proposals",  "Get all technology proposals"),
    "add_to_roadmap":            ("myclaw.agents.newtech_agent", "add_to_roadmap",            "Add a technology to the roadmap"),
    "enable_newtech_agent":      ("myclaw.agents.newtech_agent", "enable_newtech_agent",      "Enable or disable the New Tech Agent"),
    "run_newtech_scan":          ("myclaw.agents.newtech_agent", "run_newtech_scan",          "Run on-demand AI news scan"),
    "summarize_tech":            ("myclaw.agents.newtech_agent", "summarize_tech",            "Create a summary for a technology"),
    "generate_tech_proposal":    ("myclaw.agents.newtech_agent", "generate_tech_proposal",    "Generate implementation proposal for a technology"),
    "share_proposal":            ("myclaw.agents.newtech_agent", "share_proposal",            "Share a proposal on GitHub"),
    "get_roadmap":               ("myclaw.agents.newtech_agent", "get_roadmap",               "Get the technology roadmap"),
}


# -- Assemble TOOLS registry --------------------------------------------------

# Core tools
TOOLS.update({
    "shell":                {"func": shell,                "desc": "Execute a shell command locally"},
    "read_file":            {"func": read_file,            "desc": "Read a file from workspace"},
    "write_file":           {"func": write_file,           "desc": "Write a file to workspace"},
    # SSH & Hardware
    "ssh_command":          {"func": ssh_command,          "desc": "Execute a command on a remote host via SSH"},
    "ssh_put_file":         {"func": ssh_put_file,         "desc": "Upload a local file to a remote host via SFTP"},
    "ssh_get_file":         {"func": ssh_get_file,         "desc": "Download a remote file from host to workspace"},
    "get_system_diagnostic":{"func": get_system_diagnostic,"desc": "Get current CPU/GPU/RAM hardware telemetry"},
    # Internet & Download
    "browse":               {"func": browse,               "desc": "Browse a URL and return text content"},
    "download_file":        {"func": download_file,        "desc": "Download a file from URL to workspace"},
    # Delegation
    "delegate":             {"func": delegate,             "desc": "Delegate a task to a named agent"},
    # Tool management
    "list_tools":           {"func": list_tools,           "desc": "List all available tools"},
    "register_tool":        {"func": register_tool,        "desc": "Build and register a new Python tool in TOOLBOX"},
    "list_toolbox":         {"func": list_toolbox,         "desc": "List all tools stored in TOOLBOX"},
    "get_tool_documentation":{"func": get_tool_documentation,"desc": "Get documentation for a specific TOOLBOX tool"},
    # Scheduling
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
    # Agent Swarm
    "swarm_create":         {"func": swarm_create,         "desc": "Create a new agent swarm"},
    "swarm_assign":         {"func": swarm_assign,         "desc": "Assign a task to a swarm"},
    "swarm_status":         {"func": swarm_status,         "desc": "Get swarm status"},
    "swarm_result":         {"func": swarm_result,         "desc": "Get swarm execution result"},
    "swarm_terminate":      {"func": swarm_terminate,      "desc": "Terminate a running swarm"},
    "swarm_list":           {"func": swarm_list,           "desc": "List all swarms"},
    "swarm_stats":          {"func": swarm_stats,          "desc": "Get swarm statistics"},
    "swarm_message":        {"func": swarm_message,        "desc": "Send message to agents in a swarm"},
    # Hooks
    "register_hook":        {"func": register_hook,        "desc": "Register a callback for lifecycle events (pre_llm_call, post_llm_call, etc.)"},
    "list_hooks":           {"func": list_hooks,           "desc": "List all registered lifecycle hooks"},
    "clear_hooks":          {"func": clear_hooks,          "desc": "Clear all or specific lifecycle hooks"},
    # NLP Scheduling
    "nlp_schedule":         {"func": nlp_schedule,         "desc": "Schedule a task using natural language (e.g., 'in 5 minutes', 'at 8 AM daily', 'every Monday at 9pm')"},
    # Skill Management
    "get_skill_info":       {"func": get_skill_info,       "desc": "Get detailed info about a skill (version, tags, eval score, etc.)"},
    "enable_skill":         {"func": enable_skill,         "desc": "Enable a disabled skill"},
    "disable_skill":        {"func": disable_skill,        "desc": "Disable an enabled skill (soft delete)"},
    "update_skill_metadata":{"func": update_skill_metadata,"desc": "Update skill metadata (tags, description, version)"},
    "benchmark_skill":      {"func": benchmark_skill,      "desc": "Run benchmark tests on a skill with JSON test cases"},
    "evaluate_skill":       {"func": evaluate_skill,       "desc": "Run basic evaluation checks on a skill"},
    # Skill Self-Improvement
    "improve_skill":        {"func": improve_skill,        "desc": "Improve an existing skill with new code (with safety checks and rollback)"},
    "rollback_skill":       {"func": rollback_skill,       "desc": "Rollback a skill to its previous version"},
    # Agent tools registered lazily below
    # Semantic Cache Management
    "clear_semantic_cache": {"func": clear_semantic_cache, "desc": "Clear semantic LLM response cache"},
    "get_cache_stats": {"func": get_cache_stats, "desc": "Get semantic cache usage statistics"},
    # Worker Pool Management
    "get_worker_pool_stats": {"func": get_worker_pool_stats, "desc": "Get worker pool metrics and queue stats"},
    "resize_worker_pool": {"func": resize_worker_pool, "desc": "Resize worker pool max workers"},
    # Sandbox Management
    "get_sandbox_stats": {"func": get_sandbox_stats, "desc": "Get sandbox policy and runtime stats"},
    "clear_sandbox_audit_log": {"func": clear_sandbox_audit_log, "desc": "Clear sandbox audit log"},
    "add_trusted_skill": {"func": add_trusted_skill, "desc": "Add a trusted skill to bypass sandbox checks"},
    # Tamper-Evident Audit Log Management
    "verify_audit_log": {"func": verify_audit_log, "desc": "Verify audit log hash-chain integrity"},
    "get_audit_log_entries": {"func": get_audit_log_entries, "desc": "Read persistent audit log entries"},
    "export_audit_log": {"func": export_audit_log, "desc": "Export persistent audit log to a file"},
    # Log Rotation Management
    "rotate_audit_log": {"func": rotate_audit_log, "desc": "Force audit log rotation"},
    "get_log_rotation_status": {"func": get_log_rotation_status, "desc": "Get audit log rotation config/status"},
    "cleanup_old_logs": {"func": cleanup_old_logs, "desc": "Apply audit log retention cleanup"},
    # Session Reflection & Learning
    "schedule_daily_reflection": {"func": schedule_daily_reflection, "desc": "Schedule daily session reflection at a specific time"},
    "generate_session_insights": {"func": generate_session_insights, "desc": "Analyze recent conversations for insights"},
    "extract_user_preferences": {"func": extract_user_preferences, "desc": "Build user profile from conversation history"},
    "update_user_profile":  {"func": update_user_profile,   "desc": "Update the user dialectic profile with new insights"},
    "get_user_profile":     {"func": get_user_profile,      "desc": "Get the current user dialectic profile"},
})

# Register lazy agent tools (deferred import until first call)
for _tool_name, (_mod, _func, _desc) in _LAZY_AGENT_TOOLS.items():
    TOOLS[_tool_name] = {"func": _LazyCallable(_mod, _func), "desc": _desc}


# -- Lazy schema generation (Phase 3.3) ---------------------------------------
# Schemas are generated on first use rather than at module load, so heavy
# agent modules are not imported until a tool is actually invoked.

_TOOL_SCHEMAS_INITIALIZED = False


def ensure_tool_schemas() -> None:
    """Populate TOOL_SCHEMAS on first call. Safe to call repeatedly."""
    global _TOOL_SCHEMAS_INITIALIZED
    if not _TOOL_SCHEMAS_INITIALIZED:
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
        _TOOL_SCHEMAS_INITIALIZED = True

# -- Backward compatibility aliases -------------------------------------------
shell_async = shell_async

__all__ = [
    "TOOLS", "TOOL_SCHEMAS", "TOOL_FUNCTIONS",
    "register_tool", "register_mcp_tool", "list_tools", "list_toolbox",
    "trigger_hook", "register_hook", "list_hooks", "clear_hooks", "_HOOKS",
    "get_parallel_executor", "is_tool_independent",
    "validate_path", "update_allowlist",
    "set_registry", "set_config", "set_job_queue", "set_notification_callback",
    "shell", "shell_async", "read_file", "write_file",
    "browse", "download_file",
    "delegate",
    "ssh_command", "ssh_put_file", "ssh_get_file", "get_system_diagnostic",
    "schedule", "edit_schedule", "split_schedule", "suspend_schedule",
    "resume_schedule", "cancel_schedule", "list_schedules", "nlp_schedule",
    "write_to_knowledge", "search_knowledge", "read_knowledge",
    "get_knowledge_context", "list_knowledge", "sync_knowledge_base",
    "get_related_knowledge", "list_knowledge_tags",
    "swarm_create", "swarm_assign", "swarm_status", "swarm_result",
    "swarm_terminate", "swarm_list", "swarm_stats", "swarm_message",
    "get_skill_info", "enable_skill", "disable_skill", "update_skill_metadata",
    "benchmark_skill", "evaluate_skill", "improve_skill", "rollback_skill",
    "clear_semantic_cache", "get_cache_stats",
    "get_worker_pool_stats", "resize_worker_pool",
    "get_sandbox_stats", "clear_sandbox_audit_log", "add_trusted_skill",
    "verify_audit_log", "get_audit_log_entries", "export_audit_log",
    "rotate_audit_log", "get_log_rotation_status", "cleanup_old_logs",
    "generate_session_insights", "extract_user_preferences",
    "update_user_profile", "get_user_profile",
    "schedule_daily_reflection",
    "ensure_tool_schemas",
    # Phase 6.1: State store
    "get_state_store",
]
