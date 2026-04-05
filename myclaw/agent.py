"""
Agent - Core AI agent implementation for MyClaw/Zensynora.

This module provides the main Agent class that orchestrates conversations,
tool execution, memory management, and knowledge retrieval. It serves as
the primary interface between users and the LLM provider.

Key Components:
    - Agent: Main agent class with think() and think_stream() methods
    - Profile Caching: LRU cache for agent profile loading
    - Memory Integration: Per-user conversation history via Memory class
    - Tool Execution: Parallel and sequential tool calling
    - Knowledge Context: RAG-style knowledge base integration
    - Skill Preloading: Predictive skill loading for faster responses

Usage:
    from myclaw.agent import Agent
    from myclaw.config import load_config

    config = load_config()
    agent = Agent(config, name="default")

    # Non-streaming response
    response = await agent.think("Hello!")

    # Streaming response
    async for chunk in agent.think_stream("Hello!"):
        print(chunk, end="")
"""

from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .knowledge import search_notes, build_context
from .skill_preloader import get_skill_preloader, start_preloader, stop_preloader
from rich.console import Console
import json
import logging
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, AsyncIterator
from collections import OrderedDict
import json
import re
import threading

from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .semantic_cache import get_semantic_cache
from .skill_preloader import get_skill_preloader

logger = logging.getLogger(__name__)

# Profile cache for faster loading - uses OrderedDict for true LRU eviction
_profile_cache: OrderedDict[str, str] = OrderedDict()
_profile_cache_lock = threading.Lock()
_profile_cache_maxsize = 100


def _get_profile_cache_key(name: str, profile_path: Path) -> str:
    """Generate cache key based on name and file mtime."""
    try:
        mtime = profile_path.stat().st_mtime
        return f"{name}:{mtime}"
    except Exception:
        return f"{name}:0"


def _load_profile_cached(name: str, profile_path: Path) -> str:
    """Load profile with caching based on file modification time.

    Uses LRU (Least Recently Used) eviction policy via OrderedDict.
    """
    cache_key = _get_profile_cache_key(name, profile_path)

    with _profile_cache_lock:
        if cache_key in _profile_cache:
            # Move to end (most recently used)
            _profile_cache.move_to_end(cache_key)
            return _profile_cache[cache_key]

    # Load and cache
    content = profile_path.read_text(encoding="utf-8").strip()

    with _profile_cache_lock:
        _profile_cache[cache_key] = content

        # LRU eviction: remove oldest items when over capacity
        while len(_profile_cache) > _profile_cache_maxsize:
            _profile_cache.popitem(last=False)

    return content


