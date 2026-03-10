import json
import logging
from pathlib import Path
from pydantic import BaseModel, SecretStr, ValidationError

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".myclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"
WORKSPACE = CONFIG_DIR / "workspace"


# ── Schema Models ─────────────────────────────────────────────────────────────

class TelegramConfig(BaseModel):
    enabled: bool = False
    token: SecretStr = SecretStr("")
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
    profiles_dir: str = "~/.myclaw/profiles"


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()


class KnowledgeConfig(BaseModel):
    enabled: bool = True
    auto_extract: bool = False  # Auto-extract knowledge from conversations
    knowledge_dir: str = "~/.myclaw/knowledge"


class AppConfig(BaseModel):
    providers: ProvidersConfig = ProvidersConfig()
    agents:    AgentsConfig    = AgentsConfig()
    channels:  ChannelsConfig  = ChannelsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()

    def get(self, key: str, default=None):
        """Dict-style .get() for backward compatibility."""
        return getattr(self, key, default) or default

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_config() -> AppConfig:
    """Load and validate config. Exits with clear message on schema errors."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        return AppConfig()

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return AppConfig(**raw)
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