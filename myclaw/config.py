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


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class ProvidersConfig(BaseModel):
    ollama: OllamaConfig = OllamaConfig()


class AgentDefaults(BaseModel):
    model: str = "llama3.2"


class NamedAgentConfig(BaseModel):
    """A named agent with its own model and optional custom system prompt."""
    name: str
    model: str = "llama3.2"
    system_prompt: str = ""


class AgentsConfig(BaseModel):
    defaults: AgentDefaults = AgentDefaults()
    named: list[NamedAgentConfig] = []  # additional named agents


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()


class AppConfig(BaseModel):
    providers: ProvidersConfig = ProvidersConfig()
    agents: AgentsConfig = AgentsConfig()
    channels: ChannelsConfig = ChannelsConfig()

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


def save_config(config):
    """Save config dict or AppConfig to disk."""
    if isinstance(config, AppConfig):
        raw = config.model_dump()
        tg = raw.get("channels", {}).get("telegram", {})
        if tg:
            tg["token"] = config.channels.telegram.token.get_secret_value()
    else:
        raw = config
    CONFIG_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")