async def _load_profile_cached_async(name: str, profile_path: Path) -> str:
    """Async wrapper for _load_profile_cached using thread pool.
    
    Avoids blocking the event loop during file I/O.
    """
    return await asyncio.to_thread(_load_profile_cached, name, profile_path)


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

        # Store paths for lazy profile loading (to avoid blocking in __init__)
        self._local_profiles_dir = Path(__file__).parent / "profiles"
        self._user_profiles_dir = Path(getattr(config.agents, "profiles_dir", "~/.myclaw/profiles")).expanduser()
        self._custom_system_prompt = system_prompt
        self._system_prompt_loaded = False
        self._system_prompt = ""
        
        # Initialize skill preloader for this agent
        self._skill_preloader = get_skill_preloader()

        # Store pending preload tasks to prevent garbage collection
        self._pending_preloads: set[asyncio.Task] = set()

        # Store config for later use
        self.config = config

    async def _load_system_prompt(self) -> str:
        """Lazy load system prompt with async file I/O."""
        if self._system_prompt_loaded:
            return self._system_prompt

        # Try local workspace profiles first
        profile_path = self._local_profiles_dir / f"{self.name}.md"
        if profile_path.exists():
            prompt = await _load_profile_cached_async(self.name, profile_path)
        else:
            # Fall back to user home profiles
            self._user_profiles_dir.mkdir(parents=True, exist_ok=True)
            profile_path = self._user_profiles_dir / f"{self.name}.md"
            if profile_path.exists():
                prompt = await _load_profile_cached_async(self.name, profile_path)
            else:
                prompt = self._custom_system_prompt or SYSTEM_PROMPT

        # Load user dialectic profile if it exists (for personalization)
        dialectic_path = self._local_profiles_dir / "user_dialectic.md"
        if dialectic_path.exists():
            dialectic_content = await asyncio.to_thread(
                dialectic_path.read_text, encoding="utf-8"
            )
            dialectic_content = dialectic_content.strip()
            if dialectic_content and dialectic_content != prompt:
                prompt = f"{prompt}\n\n## User Profile\n{dialectic_content}"

        self._system_prompt = prompt
        self._system_prompt_loaded = True
        return prompt

    @property
    def system_prompt(self) -> str:
        """Get system prompt (may be empty string if not loaded yet)."""
        if self._system_prompt_loaded:
            return self._system_prompt
        return self._custom_system_prompt or SYSTEM_PROMPT

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

        history = await mem.get_history()
        message_count_start = len(history)
        
        # Optimization #4: Proactive skill pre-loading
        task = asyncio.create_task(
            self._skill_preloader.predict_and_preload(history, user_message)
        )
        self._pending_preloads.add(task)
        task.add_done_callback(self._pending_preloads.discard)

        # Feature: Context Summarization with trajectory compression
        threshold = getattr(self.config.agents, 'summarization_threshold', 10)
        if len(history) > threshold:
            to_summarize = history[:-5]
            recent = history[-5:]
            summary_parts = [
                "Summarize the following conversation context. Focus on key decisions, "
                "important facts, and user preferences. Preserve information that would be "
                "important for future context:\n"
            ]
            for m in to_summarize:
                summary_parts.append(f"{m['role']}: {m['content'][:200]}\n")
            summary_prompt = "".join(summary_parts)
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

        # Build system prompt with knowledge context (async load to avoid blocking)
        system_prompt = await self._load_system_prompt()
        system_content = system_prompt
        if knowledge_context:
            system_content = f"{system_prompt}\n\n{knowledge_context}"

        messages = [{"role": "system", "content": system_content}] + history

        # Trigger pre_llm_call hooks - allow hooks to modify messages
        hook_results = trigger_hook("pre_llm_call", messages, self.model)
        for result in hook_results:
            if result and isinstance(result, list):
                messages = result  # Use modified messages from hook
                logger.debug(f"pre_llm_call hook modified messages")

        try:
            response, tool_calls = await self.provider.chat(messages, self.model)
        except httpx.TimeoutException as e:
            logger.error(f"LLM provider timeout: {e}")
            return "Sorry, the LLM service timed out. Please try again."
        except (httpx.ConnectError, ConnectionError) as e:
            logger.error(f"LLM provider connection error: {e}")
            return "Sorry, I cannot connect to the LLM service. Please check your connection."
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM provider HTTP error: {e}")
            return f"Sorry, the LLM service returned an error: {e.response.status_code}"
        except Exception as e:
            logger.exception(f"Unexpected LLM provider error: {e}")
            return f"Sorry, an unexpected error occurred: {e}"

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
            summary_parts = [
                "Summarize the following conversation context. Focus on key decisions, "
                "important facts, and user preferences:\n"
            ]
            for m in to_summarize:
                summary_parts.append(f"{m['role']}: {m['content'][:200]}\n")
            summary_prompt = "".join(summary_parts)
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

        # Build system prompt with knowledge context (async load to avoid blocking)
        system_prompt = await self._load_system_prompt()
        system_content = system_prompt
        if knowledge_context:
            system_content = f"{system_prompt}\n\n{knowledge_context}"

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

        response_parts = []
        tool_calls = None

        # Iterate over the stream
        try:
            async for chunk in stream_iterator:
                response_parts.append(chunk)
                yield chunk
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            yield f"Error streaming response: {e}"
            return

        full_response = "".join(response_parts)

        # Trigger post_llm_call hooks
        trigger_hook("post_llm_call", full_response, tool_calls)

        # Note: Tool calls are not supported in streaming mode yet
        # The full response is returned as chunks
        await mem.add("assistant", full_response)
        
        # Trigger on_session_end hook
        trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
        
        yield "[TOOL_CALLS_NONE]"  # Signal that streaming is complete