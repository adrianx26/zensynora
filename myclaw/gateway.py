"""
Gateway - Application entry point and initialization.

The gateway module is responsible for bootstrapping the entire MyClaw system:
backend discovery, agent registry creation, tool initialization, and channel startup.

Key Responsibilities:
    - Backend Discovery: Detect and initialize compute backends (local, WSL2, SSH, Docker)
    - Agent Registry: Create default and named agent instances
    - Tool Initialization: Load custom tools from TOOLBOX and set registries
    - Channel Startup: Launch Telegram or WhatsApp bot channels
    - ThreadPool Management: Configure executor for concurrent operations

Usage:
    from myclaw.config import load_config
    from myclaw.gateway import start

    config = load_config()
    start(config)  # Blocks and runs the active channel

Exit:
    The function runs indefinitely until interrupted (Ctrl+C), then gracefully
    shuts down the ThreadPoolExecutor.
"""

from .agent import Agent
from .channels.telegram import TelegramChannel
from .channels.whatsapp import WhatsAppChannel
from . import tools as tool_module
from .backends import discover_backends, get_default_backend

import asyncio
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from .async_scheduler import AsyncScheduler, get_scheduler
from .knowledge.researcher import start_researcher_job
from .agent import get_last_active_time
from .config import load_config
from .agents.medic_agent import MedicAgent
from .agents.medic_agent import set_config as set_medic_config
from .agents.newtech_agent import set_config as set_newtech_config

logger = logging.getLogger(__name__)


def _run_startup_health_check(config):
    """Run MedicAgent startup scan when explicitly enabled in config."""
    try:
        medic_cfg = getattr(config, "medic", None)
        if not medic_cfg:
            logger.info("Medic config not found; skipping startup health check.")
            return
        if not getattr(medic_cfg, "enabled", False):
            logger.info("Medic agent disabled; skipping startup health check.")
            return
        if not getattr(medic_cfg, "scan_on_startup", False):
            logger.info("scan_on_startup disabled; skipping startup health check.")
            return

        logger.info("Starting health check...")
        medic = MedicAgent()
        result = medic.scan_system()
        logger.info("Startup health check completed: %s", result)
    except Exception as exc:
        logger.error("Startup health check failed (continuing startup): %s", exc)

def _run_research_if_idle():
    """Background task wrapper: only runs research if system is idle."""
    config = load_config()
    if not getattr(config.intelligence, 'research_enabled', True):
        return

    idle_threshold = getattr(config.intelligence, 'research_idle_minutes', 15) * 60
    last_active = get_last_active_time()
    idle_duration = time.time() - last_active

    if idle_duration >= idle_threshold:
        logger.info(f"System idle for {idle_duration/60:.1f} mins. Starting research batch.")
        # Use a new event loop for this background thread
        asyncio.run(start_researcher_job())
    else:
        logger.debug(f"System busy (last active {idle_duration/60:.1f} mins ago). Skipping research.")

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
        if default_backend:
            logger.info("Default backend selected: %s", default_backend.get_type())

        _run_startup_health_check(config)

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
        set_medic_config(config)
        set_newtech_config(config)

        # ── Start MCP Client ──────────────────────────────────────────────────────
        if hasattr(config, 'mcp') and config.mcp.enabled:
            from .mcp import MCPClientManager
            mcp_manager = MCPClientManager(config.model_dump())
            loop.create_task(mcp_manager.start_all())

        # ── Start Background Research Scheduler ───────────────────────────────────
        # Phase 6.2: Replaced apscheduler.BackgroundScheduler with AsyncScheduler
        scheduler = None
        if config.intelligence.research_enabled:
            scheduler = get_scheduler()
            interval = config.intelligence.research_interval_hours
            scheduler.add_job(
                _run_research_if_idle,
                'interval',
                hours=interval,
                id="research_job",
            )
            loop.create_task(scheduler.start())
            logger.info(f"Knowledge Researcher scheduled every {interval} hours")

        # ── Start Background Knowledge File-Sync Extraction ───────────────────────
        # Triggered when config.knowledge.auto_extract = true (default: false).
        # Runs sync_knowledge() every 60 s so any Markdown notes written to disk
        # are automatically picked up and indexed in the SQLite knowledge graph.
        if getattr(config.knowledge, 'auto_extract', False):
            from .knowledge.sync import start_background_extraction
            try:
                start_background_extraction(user_id="default", interval_seconds=60)
                logger.info("Background knowledge file-sync extraction started (interval: 60s)")
            except Exception as _ke:
                logger.warning(f"Could not start background knowledge extraction: {_ke}")

        # ── Start channel ─────────────────────────────────────────────────────────
        if config.channels.telegram.enabled:
            TelegramChannel(config, registry).run()
        elif config.channels.whatsapp.enabled:
            WhatsAppChannel(config, registry).run()
        else:
            print("No channel is active. Run `python cli.py agent` for console chat.")
    finally:
        # Phase 6.2: Shutdown AsyncScheduler gracefully
        try:
            _sched = get_scheduler()
            if _sched._running:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(_sched.shutdown(wait=True))
                else:
                    asyncio.run(_sched.shutdown(wait=True))
                logger.info("AsyncScheduler shutdown complete")
        except Exception as _sched_err:
            logger.debug(f"AsyncScheduler shutdown note: {_sched_err}")

        print("\nShutting down global ThreadPoolExecutor gracefully...")
        # Use shutdown(wait=False) to avoid blocking the event loop
        # The executor will finish pending tasks in background
        executor.shutdown(wait=False)
        # Give tasks a moment to complete cleanup
        import time
        time.sleep(0.5)
        print("ThreadPoolExecutor shutdown complete.")
