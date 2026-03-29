from .agent import Agent
from .channels.telegram import TelegramChannel
from .channels.whatsapp import WhatsAppChannel
from . import tools as tool_module
from .backends import discover_backends, get_default_backend

import asyncio
from concurrent.futures import ThreadPoolExecutor

def start(config):
    """Build agent registry, initialize tools, and start the active channel."""

    max_workers = config.channels.telegram.max_workers if hasattr(config.channels, 'telegram') else 20
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="MyClawWorker")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.set_default_executor(executor)

    try:
        discover_backends()
        backend_config = config.backends.__dict__ if hasattr(config, 'backends') else None
        default_backend = get_default_backend(backend_config)
        
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
        
        # Inject config into tools module (enables timeout configuration)
        tool_module.set_config(config)

        # ── Start channel ─────────────────────────────────────────────────────────
        if config.channels.telegram.enabled:
            TelegramChannel(config, registry).run()
        elif config.channels.whatsapp.enabled:
            WhatsAppChannel(config, registry).run()
        else:
            print("No channel is active. Run `python cli.py agent` for console chat.")
    finally:
        print("\nShutting down global ThreadPoolExecutor gracefully...")
        executor.shutdown(wait=True)