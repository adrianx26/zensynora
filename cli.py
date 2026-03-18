import sys
import asyncio
import signal
import atexit
import logging
from myclaw.config import load_config
from myclaw.agent import Agent
from onboard import onboard
from myclaw.gateway import start
from myclaw.knowledge import (
    search_notes, list_notes, read_note, sync_knowledge, get_all_tags,
    write_note, Observation
)

logger = logging.getLogger(__name__)


def _graceful_shutdown():
    """6.3: Graceful shutdown handler - close all pools and connections."""
    logger.info("Shutting down gracefully...")
    try:
        # Close HTTP client pool
        import myclaw.provider
        if hasattr(myclaw.provider, 'HTTPClientPool'):
            asyncio.run(myclaw.provider.HTTPClientPool.close())
    except Exception as e:
        logger.error(f"Error closing HTTP pool: {e}")
    
    try:
        # Close SQLite pool
        import myclaw.memory
        if hasattr(myclaw.memory, 'SQLitePool'):
            myclaw.memory.SQLitePool.close_all()
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
    from myclaw import tools as tool_module
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


def main():
    # 9.3: Standardized comprehensive system-wide logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    config = load_config()
    if len(sys.argv) < 2:
        print("Commands: onboard | agent | gateway | knowledge")
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
                        agent = registry.get(name) or registry["default"]
                    else:
                        agent = registry["default"]
                        text  = msg

                    response = await agent.think(text)
                    print("Claw:", response)

        asyncio.run(run_agent_chat(registry, agent_names))

    elif cmd == "gateway":
        start(config)

    elif cmd == "knowledge":
        # Knowledge base CLI commands
        if len(sys.argv) < 3:
            print("Knowledge commands:")
            print("  search <query>     - Search knowledge base")
            print("  write              - Create a new note (interactive)")
            print("  read <permalink>   - Read a specific note")
            print("  list               - List all notes")
            print("  sync               - Sync database with files")
            print("  tags               - List all tags")
            return
        
        kb_cmd = sys.argv[2]
        user_id = "default"
        
        if kb_cmd == "search":
            query = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else input("Search query: ")
            notes = search_notes(query, user_id)
            if notes:
                print(f"\n🔍 Found {len(notes)} results for '{query}':\n")
                for i, note in enumerate(notes, 1):
                    print(f"{i}. {note.title} ({note.permalink})")
                    if note.observations:
                        for obs in note.observations[:2]:
                            print(f"   - [{obs.category}] {obs.content[:60]}...")
            else:
                print(f"No results found for: {query}")
        
        elif kb_cmd == "write":
            title = input("Title: ").strip()
            if not title:
                print("Error: Title is required")
                return
            
            print("Content (press Enter twice to finish):")
            lines = []
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
            content = "\n".join(lines)
            
            tags_input = input("Tags (comma-separated): ").strip()
            tags = [t.strip() for t in tags_input.split(",") if t.strip()]
            
            permalink = write_note(
                name=title,
                title=title,
                content=content,
                tags=tags,
                user_id=user_id
            )
            print(f"\n✅ Created: {permalink}")
        
        elif kb_cmd == "read":
            permalink = sys.argv[3] if len(sys.argv) > 3 else input("Permalink: ")
            note = read_note(permalink, user_id)
            if note:
                print(f"\n# {note.title}")
                print(f"Permalink: {note.permalink}")
                if note.tags:
                    print(f"Tags: {', '.join(f'#{t}' for t in note.tags)}")
                print()
                if note.observations:
                    print("## Observations")
                    for obs in note.observations:
                        print(f"- [{obs.category}] {obs.content}")
                    print()
                if note.relations:
                    print("## Relations")
                    for rel in note.relations:
                        print(f"- {rel.relation_type} → [[{rel.target}]]")
            else:
                print(f"Note not found: {permalink}")
        
        elif kb_cmd == "list":
            notes = list_notes(user_id)
            if notes:
                print(f"\n📚 {len(notes)} notes:\n")
                for note in notes:
                    tag_str = f" [{' '.join(f'#{t}' for t in note.tags)}]" if note.tags else ""
                    print(f"- {note.title}{tag_str}")
            else:
                print("Knowledge base is empty.")
        
        elif kb_cmd == "sync":
            print("Syncing knowledge base...")
            stats = sync_knowledge(user_id)
            total = stats['added'] + stats['updated'] + stats['deleted']
            print(f"✅ Sync complete: {total} changes")
            print(f"   Added: {stats['added']}")
            print(f"   Updated: {stats['updated']}")
            print(f"   Deleted: {stats['deleted']}")
        
        elif kb_cmd == "tags":
            tags = get_all_tags(user_id)
            if tags:
                print("\n🏷️ Tags:")
                print(" ".join(f"#{t}" for t in tags))
            else:
                print("No tags found.")
        
        else:
            print(f"Unknown knowledge command: {kb_cmd}")
            print("Commands: search | write | read | list | sync | tags")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: onboard | agent | gateway | knowledge")


if __name__ == "__main__":
    main()