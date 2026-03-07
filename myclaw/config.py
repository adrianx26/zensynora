import json
from pathlib import Path
from typing import Dict, Any

CONFIG_DIR = Path.home() / ".myclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"
WORKSPACE = CONFIG_DIR / "workspace"

def load_config() -> Dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())

def save_config(config: Dict):
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))