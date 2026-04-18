import sys
import asyncio
import signal
import atexit
import logging
from pathlib import Path
import click
from .config import load_config
from .agent import Agent
from .onboard import onboard as onboard_script
from .gateway import start
from .knowledge import (
    search_notes, list_notes, read_note, sync_knowledge, get_all_tags,
    write_note, Observation
)

logger = logging.getLogger(__name__)


def _graceful_shutdown():
    """6.3: Graceful shutdown handler - close all pools and connections."""
    logger.info("Shutting down gracefully...")
    try:
        # Close HTTP client pool
        from . import provider
        if hasattr(provider, 'HTTPClientPool'):
            asyncio.run(provider.HTTPClientPool.close())
    except Exception as e:
        logger.error(f"Error closing HTTP pool: {e}")
    
    try:
        # Close SQLite pool
        from . import memory
        if hasattr(memory, 'SQLitePool'):
            memory.SQLitePool.close_all()
    except Exception as e:
        logger.error(f"Error closing SQLite pool: {e}")
    
    logger.info("Graceful shutdown complete")


def _setup_shutdown_handlers():
    """6.3: Setup signal handlers for graceful shutdown."""
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        _graceful_shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(_graceful_shutdown)


_setup_shutdown_handlers()


def _build_registry(config) -> dict:
    """Build the agent registry from config."""
    from . import tools as tool_module
    registry = {"default": Agent(config, name="default")}
    for nc in config.agents.named:
        registry[nc.name] = Agent(
            config,
            name=nc.name,
            model=nc.model,
            system_prompt=nc.system_prompt or None
        )
    tool_module.load_custom_tools()
    tool_module.set_registry(registry)
    return registry


