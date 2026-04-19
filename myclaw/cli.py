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

        # ── Start Background Research Scheduler (Phase 6.2: AsyncScheduler) ───────
        _sched = None
        if hasattr(config.intelligence, 'research_enabled') and config.intelligence.research_enabled:
            from .gateway import _run_research_if_idle
            from .async_scheduler import get_scheduler
            _sched = get_scheduler()
            interval = config.intelligence.research_interval_hours
            _sched.add_job(_run_research_if_idle, 'interval', hours=interval, id="cli_research")
            await _sched.start()
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

        # Shutdown AsyncScheduler gracefully
        if _sched is not None:
            await _sched.shutdown(wait=True)
            logger.info("AsyncScheduler shutdown complete")

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


# ── Config encryption commands ───────────────────────────────────────────────

@cli.group()
def config_cmd():
    """Configuration management commands."""
    pass


@config_cmd.command(name="encrypt")
def config_encrypt():
    """Encrypt config.json secrets at rest using Fernet."""
    from .config_encryption import encrypt_config
    try:
        encrypt_config()
        click.echo("✅ Config encrypted successfully.")
    except Exception as e:
        click.echo(f"❌ Encryption failed: {e}")
        raise click.ClickException(str(e))


@config_cmd.command(name="decrypt")
def config_decrypt():
    """Decrypt config.json to plaintext (for editing)."""
    from .config_encryption import decrypt_config
    try:
        decrypt_config()
        click.echo("✅ Config decrypted successfully.")
    except Exception as e:
        click.echo(f"❌ Decryption failed: {e}")
        raise click.ClickException(str(e))


@config_cmd.command(name="status")
def config_status():
    """Show config encryption status."""
    from .config_encryption import is_encrypted, CONFIG_FILE, _load_key
    encrypted = is_encrypted(CONFIG_FILE)
    key_exists = _load_key() is not None
    click.echo(f"Config file: {CONFIG_FILE}")
    click.echo(f"Encrypted: {'yes' if encrypted else 'no'}")
    click.echo(f"Key available: {'yes' if key_exists else 'no'}")
    if encrypted and not key_exists:
        click.echo("⚠️  WARNING: Config is encrypted but no key found!")


# ── Audit log commands ───────────────────────────────────────────────────────

@cli.group()
def audit():
    """Audit log management commands."""
    pass


@audit.command(name="verify")
def audit_verify():
    """Verify the tamper-evident integrity of the audit log."""
    from .audit_log import TamperEvidentAuditLog
    log = TamperEvidentAuditLog()
    result = log.verify_integrity()
    if result["valid"]:
        click.echo(f"✅ Audit log integrity verified ({result['entries']} entries, last hash: {result['last_hash'][:16]}...)")
    else:
        click.echo(f"❌ Audit log integrity FAILED at entry {result['index']}: {result['reason']}")
        raise click.ClickException("Audit log has been tampered with or is corrupted.")


@audit.command(name="export")
@click.argument("output_path", required=False, default="audit_export.jsonl")
def audit_export(output_path: str):
    """Export the audit log to a file."""
    from .audit_log import TamperEvidentAuditLog
    log = TamperEvidentAuditLog()
    try:
        exported = log.export(output_path)
        click.echo(f"✅ Audit log exported to: {exported}")
    except Exception as e:
        click.echo(f"❌ Export failed: {e}")
        raise click.ClickException(str(e))


@audit.command(name="status")
def audit_status():
    """Show audit log status and recent entries."""
    from .audit_log import TamperEvidentAuditLog
    log = TamperEvidentAuditLog()
    entries = log.read_entries(limit=5)
    click.echo(f"Audit log path: {log.log_path}")
    click.echo(f"Recent entries: {len(entries)}")
    for entry in entries:
        ts = entry.get("timestamp", "?")
        event = entry.get("event_type", "?")
        severity = entry.get("severity", "INFO")
        click.echo(f"  [{ts}] {severity}: {event}")


