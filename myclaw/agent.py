from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .knowledge import search_notes, build_context
from .skill_preloader import get_skill_preloader, start_preloader, stop_preloader
from rich.console import Console
import json
import logging
import asyncio
import inspect
import re
import threading
from pathlib import Path
from typing import AsyncIterator, List, Dict, Tuple, Optional

console = Console()
logger = logging.getLogger(__name__)

# Profile cache for faster loading
_profile_cache: dict[str, str] = {}
_profile_cache_lock = threading.Lock()


def _get_profile_cache_key(name: str, profile_path: Path) -> str:
    """Generate cache key based on name and file mtime."""
    try:
        mtime = profile_path.stat().st_mtime
        return f"{name}:{mtime}"
    except Exception:
        return f"{name}:0"


def _load_profile_cached(name: str, profile_path: Path) -> str:
    """Load profile with caching based on file modification time."""
    cache_key = _get_profile_cache_key(name, profile_path)
    
    with _profile_cache_lock:
        if cache_key in _profile_cache:
            return _profile_cache[cache_key]
    
    # Load and cache
    content = profile_path.read_text(encoding="utf-8").strip()
    
    with _profile_cache_lock:
        _profile_cache[cache_key] = content
        
        # Limit cache size
        if len(_profile_cache) > 100:
            # Remove oldest entries (simple FIFO)
            keys_to_remove = list(_profile_cache.keys())[:50]
            for key in keys_to_remove:
                del _profile_cache[key]
    
    return content


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

        # ── Lazy provider initialization ────────────────────────────────────────
        # Store config and provider name for lazy initialization
        self._config = config
        self._provider = None  # Will be initialized on first access
        
        try:
            default_provider = config.agents.defaults.provider or "ollama"
        except Exception:
            default_provider = "ollama"
        self._provider_name = provider_name or default_provider

        # ── Resolve model ─────────────────────────────────────────────────────
        try:
            cfg_model = config.agents.defaults.model
        except Exception:
            cfg_model = "llama3.2"
        self.model = model or cfg_model

        # Check for local workspace profiles first, then fall back to user home
        local_profiles_dir = Path(__file__).parent / "profiles"
        user_profiles_dir = Path(getattr(config.agents, "profiles_dir", "~/.myclaw/profiles")).expanduser()
        
        # Try local workspace profiles first
        profile_path = local_profiles_dir / f"{self.name}.md"
        if profile_path.exists():
            self.system_prompt = _load_profile_cached(self.name, profile_path)
        else:
            # Fall back to user home profiles
            user_profiles_dir.mkdir(parents=True, exist_ok=True)
            profile_path = user_profiles_dir / f"{self.name}.md"
            if profile_path.exists():
                self.system_prompt = _load_profile_cached(self.name, profile_path)
            else:
                self.system_prompt = system_prompt or SYSTEM_PROMPT
        
        # Load user dialectic profile if it exists (for personalization)
        dialectic_path = local_profiles_dir / "user_dialectic.md"
        if dialectic_path.exists():
            dialectic_content = dialectic_path.read_text(encoding="utf-8").strip()
            if dialectic_content and dialectic_content != self.system_prompt:
                self.system_prompt = f"{self.system_prompt}\n\n## User Profile\n{dialectic_content}"
        
        # Initialize skill preloader for this agent
        self._skill_preloader = get_skill_preloader()
        
        # Store config for later use
        self.config = config

    async def _get_memory(self, user_id: str) -> Memory:
        if user_id not in self._memories:
            mem = Memory(user_id=user_id)
            await mem.initialize()
            self._memories[user_id] = mem
        return self._memories[user_id]

    @property
    def provider(self):
        """Lazy provider initialization - initializes on first access.
        
        This improves startup performance by deferring provider initialization
        until the provider is actually needed (e.g., on first think() call).
        """
        if self._provider is None:
            try:
                self._provider = get_provider(self._config, self._provider_name)
            except Exception as e:
                logger.warning(
                    f"Could not init provider '{self._provider_name}' ({e}). "
                    "Falling back to Ollama."
                )
                self._provider = get_provider(self._config, "ollama")
                self._provider_name = "ollama"
        return self._provider

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

    async def close(self):
        for mem in self._memories.values():
            await mem.close()
        self._memories.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.

        _depth tracks sub-agent delegation depth — prevents infinite loops.
        """
        # Agent Pipeline Integration: Loop prevention
        if _depth > 10:
            logger.warning(f"Max delegation depth reached ({_depth}). Preventing potential infinite loop.")
            return "I've reached the maximum delegation depth. Let me handle this request directly."
        
        # Medic Agent: Check loop prevention before processing
        try:
            from myclaw.agents.medic_agent import prevent_infinite_loop
            loop_status = prevent_infinite_loop()
            if "limit reached" in loop_status.lower():
                logger.warning("Execution limit reached by loop prevention")
                return "I'm detecting repeated patterns in the request. Let me break out of the loop and handle this directly."
        except Exception:
            pass
        
        mem = await self._get_memory(user_id)
        await mem.add("user", user_message)

        trigger_hook("on_session_start", user_id, self.name)

        message_count_start = len(await mem.get_history()) if hasattr(mem, 'get_history') else 0

        history = await mem.get_history()
        
        # Optimization #4: Proactive skill pre-loading
        asyncio.create_task(self._skill_preloader.predict_and_preload(history, user_message))

        # Feature: Context Summarization with trajectory compression
        threshold = getattr(self.config.agents, 'summarization_threshold', 10)
        if len(history) > threshold:
            to_summarize = history[:-5]
            recent = history[-5:]
            summary_prompt = (
                "Summarize the following conversation context. Focus on key decisions, "
                "important facts, and user preferences. Preserve information that would be "
                "important for future context:\n"
            )
            for m in to_summarize:
                summary_prompt += f"{m['role']}: {m['content'][:200]}\n"
            summary_msgs = [{"role": "system", "content": "You summarize conversations concisely."}, 
                          {"role": "user", "content": summary_prompt}]
            try:
                summary_text, _ = await self.provider.chat(summary_msgs, self.model)
                original_len = sum(len(m['content']) for m in to_summarize)
                compressed_len = len(summary_text)
                compression_ratio = (1 - compressed_len / original_len) * 100 if original_len > 0 else 0
                logger.debug(f"Trajectory compressed: {original_len} -> {compressed_len} chars ({compression_ratio:.1f}% reduction)")
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

        # Trigger pre_llm_call hooks - allow hooks to modify messages
        hook_results = trigger_hook("pre_llm_call", messages, self.model)
        for result in hook_results:
            if result and isinstance(result, list):
                messages = result  # Use modified messages from hook
                logger.debug(f"pre_llm_call hook modified messages")

        try:
            response, tool_calls = await self.provider.chat(messages, self.model)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            return f"Sorry, I encountered an error: {e}"

        # Trigger post_llm_call hooks
        hook_results = trigger_hook("post_llm_call", response, tool_calls)
        for result in hook_results:
            if result and isinstance(result, tuple) and len(result) == 2:
                response, tool_calls = result  # Allow hooks to modify response/tool_calls

        if tool_calls:
            # Determine if we can use parallel execution
            independent_tools = [tc for tc in tool_calls if is_tool_independent(tc.get("function", {}).get("name", ""))]
            
            if len(independent_tools) > 1:
                # Use parallel execution for independent tools
                logger.info(f"Executing {len(independent_tools)} tools in parallel")
                executor = get_parallel_executor()
                exec_results = await executor.execute_tools(independent_tools, user_id)
                
                # Format results for the LLM
                result_parts = []
                for r in exec_results:
                    if r["success"]:
                        result_parts.append(f"Tool {r['tool_name']} returned: {r['result']}")
                    else:
                        result_parts.append(f"Tool {r['tool_name']} error: {r['error']}")
                
                results = result_parts
            else:
                # Fall back to sequential execution for single tool or dependent tools
                results = []
                for tc in tool_calls:
                    tool_name = tc.get("function", {}).get("name", "")
                    args = tc.get("function", {}).get("arguments", {})

                    if tool_name not in TOOLS:
                        results.append(f"Unknown tool: {tool_name}")
                        logger.warning(f"Unknown tool called: {tool_name}")
                        continue

                    if tool_name == "delegate":
                        args["_depth"] = _depth + 1

                    import time
                    start_time = time.time()
                    logger.info(f"[AUDIT] Tool execution started: {tool_name} with args: {args}")

                    try:
                        func = TOOLS[tool_name]["func"]
                        if inspect.iscoroutinefunction(func):
                            result = await func(**args)
                        else:
                            result = await asyncio.to_thread(func, **args)
                        await mem.add("tool", f"Tool {tool_name} returned: {result}")
                        results.append(str(result))
                        duration = time.time() - start_time
                        logger.info(f"[AUDIT] Tool executed successfully: {tool_name} (took {duration:.2f}s)")
                    except Exception as e:
                        logger.error(f"Tool execution error ({tool_name}): {e}")
                        logger.error(f"[AUDIT] Tool execution failed: {tool_name} - {e}")
                        results.append(f"Tool error: {e}")

            tool_result_msg = "\n".join(results)
            followup = messages + [{"role": "tool", "content": tool_result_msg}]
            
            # Trigger pre_llm_call hooks for followup
            hook_results = trigger_hook("pre_llm_call", followup, self.model)
            for result in hook_results:
                if result and isinstance(result, list):
                    followup = result
            
            try:
                final_response, _ = await self.provider.chat(followup, self.model)
                
                # Trigger post_llm_call hooks for followup
                trigger_hook("post_llm_call", final_response, None)
                
                await mem.add("assistant", final_response)
                
                # Trigger on_session_end hook
                message_count = len(await mem.get_history()) if hasattr(mem, 'get_history') else 0
                trigger_hook("on_session_end", user_id, self.name, message_count)
                
                return final_response
            except Exception as e:
                logger.error(f"LLM second call error: {e}")
                return f"Tool executed but error getting response: {e}"

        await mem.add("assistant", response)
        
        # Trigger on_session_end hook
        message_count = len(await mem.get_history()) if hasattr(mem, 'get_history') else 0
        trigger_hook("on_session_end", user_id, self.name, message_count)
        
        return response

    async def stream_think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> AsyncIterator[str]:
        """Process a user message and yield response chunks in real-time.

        This is a streaming version of think() that yields content chunks
        as they arrive from the provider, enabling real-time display.

        _depth tracks sub-agent delegation depth — prevents infinite loops.

        Yields:
            Content chunks as they arrive from the LLM provider.
        """
        mem = await self._get_memory(user_id)
        await mem.add("user", user_message)

        # Trigger on_session_start hook
        trigger_hook("on_session_start", user_id, self.name)

        history = await mem.get_history()

        # Feature: Context Summarization with trajectory compression
        threshold = getattr(self.config.agents, 'summarization_threshold', 10)
        if len(history) > threshold:
            to_summarize = history[:-5]
            recent = history[-5:]
            summary_prompt = (
                "Summarize the following conversation context. Focus on key decisions, "
                "important facts, and user preferences:\n"
            )
            for m in to_summarize:
                summary_prompt += f"{m['role']}: {m['content'][:200]}\n"
            summary_msgs = [{"role": "system", "content": "You summarize conversations concisely."}, 
                          {"role": "user", "content": summary_prompt}]
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

        # Trigger pre_llm_call hooks
        hook_results = trigger_hook("pre_llm_call", messages, self.model)
        for result in hook_results:
            if result and isinstance(result, list):
                messages = result

        try:
            # Use stream_chat for streaming response
            stream_iterator = await self.provider.chat(messages, self.model, stream=True)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
            yield f"Sorry, I encountered an error: {e}"
            return

        full_response = ""
        tool_calls = None
        
        # Iterate over the stream
        try:
            async for chunk in stream_iterator:
                full_response += chunk
                yield chunk
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            yield f"Error streaming response: {e}"
            return

        # Trigger post_llm_call hooks
        trigger_hook("post_llm_call", full_response, tool_calls)

        # Note: Tool calls are not supported in streaming mode yet
        # The full response is returned as chunks
        await mem.add("assistant", full_response)
        
        # Trigger on_session_end hook
        trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
        
        yield "[TOOL_CALLS_NONE]"  # Signal that streaming is complete