@click.group()
def cli():
    """ZenSynora (MyClaw) CLI"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

@cli.command()
def onboard():
    """Run the initial onboarding workflow"""
    onboard_script()

@cli.command()
def agent():
    """Start the interactive AI console with readline support"""
    config = load_config()
    registry = _build_registry(config)
    agent_names = ", ".join(registry.keys())

    # ── Readline setup for history and navigation ────────────────────────────
    try:
        import readline
        histfile = Path.home() / ".myclaw" / ".console_history"
        histfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(str(histfile))
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)

        # Enable tab completion for @agentname
        agent_names_list = list(registry.keys())
        def completer(text, state):
            if text.startswith("@"):
                matches = [f"@{n} " for n in agent_names_list if n.startswith(text[1:])]
            else:
                matches = []
            if state < len(matches):
                return matches[state]
            return None

        readline.parse_and_bind("tab: complete")
        readline.set_completer(completer)
    except ImportError:
        readline = None
        histfile = None

    async def run_agent_chat(registry, agent_names):
        if hasattr(config, 'mcp') and config.mcp.enabled:
            from .mcp import MCPClientManager
            mcp_manager = MCPClientManager(config.model_dump())
            await mcp_manager.start_all()

        # ── Start Background Research Scheduler ───────────────────────────────────
        if hasattr(config.intelligence, 'research_enabled') and config.intelligence.research_enabled:
            from .gateway import _run_research_if_idle
            from apscheduler.schedulers.background import BackgroundScheduler
            scheduler = BackgroundScheduler()
            interval = config.intelligence.research_interval_hours
            scheduler.add_job(_run_research_if_idle, 'interval', hours=interval)
            scheduler.start()
            logger.info(f"Background Researcher active (every {interval}h)")

        async with registry["default"]:
            print(f"💬 MyClaw console — agents: {agent_names}")
            print("   Use @agentname to address a specific agent. Type 'exit' to quit.")
            print("   Tab-complete agent names with @. Arrow keys navigate history.\n")
            while True:
                try:
                    msg = await asyncio.to_thread(input, "You: ")
                except (EOFError, KeyboardInterrupt):
                    print("\nBye!")
                    break
                if msg.strip().lower() in ["exit", "quit"]:
                    break

                if msg.startswith("@"):
                    parts = msg.split(None, 1)
                    name  = parts[0][1:]
                    text  = parts[1] if len(parts) > 1 else ""
                    agent_cls = registry.get(name) or registry["default"]
                else:
                    agent_cls = registry["default"]
                    text  = msg

                response = await agent_cls.think(text)
                print("Claw:", response)

        # Save history on exit
        if readline and histfile:
            readline.write_history_file(str(histfile))

    asyncio.run(run_agent_chat(registry, agent_names))

@cli.command()
def gateway():
    """Start the messaging gateway (Telegram/WhatsApp)"""
    config = load_config()
    start(config)

@cli.command(name="mcp-server")
def mcp_server():
    """Start ZenSynora exposed as an MCP Server"""
    config = load_config()
    _build_registry(config)
    from .mcp import start_mcp_server
    try:
        asyncio.run(start_mcp_server())
    except KeyboardInterrupt:
        print("\nMCP Server shutting down...")

### KNOWLEDGE COMMANDS ###
@cli.group()
def knowledge():
    """Manage the Knowledge Base"""
    pass

@knowledge.command()
@click.argument('query', required=False)
def search(query):
    """Search knowledge base"""
    if not query:
        query = click.prompt("Search query")
    notes = search_notes(query, "default")
    if notes:
        click.echo(f"\n🔍 Found {len(notes)} results for '{query}':\n")
        for i, note in enumerate(notes, 1):
            click.echo(f"{i}. {note.title} ({note.permalink})")
            if note.observations:
                for obs in note.observations[:2]:
                    click.echo(f"   - [{obs.category}] {obs.content[:60]}...")
    else:
        click.echo(f"No results found for: {query}")

@knowledge.command()
def write():
    """Create a new note (interactive)"""
    title = click.prompt("Title")
    click.echo("Content (press Enter twice to finish):")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    content = "\n".join(lines)
    tags_input = click.prompt("Tags (comma-separated)", default="", show_default=False)
    tags = [t.strip() for t in tags_input.split(",") if t.strip()]
    permalink = write_note(title, title, content, tags, "default")
    click.echo(f"\n✅ Created: {permalink}")

@knowledge.command()
@click.argument('permalink', required=False)
def read(permalink):
    """Read a specific note"""
    if not permalink:
        permalink = click.prompt("Permalink")
    note = read_note(permalink, "default")
    if note:
        click.echo(f"\n# {note.title}\nPermalink: {note.permalink}")
        if note.tags:
            click.echo(f"Tags: {', '.join(f'#{t}' for t in note.tags)}")
        if note.observations:
            click.echo("\n## Observations")
            for obs in note.observations:
                click.echo(f"- [{obs.category}] {obs.content}")
        if note.relations:
            click.echo("\n## Relations")
            for rel in note.relations:
                click.echo(f"- {rel.relation_type} → [[{rel.target}]]")
    else:
        click.echo(f"Note not found: {permalink}")

@knowledge.command()
def list():
    """List all notes"""
    notes = list_notes("default")
    if notes:
        click.echo(f"\n📚 {len(notes)} notes:\n")
        for note in notes:
            tag_str = f" [{' '.join(f'#{t}' for t in note.tags)}]" if note.tags else ""
            click.echo(f"- {note.title}{tag_str}")
    else:
        click.echo("Knowledge base is empty.")

@knowledge.command()
def sync():
    """Sync database with files"""
    click.echo("Syncing knowledge base...")
    stats = sync_knowledge("default")
    click.echo(f"✅ Sync complete: {stats['added'] + stats['updated'] + stats['deleted']} changes")

@knowledge.command()
def tags():
    """List all tags"""
    tags = get_all_tags("default")
    if tags:
        click.echo("\n🏷️ Tags: " + " ".join(f"#{t}" for t in tags))
    else:
        click.echo("No tags found.")

### MEMORY COMMANDS ###
@cli.group()
def memory():
    """Manage and inspect Conversation Memory"""
    pass

@memory.command()
def list_sessions():
    """List active user sessions and sizes"""
    from .memory import get_db
    with get_db() as conn:
        users = conn.execute("SELECT user_id, COUNT(id) FROM messages GROUP BY user_id").fetchall()
        if not users:
            click.echo("No memory records found.")
        else:
            for uid, count in users:
                click.echo(f"- Session: {uid} | Messages: {count}")

@memory.command()
@click.argument('user_id', default="default")
def clear(user_id):
    """Clear memory for a specific session"""
    from .memory import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    click.echo(f"Cleared memory for session {user_id}")

### SWARM COMMANDS ###
@cli.group()
def swarm():
    """Manage dynamic Swarm Agents"""
    pass

@swarm.command()
def status():
    """Print the current swarm orchestrator status"""
    click.echo("🐝 Swarm System: Active (Awaiting UI context)")

### SKILLS COMMANDS ###
@cli.group()
def skills():
    """Manage Python Tools and External Skills"""
    pass

@skills.command(name='list')
def list_skills():
    """List all registered tools in ZenSynora"""
    from .tools import TOOLS
    click.echo(f"🛠 Registered Tools ({len(TOOLS)}):")
    for name, metadata in TOOLS.items():
        click.echo(f" - {name}")

### WEB UI COMMANDS ###
@cli.command()
@click.option('--port', default=8000, help='Port to run the FastAPI proxy server')
def webui(port):
    """Start the Web UI Backend Server"""
    import uvicorn
    click.echo(f"Starting ZenSynora Web UI Backend on port {port}...")
    uvicorn.run("myclaw.web.api:app", host="0.0.0.0", port=port, reload=True)


@cli.command()
@click.option('--model', help='Specific model to benchmark')
@click.option('--provider', help='Specific provider to benchmark')
def benchmark(model, provider):
    """Run performance benchmarks on configured LLMs"""
    from .benchmark_runner import run_all_benchmarks, BenchmarkRunner
    import asyncio

    if model:
        config = load_config()
        runner = BenchmarkRunner(config)
        click.echo(f"🚀 Benchmarking {model}...")
        asyncio.run(runner.run_model_benchmark(model, provider))
        click.echo("\n📊 Results:")
        click.echo(runner.get_comparison_table())
    else:
        click.echo("🚀 Running full benchmark suite...")
        asyncio.run(run_all_benchmarks())


@cli.command()
def hardware():
    """Show detailed system hardware information and optimization suggestions."""
    from .backends.hardware import get_system_metrics, get_optimization_suggestions
    from rich.panel import Panel
    from rich.table import Table
    from rich.console import Console
    
    cons = Console()
    cons.print(Panel("[bold cyan]🔍 ZenSynora Hardware Diagnostic[/bold cyan]"))
    
    with cons.status("[bold green]Probing hardware metrics..."):
        metrics = get_system_metrics()
        suggestions = get_optimization_suggestions(metrics)
    
    # CPU Table
    cpu = metrics["cpu"]
    cpu_table = Table(title="CPU Information", box=None)
    cpu_table.add_column("Property", style="dim")
    cpu_table.add_column("Value")
    cpu_table.add_row("Model", cpu["model"])
    cpu_table.add_row("Cores/Threads", f"{cpu['physical_cores']} / {cpu['logical_threads']}")
    cpu_table.add_row("Current Load", f"{cpu['usage_pct']}%")
    if cpu["temperature_c"]:
        cpu_table.add_row("Temperature", f"{cpu['temperature_c']}°C")
    
    cons.print(cpu_table)
    
    # Memory Table
    mem = metrics["memory"]
    mem_table = Table(title="Memory (RAM)", box=None)
    mem_table.add_column("Property", style="dim")
    mem_table.add_column("Value")
    mem_table.add_row("Total Size", f"{mem['total_gb']} GB")
    mem_table.add_row("Available", f"{mem['available_gb']} GB")
    mem_table.add_row("Type Hint", mem["type"])
    
    cons.print(mem_table)
    
    # GPU Table
    if metrics["gpu"]:
        gpu_table = Table(title="GPU Details", box=None)
        gpu_table.add_column("GPU", style="dim")
        gpu_table.add_column("Memory", style="dim")
        gpu_table.add_column("Temp")
        for g in metrics["gpu"]:
            gpu_table.add_row(g.get("model", "Unknown"), f"{g.get('memory_total_mb', 'N/A')} MB", f"{g.get('temperature_c', 'N/A')}°C")
        cons.print(gpu_table)
    else:
        cons.print("[yellow]No dedicated GPU detected.[/yellow]")
        
    # NPU & Network
    npu = metrics["npu"]
    net = metrics["network"]
    cons.print(f"\n[bold]NPU Support:[/bold] {npu['type']} ({'Active' if npu['active'] else 'Inactive'})")
    cons.print(f"[bold]Network Lag:[/bold] {net['ping_ms']}ms to 8.8.8.8")
    
    # Suggestions
    if suggestions:
        cons.print("\n[bold green]🛠️ Optimization Suggestions:[/bold green]")
        for s in suggestions:
            cons.print(f" • {s}")