# ── GDPR compliance commands ─────────────────────────────────────────────────

@cli.group()
def gdpr():
    """GDPR compliance helpers (requires gdpr_enabled in config)."""
    pass


@gdpr.command(name="delete")
@click.argument("user_id")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
def gdpr_delete(user_id: str, dry_run: bool):
    """Delete all data for a user (Right to Erasure)."""
    from .config import load_config
    config = load_config()
    if not getattr(getattr(config, "security", None), "gdpr_enabled", False):
        click.echo("❌ GDPR features are disabled. Enable in config: security.gdpr_enabled = true")
        raise click.ClickException("GDPR features disabled")

    from .gdpr import delete_user_data
    result = delete_user_data(user_id, dry_run=dry_run)

    action = "Would delete" if dry_run else "Deleted"
    click.echo(f"{action} data for user: {user_id}")
    for item in result["items"]:
        if "count" in item:
            click.echo(f"  • {item['type']}: {item['count']} items")
        elif "file_count" in item:
            click.echo(f"  • {item['type']}: {item['file_count']} files")
        else:
            click.echo(f"  • {item['type']}: {item['path']}")

    if not dry_run:
        click.echo(f"✅ Total items deleted: {result['total_items']}")
    else:
        click.echo(f"🔍 Total items that would be deleted: {result['total_items']}")


@gdpr.command(name="export")
@click.argument("user_id")
@click.option("--output", "-o", help="Output ZIP file path")
def gdpr_export(user_id: str, output: Optional[str]):
    """Export all data for a user (Right to Data Portability)."""
    from .config import load_config
    config = load_config()
    if not getattr(getattr(config, "security", None), "gdpr_enabled", False):
        click.echo("❌ GDPR features are disabled. Enable in config: security.gdpr_enabled = true")
        raise click.ClickException("GDPR features disabled")

    from .gdpr import export_user_data
    path = export_user_data(user_id, output)
    click.echo(f"✅ User data exported to: {path}")


# ── MFA / TOTP commands ──────────────────────────────────────────────────────

@cli.group()
def mfa():
    """Multi-factor authentication (TOTP) management."""
    pass


@mfa.command(name="setup")
@click.argument("user_id")
def mfa_setup(user_id: str):
    """Set up MFA for a user. Displays QR code URL and secret."""
    from .mfa import MFAAuth
    auth = MFAAuth()
    if not auth.is_available():
        click.echo("❌ pyotp not installed. Run: pip install pyotp")
        return
    result = auth.provision_user(user_id)
    click.echo(f"✅ MFA provisioned for user: {user_id}")
    click.echo(f"Secret: {result['secret']}")
    click.echo(f"Provisioning URI: {result['provisioning_uri']}")
    if result.get("qr_code_png_base64"):
        click.echo("QR Code (base64 PNG) available. Use an authenticator app to scan.")


@mfa.command(name="verify")
@click.argument("user_id")
@click.argument("code")
def mfa_verify(user_id: str, code: str):
    """Verify a TOTP code for a user."""
    from .mfa import MFAAuth
    auth = MFAAuth()
    if not auth.is_available():
        click.echo("❌ pyotp not installed.")
        return
    ok = auth.verify(user_id, code)
    click.echo("✅ Code valid" if ok else "❌ Code invalid")


@mfa.command(name="disable")
@click.argument("user_id")
def mfa_disable(user_id: str):
    """Disable MFA for a user."""
    from .mfa import MFAAuth
    auth = MFAAuth()
    if not auth.is_available():
        click.echo("❌ pyotp not installed.")
        return
    auth.disable_user(user_id)
    click.echo(f"✅ MFA disabled for user: {user_id}")


@mfa.command(name="status")
@click.argument("user_id")
def mfa_status(user_id: str):
    """Check MFA status for a user."""
    from .mfa import MFAAuth
    auth = MFAAuth()
    enabled = auth.is_enabled_for_user(user_id)
    click.echo(f"MFA for {user_id}: {'enabled' if enabled else 'disabled'}")


