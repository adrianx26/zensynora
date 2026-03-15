from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS
from .knowledge import search_notes, build_context
from rich.console import Console
import json
import logging
import asyncio
import inspect
import re
from pathlib import Path

console = Console()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are MyClaw, a personal AI agent with access to a knowledge base, TOOLBOX, and Agent Swarms. "
    "You can call tools by responding ONLY with JSON: "
    '{"tool": "<name>", "args": {<key>: <value>}}. '
    "Core tools: shell(cmd), read_file(path), write_file(path, content), "
    "browse(url, max_length), download_file(url, path), delegate(agent_name, task). "
    "Tool management: list_tools(), register_tool(name, code, documentation), "
    "list_toolbox(), get_tool_documentation(name). "
    "Scheduling: schedule(task, delay, every, user_id), edit_schedule(job_id, new_task, delay, every), "
    "split_schedule(job_id, sub_tasks_json), suspend_schedule(job_id), resume_schedule(job_id), "
    "cancel_schedule(job_id), list_schedules(). "
    "Knowledge: write_to_knowledge(title, content), search_knowledge(query), read_knowledge(permalink), "
    "get_knowledge_context(permalink, depth), list_knowledge(), get_related_knowledge(permalink), "
    "sync_knowledge_base(), list_knowledge_tags(). "
    "Agent Swarms: swarm_create(name, strategy, workers, coordinator, aggregation), "
    "swarm_assign(swarm_id, task), swarm_status(swarm_id), swarm_result(swarm_id), "
    "swarm_terminate(swarm_id), swarm_list(status), swarm_stats(). "
    "IMPORTANT: When creating tools with register_tool(), first use list_toolbox() to check if a similar tool exists. "
    "You can reference knowledge with memory://permalink. "
    "For all other responses, reply in plain text."
)


