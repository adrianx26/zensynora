"""
Config - Configuration management for MyClaw.

Provides configuration schemas, loading, validation, and persistence using
Pydantic models. Supports environment variable overrides and file watching
for auto-reload.

Key Components:
    - AppConfig: Root configuration model
    - AgentsConfig: Agent-specific settings (defaults, named agents)
    - ProvidersConfig: LLM provider settings (Ollama, OpenAI, Anthropic, etc.)
    - ChannelsConfig: Messaging channel settings (Telegram, WhatsApp)
    - SwarmConfig: Agent swarm configuration
    - SecurityConfig: API keys, secrets, security settings

Features:
    - JSON-based configuration with Pydantic validation
    - Environment variable overrides (ENV_OVERRIDES mapping)
    - Automatic config file watching for hot-reload
    - Thread-safe config loading with locks
    - Default values for all settings

Usage:
    from myclaw.config import load_config

    config = load_config()
    model = config.agents.defaults.model
    api_key = config.providers.openai.api_key.get_secret_value()
"""

import json
import logging
import os
import time
import threading
from pathlib import Path
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator
from typing import Optional, Dict, Any, List

from .exceptions import ConfigurationError
from .config_encryption import load_encrypted_or_plain, save_encrypted

# Thread-safe config loading lock
_config_lock = threading.Lock()

# 6.1: Optional watchdog for file watching
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = None

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".myclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"
WORKSPACE = CONFIG_DIR / "workspace"

# Config caching for faster imports
_cached_config: Optional["AppConfig"] = None
_config_mtime: float = 0

# 6.1: File watcher for config auto-reload
_config_file_watcher = None


