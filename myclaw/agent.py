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

console = Console()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are MyClaw, a personal AI agent with access to a knowledge base. "
    "You can call tools by responding ONLY with JSON: "
    '{"tool": "<name>", "args": {<key>: <value>}}. '
    "Available tools: shell(cmd), read_file(path), write_file(path, content), "
    "delegate(agent_name, task), list_tools(), register_tool(name, code), "
    "schedule(task, delay, every, user_id), edit_schedule(job_id, new_task, delay, every), "
    "split_schedule(job_id, sub_tasks_json), suspend_schedule(job_id), resume_schedule(job_id), "
    "cancel_schedule(job_id), list_schedules(), "
    "write_to_knowledge(title, content), search_knowledge(query), read_knowledge(permalink), "
    "get_knowledge_context(permalink, depth), list_knowledge(), get_related_knowledge(permalink), "
    "sync_knowledge_base(), list_knowledge_tags(). "
    "You can reference knowledge with memory://permalink. "
    "For all other responses, reply in plain text."
)


class Agent:
    """Personal AI agent with per-user memory, native tool calling, multi-agent delegation."""

    def __init__(self, config, model: str = None, system_prompt: str = None, provider_name: str = None):
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

        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def _get_memory(self, user_id: str) -> Memory:
        if user_id not in self._memories:
            self._memories[user_id] = Memory(user_id=user_id)
        return self._memories[user_id]

    def _search_knowledge_context(self, message: str, user_id: str, max_results: int = 3) -> str:
        """
        Auto-search knowledge base for relevant context.
        
        Extracts key terms from the message and searches the knowledge base.
        Returns formatted context string for injection into system prompt.
        """
        try:
            # Extract potential search terms (nouns, proper names, etc.)
            # Simple approach: use the whole message or key phrases
            search_terms = []
            
            # Look for memory:// references in the message
            memory_refs = re.findall(r'memory://([\w\-]+)', message)
            search_terms.extend(memory_refs)
            
            # Also try searching with the whole message (FTS5 will rank results)
            # Clean up the message for searching
            cleaned = re.sub(r'[^\w\s]', ' ', message.lower())
            words = [w for w in cleaned.split() if len(w) > 3]
            
            if words:
                # Try different search strategies
                # First: search for exact phrase
                notes = search_notes(message, user_id, limit=max_results)
                
                if not notes and len(words) > 0:
                    # Try searching for top keywords
                    query = " OR ".join(words[:5])  # Use up to 5 keywords
                    notes = search_notes(query, user_id, limit=max_results)
            else:
                notes = []
            
            if not notes and not memory_refs:
                return ""
            
            # Build context from results
            context_lines = ["## Relevant Knowledge"]
            
            for note in notes:
                context_lines.append(f"\n**{note.title}** ({note.permalink}):")
                
                # Add observations
                if note.observations:
                    for obs in note.observations[:3]:  # Limit to 3 observations
                        context_lines.append(f"- [{obs.category}] {obs.content}")
                
                # If this note was directly referenced, include more details
                if note.permalink in memory_refs:
                    # Get full context with related entities
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