class Agent:
    """Personal AI agent with per-user memory, native tool calling, multi-agent delegation."""

    def __init__(self, config, name: str = "default", model: str = None, system_prompt: str = None, provider_name: str = None):
        self.name = name
        self._memories: dict[str, Memory] = {}

        # ── Resolve provider ──────────────────────────────────────────────────
        try:
            default_provider = config.agents.defaults.provider or "ollama"
        except Exception:
            default_provider = "ollama"
        resolved_provider = provider_name or default_provider

        try:
            self.provider = get_provider(config, resolved_provider)
        except Exception as e:
            logger.warning(
                f"Could not init provider '{resolved_provider}' ({e}). "
                "Falling back to Ollama."
            )
            self.provider = get_provider(config, "ollama")

        # ── Resolve model ─────────────────────────────────────────────────────
        try:
            cfg_model = config.agents.defaults.model
        except Exception:
            cfg_model = "llama3.2"
        self.model = model or cfg_model

        profiles_dir_str = getattr(config.agents, "profiles_dir", "~/.myclaw/profiles")
        profiles_dir = Path(profiles_dir_str).expanduser()
        profiles_dir.mkdir(parents=True, exist_ok=True)
        
        profile_path = profiles_dir / f"{self.name}.md"
        if profile_path.exists():
            self.system_prompt = profile_path.read_text(encoding="utf-8").strip()
        else:
            self.system_prompt = system_prompt or SYSTEM_PROMPT

    def _get_memory(self, user_id: str) -> Memory:
        if user_id not in self._memories:
            self._memories[user_id] = Memory(user_id=user_id)
        return self._memories[user_id]

    def _search_knowledge_context(self, message: str, user_id: str, max_results: int = 3) -> str:
        """Auto-search the knowledge base for context relevant to message.

        Uses a multi-strategy search:
        1. Resolves any explicit memory:// permalink references
        2. Searches using the full message (FTS5 ranked)
        3. Falls back to bigram + keyword OR-query for fuzzy matching

        Args:
            message: The user's message to extract search terms from
            user_id: User ID for per-user knowledge isolation
            max_results: Maximum notes to retrieve (default: 3)

        Returns:
            Formatted '## Relevant Knowledge' context block, or '' if nothing found.
        """
        try:
            search_terms = []

            # Resolve explicit memory:// references
            memory_refs = re.findall(r'memory://([\w\-]+)', message)
            search_terms.extend(memory_refs)

            # Clean the message for keyword extraction
            cleaned = re.sub(r'[^\w\s]', ' ', message.lower())
            words = [w for w in cleaned.split() if len(w) > 3]

            notes = []
            if words:
                # Strategy 1: search with full message text (FTS5 ranked)
                notes = search_notes(message, user_id, limit=max_results)

                if not notes:
                    # Strategy 2: bigram + single-keyword OR query
                    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
                    candidates = bigrams[:3] + words[:5]
                    query = " OR ".join(f'"{t}"' if ' ' in t else t for t in candidates)
                    notes = search_notes(query, user_id, limit=max_results)

            if not notes and not memory_refs:
                return ""

            context_lines = ["## Relevant Knowledge"]

            for note in notes:
                context_lines.append(f"\n**{note.title}** ({note.permalink}):")

                if note.observations:
                    for obs in note.observations[:3]:
                        context_lines.append(f"- [{obs.category}] {obs.content}")

                if note.permalink in memory_refs:
                    full_context = build_context(note.permalink, user_id, depth=1)
                    context_lines.append("\nRelated context:")
                    context_lines.append(full_context[:500] + "..." if len(full_context) > 500 else full_context)

            context_lines.append("\n---\n")
            return "\n".join(context_lines)

        except Exception as e:
            logger.error(f"Error searching knowledge: {e}")
            return ""

    def close(self):
        for mem in self._memories.values():
            mem.close()
        self._memories.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    async def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.

        _depth tracks sub-agent delegation depth — prevents infinite loops.
        """
        mem = self._get_memory(user_id)
        mem.add("user", user_message)

        history = mem.get_history()

        # Feature: Context Summarization
        if len(history) > 10:
            to_summarize = history[:-5]
            recent = history[-5:]
            summary_prompt = "Summarize the following conversation context briefly in one paragraph:\n"
            for m in to_summarize:
                summary_prompt += f"{m['role']}: {m['content']}\n"
            summary_msgs = [{"role": "system", "content": "You summarize conversations."}, {"role": "user", "content": summary_prompt}]
            try:
                summary_text, _ = await self.provider.chat(summary_msgs, self.model)
                history = [{"role": "system", "content": f"Previous conversation summary: {summary_text}"}] + recent
            except Exception as e:
                logger.error(f"Error summarizing history: {e}")
                # fallback to raw history if summary fails

        # Search knowledge base for relevant context
        knowledge_context = self._search_knowledge_context(user_message, user_id)
        
        # Build system prompt with knowledge context
        system_content = self.system_prompt
        if knowledge_context:
            system_content = f"{self.system_prompt}\n\n{knowledge_context}"
        
        messages = [{"role": "system", "content": system_content}] + history

        try:
            response, tool_calls = await self.provider.chat(messages, self.model)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            return f"Sorry, I encountered an error: {e}"

        if tool_calls:
            results = []
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                args = tc.get("function", {}).get("arguments", {})

                if tool_name not in TOOLS:
                    results.append(f"Unknown tool: {tool_name}")
                    continue

                # Inject delegation depth so delegate() can enforce the limit
                if tool_name == "delegate":
                    args["_depth"] = _depth + 1

                try:
                    func = TOOLS[tool_name]["func"]
                    if inspect.iscoroutinefunction(func):
                        result = await func(**args)
                    else:
                        result = await asyncio.to_thread(func, **args)
                    mem.add("tool", f"Tool {tool_name} returned: {result}")
                    results.append(str(result))
                except Exception as e:
                    logger.error(f"Tool execution error ({tool_name}): {e}")
                    results.append(f"Tool error: {e}")

            tool_result_msg = "\n".join(results)
            followup = messages + [{"role": "tool", "content": tool_result_msg}]
            try:
                final_response, _ = await self.provider.chat(followup, self.model)
                mem.add("assistant", final_response)
                return final_response
            except Exception as e:
                logger.error(f"LLM second call error: {e}")
                return f"Tool executed but error getting response: {e}"

        mem.add("assistant", response)
        return response