class _ConfigFileWatcher(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """6.1: Watch config file for changes and invalidate cache."""

    def __init__(self):
        super().__init__()
        self._last_reload_time = 0

    def on_modified(self, event):
        if event.src_path == str(CONFIG_FILE):
            # Debounce: only reload if at least 1 second since last reload
            current_time = time.time()
            if current_time - self._last_reload_time > 1:
                global _cached_config
                _cached_config = None  # Invalidate cache
                logger.info(f"Config file changed, cache invalidated")
                self._last_reload_time = current_time


def _start_config_watcher():
    """6.1: Start file watcher for config changes."""
    global _config_file_watcher

    if not WATCHDOG_AVAILABLE:
        logger.debug("Watchdog not available, using mtime-based caching")
        return

    if _config_file_watcher is not None:
        return  # Already watching

    try:
        _config_file_watcher = _ConfigFileWatcher()
        observer = Observer()
        observer.schedule(_config_file_watcher, str(CONFIG_DIR), recursive=False)
        observer.daemon = True
        observer.start()
        logger.info("Config file watcher started")
    except Exception as e:
        logger.warning(f"Failed to start config file watcher: {e}")


# Environment variable override mapping
ENV_OVERRIDES = {
    # Providers
    "providers.ollama.base_url": "MYCLAW_OLLAMA_BASE_URL",
    "providers.openai.api_key": "MYCLAW_OPENAI_API_KEY",
    "providers.anthropic.api_key": "MYCLAW_ANTHROPIC_API_KEY",
    "providers.gemini.api_key": "MYCLAW_GEMINI_API_KEY",
    "providers.groq.api_key": "MYCLAW_GROQ_API_KEY",
    # Added 0.7.0: local OpenAI-compatible servers and OpenRouter were not overridable.
    "providers.llamacpp.base_url": "MYCLAW_LLAMACPP_BASE_URL",
    "providers.lmstudio.base_url": "MYCLAW_LMSTUDIO_BASE_URL",
    "providers.openrouter.api_key": "MYCLAW_OPENROUTER_API_KEY",
    # Telegram
    "channels.telegram.token": "MYCLAW_TELEGRAM_TOKEN",
    # WhatsApp
    "channels.whatsapp.phone_number_id": "MYCLAW_WHATSAPP_PHONE_NUMBER_ID",
    "channels.whatsapp.business_account_id": "MYCLAW_WHATSAPP_BUSINESS_ACCOUNT_ID",
    "channels.whatsapp.access_token": "MYCLAW_WHATSAPP_ACCESS_TOKEN",
    "channels.whatsapp.verify_token": "MYCLAW_WHATSAPP_VERIFY_TOKEN",
    # Defaults
    "agents.defaults.provider": "MYCLAW_DEFAULT_PROVIDER",
    "agents.defaults.model": "MYCLAW_DEFAULT_MODEL",
    # Swarm
    "swarm.enabled": "MYCLAW_SWARM_ENABLED",
    "swarm.max_concurrent_swarms": "MYCLAW_MAX_CONCURRENT_SWARMS",
    "swarm.timeout_seconds": "MYCLAW_SWARM_TIMEOUT",
    # Timeouts
    "timeouts.shell_seconds": "MYCLAW_SHELL_TIMEOUT",
    "timeouts.llm_seconds": "MYCLAW_LLM_TIMEOUT",
    "timeouts.http_seconds": "MYCLAW_HTTP_TIMEOUT",
    # Memory
    "memory.auto_cleanup_days": "MYCLAW_MEMORY_CLEANUP_DAYS",
    "memory.vacuum_threshold": "MYCLAW_MEMORY_VACUUM_THRESHOLD",
    "agents.summarization_threshold": "MYCLAW_AGENTS_SUMMARIZATION_THRESHOLD",
    # Knowledge
    "knowledge.auto_extract": "MYCLAW_KNOWLEDGE_AUTO_EXTRACT",
    # New Tech
    "newtech.github_token": "MYCLAW_GITHUB_TOKEN",
    "newtech.github_repo_owner": "MYCLAW_GITHUB_REPO_OWNER",
    "newtech.github_repo_name": "MYCLAW_GITHUB_REPO_NAME",
    # Semantic Cache
    "semantic_cache.enabled": "MYCLAW_SEMANTIC_CACHE_ENABLED",
    "semantic_cache.max_size": "MYCLAW_SEMANTIC_CACHE_MAX_SIZE",
    "semantic_cache.ttl": "MYCLAW_SEMANTIC_CACHE_TTL",
    "semantic_cache.similarity_threshold": "MYCLAW_SEMANTIC_CACHE_SIMILARITY",
    # Worker Pool
    "worker_pool.max_workers": "MYCLAW_WORKER_POOL_MAX_WORKERS",
    "worker_pool.task_timeout": "MYCLAW_WORKER_POOL_TASK_TIMEOUT",
    "worker_pool.queue_size": "MYCLAW_WORKER_POOL_QUEUE_SIZE",
    # Sandbox
    "sandbox.enabled": "MYCLAW_SANDBOX_ENABLED",
    "sandbox.max_memory_mb": "MYCLAW_SANDBOX_MAX_MEMORY_MB",
    "sandbox.max_time_seconds": "MYCLAW_SANDBOX_MAX_TIME_SECONDS",
    "sandbox.allow_network": "MYCLAW_SANDBOX_ALLOW_NETWORK",
    # Log Rotation
    "log_rotation.max_size_mb": "MYCLAW_LOG_ROTATION_MAX_SIZE_MB",
    "log_rotation.max_age_days": "MYCLAW_LOG_ROTATION_MAX_AGE_DAYS",
    "log_rotation.max_files": "MYCLAW_LOG_ROTATION_MAX_FILES",
    "log_rotation.compress": "MYCLAW_LOG_ROTATION_COMPRESS",
    # Intelligence
    "intelligence.research_enabled": "MYCLAW_RESEARCH_ENABLED",
    "intelligence.research_interval_hours": "MYCLAW_RESEARCH_INTERVAL",
    "intelligence.research_idle_minutes": "MYCLAW_RESEARCH_IDLE",
    "intelligence.intelligent_routing": "MYCLAW_INTELLIGENT_ROUTING",
    "intelligence.benchmarking_enabled": "MYCLAW_BENCHMARKING_ENABLED",
    "intelligence.offline_mode": "MYCLAW_OFFLINE_MODE",
    "intelligence.routing.enabled": "MYCLAW_ROUTING_ENABLED",
    "intelligence.routing.prefer_free_models": "MYCLAW_ROUTING_PREFER_FREE",
    "intelligence.routing.allowed_models": "MYCLAW_ROUTING_ALLOWED_MODELS",
    "intelligence.routing.allowed_providers": "MYCLAW_ROUTING_ALLOWED_PROVIDERS",
    # Backends
    "backends.ssh.host": "MYCLAW_SSH_HOST",
    "backends.ssh.user": "MYCLAW_SSH_USER",
    # NOTE: backends.ssh.password removed (Phase 1.6 security hotfix)
    # Plaintext SSH passwords are no longer accepted via env var to prevent
    # credential leakage in logs and process listings. Use key-based auth
    # or interactive getpass prompt instead.
    "backends.ssh.key_path": "MYCLAW_SSH_KEY_PATH",
}


def _apply_env_overrides(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides to config dictionary."""

    def deep_get(d: Dict[str, Any], key: str) -> Any:
        """Get nested dict value using dot notation."""
        keys = key.split(".")
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, {})
            else:
                return None
        return d if d else None

    def deep_set(d: Dict[str, Any], key: str, value: Any) -> None:
        """Set nested dict value using dot notation."""
        keys = key.split(".")
        current = d
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    for config_key, env_var in ENV_OVERRIDES.items():
        env_value = os.environ.get(env_var)
        if env_value is not None:
            logger.info(f"Applying env override: {env_var} -> {config_key}")

            # Try to infer type from current value
            current_value = deep_get(config_dict, config_key)
            if isinstance(current_value, bool):
                # Parse boolean
                deep_set(config_dict, config_key, env_value.lower() in ("true", "1", "yes"))
            elif isinstance(current_value, int):
                # Parse integer
                try:
                    deep_set(config_dict, config_key, int(env_value))
                except ValueError:
                    logger.warning(f"Cannot convert {env_var} value to int")
            else:
                # String value
                deep_set(config_dict, config_key, env_value)

    return config_dict


# ── Schema Models ─────────────────────────────────────────────────────────────


class TelegramConfig(BaseModel):
    enabled: bool = False
    max_workers: int = 20  # 7.1: Configurable ThreadPoolExecutor size
    token: SecretStr = SecretStr("")
    # Telegram user ids (numbers, see /whoami) or @usernames allowed to use the bot
    allowFrom: list[str] = []
    # Run the bot inside `zensynora webui` (no separate `zensynora gateway` needed)
    run_in_webui: bool = True
    # Allowed users share the main knowledge base with the Web UI (conversation
    # memory stays per user). False = every Telegram user has their own.
    shared_knowledge: bool = True


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    phone_number_id: str = ""
    business_account_id: str = ""
    access_token: SecretStr = SecretStr("")
    verify_token: SecretStr = SecretStr("")
    allowFrom: list[str] = []


# ── Several API keys per provider (v0.7.5) ───────────────────────────────────

# Key ids: short slugs ("work", "free-2"). "default" is the provider's api_key.
KEY_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class ProviderKey(BaseModel):
    """An extra named API key of a provider (the plain ``api_key`` is the default key)."""

    id: str
    label: str = ""
    api_key: SecretStr = SecretStr("")

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        import re

        v = (v or "").strip().lower()
        if v == "default":
            raise ValueError("'default' is reserved for the provider's main API key")
        if not re.match(KEY_ID_PATTERN, v):
            raise ValueError(f"key id '{v}' must use 1-32 lower-case letters, digits, - or _")
        return v


class MultiKeyMixin(BaseModel):
    """Extra keys of a provider; agents pick one by id (``agents.*.key``)."""

    keys: list[ProviderKey] = []
    # On a rate limit, quota or key error, try the same model with this
    # provider's other keys before moving to the next model.
    rotate_keys: bool = False

    @field_validator("keys")
    @classmethod
    def _unique_ids(cls, v: list) -> list:
        ids = [k.id for k in v]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"two keys have the id {', '.join(dupes)}")
        return v


# ── Local Providers ───────────────────────────────────────────────────────────


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class LMStudioConfig(MultiKeyMixin):
    """LM Studio local server — OpenAI-compatible REST API."""

    base_url: str = "http://localhost:1234/v1"
    api_key: SecretStr = SecretStr(
        "lm-studio"
    )  # LM Studio ignores the key but openai SDK requires one


class LlamaCppConfig(MultiKeyMixin):
    """llama.cpp server (`llama-server`) — OpenAI-compatible REST API."""

    base_url: str = "http://localhost:8080/v1"
    api_key: SecretStr = SecretStr("no-key")


# ── Online Providers ──────────────────────────────────────────────────────────


class OpenAIConfig(MultiKeyMixin):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.openai.com/v1"  # override for Azure, proxies, etc.


class AnthropicConfig(MultiKeyMixin):
    api_key: SecretStr = SecretStr("")


class GeminiConfig(MultiKeyMixin):
    api_key: SecretStr = SecretStr("")


class GroqConfig(MultiKeyMixin):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.groq.com/openai/v1"


class OpenRouterConfig(MultiKeyMixin):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://openrouter.ai/api/v1"
    site_url: str = ""  # optional X-OpenRouter-Site-URL header
    site_name: str = ""  # optional X-OpenRouter-Title header


# ── Providers Container ───────────────────────────────────────────────────────


class CustomProviderConfig(MultiKeyMixin):
    """A provider added by the user (v0.7.1).

    Any OpenAI-compatible server — local (vLLM, LocalAI, Jan, KoboldCpp,
    text-generation-webui, a second llama.cpp…) or online (DeepSeek, Mistral,
    Cerebras, Together, GitHub Models…) — or another Ollama server.
    """

    name: str  # unique id used in settings, e.g. "deepseek" or "office-gpu"
    label: str = ""  # display name
    kind: str = "openai"  # "openai" (OpenAI-compatible) or "ollama"
    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    local: bool = False  # True = runs on your machine/network (shown as "local")
    models: list[str] = []  # optional list for model pickers when /models is unavailable
    notes: str = ""


class ProvidersConfig(BaseModel):
    ollama: OllamaConfig = OllamaConfig()
    lmstudio: LMStudioConfig = LMStudioConfig()
    llamacpp: LlamaCppConfig = LlamaCppConfig()
    openai: OpenAIConfig = OpenAIConfig()
    anthropic: AnthropicConfig = AnthropicConfig()
    gemini: GeminiConfig = GeminiConfig()
    groq: GroqConfig = GroqConfig()
    openrouter: OpenRouterConfig = OpenRouterConfig()
    # v0.7.1: user-defined providers (see CustomProviderConfig).
    custom: list[CustomProviderConfig] = []


# ── Agent Config ──────────────────────────────────────────────────────────────


class AgentDefaults(BaseModel):
    model: str = "llama3.2"
    provider: str = "ollama"  # which provider to use by default
    # v0.7.5: skills of the default agent (names from the Skills page)
    skills: list[str] = []
    # v0.7.5: id of the provider key to use ("" = the provider's default key)
    key: str = ""
    # Context length in tokens (0 = the model's default). Ollama gets it as
    # num_ctx; for every provider the oldest messages are left out so a turn
    # fits (myclaw/context_budget.py). Settings → Agents → Context length.
    context_length: int = Field(0, ge=0, le=10_000_000)


class NamedAgentConfig(BaseModel):
    """A named agent with its own model, provider, and optional custom system prompt."""

    name: str
    model: str = "llama3.2"
    provider: str = ""  # empty = inherit from defaults
    system_prompt: str = ""
    # v0.7.1: short "what this agent does"; shown in the Web UI and used by
    # the prompt generator (Settings → Agents → ✨ Generate prompt).
    description: str = ""
    # v0.7.5: skills this agent knows about (see myclaw/agent_skills.py)
    skills: list[str] = []
    # v0.7.5: provider key id ("" = default key; with no provider of its own,
    # the agent also uses the default agent's key)
    key: str = ""
    # Context length in tokens (0 = the model's default) — see AgentDefaults.
    context_length: int = Field(0, ge=0, le=10_000_000)


class ModelChoice(BaseModel):
    """One provider + model pair (used by the rotation list)."""

    provider: str
    model: str
    key: str = ""  # v0.7.5: provider key id ("" = default key)


class RotationConfig(BaseModel):
    """Default-LLM rotation (v0.7.1).

    strategy:
      * "fallback"    — always start with the default model; on an error
                        (rate limit, server down, missing model) try the next.
      * "round_robin" — spread requests: each new request starts with the
                        next model in the list, still falling back on errors.
    Applies to the default agent and to named agents without their own model.
    """

    enabled: bool = False
    strategy: str = "fallback"
    entries: list[ModelChoice] = []


class AgentsConfig(BaseModel):
    defaults: AgentDefaults = AgentDefaults()
    named: list[NamedAgentConfig] = []
    # v0.7.1: rotate / fall back across several provider+model pairs.
    rotation: RotationConfig = RotationConfig()
    # Configurable context summarization threshold
    summarization_threshold: int = 10
    # Fallback profiles directory (local workspace profiles take precedence)
    # Local profiles are loaded from: myclaw/profiles/{agent_name}.md
    # Fallback profiles are loaded from: profiles_dir/{agent_name}.md
    profiles_dir: str = "~/.myclaw/profiles"
    # v0.7.5: agents may create new named agents with the create_agent tool
    # Tool rounds an agent may use for ONE chat message after its first tool
    # call (2026-10-21: was a fixed 4 — "review all tools" stopped after 5
    # calls). Jobs have their own "max tool steps". Settings → Features.
    max_tool_rounds: int = Field(12, ge=1, le=50)
    allow_agent_creation: bool = True
    # Agents may create / pause / change jobs on the Jobs page (tools job_create …).
    allow_job_creation: bool = True


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()


class KnowledgeConfig(BaseModel):
    enabled: bool = True
    auto_extract: bool = False  # Auto-extract knowledge from conversations
    knowledge_dir: str = "~/.myclaw/knowledge"
    # 2026-10-24 (KB phase 2): quality layer. Conflict detection only flags
    # contradicting facts (never changes a note); notes with more than
    # hub_degree links rank lower in the automatic context.
    detect_conflicts: bool = True
    hub_penalty: bool = True
    hub_degree: int = Field(25, ge=1, le=10000)
    # 2026-10-24 (KB phase 3): semantic search (local embeddings, optional
    # package fastembed). Off by default; vectors live in the index database.
    semantic_search: bool = False
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # cosine similarity below which a note is no semantic match (0 = the model's default)
    semantic_min_score: float = Field(0.0, ge=0.0, le=1.0)


class SwarmConfig(BaseModel):
    """Configuration for Agent Swarm functionality."""

    enabled: bool = True
    max_concurrent_swarms: int = 3
    default_strategy: str = "parallel"
    default_aggregation: str = "synthesis"
    timeout_seconds: int = 300
    swarm_memory_limit: int = 50  # Max messages per swarm


class TimeoutConfig(BaseModel):
    """Timeout configuration for various operations."""

    shell_seconds: int = 30
    llm_seconds: int = 60
    http_seconds: int = 30
    # Longest an agent may work on one message before it is stopped
    # (2026-10-21: was a fixed 300 s in task_timer; jobs use their own timeout).
    task_seconds: int = Field(600, ge=60, le=7200)


class MemoryCleanupConfig(BaseModel):
    """Memory cleanup configuration."""

    auto_cleanup_days: int = 30
    auto_cleanup_enabled: bool = True  # Set to False to disable cleanup on init
    vacuum_threshold: int = 100  # Run VACUUM after this many deletions


class SecurityConfig(BaseModel):
    """Security configuration for command allowlists and GDPR."""

    allowed_commands: list[str] = [
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
        # SECURITY: Removed python, python3, pip, curl, wget — an AI agent
        # executing arbitrary code or downloading files is a remote-code-execution
        # vulnerability. Use dedicated tools (e.g. browse, download) instead.
    ]
    blocked_commands: list[str] = [
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
    ]
    # CORS origins for the WebUI (default: local Vite dev server)
    cors_origins: list[str] = ["http://localhost:5173"]
    # Admin API key for protecting sensitive endpoints
    admin_api_key: SecretStr = SecretStr("")
    # GDPR compliance features (opt-in, default disabled)
    gdpr_enabled: bool = False


class SelfUpdateConfig(BaseModel):
    """Approval-gated self-update (v0.7.1) — see myclaw/selfupdate.py.

    Agents may *propose* changes to ZenSynora's code; nothing is installed
    until you approve it, and then only through the safe updater.
    """

    enabled: bool = True  # agents can read the code and file proposals
    auto_approve_low_risk: bool = False  # documentation-only changes install without asking
    run_tests: bool = False  # run the test suite on the new release before switching (needs pytest)


class MedicConfig(BaseModel):
    """Configuration for Medic Agent system health monitoring."""

    enabled: bool = False
    enable_hash_check: bool = True
    repo_url: str = "https://github.com/adrianx26/zensynora"  # used by recover_file
    scan_on_startup: bool = False
    max_loop_iterations: int = 100
    secondary_llm_provider: str = ""
    secondary_llm_model: str = ""
    backup_dir: str = ""  # Local backup directory
    virustotal_api_key: SecretStr = SecretStr("")  # VirusTotal API key
    self_update: SelfUpdateConfig = SelfUpdateConfig()  # v0.7.1


class NewTechConfig(BaseModel):
    """Configuration for New Tech Agent AI news monitoring."""

    enabled: bool = False
    interval_hours: int = 24
    share_consent: bool = False
    github_repo_for_share: str = ""
    max_news_items: int = 10
    github_token: SecretStr = SecretStr("")  # GitHub token for API auth
    github_repo_owner: str = ""
    github_repo_name: str = ""


class SemanticCacheConfig(BaseModel):
    """Configuration for semantic LLM response caching."""

    enabled: bool = True
    max_size: int = 256
    ttl: int = 3600
    similarity_threshold: float = 0.92


class WorkerPoolConfig(BaseModel):
    """Configuration for worker pool used by tool execution."""

    max_workers: int = 5
    task_timeout: int = 30
    queue_size: int = 100


class SandboxConfig(BaseModel):
    """Configuration for skill sandboxing."""

    enabled: bool = True
    max_memory_mb: int = 256
    max_time_seconds: int = 30
    allow_network: bool = False
    trusted_skills: list[str] = []


class LogRotationConfig(BaseModel):
    """Configuration for audit/application log rotation."""

    max_size_mb: int = 10
    max_age_days: int = 7
    max_files: int = 10
    compress: bool = True


class SkillAdapterConfig(BaseModel):
    """Configuration for Skill Adapter external skill integration."""

    enabled: bool = True
    external_skill_sources: list[str] = ["agentskills.io"]
    allow_external_registration: bool = True


class DockerBackendConfig(BaseModel):
    container: str = "zensynora"
    image: str = "zensynora:latest"


class SSHBackendConfig(BaseModel):
    host: str = ""
    user: str = "root"
    port: int = 22
    key_path: str = ""
    password: SecretStr = SecretStr("")


class WSL2BackendConfig(BaseModel):
    distro: str = "Ubuntu"


class BackendConfig(BaseModel):
    """Configuration for terminal backends."""

    default_backend: str = "local"
    docker: DockerBackendConfig = DockerBackendConfig()
    ssh: SSHBackendConfig = SSHBackendConfig()
    wsl2: WSL2BackendConfig = WSL2BackendConfig()


class MCPServerConfig(BaseModel):
    command: str = ""
    args: list[str] = []
    env: dict = {}


class MCPConfig(BaseModel):
    enabled: bool = True
    expose_server: bool = False
    servers: dict[str, MCPServerConfig] = {}


class RoutingConfig(BaseModel):
    """Detailed settings for Intelligent Routing."""

    enabled: bool = False
    prefer_free_models: bool = True
    allowed_models: list[str] = []  # Empty means all registered
    allowed_providers: list[str] = []  # Empty means all registered
    auto_disable_on_single: bool = True


class IntelligenceConfig(BaseModel):
    """Configuration for AI intelligence features (Research, Routing, Benchmarking)."""

    research_enabled: bool = True
    research_interval_hours: int = 6
    research_idle_minutes: int = 15
    routing: RoutingConfig = RoutingConfig()
    intelligent_routing: bool = False  # Deprecated but kept for compatibility
    benchmarking_enabled: bool = False
    benchmark_interval_days: int = 7
    offline_mode: bool = True  # Auto-fallback to local LLM on connection failure


class BackupConfig(BaseModel):
    """Backups of settings, API keys and the knowledge base (v0.7.1)."""

    auto_enabled: bool = False  # periodic backups while the Web UI runs
    interval_hours: int = 24
    keep: int = 10  # automatic backups to keep (manual ones are never pruned)
    # What a backup contains. Settings + API keys are always included.
    include_knowledge: bool = True
    include_memory: bool = True  # conversations, chat channels
    include_jobs: bool = True  # jobs, self-made tools, skills
    before_update: bool = True  # the updater always backs up; this adds a full app backup
    dir: str = ""  # empty = <install base>/backups or ~/.myclaw-backups


class AppConfig(BaseModel):
    providers: ProvidersConfig = ProvidersConfig()
    agents: AgentsConfig = AgentsConfig()
    channels: ChannelsConfig = ChannelsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    swarm: SwarmConfig = SwarmConfig()
    timeouts: TimeoutConfig = TimeoutConfig()
    memory: MemoryCleanupConfig = MemoryCleanupConfig()
    security: SecurityConfig = SecurityConfig()
    medic: MedicConfig = MedicConfig()
    newtech: NewTechConfig = NewTechConfig()
    semantic_cache: SemanticCacheConfig = SemanticCacheConfig()
    worker_pool: WorkerPoolConfig = WorkerPoolConfig()
    sandbox: SandboxConfig = SandboxConfig()
    log_rotation: LogRotationConfig = LogRotationConfig()
    skill_adapter: SkillAdapterConfig = SkillAdapterConfig()
    backends: BackendConfig = BackendConfig()
    mcp: MCPConfig = MCPConfig()
    intelligence: IntelligenceConfig = IntelligenceConfig()
    backup: BackupConfig = BackupConfig()

    def get(self, key: str, default=None):
        """Dict-style .get() for backward compatibility."""
        return getattr(self, key, default) or default

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


# ── Validation ────────────────────────────────────────────────────────────────


_last_config_warnings: tuple = ()  # last warnings logged by _validate_config


def _validate_config(config: AppConfig) -> None:
    """Validate critical configuration and warn about missing required values.

    Checks:
      - At least one LLM provider is configured (local or API key)
      - Required credentials are present for enabled channels
      - Critical environment variables are set

    This function logs warnings but does not exit — the agent may still work
    with interactive configuration or delayed provider setup.
    """
    warnings = []

    # ── LLM Provider Check ──────────────────────────────────────────────────
    has_local = False
    has_api_key = False

    # Check Ollama / LM Studio / llama.cpp (local)
    if config.providers.ollama.base_url:
        has_local = True
    if hasattr(config.providers, "lmstudio") and config.providers.lmstudio.base_url:
        has_local = True
    if hasattr(config.providers, "llamacpp") and config.providers.llamacpp.base_url:
        has_local = True

    # Check cloud provider API keys
    for prov_name in ("openai", "anthropic", "gemini", "groq", "openrouter"):
        prov = getattr(config.providers, prov_name, None)
        if prov and prov.api_key and prov.api_key.get_secret_value():
            has_api_key = True
            break

    if not has_local and not has_api_key:
        warnings.append(
            "No LLM provider configured. Set at least one of:\n"
            "  - Ollama/LM Studio base_url (local)\n"
            "  - OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY\n"
            "  Run `python cli.py onboard` for interactive setup."
        )

    # ── Telegram Check ──────────────────────────────────────────────────────
    if config.channels.telegram.enabled:
        if not config.channels.telegram.token.get_secret_value():
            warnings.append(
                "Telegram is enabled but no bot token is set.\n"
                "  Get one from @BotFather and paste it in Web UI → Settings → Channels "
                "(or set MYCLAW_TELEGRAM_TOKEN)."
            )
        if not config.channels.telegram.allowFrom:
            warnings.append(
                "Telegram is enabled but no user IDs are in allowFrom.\n"
                "  Add your user ID to channels.telegram.allowFrom in config.json"
            )

    # ── WhatsApp Check ──────────────────────────────────────────────────────
    if config.channels.whatsapp.enabled:
        missing = []
        if not config.channels.whatsapp.phone_number_id:
            missing.append("phone_number_id")
        if not config.channels.whatsapp.access_token.get_secret_value():
            missing.append("access_token")
        if not config.channels.whatsapp.verify_token.get_secret_value():
            missing.append("verify_token")
        if missing:
            warnings.append(
                f"WhatsApp is enabled but missing: {', '.join(missing)}.\n"
                "  See docs/dev/plans/whatsapp_implementation_plan.md for setup."
            )

    # ── Security: allowed_commands validation ───────────────────────────────
    dangerous_commands = {
        "rm",
        "del",
        "erase",
        "format",
        "rd",
        "rmdir",
        "powershell",
        "cmd",
        "python",
        "python3",
        "pip",
        "curl",
        "wget",
        "sh",
        "bash",
        "zsh",
        "nc",
        "netcat",
    }
    bad_allowed = [
        cmd for cmd in config.security.allowed_commands if cmd.lower() in dangerous_commands
    ]
    if bad_allowed:
        warnings.append(
            f"SECURITY: Dangerous commands in allowed_commands: {', '.join(bad_allowed)}.\n"
            f"  These can lead to remote code execution. Remove them immediately."
        )

    # ── Log all warnings ────────────────────────────────────────────────────
    # The config is re-read after every save (and by every agent created), so
    # the same warnings were logged hundreds of times a day (2026-10-18 log:
    # 232×). Log them once, as one entry, and again only when they change.
    global _last_config_warnings
    key = tuple(warnings)
    if key == _last_config_warnings:
        if warnings:
            logger.debug("Configuration warnings unchanged (%d)", len(warnings))
        return
    _last_config_warnings = key
    if warnings:
        body = "\n".join(f"  {i}. {w}" for i, w in enumerate(warnings, 1))
        logger.warning(
            "CONFIGURATION WARNINGS — ZenSynora may not work correctly:\n%s\n"
            "Fix them in the Web UI → Settings or run `zensynora onboard`.", body)


# ── Loaders ───────────────────────────────────────────────────────────────────


_DEFAULT_KNOWLEDGE_DIR = "~/.myclaw/knowledge"


def _apply_knowledge_dir(config: AppConfig) -> None:
    """Point the knowledge storage at ``knowledge.knowledge_dir``.

    2026-10-23 (KB phase 0): the setting was ignored everywhere. The default
    value resets to ``None`` (= ``~/.myclaw/knowledge`` of the current HOME), so
    tests that change HOME keep working. The index databases stay at
    ``~/.myclaw/knowledge_<base>.db``. Never makes loading the config fail.
    """
    try:
        value = str(getattr(getattr(config, "knowledge", None), "knowledge_dir", "") or "").strip()
        default = os.path.abspath(os.path.expanduser(_DEFAULT_KNOWLEDGE_DIR))
        target = os.path.abspath(os.path.expanduser(value)) if value else default
        from .knowledge.storage import set_knowledge_root

        set_knowledge_root(None if target == default else target)
    except Exception as e:  # a bad value must not stop ZenSynora from starting
        logger.warning("knowledge.knowledge_dir could not be applied (%s); using ~/.myclaw/knowledge", e)


def _apply_knowledge_quality(config: AppConfig) -> None:
    """Apply ``knowledge.detect_conflicts`` / ``hub_penalty`` / ``hub_degree``
    (2026-10-24, KB phase 2). Never makes loading the config fail."""
    try:
        kc = getattr(config, "knowledge", None)
        from .knowledge.quality import set_options

        set_options(detect_conflicts=getattr(kc, "detect_conflicts", True),
                    hub_penalty=getattr(kc, "hub_penalty", True),
                    hub_degree=getattr(kc, "hub_degree", 25))
    except Exception as e:
        logger.warning("knowledge quality settings could not be applied (%s); using defaults", e)


def _apply_knowledge_semantic(config: AppConfig) -> None:
    """Apply ``knowledge.semantic_search`` / ``embedding_model`` /
    ``semantic_min_score`` (2026-10-24, KB phase 3). Never makes loading fail."""
    try:
        kc = getattr(config, "knowledge", None)
        from .knowledge.semantic import set_options

        set_options(semantic_search=getattr(kc, "semantic_search", False),
                    embedding_model=getattr(kc, "embedding_model", None),
                    kb_enabled=getattr(kc, "enabled", True),
                    min_score=getattr(kc, "semantic_min_score", 0.0))
    except Exception as e:
        logger.warning("semantic search settings could not be applied (%s); it stays off", e)


def load_config(force_reload: bool = False) -> AppConfig:
    """Load and validate config. Exits with clear message on schema errors.

    Thread-safe: Uses a lock to prevent race conditions during config reload.
    """
    global _cached_config, _config_mtime

    with _config_lock:
        # 6.1: Start file watcher on first load
        _start_config_watcher()

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE.mkdir(parents=True, exist_ok=True)

        if not CONFIG_FILE.exists():
            return AppConfig()

        try:
            # Check if config file has been modified
            current_mtime = CONFIG_FILE.stat().st_mtime

            # Return cached config if not modified and not force reload
            if not force_reload and _cached_config is not None and current_mtime == _config_mtime:
                return _cached_config

            raw = load_encrypted_or_plain(CONFIG_FILE)
            # Apply environment variable overrides
            raw = _apply_env_overrides(raw)

            _cached_config = AppConfig(**raw)
            _config_mtime = current_mtime
            _apply_knowledge_dir(_cached_config)
            _apply_knowledge_quality(_cached_config)
            _apply_knowledge_semantic(_cached_config)

            # Validate critical configuration
            _validate_config(_cached_config)

            return _cached_config
        except json.JSONDecodeError as e:
            raise SystemExit(f"❌ Config error: {CONFIG_FILE} is not valid JSON — {e}")
        except ValidationError as e:
            raise SystemExit(f"❌ Config validation error:\n{e}")


def _reveal_secrets(raw: dict) -> dict:
    """Walk a plain dict and expose SecretStr values so they serialise correctly."""
    from pydantic import SecretStr

    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = _reveal_secrets(v)
        elif isinstance(v, SecretStr):
            out[k] = v.get_secret_value()
        else:
            out[k] = v
    return out


def save_config(config):
    """Save config dict or AppConfig to disk."""
    if isinstance(config, AppConfig):
        raw = config.model_dump()

        # Re-inject plaintext secrets that pydantic hides
        def _inject_secrets(raw_dict, model_obj):
            for field_name, field_val in raw_dict.items():
                obj_val = getattr(model_obj, field_name, None)
                if isinstance(field_val, dict) and obj_val is not None:
                    _inject_secrets(field_val, obj_val)
                elif isinstance(field_val, list) and isinstance(obj_val, list):
                    # v0.7.5: secrets inside list items (custom providers, provider keys)
                    for item_raw, item_obj in zip(field_val, obj_val):
                        if isinstance(item_raw, dict) and isinstance(item_obj, BaseModel):
                            _inject_secrets(item_raw, item_obj)
                elif isinstance(obj_val, SecretStr):
                    raw_dict[field_name] = obj_val.get_secret_value()

        _inject_secrets(raw, config)
    else:
        raw = config

    save_encrypted(CONFIG_FILE, raw)
