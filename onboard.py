from myclaw.config import save_config, CONFIG_DIR

def onboard():
    print("🦞 Welcome to MyClaw Onboard!")
    config = {
        "providers": {"ollama": {"base_url": "http://localhost:11434"}},
        "agents": {"defaults": {"model": "llama3.2"}},
        "channels": {
            "telegram": {
                "enabled": False,
                "token": "YOUR_TOKEN",
                "allowFrom": ["YOUR_USER_ID"]
            }
        }
    }
    save_config(config)
    print(f"Config created in {CONFIG_DIR}/config.json")
    print("Start Ollama + `ollama run llama3.2` then run `python cli.py gateway`")