# ── Metering commands ────────────────────────────────────────────────────────

@cli.group()
def metering():
    """Usage-based metering and quota management."""
    pass


@metering.command(name="status")
@click.argument("user_id")
def metering_status(user_id: str):
    """Show usage and quota status for a user."""
    from .metering import get_user_summary
    summary = get_user_summary(user_id)
    click.echo(f"Usage for {user_id} (period: {summary['period']}):")
    for qname, info in summary["quotas"].items():
        click.echo(f"  {qname}: {info['used']} / {info['limit']} (remaining: {info['remaining']})")


@metering.command(name="set-quota")
@click.argument("user_id")
@click.argument("quota_name")
@click.argument("limit_value", type=int)
def metering_set_quota(user_id: str, quota_name: str, limit_value: int):
    """Set a quota limit for a user."""
    from .metering import set_quota
    set_quota(user_id, quota_name, limit_value)
    click.echo(f"✅ Quota set: {user_id} {quota_name} = {limit_value}")


# ── Knowledge Spaces commands ────────────────────────────────────────────────

@cli.group()
def spaces():
    """Collaborative knowledge spaces with RBAC."""
    pass


@spaces.command(name="create")
@click.argument("name")
@click.option("--owner", "-o", required=True, help="Owner user ID")
@click.option("--description", "-d", default="", help="Space description")
def spaces_create(name: str, owner: str, description: str):
    """Create a new knowledge space."""
    from .knowledge_spaces import create_space
    sid = create_space(name=name, owner=owner, description=description)
    click.echo(f"✅ Space created: {sid}")


@spaces.command(name="list")
@click.argument("user_id")
def spaces_list(user_id: str):
    """List spaces for a user."""
    from .knowledge_spaces import list_spaces
    spaces = list_spaces(user_id)
    if not spaces:
        click.echo("No spaces found.")
        return
    click.echo(f"Spaces for {user_id}:")
    for s in spaces:
        click.echo(f"  • {s['id']}: {s['name']} (role: {s['user_role']})")


@spaces.command(name="members")
@click.argument("space_id")
def spaces_members(space_id: str):
    """Show members of a space."""
    from .knowledge_spaces import get_space
    space = get_space(space_id)
    if not space:
        click.echo("❌ Space not found.")
        return
    click.echo(f"Space: {space['name']} (owner: {space['owner']})")
    click.echo("Members:")
    for m in space["members"]:
        click.echo(f"  • {m['user_id']}: {m['role']}")


@spaces.command(name="add-member")
@click.argument("space_id")
@click.argument("user_id")
@click.argument("role")
@click.option("--by", "added_by", required=True, help="Admin user performing the action")
def spaces_add_member(space_id: str, user_id: str, role: str, added_by: str):
    """Add a member to a space."""
    from .knowledge_spaces import add_member
    if add_member(space_id, user_id, role, added_by):
        click.echo(f"✅ Added {user_id} as {role}")
    else:
        click.echo("❌ Failed to add member (check permissions and role).")


@spaces.command(name="remove-member")
@click.argument("space_id")
@click.argument("user_id")
@click.option("--by", "removed_by", required=True, help="Admin user performing the action")
def spaces_remove_member(space_id: str, user_id: str, removed_by: str):
    """Remove a member from a space."""
    from .knowledge_spaces import remove_member
    if remove_member(space_id, user_id, removed_by):
        click.echo(f"✅ Removed {user_id}")
    else:
        click.echo("❌ Failed to remove member (check permissions).")


@spaces.command(name="delete")
@click.argument("space_id")
@click.argument("owner")
def spaces_delete(space_id: str, owner: str):
    """Delete a space (owner only)."""
    from .knowledge_spaces import delete_space
    if delete_space(space_id, owner):
        click.echo(f"✅ Space {space_id} deleted.")
    else:
        click.echo("❌ Failed to delete space (not found or not owner).")
