from .agent import Agent
from .channels.telegram import TelegramChannel

def start(config):
    agent = Agent(config)
    if config.get("channels", {}).get("telegram", {}).get("enabled"):
        TelegramChannel(config, agent).run()
    else:
        print("No channel is active. Run `python cli.py agent` for console chat.")