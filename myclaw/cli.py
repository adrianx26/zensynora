import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional
import click
from . import init_app
from .config import load_config
from .agent import Agent
from .onboard import onboard as onboard_script
from .gateway import start
from .knowledge import (
    search_notes, list_notes, read_note, sync_knowledge, get_all_tags,
    write_note, Observation
)

logger = logging.getLogger(__name__)

# Centralised bootstrapping: logging config, shutdown handlers, directory checks.
init_app()


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


def _switch_to_active_release() -> None:
    """Re-run this command with the installed version if we are an old one.

    Added 2026-10-14 (v0.7.2). `zensynora update` installs the new version
    next to the old one and repoints ``~/.local/share/zensynora/source``. A
    terminal that activated the venv *before* the update still has the old
    release's ``venv/bin`` on its PATH, so `zensynora webui` started there
    ran the old version — the Web UI kept showing the previous release.
    Now the command notices and hands over to the installed version.
    Set ZENSYNORA_NO_REEXEC=1 to disable.
    """
    if os.environ.get("ZENSYNORA_NO_REEXEC"):
        return
    try:
        from .doctor.paths import active_release, install_root, stale_release

        stale = stale_release()
        if not stale:
            return
        active = active_release()
        py = active / "venv" / "bin" / "python"
        if not py.exists():
            return
    except Exception:
        return
    old_bin = str(install_root() / "venv" / "bin")
    new_bin = str(active / "venv" / "bin")
    click.echo(f"ZenSynora {stale['installed']} is installed; this terminal still points at "
               f"{stale['running']} — switching to {stale['installed']}.", err=True)
    click.echo("  (Open a new terminal, or run `source ~/.local/share/zensynora/source/venv/bin/activate`, "
               "to stop seeing this.)", err=True)
    env = dict(os.environ)
    env["ZENSYNORA_NO_REEXEC"] = "1"
    env["VIRTUAL_ENV"] = str(active / "venv")
    env.pop("PYTHONPATH", None)
    env["PATH"] = os.pathsep.join([new_bin] + [p for p in env.get("PATH", "").split(os.pathsep) if p and p != old_bin])
    os.execve(str(py), [str(py), "-m", "myclaw", *sys.argv[1:]], env)


@click.group()
@click.pass_context
def cli(ctx):
    """ZenSynora (MyClaw) CLI"""
    # `update` must run from wherever it was started (it manages releases).
    if ctx.invoked_subcommand not in (None, "update", "version"):
        _switch_to_active_release()

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

    async def _run_and_cleanup():
        try:
            await run_agent_chat(registry, agent_names)
        finally:
            # Pooled aiosqlite connections run on non-daemon threads; if they
            # are not closed the interpreter waits on them forever at exit.
            from .memory import cleanup_on_shutdown
            await cleanup_on_shutdown()

    asyncio.run(_run_and_cleanup())

@cli.command()
@click.option("--webhook-url", default=None, help="Telegram webhook mode: the public HTTPS URL Telegram calls")
@click.option("--port", default=8443, show_default=True, help="Port for the webhook server")
@click.option("--telegram", "force_telegram", is_flag=True,
              help="Run the Telegram bot here even if it is set to run inside the Web UI")
def gateway(webhook_url, port, force_telegram):
    """Start the messaging gateway (Telegram/WhatsApp) without the Web UI"""
    config = load_config()
    start(config, webhook_url=webhook_url, port=port, force_telegram=force_telegram)

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

# 2026-10-23 (KB phase 0): every knowledge command was hard-coded to the
# "default" base; --base/-b picks another one (a folder in the knowledge dir).
def _check_base(ctx, param, value):
    import re

    value = (value or "default").strip() or "default"
    # Same rule as the Web UI: a plain folder name, no path, no hidden folder.
    if not re.match(r"^[\w.@-]{1,64}$", value) or value.startswith("."):
        raise click.BadParameter(
            f"'{value}' is not a knowledge base name (letters, digits, '.', '@', '-', '_').")
    return value


_BASE_OPTION = click.option("--base", "-b", default="default", show_default=True,
                            callback=_check_base, help="Knowledge base to use")


@knowledge.command()
@click.argument('query', required=False)
@_BASE_OPTION
def search(query, base):
    """Search knowledge base (supports AND / OR / NOT and "quoted phrases")"""
    if not query:
        query = click.prompt("Search query")
    # 2026-10-23 (KB phase 0): boolean=True keeps AND/OR/NOT as search syntax.
    notes = search_notes(query, base, boolean=True)
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
@_BASE_OPTION
def write(base):
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
    permalink = write_note(title, title=title, content=content, tags=tags, user_id=base)
    click.echo(f"\n✅ Created: {permalink}")

