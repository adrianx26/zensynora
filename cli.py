import sys
import json
from myclaw.config import load_config
from myclaw.agent import Agent
from onboard import onboard
from myclaw.gateway import start

def main():
    config = load_config()
    if len(sys.argv) < 2:
        print("Comenzi: onboard | agent | gateway")
        return

    cmd = sys.argv[1]
    if cmd == "onboard":
        onboard()
    elif cmd == "agent":
        agent = Agent(config)
        print("💬 MyClaw consolă (scrie 'exit' să ieși)")
        while True:
            msg = input("Tu: ")
            if msg.strip().lower() in ["exit", "quit"]:
                break
            print("Claw:", agent.think(msg))
    elif cmd == "gateway":
        start(config)
    else:
        print("Comandă necunoscută")

if __name__ == "__main__":
    main()