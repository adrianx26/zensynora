import json
import logging
import os
import time
from pathlib import Path
from pydantic import BaseModel, SecretStr, ValidationError
from typing import Optional, Dict, Any, List

from .exceptions import ConfigurationError

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
_cached_config: Optional['AppConfig'] = None
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
                deep_set(config_dict, config_key, env_value.lower() in ('true', '1', 'yes'))
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
    allowFrom: list[str] = []


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    phone_number_id: str = ""
    business_account_id: str = ""
    access_token: SecretStr = SecretStr("")
    verify_token: SecretStr = SecretStr("")
    allowFrom: list[str] = []


# ── Local Providers ───────────────────────────────────────────────────────────

class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class LMStudioConfig(BaseModel):
    """LM Studio local server — OpenAI-compatible REST API."""
    base_url: str = "http://localhost:1234/v1"
    api_key: SecretStr = SecretStr("lm-studio")   # LM Studio ignores the key but openai SDK requires one


class LlamaCppConfig(BaseModel):
    """llama.cpp server (`llama-server`) — OpenAI-compatible REST API."""
    base_url: str = "http://localhost:8080/v1"
    api_key: SecretStr = SecretStr("no-key")


# ── Online Providers ──────────────────────────────────────────────────────────

class OpenAIConfig(BaseModel):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.openai.com/v1"   # override for Azure, proxies, etc.


class AnthropicConfig(BaseModel):
    api_key: SecretStr = SecretStr("")


class GeminiConfig(BaseModel):
    api_key: SecretStr = SecretStr("")


class GroqConfig(BaseModel):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.groq.com/openai/v1"


class OpenRouterConfig(BaseModel):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://openrouter.ai/api/v1"
    site_url: str = ""    # optional X-OpenRouter-Site-URL header
    site_name: str = ""   # optional X-OpenRouter-Title header


# ── Providers Container ───────────────────────────────────────────────────────

class ProvidersConfig(BaseModel):
    ollama:      OllamaConfig      = OllamaConfig()
    lmstudio:    LMStudioConfig    = LMStudioConfig()
    llamacpp:    LlamaCppConfig    = LlamaCppConfig()
    openai:      OpenAIConfig      = OpenAIConfig()
    anthropic:   AnthropicConfig   = AnthropicConfig()
    gemini:      GeminiConfig      = GeminiConfig()
    groq:        GroqConfig        = GroqConfig()
    openrouter:  OpenRouterConfig  = OpenRouterConfig()


# ── Agent Config ──────────────────────────────────────────────────────────────

class AgentDefaults(BaseModel):
    model: str    = "llama3.2"
    provider: str = "ollama"   # which provider to use by default


class NamedAgentConfig(BaseModel):
    """A named agent with its own model, provider, and optional custom system prompt."""
    name: str
    model: str    = "llama3.2"
    provider: str = ""         # empty = inherit from defaults
    system_prompt: str = ""


class AgentsConfig(BaseModel):
    defaults: AgentDefaults = AgentDefaults()
    named: list[NamedAgentConfig] = []
    # Configurable context summarization threshold
    summarization_threshold: int = 10
    # Fallback profiles directory (local workspace profiles take precedence)
    # Local profiles are loaded from: myclaw/profiles/{agent_name}.md
    # Fallback profiles are loaded from: profiles_dir/{agent_name}.md
    profiles_dir: str = "~/.myclaw/profiles"


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()


class KnowledgeConfig(BaseModel):
    enabled: bool = True
    auto_extract: bool = False  # Auto-extract knowledge from conversations
    knowledge_dir: str = "~/.myclaw/knowledge"


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


class MemoryCleanupConfig(BaseModel):
    """Memory cleanup configuration."""
    auto_cleanup_days: int = 30
    auto_cleanup_enabled: bool = True  # Set to False to disable cleanup on init
    vacuum_threshold: int = 100  # Run VACUUM after this many deletions


class SecurityConfig(BaseModel):
    """Security configuration for command allowlists."""
    allowed_commands: list[str] = [
        'ls', 'dir', 'cat', 'type', 'find', 'grep', 'findstr',
        'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'git',
        'echo', 'pwd', 'python', 'python3', 'pip', 'curl', 'wget'
    ]
    blocked_commands: list[str] = [
        'rm', 'del', 'erase', 'format', 'rd', 'rmdir',
        'powershell', 'cmd', 'certutil', 'bitsadmin', 'icacls',
        'takeown', 'reg', 'schtasks', 'net', 'tasklist',
        'wmic', 'msiexec', 'control', 'explorer', 'shutdown', 'restart'
    ]


class MedicConfig(BaseModel):
    """Configuration for Medic Agent system health monitoring."""
    enabled: bool = False
    enable_hash_check: bool = True
    repo_url: str = "https://github.com/zensynora/zensynora"
    scan_on_startup: bool = False
    max_loop_iterations: int = 100
    secondary_llm_provider: str = ""
    secondary_llm_model: str = ""
    backup_dir: str = ""  # Local backup directory
    virustotal_api_key: SecretStr = SecretStr("")  # VirusTotal API key


class NewTechConfig(BaseModel):
    """Configuration for New Tech Agent AI news monitoring."""
    enabled: bool = False
    interval_hours: int = 24
    share_consent: bool = False
    github_repo_for_share: str = ""
    max_news_items: int = 10
    github_token: SecretStr = SecretStr("")  # GitHub token for API auth


class SkillAdapterConfig(BaseModel):
    """Configuration for Skill Adapter external skill integration."""
    enabled: bool = True
    external_skill_sources: list[str] = ["agentskills.io"]
    allow_external_registration: bool = True


class BackendConfig(BaseModel):
    """Configuration for terminal backends."""
    default_backend: str = "local"
    docker: dict = {"container": "zensynora", "image": "zensynora:latest"}
    ssh: dict = {"host": "", "user": "", "port": 22, "key_path": ""}
    wsl2: dict = {"distro": "Ubuntu"}


class AppConfig(BaseModel):
    providers: ProvidersConfig = ProvidersConfig()
    agents:    AgentsConfig    = AgentsConfig()
    channels:  ChannelsConfig  = ChannelsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    swarm:     SwarmConfig     = SwarmConfig()
    timeouts:  TimeoutConfig   = TimeoutConfig()
    memory:    MemoryCleanupConfig = MemoryCleanupConfig()
    security:  SecurityConfig  = SecurityConfig()
    medic:     MedicConfig     = MedicConfig()
    newtech:   NewTechConfig   = NewTechConfig()
    skill_adapter: SkillAdapterConfig = SkillAdapterConfig()
    backends:  BackendConfig   = BackendConfig()

    def get(self, key: str, default=None):
        """Dict-style .get() for backward compatibility."""
        return getattr(self, key, default) or default

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_config(force_reload: bool = False) -> AppConfig:
    """Load and validate config. Exits with clear message on schema errors."""
    global _cached_config, _config_mtime
    
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
        
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # Apply environment variable overrides
        raw = _apply_env_overrides(raw)
        
        _cached_config = AppConfig(**raw)
        _config_mtime = current_mtime
        
        return _cached_config
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ Config error: {CONFIG_FILE} is not valid JSON — {e}")
    except ValidationError as e:
        raise SystemExit(f"❌ Config validation error:\n{e}")


def _reveal_secrets(raw: dict) -> dict:
    """Walk a plain dict and expose SecretStr values so they serialise correctly."""
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = _reveal_secrets(v)
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
                elif isinstance(obj_val, SecretStr):
                    raw_dict[field_name] = obj_val.get_secret_value()
        _inject_secrets(raw, config)
    else:
        raw = config

    CONFIG_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")