@knowledge.command()
@click.argument('permalink', required=False)
@_BASE_OPTION
def read(permalink, base):
    """Read a specific note"""
    from .knowledge.parser import extract_body

    if not permalink:
        permalink = click.prompt("Permalink")
    note = read_note(permalink, base)
    if note:
        click.echo(f"\n# {note.title}\nPermalink: {note.permalink}")
        if note.tags:
            click.echo(f"Tags: {', '.join(f'#{t}' for t in note.tags)}")
        # 2026-10-23 (KB phase 0): show the note's text, not only facts and links.
        body = extract_body(note.content or "")
        if body:
            click.echo(f"\n{body}")
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

# Named list_cmd so the module-level name does not shadow the builtin list():
# `agent` calls list(registry.keys()) and crashed with
# "Got unexpected extra argument (default)".
@knowledge.command(name="list")
@_BASE_OPTION
def list_cmd(base):
    """List all notes"""
    notes = list_notes(base)
    if notes:
        click.echo(f"\n📚 {len(notes)} notes:\n")
        for note in notes:
            tag_str = f" [{' '.join(f'#{t}' for t in note.tags)}]" if note.tags else ""
            click.echo(f"- {note.title}{tag_str}")
    else:
        click.echo("Knowledge base is empty.")

@knowledge.command()
@_BASE_OPTION
@click.option("--force", is_flag=True, help="Re-index every note, not only changed files")
def sync(base, force):
    """Sync database with files"""
    click.echo("Syncing knowledge base...")
    stats = sync_knowledge(base, force=force)
    click.echo(f"✅ Sync complete: {stats['added'] + stats['updated'] + stats['deleted']} changes")

@knowledge.command()
@_BASE_OPTION
def tags(base):
    """List all tags"""
    tags = get_all_tags(base)
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
    # One SQLite file per user: ~/.myclaw/memory_<user>.db
    import sqlite3
    from pathlib import Path

    found = False
    for db in sorted((Path.home() / ".myclaw").glob("memory_*.db")):
        try:
            with sqlite3.connect(db) as conn:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        except sqlite3.Error as e:
            click.echo(f"- Session: {db.stem[7:]} | unreadable ({e})")
            continue
        found = True
        click.echo(f"- Session: {db.stem[7:]} | Messages: {count}")
    if not found:
        click.echo("No memory records found.")

@memory.command()
@click.argument('user_id', default="default")
def clear(user_id):
    """Clear memory for a specific session"""
    import sqlite3
    from pathlib import Path

    db = Path.home() / ".myclaw" / f"memory_{user_id}.db"
    if not db.exists():
        click.echo(f"No memory for session {user_id}.")
        return
    with sqlite3.connect(db) as conn:
        n = conn.execute("DELETE FROM messages").rowcount
    click.echo(f"Cleared memory for session {user_id} ({n} messages)")

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
@click.option("--tools", "show_tools", is_flag=True, help="Also list every tool")
def list_skills(show_tools):
    """List agent skills (and, with --tools, all tools)"""
    from .agent_skills import list_skills as _skills, user_dir
    from .config import load_config
    from .tools import TOOLS

    cfg = load_config()
    used = {}
    for a in ["default"] + [n.name for n in cfg.agents.named]:
        from .agent_skills import skills_for
        for s in skills_for(cfg, a):
            used.setdefault(s, []).append(a)
    rows = _skills()
    click.echo(f"📚 Skills ({len(rows)}) — yours are in {user_dir()}:")
    for s in rows:
        who = f"  [{', '.join('@' + a for a in used.get(s['name'], []))}]" if used.get(s["name"]) else ""
        click.echo(f" - {s['name']} ({'built-in' if s['source'] == 'builtin' else 'yours'}): {s['description']}{who}")
    click.echo("Give skills to agents in the Web UI (Skills page or Settings → Agents).")
    if show_tools:
        click.echo(f"\n🛠 Tools ({len(TOOLS)}):")
        for name in TOOLS:
            click.echo(f" - {name}")

### UPDATE / VERSION ###
@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
)
@click.argument("updater_args", nargs=-1, type=click.UNPROCESSED)
def update(updater_args):
    """Safely update ZenSynora (backup, side-by-side build, auto-rollback).

    \b
    zensynora update                 update to the latest version
    zensynora update status --check  show installed/latest version
    zensynora update rollback        go back to the previous version
    zensynora update --help          all options
    """
    import os
    import shutil

    root = Path(__file__).resolve().parent.parent
    script = root / "update.sh"
    if not script.exists():
        raise click.ClickException(
            f"update.sh not found in {root}. Update once with install-online.sh to get the updater."
        )
    bash = shutil.which("bash") or "/bin/bash"
    os.execv(bash, [bash, str(script), *updater_args])


