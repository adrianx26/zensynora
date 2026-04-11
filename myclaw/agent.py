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

import json
import logging
import asyncio
import re
import threading
from pathlib import Path
from typing import List, Dict, Tuple, Optional, AsyncIterator, Set, Union, Any
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import time

from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .knowledge import search_notes, build_context
from .semantic_cache import get_semantic_cache
from .skill_preloader import get_skill_preloader, start_preloader, stop_preloader
from .task_timer import get_task_timer_orchestrator, TaskStatus, Colors as TimerColors
from rich.console import Console

logger = logging.getLogger(__name__)
kb_gap_logger = logging.getLogger("myclaw.knowledge.gaps")

GAP_FILE = Path.home() / ".myclaw" / "knowledge_gaps.jsonl"
_LAST_ACTIVE_TIME = time.time()


def get_last_active_time() -> float:
    """Returns the globally tracked last activity timestamp."""
    return _LAST_ACTIVE_TIME


@dataclass
class KnowledgeSearchResult:
    """Structured result from knowledge base search.

    Attributes:
        context: Formatted knowledge context string for LLM consumption
        has_results: Whether any knowledge entries were found
        suggested_topics: List of suggested topic keywords from the query
        gap_logged: Whether this search was recorded as a knowledge gap
        metadata: Additional search metadata (query, timestamp, etc.)
    """
    context: str
    has_results: bool
    suggested_topics: List[str] = field(default_factory=list)
    gap_logged: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class KnowledgeGapCache:
    """Per-session cache to prevent duplicate gap logging.

    Implements a short-lived in-process cache keyed by query and session id,
    with a configurable timeout to reduce noise from repeated empty searches.
    """

    def __init__(self, timeout_seconds: float = 300.0):
        self._cache: Dict[str, float] = {}
        self._timeout = timeout_seconds
        self._enabled = True

    def is_duplicate(self, query: str, session_id: str) -> bool:
        """Check if this query has been logged recently in this session.

        Args:
            query: The search query to check
            session_id: Session identifier for isolation

        Returns:
            True if this is a duplicate (already logged recently), False otherwise
        """
        if not self._enabled:
            return False

        key = f"{session_id}:{query.lower().strip()}"
        now = time.time()

        # Clean expired entries
        self._cache = {k: v for k, v in self._cache.items() if now - v < self._timeout}

        if key in self._cache:
            return True

        self._cache[key] = now
        return False

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the cache (useful for testing).

        Args:
            enabled: Whether to enable deduplication caching
        """
        self._enabled = enabled


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

        # Initialize knowledge gap cache for deduplication
        self._gap_cache = KnowledgeGapCache(timeout_seconds=300.0)
        
        # Initialize task timer orchestrator for tracking user question timeouts
        self._task_timer = get_task_timer_orchestrator()
        self._current_task_id: Optional[str] = None

        # ── Hardware Awareness & Optimization Check ──────────────────────────
        try:
            from .backends.hardware import get_system_metrics, get_optimization_suggestions
            metrics = get_system_metrics()
            suggestions = get_optimization_suggestions(metrics)
            if suggestions:
                for s in suggestions:
                    logger.info(f"System Optimization Note: {s}")
        except Exception as e:
            logger.debug(f"Hardware optimization check skipped: {e}")
    
    def _handle_task_status_update(self, update) -> None:
        """Handle status updates from the task timer."""
        # Format and print the status update to console
        timestamp = datetime.fromtimestamp(update.timestamp).strftime("%H:%M:%S")
        elapsed = f"[{update.elapsed_seconds:.1f}s]"
        
        print(f"\n{TimerColors.TIMESTAMP}[{timestamp}]{TimerColors.RESET} "
              f"{TimerColors.METRIC}{elapsed}{TimerColors.RESET}", end="")
        
        if update.threshold:
            print(f" {TimerColors.WARNING}[THRESHOLD: {update.threshold}s]{TimerColors.RESET}", end="")
        print()
        
        if update.step_name:
            print(f"  Step: {TimerColors.STEP_NAME}{update.step_name}{TimerColors.RESET}")
        
        print(f"  {update.color}{update.message}{TimerColors.RESET}")
        
        # If it's the max timeout failure, the task is already being terminated
        if update.threshold == 300 and update.message_type == "fatal":
            logger.critical(f"Task {update.task_id} reached maximum timeout")

    # ── Context-gap tracking ─────────────────────────────────────────────────
    # Tracks topics per user for which the KB returned no results, so they can
    # be flagged for manual review or KB creation later.
    _kb_gaps: Dict[str, Set[str]] = {}
    _knowledge_gap_cache_enabled: bool = True  # Test hook to disable caching

    def _record_kb_gap(self, user_id: str, topic: str, skip_cache: bool = False) -> bool:
        """Record a knowledge-base gap topic for a user.

        Args:
            user_id: User identifier
            topic: The topic/query that yielded no results
            skip_cache: If True, bypass deduplication cache

        Returns:
            True if gap was recorded, False if duplicate (not recorded)
        """
        # Check deduplication cache unless skipped
        if not skip_cache and hasattr(self, '_gap_cache'):
            if self._gap_cache.is_duplicate(topic, user_id):
                logger.debug(f"KB gap duplicate suppressed for user '{user_id}': {topic[:60]}")
                return False

        if user_id not in self._kb_gaps:
            self._kb_gaps[user_id] = set()
        self._kb_gaps[user_id].add(topic[:120])  # cap length
        logger.debug(f"KB gap recorded for user '{user_id}': {topic[:60]}")
        return True

    def get_kb_gaps(self, user_id: str) -> List[str]:
        """Return accumulated KB-gap topics for a user (useful for diagnostics)."""
        return list(self._kb_gaps.get(user_id, set()))

    def clear_gap_cache(self) -> None:
        """Clear the gap deduplication cache (useful for testing)."""
        if hasattr(self, '_gap_cache'):
            self._gap_cache.clear()

    def set_gap_cache_enabled(self, enabled: bool) -> None:
        """Enable or disable gap cache (test hook).

        Args:
            enabled: Whether to enable deduplication caching
        """
        self._knowledge_gap_cache_enabled = enabled
        if hasattr(self, '_gap_cache'):
            self._gap_cache.set_enabled(enabled)

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

    def _analyze_complexity(self, message: str) -> str:
        """Simple heuristic to determine task complexity."""
        high_complexity_keywords = [
            'implement', 'debug', 'fix', 'refactor', 'architect',
            'analyze', 'compare', 'optimize', 'write a program',
            'complex', 'threading', 'asyncio', 'database schema'
        ]
        message_lower = message.lower()
        
        # Check for length and keywords
        if len(message) > 500 or any(kw in message_lower for kw in high_complexity_keywords):
            return "high"
        return "standard"

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

    def _extract_suggested_topics(self, message: str) -> List[str]:
        """Extract suggested topics from a message for knowledge gap guidance.

        Args:
            message: The user's message to analyze

        Returns:
            List of suggested topic keywords (words > 3 chars, bigrams)
        """
        cleaned = re.sub(r'[^\w\s]', ' ', message.lower())
        words = [w for w in cleaned.split() if len(w) > 3]

        topics = words[:5]  # Top single words

        # Add bigrams for more context
        if len(words) >= 2:
            bigrams = [f"{words[i]} {words[i+1]}" for i in range(min(len(words) - 1, 3))]
            topics.extend(bigrams)

        return list(dict.fromkeys(topics))  # Remove duplicates, preserve order

    def _search_knowledge_context(
        self,
        message: str,
        user_id: str,
        max_results: int = 3,
        return_structured: bool = False
    ) -> Union[str, KnowledgeSearchResult]:
        """Auto-search the knowledge base for context relevant to message.

        Uses a multi-strategy search:
        1. Resolves any explicit memory:// permalink references
        2. Searches using the full message (FTS5 ranked)
        3. Falls back to bigram + keyword OR-query for fuzzy matching

        When both strategies return nothing the topic is recorded as a KB gap
        so it can be flagged for manual review or KB-entry creation.

        Args:
            message: The user's message to extract search terms from
            user_id: User ID for per-user knowledge isolation
            max_results: Maximum notes to retrieve (default: 3)
            return_structured: If True, return KnowledgeSearchResult dataclass.
                             If False (default), return context string for backward compatibility.

        Returns:
            Formatted context string (default) or KnowledgeSearchResult (if structured=True).
            For empty results with structured=True, returns contextual guidance including
            suggested topics and a pointer to write_to_knowledge().
        """
        metadata = {
            "query": message[:200],
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
            "max_results": max_results
        }

        try:
            # Resolve explicit memory:// references
            memory_refs = re.findall(r'memory://([\w\-]+)', message)

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
                # Record gap so the agent can suggest KB creation
                gap_logged = self._record_kb_gap(user_id, message[:120])
                suggested_topics = self._extract_suggested_topics(message)

                # Build contextual guidance for empty results
                guidance_lines = [
                    "## Knowledge Base Status",
                    f"\nNo existing knowledge entries found for your query.",
                    f"\n**Suggested topics to explore:**",
                ]
                for topic in suggested_topics[:5]:
                    guidance_lines.append(f"- {topic}")

                guidance_lines.extend([
                    "\n**You can:**",
                    f"1. Create a new knowledge entry: `write_to_knowledge(title='Your Topic', content='Details...')`",
                    f"2. Browse existing entries: `list_knowledge()`",
                    f"3. Try different keywords in your search",
                    "\n---\n"
                ])

                context = "\n".join(guidance_lines)

                result = KnowledgeSearchResult(
                    context=context,
                    has_results=False,
                    suggested_topics=suggested_topics,
                    gap_logged=gap_logged,
                    metadata=metadata
                )

                return result if return_structured else ""

            # Build context from found notes
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
            context = "\n".join(context_lines)

            result = KnowledgeSearchResult(
                context=context,
                has_results=True,
                suggested_topics=[],
                gap_logged=False,
                metadata={**metadata, "results_count": len(notes)}
            )

            return result if return_structured else context

        except Exception as e:
            logger.error(f"Error searching knowledge: {e}")
            metadata["error"] = str(e)
            result = KnowledgeSearchResult(
                context="",
                has_results=False,
                suggested_topics=[],
                gap_logged=False,
                metadata=metadata
            )
            return result if return_structured else ""

    async def close(self):
        for mem in self._memories.values():
            await mem.close()
        self._memories.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Browse-failure alternative-source suggestions ─────────────────────────
    # Simple heuristics for proposing backup sources when browse() returns an error.
    _BROWSE_ALTERNATIVES: Dict[str, str] = {
        "wikipedia": "Try: https://en.wikipedia.org/wiki/{topic}",
        "github": "Try the GitHub API: https://api.github.com/search/repositories?q={topic}",
        "docs": "Try searching documentation via Google: https://www.google.com/search?q=site:docs+{topic}",
        "default": "You can try: DuckDuckGo (https://duckduckgo.com/?q={topic}), Archive (https://web.archive.org/), or a different mirror.",
    }

    @staticmethod
    def _detect_browse_failure(tool_result: str) -> bool:
        """Return True when a tool result string signals a web-browse failure."""
        failure_markers = (
            "Error browsing",
            "Error: Connection",
            "Error: timed out",
            "ConnectionError",
            "HTTPError",
            "SSLError",
            "[Errno",
            "Failed to fetch",
            "status code 4",
            "status code 5",
        )
        return any(m.lower() in tool_result.lower() for m in failure_markers)

    @staticmethod
    def _browse_alternative_hint(url: str, user_message: str) -> str:
        """Generate an alternative-source suggestion for a failed browse."""
        # Extract a simple topic from the URL or user message
        topic = re.sub(r'https?://[^/]*', '', url).strip('/').replace('-', '+').replace('_', '+')[:60]
        if not topic:
            # Fall back to first 4 meaningful words from the user message
            topic = '+'.join(
                w for w in re.sub(r'[^\w\s]', '', user_message).split() if len(w) > 2
            )[:60]
        hint = Agent._BROWSE_ALTERNATIVES["default"].format(topic=topic)
        return f"\n\n[Web-browsing failed. Alternative sources you could try: {hint}]"

    # ── Empty-response recovery ───────────────────────────────────────────────

    @staticmethod
    def _is_empty_response(text: str) -> bool:
        """Return True when the LLM returned a blank or whitespace-only response."""
        return not text or not text.strip()

    async def _recover_empty_response(
        self,
        messages: list,
        user_message: str,
        user_id: str,
        had_kb_results: bool,
    ) -> str:
        """Attempt to recover from an empty LLM response.

        Strategy:
        1. Re-prompt the model with an explicit non-empty instruction.
        2. If the KB had no results, append a suggestion to create a KB entry.
        3. Return a graceful fallback string if the second attempt also fails.
        """
        logger.warning("Empty LLM response detected — attempting recovery.")

        recovery_prompt = (
            "Your previous response was empty. "
            "Please provide a helpful, non-empty answer to the user's request."
        )
        recovery_messages = messages + [
            {"role": "assistant", "content": ""},
            {"role": "user", "content": recovery_prompt},
        ]

        try:
            recovered, _ = await self.provider.chat(recovery_messages, self.model)
            if not self._is_empty_response(recovered):
                logger.info("Empty-response recovery succeeded.")
                return recovered
        except Exception as e:
            logger.error(f"Recovery LLM call failed: {e}")

        # Build a meaningful fallback
        fallback_parts = [
            "I'm sorry — I wasn't able to generate a useful response right now."
        ]
        if not had_kb_results:
            fallback_parts.append(
                f"\n\nI also noticed my knowledge base has no entries related to your query. "
                f"You can add information with:\n"
                f"  `write_to_knowledge(title=\"<topic>\", content=\"<details>\")`"
            )
        return " ".join(fallback_parts)

    # ── Main think() ─────────────────────────────────────────────────────────

    async def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.
        
        Intelligent Routing: choosing model based on task complexity.
        """
        global _LAST_ACTIVE_TIME
        _LAST_ACTIVE_TIME = time.time()

        # Intelligent Routing: Choose model based on complexity if enabled
        if getattr(self.config.intelligence, 'intelligent_routing', False):
            complexity = self._analyze_complexity(user_message)
            if complexity == "high":
                from .llm_library import get_optimal_model
                optimal = get_optimal_model(tier="premium")
                if optimal != self.model:
                    logger.info(f"Intelligent Routing: Upgrading to {optimal} for high complexity task")
                    self.model = optimal

        import uuid
        
        # Generate unique task ID for this request
        self._current_task_id = f"task_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
        
        # Start task timer for tracking timeouts
        await self._task_timer.start_task_timer(
            task_id=self._current_task_id,
            user_question=user_message,
            on_status_update=self._handle_task_status_update,
            steps_total=5  # Approximate: memory, knowledge, LLM, tools, response
        )
        
        # Agent Pipeline Integration: Loop prevention
        if _depth > 10:
            logger.warning(f"Max delegation depth reached ({_depth}). Preventing potential infinite loop.")
            await self._task_timer.complete_task(self._current_task_id, success=False, 
                                                 error_message="Max delegation depth reached")
            return "I've reached the maximum delegation depth. Let me handle this request directly."

        # Medic Agent: Check loop prevention before processing
        try:
            from myclaw.agents.medic_agent import prevent_infinite_loop
            loop_status = prevent_infinite_loop()
            if "limit reached" in loop_status.lower():
                logger.warning("Execution limit reached by loop prevention")
                await self._task_timer.complete_task(self._current_task_id, success=False,
                                                     error_message="Loop prevention limit reached")
                return "I'm detecting repeated patterns in the request. Let me break out of the loop and handle this directly."
        except Exception:
            pass

        # Check if task has been cancelled due to timeout
        if self._current_task_id and not self._task_timer.is_task_active(self._current_task_id):
            logger.warning(f"Task {self._current_task_id} was cancelled or timed out")
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."

        mem = await self._get_memory(user_id)
        await mem.add("user", user_message)
        
        # Update timer with current step
        await self._task_timer.update_step(self._current_task_id, "memory_loading", 1, 5)

        trigger_hook("on_session_start", user_id, self.name)

        history = await mem.get_history()

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
            summary_msgs = [
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": summary_prompt},
            ]
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
        had_kb_results = bool(knowledge_context)

        # If KB gap exists and no context, hint the agent about KB creation and log the gap
        kb_gap_hint = ""
        if not had_kb_results and self._kb_gaps.get(user_id):
            last_gap = next(iter(self._kb_gaps[user_id]))
            kb_gap_hint = (
                f"\n\n[Note: The knowledge base has no entries related to '{last_gap[:60]}'. "
                "Consider using write_to_knowledge() to store useful information for future queries.]"
            )

            # Emit structured log entry for knowledge gap (with deduplication)
            if not self._gap_cache.is_duplicate(last_gap, user_id):
                gap_data = {
                    "event": "knowledge_gap_detected",
                    "query": last_gap,
                    "description": "No knowledge base entries found for query",
                    "user_id": user_id,
                    "session_context": "System will preserve context to avoid redundant empty searches in this session",
                    "timestamp": datetime.utcnow().isoformat(),
                    "recommendation": "Use write_to_knowledge() to create a new entry for future queries"
                }
                kb_gap_logger.info(gap_data)
                
                # Write to researchers JSONL file
                try:
                    GAP_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(GAP_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(gap_data) + "\n")
                except Exception as e:
                    logger.error(f"Failed to record gap to file: {e}")

        # Check if task has been cancelled due to timeout
        if self._current_task_id and not self._task_timer.is_task_active(self._current_task_id):
            logger.warning(f"Task {self._current_task_id} was cancelled or timed out")
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."

        # Update timer - building system prompt
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "building_prompt", 2, 5)
        
        # Build system prompt with knowledge context (async load to avoid blocking)
        system_prompt = await self._load_system_prompt()
        system_content = system_prompt
        if knowledge_context:
            system_content = f"{system_prompt}\n\n{knowledge_context}"
        if kb_gap_hint:
            system_content = f"{system_content}{kb_gap_hint}"

        messages = [{"role": "system", "content": system_content}] + history

        # Trigger pre_llm_call hooks - allow hooks to modify messages
        hook_results = trigger_hook("pre_llm_call", messages, self.model)
        for result in hook_results:
            if result and isinstance(result, list):
                messages = result  # Use modified messages from hook
                logger.debug("pre_llm_call hook modified messages")

        # Update timer - calling LLM
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "llm_call", 3, 5)
        
        try:
            import httpx
            response, tool_calls = await self.provider.chat(messages, self.model)
        except httpx.TimeoutException as e:
            logger.error(f"LLM provider timeout: {e}")
            error_msg = "Sorry, the LLM service timed out. Please try again."
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except (httpx.ConnectError, ConnectionError) as e:
            logger.error(f"LLM provider connection error: {e}")
            error_msg = "Sorry, I cannot connect to the LLM service. Please check your connection."
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM provider HTTP error: {e}")
            error_msg = f"Sorry, the LLM service returned an error: {e.response.status_code}"
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except Exception as e:
            logger.exception(f"Unexpected LLM provider error: {e}")
            error_msg = f"Sorry, an unexpected error occurred: {e}"
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg

        # Trigger post_llm_call hooks
        hook_results = trigger_hook("post_llm_call", response, tool_calls)
        for result in hook_results:
            if result and isinstance(result, tuple) and len(result) == 2:
                response, tool_calls = result  # Allow hooks to modify response/tool_calls

        if tool_calls:
            # Update timer - executing tools
            if self._current_task_id:
                await self._task_timer.update_step(self._current_task_id, "executing_tools", 4, 5)
            
            # Determine if we can use parallel execution
            independent_tools = [tc for tc in tool_calls if is_tool_independent(tc.get("function", {}).get("name", ""))]

            if len(independent_tools) > 1:
                # Use parallel execution for independent tools
                logger.info(f"Executing {len(independent_tools)} tools in parallel")
                executor = get_parallel_executor()
                exec_results = await executor.execute_tools(independent_tools, user_id)

                # Format results for the LLM; annotate browse failures
                result_parts = []
                for r in exec_results:
                    if r["success"]:
                        tool_output = r['result']
                        if r['tool_name'] == 'browse' and self._detect_browse_failure(tool_output):
                            url_match = re.search(r'https?://\S+', tool_output)
                            url = url_match.group(0) if url_match else ""
                            tool_output += self._browse_alternative_hint(url, user_message)
                        result_parts.append(f"Tool {r['tool_name']} returned: {tool_output}")
                    else:
                        result_parts.append(f"Tool {r['tool_name']} error: {r['error']}")

                results = result_parts
            else:
                # Fall back to sequential execution for single tool or dependent tools
                import time
                import inspect
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

                    start_time = time.time()
                    logger.info(f"[AUDIT] Tool execution started: {tool_name} with args: {args}")

                    try:
                        func = TOOLS[tool_name]["func"]
                        if inspect.iscoroutinefunction(func):
                            result = await func(**args)
                        else:
                            result = await asyncio.to_thread(func, **args)

                        tool_output = str(result)

                        # Error Handling Enhancement 1: browse failure → suggest alternatives
                        if tool_name == "browse" and self._detect_browse_failure(tool_output):
                            url = args.get("url", "")
                            tool_output += self._browse_alternative_hint(url, user_message)
                            logger.info(f"Browse failure detected for {url}; alternative hint appended.")

                        # Error Handling Enhancement 2: empty KB search → nudge KB creation
                        if tool_name == "search_knowledge":
                            if "No results found" in tool_output or "Error" in tool_output:
                                query = args.get("query", user_message[:60])
                                self._record_kb_gap(user_id, query)
                                tool_output += (
                                    f"\n\n[Tip: No knowledge base entries matched '{query}'. "
                                    "Use write_to_knowledge() to persist useful information for future use.]"
                                )

                        await mem.add("tool", f"Tool {tool_name} returned: {tool_output}")
                        results.append(tool_output)
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
                import httpx as _httpx  # local alias to avoid shadowing outer scope
                final_response, _ = await self.provider.chat(followup, self.model)

                # Trigger post_llm_call hooks for followup
                trigger_hook("post_llm_call", final_response, None)

                # Empty-response recovery after tool-use followup
                if self._is_empty_response(final_response):
                    final_response = await self._recover_empty_response(
                        followup, user_message, user_id, had_kb_results
                    )

                await mem.add("assistant", final_response)

                # Trigger on_session_end hook
                message_count = len(await mem.get_history()) if hasattr(mem, 'get_history') else 0
                trigger_hook("on_session_end", user_id, self.name, message_count)

                # Complete task timer successfully
                if self._current_task_id:
                    await self._task_timer.complete_task(self._current_task_id, success=True)
                    self._current_task_id = None

                return final_response
            except Exception as e:
                logger.error(f"LLM second call error: {e}")
                error_msg = f"Tool executed but error getting response: {e}"
                # Complete task timer with error
                if self._current_task_id:
                    await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                    self._current_task_id = None
                return error_msg

        # ── No tool calls: validate response is non-empty ────────────────────
        if self._is_empty_response(response):
            response = await self._recover_empty_response(
                messages, user_message, user_id, had_kb_results
            )

        await mem.add("assistant", response)

        # Trigger on_session_end hook
        message_count = len(await mem.get_history()) if hasattr(mem, 'get_history') else 0
        trigger_hook("on_session_end", user_id, self.name, message_count)

        # Complete task timer successfully
        if self._current_task_id:
            await self._task_timer.complete_task(self._current_task_id, success=True)
            self._current_task_id = None

        return response

    async def stream_think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> AsyncIterator[str]:
        """Process a user message and yield response chunks in real-time."""
        global _LAST_ACTIVE_TIME
        _LAST_ACTIVE_TIME = time.time()
        
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
            summary_msgs = [
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": summary_prompt},
            ]
            try:
                summary_text, _ = await self.provider.chat(summary_msgs, self.model)
                history = [{"role": "system", "content": f"Previous conversation summary: {summary_text}"}] + recent
            except Exception as e:
                logger.error(f"Error summarizing history: {e}")
                # fallback to raw history if summary fails

        # Search knowledge base for relevant context
        knowledge_context = self._search_knowledge_context(user_message, user_id)
        had_kb_results = bool(knowledge_context)

        # KB gap hint for streaming mode
        kb_gap_hint = ""
        if not had_kb_results and self._kb_gaps.get(user_id):
            last_gap = next(iter(self._kb_gaps[user_id]))
            kb_gap_hint = (
                f"\n\n[Note: The knowledge base has no entries related to '{last_gap[:60]}'. "
                "Consider using write_to_knowledge() to store useful information for future queries.]"
            )

        # Build system prompt with knowledge context (async load to avoid blocking)
        system_prompt = await self._load_system_prompt()
        system_content = system_prompt
        if knowledge_context:
            system_content = f"{system_prompt}\n\n{knowledge_context}"
        if kb_gap_hint:
            system_content = f"{system_content}{kb_gap_hint}"

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

        # Empty-response recovery for streaming: emit fallback if nothing arrived
        if self._is_empty_response(full_response):
            logger.warning("Streaming produced empty response — emitting fallback.")
            fallback = "I'm sorry — I wasn't able to generate a response."
            if not had_kb_results:
                fallback += (
                    " My knowledge base has no entries on this topic. "
                    "You can add information with: write_to_knowledge(title=\"<topic>\", content=\"<details>\")"
                )
            yield fallback
            full_response = fallback

        # Trigger post_llm_call hooks
        trigger_hook("post_llm_call", full_response, tool_calls)

        # Note: Tool calls are not supported in streaming mode yet
        # The full response is returned as chunks
        await mem.add("assistant", full_response)

        # Trigger on_session_end hook
        trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))

        yield "[TOOL_CALLS_NONE]"  # Signal that streaming is complete