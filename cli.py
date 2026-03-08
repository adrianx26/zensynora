import sys
import asyncio
from myclaw.config import load_config
from myclaw.agent import Agent
from onboard import onboard
from myclaw.gateway import start


def _build_registry(config) -> dict:
    """Build the agent registry from config."""
    from myclaw import tools as tool_module
    registry = {"default": Agent(config)}
    for nc in config.agents.named:
        registry[nc.name] = Agent(
            config,
            model=nc.model,
            system_prompt=nc.system_prompt or None
        )
    tool_module.load_custom_tools()
    tool_module.set_registry(registry)
    return registry


def main():
    config = load_config()
    if len(sys.argv) < 2:
        print("Commands: onboard | agent | gateway")
        return

    cmd = sys.argv[1]

    if cmd == "onboard":
        onboard()

    elif cmd == "agent":
        registry = _build_registry(config)
        agent_names = ", ".join(registry.keys())

        # Use the default agent as the context manager for clean shutdown
        async def run_agent_chat(registry, agent_names):
            with registry["default"]:
                print(f"💬 MyClaw console — agents: {agent_names}")
                print("   Use @agentname to address a specific agent. Type 'exit' to quit.")
                while True:
                    try:
                        # In true async we should use aioconsole or run_in_executor for input, but input() is fine for MVP CLI
                        import asyncio
                        msg = await asyncio.to_thread(input, "Tu: ")
                    except (EOFError, KeyboardInterrupt):
                        print("\nBye!")
                        break
                    if msg.strip().lower() in ["exit", "quit"]:
                        break

                    if msg.startswith("@"):
                        parts = msg.split(None, 1)
                        name  = parts[0][1:]
                        text  = parts[1] if len(parts) > 1 else ""
                        agent = registry.get(name) or registry["default"]
                    else:
                        agent = registry["default"]
                        text  = msg

                    response = await agent.think(text)
                    print("Claw:", response)

        asyncio.run(run_agent_chat(registry, agent_names))

    elif cmd == "gateway":
        start(config)

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: onboard | agent | gateway")


if __name__ == "__main__":
    main()