@cli.command()
@click.option("--fix", is_flag=True, help="Apply safe automatic fixes (folders, permissions, migrations).")
@click.option("--offline", is_flag=True, help="Skip checks that need the network (LLM server).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option("--problems", is_flag=True, help="Only show warnings and failures.")
@click.option("--report", "report_path", is_flag=False, flag_value=".", default=None, metavar="[PATH]",
              help="Write a full diagnostics report (Markdown; .json or .zip by extension) and print its path.")
@click.option("--hours", default=24, show_default=True, help="Period the report covers.")
def doctor(fix, offline, as_json, problems, report_path, hours):
    """Check this installation and explain how to fix problems.

    \b
    zensynora doctor --report              report in this folder (zensynora-report-….md)
    zensynora doctor --report ~/r.json     JSON (or .zip: report + log tail)
    Exit code: 0 = healthy, 1 = warnings, 2 = failures.
    """
    import json as _json

    from .doctor import apply_fixes, run_checks

    if report_path is not None:
        from .doctor import report as _report

        click.echo(_report.write(report_path, hours=hours, offline=offline))
        return

    report = run_checks(offline=offline)
    if fix:
        done = apply_fixes(report)
        if done and not as_json:
            click.echo("Fixes applied:\n  " + "\n  ".join(done) + "\n")
        report = run_checks(offline=offline) if done else report
    if as_json:
        click.echo(_json.dumps(report.to_dict(), indent=2))
    else:
        click.echo(report.to_text(show_ok=not problems))
    sys.exit({"ok": 0, "warn": 1, "fail": 2}[report.overall])


### BACKUPS (v0.7.1) ###
@cli.group()
def backup():
    """Back up / restore settings, API keys, knowledge base, memory and jobs.

    \b
    zensynora backup create              everything (as set in Settings → Backups)
    zensynora backup create -p settings  only settings + API keys
    zensynora backup list
    zensynora backup restore NAME        (a safety backup is taken first)
    """


@backup.command("create")
@click.option("-p", "--part", "parts", multiple=True,
              type=click.Choice(["settings", "knowledge", "memory", "jobs"]),
              help="What to include (repeat). Default: the parts chosen in Settings.")
@click.option("--note", default="", help="A note stored with the backup.")
def backup_create(parts, note):
    """Create a backup now."""
    from . import backup as _backup

    info = _backup.create_backup(list(parts) or None, "manual", note)
    click.echo(f"✔ {info['name']}  ({info['bytes'] // 1024} KB, parts: {', '.join(info.get('parts', []))})")
    click.echo(f"  in {Path(info['path']).parent}")


@backup.command("list")
def backup_list():
    """List backups (newest first)."""
    from . import backup as _backup

    items = _backup.list_backups()
    if not items:
        click.echo(f"No backups yet in {_backup.backup_dir()}.")
        return
    for b in items:
        state = "" if b.get("valid") else "  [unreadable]"
        click.echo(f"{b['name']}  {b['bytes'] // 1024:>7} KB  v{b.get('version', '?')}  "
                   f"{','.join(b.get('parts', []))}{state}")


@backup.command("restore")
@click.argument("name")
@click.option("-p", "--part", "parts", multiple=True,
              type=click.Choice(["settings", "knowledge", "memory", "jobs"]),
              help="Only restore these parts (repeat). Default: everything in the backup.")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def backup_restore(name, parts, yes):
    """Restore a backup (stop the web UI / agents first)."""
    from . import backup as _backup

    if not yes:
        click.confirm(f"Restore {name}? Current files will be replaced (a safety backup is made first)",
                      abort=True)
    try:
        r = _backup.restore_backup(name, list(parts) or None)
    except FileNotFoundError:
        raise click.ClickException(f"No backup called {name}. See: zensynora backup list")
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"✔ Restored {r['restored']} files ({', '.join(r['parts'])}). "
               f"Undo with: zensynora backup restore {r['safety_backup']}")


@backup.command("delete")
@click.argument("name")
def backup_delete(name):
    """Delete a backup."""
    from . import backup as _backup

    try:
        _backup.delete_backup(name)
    except FileNotFoundError:
        raise click.ClickException(f"No backup called {name}.")
    click.echo(f"Deleted {name}")


### MEDIC SELF-UPDATE (v0.7.1) ###
@cli.group()
def medic():
    """Review code changes proposed by agents (approval-gated self-update).

    \b
    zensynora medic proposals            list (add --status pending)
    zensynora medic show ID              reason + diff
    zensynora medic approve ID           approve and install via the safe updater
    zensynora medic reject ID --note …
    zensynora medic problems             what the medic would look at
    """


