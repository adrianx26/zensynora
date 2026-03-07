import json
import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, SecretStr, field_validator, ValidationError

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".myclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"
WORKSPACE = CONFIG_DIR / "workspace"


# ── Schema Models ────────────────────────────────────────────────────────────

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


class AgentsConfig(BaseModel):
    defaults: AgentDefaults = AgentDefaults()


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()


class AppConfig(BaseModel):
    providers: ProvidersConfig = ProvidersConfig()
    agents: AgentsConfig = AgentsConfig()
    channels: ChannelsConfig = ChannelsConfig()

    def get(self, key: str, default=None):
        """Dict-style .get() for backward compatibility with agent.py / gateway.py."""
        return getattr(self, key, default) or default

    def __getitem__(self, key: str):
        """Dict-style [] access for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_config() -> AppConfig:
    """Load and validate config from disk. Exits cleanly on schema errors."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        return AppConfig()  # safe defaults

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return AppConfig(**raw)
    except json.JSONDecodeError as e:
        logger.error(f"Config file is not valid JSON: {e}")
        raise SystemExit(f"❌ Config error: {CONFIG_FILE} is not valid JSON — {e}")
    except ValidationError as e:
        logger.error(f"Config validation failed: {e}")
        raise SystemExit(f"❌ Config validation error:\n{e}")


def save_config(config: AppConfig):
    """Save config to disk, serializing SecretStr safely."""
    raw = config.model_dump()
    # Re-serialize SecretStr as plain string for storage
    if "channels" in raw and "telegram" in raw["channels"]:
        tg = raw["channels"]["telegram"]
        if hasattr(config.channels.telegram.token, "get_secret_value"):
            tg["token"] = config.channels.telegram.token.get_secret_value()
    CONFIG_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")