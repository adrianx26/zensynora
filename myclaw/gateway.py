from .agent import Agent
from .channels.telegram import TelegramChannel
from . import tools as tool_module


def start(config):
    """Build agent registry, initialize tools, and start the active channel."""

    # ── Feature 2: Multi-Agent Registry ──────────────────────────────────────
    registry = {"default": Agent(config, name="default")}

    for nc in config.agents.named:
        # Named agents can specify their own provider; fall back to defaults.
        agent_provider = nc.provider or None
        registry[nc.name] = Agent(
            config,
            name=nc.name,
            model=nc.model,
            system_prompt=nc.system_prompt or None,
            provider_name=agent_provider,
        )

    # Load any persisted custom tools (Feature 4)
    tool_module.load_custom_tools()

    # Inject registry into tools module (enables delegation, scheduling)
    tool_module.set_registry(registry)

    # ── Start channel ─────────────────────────────────────────────────────────
    if config.channels.telegram.enabled:
        TelegramChannel(config, registry).run()
    else:
        print("No channel is active. Run `python cli.py agent` for console chat.")