@medic.command("proposals")
@click.option("--status", type=click.Choice(["pending", "approved", "applying", "applied", "failed", "rejected"]))
def medic_proposals(status):
    from . import selfupdate

    items = selfupdate.list_proposals(status)
    if not items:
        click.echo("No proposals.")
    for p in items:
        click.echo(f"{p['id']}  [{p['status']:<8}] {p.get('risk', '?'):<4} risk  {p['title']}  (by {p['author']})")


@medic.command("show")
@click.argument("pid")
def medic_show(pid):
    from . import selfupdate

    p = selfupdate.get_proposal(pid)
    if not p:
        raise click.ClickException(f"No proposal {pid}.")
    click.echo(f"{p['title']}\nstatus: {p['status']} · risk: {p['risk']} · by {p['author']}\n\n{p['reason']}\n")
    click.echo(p.get("diff") or "(no diff)")
    if p.get("result"):
        click.echo(f"\nResult: {p['result'].get('note')}\nLog: {p['result'].get('log')}")


@medic.command("approve")
@click.argument("pid")
@click.option("--no-apply", is_flag=True, help="Only approve; install later with `medic apply`.")
def medic_approve(pid, no_apply):
    """Approve a proposal and install it (backup, side-by-side build, checks, rollback on failure)."""
    from . import selfupdate

    try:
        selfupdate.approve(pid, by="you (cli)")
        if no_apply:
            click.echo("Approved.")
            return
        click.echo("Installing through the safe updater… (this can take a few minutes)")
        p = selfupdate.apply_proposal(pid)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(("✔ " if p["status"] == "applied" else "✘ ") + p.get("result", {}).get("note", p["status"]))
    if p.get("result", {}).get("log"):
        click.echo(f"  log: {p['result']['log']}")


@medic.command("apply")
@click.argument("pid")
def medic_apply(pid):
    """Install an approved proposal (or retry a failed one)."""
    from . import selfupdate

    p = selfupdate.get_proposal(pid)
    if p and p["status"] == "failed":
        selfupdate.approve(pid, by="you (cli retry)")
    try:
        p = selfupdate.apply_proposal(pid)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(("✔ " if p["status"] == "applied" else "✘ ") + p.get("result", {}).get("note", p["status"]))


@medic.command("reject")
@click.argument("pid")
@click.option("--note", default="")
def medic_reject(pid, note):
    from . import selfupdate

    try:
        selfupdate.reject(pid, by="you (cli)", note=note)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo("Rejected.")


@medic.command("problems")
@click.option("--hours", default=24, show_default=True)
def medic_problems(hours):
    """Recent errors, failed/low-score job runs and open proposals."""
    from . import selfupdate

    click.echo(selfupdate.get_recent_problems(hours))


@cli.command()
def version():
    """Show the installed ZenSynora version."""
    from . import __version__

    click.echo(f"ZenSynora {__version__}  ({Path(__file__).resolve().parent.parent})")
    # v0.7.2: say so when a newer version is installed than the one running here.
    try:
        from .doctor.paths import stale_release

        stale = stale_release()
    except Exception:
        stale = None
    if stale:
        click.echo(f"Installed: {stale['installed']} ({stale['folder']}) — this terminal still uses "
                   f"{stale['running']}. Open a new terminal or run: "
                   "source ~/.local/share/zensynora/source/venv/bin/activate")


### WEB UI COMMANDS ###
@cli.command()
@click.option('--port', default=8000, help='Port to run the FastAPI proxy server')
@click.option('--host', default="0.0.0.0", show_default=True,
              help='Address to listen on (127.0.0.1 = this computer only)')
@click.option('--reload', is_flag=True, help='Auto-restart on code changes (development only)')
def webui(port, host, reload):
    """Start the Web UI Backend Server"""
    # Changed 2026-10-13 (v0.7.0): auto-reload used to be always on. It watches
    # the current directory, restarts the server on any file change and doubles
    # the processes — wrong for normal use, services and Docker. It is now
    # opt-in (--reload), and --host lets you keep the UI on this computer only.
    import uvicorn
    click.echo(f"Starting ZenSynora Web UI Backend on http://{host}:{port} ...")
    uvicorn.run("myclaw.web.api:app", host=host, port=port, reload=reload)


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


# Added 2026-10-13 (v0.7.0): `python -m myclaw.cli …` used to import the module
# and exit silently (the portable launchers relied on it). It now runs the CLI.
if __name__ == "__main__":
    cli()
