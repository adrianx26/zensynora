from .agent import Agent
from .channels.telegram import TelegramChannel

def start(config):
    agent = Agent(config)
    if config.get("channels", {}).get("telegram", {}).get("enabled"):
        TelegramChannel(config, agent).run()
    else:
        print("Nu e activ niciun canal. Rulează `python cli.py agent` pentru chat în consolă.")