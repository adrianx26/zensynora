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
import os
import re
import threading
import inspect
from pathlib import Path
from typing import List, Dict, Tuple, Optional, AsyncIterator, Set, Union, Any
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import time

from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .knowledge import search_notes, build_context, write_note
from .semantic_cache import get_semantic_cache
from .skill_preloader import get_skill_preloader, start_preloader, stop_preloader
from .task_timer import get_task_timer_orchestrator, TaskStatus, Colors as TimerColors
from rich.console import Console

logger = logging.getLogger(__name__)
kb_gap_logger = logging.getLogger("myclaw.knowledge.gaps")

# Re-export the canonical default from `defaults.py`. Existing code that
# imports `GAP_FILE` from `myclaw.agent` keeps working; new code should
# prefer `from myclaw.defaults import GAP_FILE` to avoid the indirection.
from .defaults import GAP_FILE  # noqa: E402

_LAST_ACTIVE_TIME = time.time()


# ── Dedicated KB-search executor ──────────────────────────────────────────
# Sprint 10 #5: KB FTS5 queries used to share the default ``asyncio.to_thread``
# pool. Under concurrent load they queued behind unrelated I/O. This pool is
# sized for KB latency profiles (mostly disk-bound) and is module-global so
# multiple Agent instances share it.

from concurrent.futures import ThreadPoolExecutor as _KBExecutor

_kb_search_executor: Optional[_KBExecutor] = None
_kb_search_executor_lock = threading.Lock()
# Source of truth lives in `defaults.py`; alias kept for backward compatibility.
from .defaults import KB_SEARCH_EXECUTOR_WORKERS as _KB_SEARCH_EXECUTOR_WORKERS  # noqa: E402


def _get_kb_search_executor() -> _KBExecutor:
    """Lazily create the shared KB-search executor.

    The default of 8 workers balances throughput against the SQLite
    write-lock contention you'd see if every reader monopolized a thread.
    Override with ``MYCLAW_KB_SEARCH_WORKERS`` for benchmarking.
    """
    global _kb_search_executor
    if _kb_search_executor is not None:
        return _kb_search_executor
    with _kb_search_executor_lock:
        if _kb_search_executor is None:
            _kb_search_executor = _KBExecutor(
                max_workers=_KB_SEARCH_EXECUTOR_WORKERS,
                thread_name_prefix="myclaw-kb",
            )
    return _kb_search_executor


def shutdown_kb_search_executor() -> None:
    """Shut down the KB-search executor. Mostly for tests + clean reloads."""
    global _kb_search_executor
    with _kb_search_executor_lock:
        if _kb_search_executor is not None:
            _kb_search_executor.shutdown(wait=False, cancel_futures=True)
            _kb_search_executor = None


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

    Optimization: Amortized cleanup runs every N calls instead of every call
    to avoid O(n) dict rebuild overhead under high load.
    """

    def __init__(self, timeout_seconds: float = 300.0, cleanup_interval: int = 100):
        self._cache: Dict[str, float] = {}
        self._timeout = timeout_seconds
        self._enabled = True
        self._cleanup_interval = cleanup_interval
        self._call_count = 0

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

        # Amortized cleanup: only clean expired entries every N calls
        self._call_count += 1
        if self._call_count >= self._cleanup_interval:
            self._cache = {k: v for k, v in self._cache.items() if now - v < self._timeout}
            self._call_count = 0

        if key in self._cache:
            return True

        self._cache[key] = now
        return False

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._call_count = 0

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
    except Exception as e:
        # Permissions / missing path / network FS hiccup. We still return
        # a usable key (cache stays consistent within the process), but
        # log so it's not silently weird.
        logger.debug(f"Profile mtime stat failed for {profile_path}: {e}")
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

        # LRU eviction: remove oldest items when at or over capacity.
        # Using >= keeps the cache strictly bounded; the previous > allowed
        # one-over-limit growth between evictions.
        while len(_profile_cache) >= _profile_cache_maxsize:
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
    "KNOWLEDGE GROWTH MANDATE: After EVERY interaction where the user shares factual information, "
    "preferences, configurations, server details, hostnames, ports, credentials, file paths, "
    "important decisions, or any data worth remembering — you MUST proactively call "
    "write_to_knowledge() to persist it permanently. Do NOT wait to be asked. "
    "Every meaningful exchange must grow the knowledge base. "
    "For all other responses, reply in plain text."
)


class Agent:
    """Personal AI agent with per-user memory, native tool calling, multi-agent delegation."""

    @property
    def _kb_auto_extract(self) -> bool:
        """Read-once helper: is automatic KB extraction enabled in config?"""
        try:
            return bool(self.config.knowledge.auto_extract)
        except Exception as e:
            # Defaults to False on any config-read failure. We log only
            # once per agent to avoid spamming the logs on repeated reads.
            if not getattr(self, "_kb_auto_extract_warned", False):
                logger.warning(
                    "config.knowledge.auto_extract unreadable; defaulting to False",
                    exc_info=e,
                )
                # Mark via a normal attribute — properties can't shortcut
                # __setattr__ without breaking the descriptor protocol.
                object.__setattr__(self, "_kb_auto_extract_warned", True)
            return False

    def __init__(
        self,
        config,
        name: str = "default",
        model: str = None,
        system_prompt: str = None,
        provider_name: str = None,
    ):
        self.name = name
        self._memories: dict[str, Memory] = {}

        # ── Lazy provider initialization ────────────────────────────────────────
        # Store config and provider name for lazy initialization
        self._config = config
        self._provider = None  # Will be initialized on first access

        try:
            default_provider = config.agents.defaults.provider or "ollama"
        except Exception as e:
            logger.warning(
                "config.agents.defaults.provider unreadable; defaulting to 'ollama'",
                exc_info=e,
            )
            default_provider = "ollama"
        self._provider_name = provider_name or default_provider

        # ── Resolve model ─────────────────────────────────────────────────────
        try:
            cfg_model = config.agents.defaults.model
        except Exception as e:
            logger.warning(
                "config.agents.defaults.model unreadable; defaulting to 'llama3.2'",
                exc_info=e,
            )
            cfg_model = "llama3.2"
        self.model = model or cfg_model

        # Store paths for lazy profile loading (to avoid blocking in __init__)
        self._local_profiles_dir = Path(__file__).parent / "profiles"
        self._user_profiles_dir = Path(
            getattr(config.agents, "profiles_dir", "~/.myclaw/profiles")
        ).expanduser()
        self._custom_system_prompt = system_prompt
        self._system_prompt_loaded = False
        self._system_prompt = ""

        # Initialize skill preloader for this agent
        self._skill_preloader = get_skill_preloader()

        # Store pending preload tasks to prevent garbage collection
        # PERF FIX (2026-04-23): Bounded set to prevent memory leaks under burst load.
        self._pending_preloads: set[asyncio.Task] = set()
        self._max_pending_preloads = 100

        # Store config for later use
        self.config = config

        # Offline mode: enable fallback to local providers on connection failure
        self._offline_mode = getattr(getattr(config, "intelligence", None), "offline_mode", True)
        self._fallback_wrapper: Optional[Any] = None

        # ── Circuit breaker around the primary provider call ─────────────────
        # Wraps ``self.provider.chat`` so persistent failures don't keep
        # hammering a flapping endpoint. Once OPEN, requests fall straight
        # through to the offline fallback (or raise to the caller when no
        # fallback is configured). Tunable via config.resilience.* with safe
        # defaults; the breaker is disabled entirely when failure_threshold
        # is set to 0 — useful for tests and single-provider deployments
        # that prefer the historical behavior.
        from .resilience import CircuitBreaker
        _resil = getattr(config, "resilience", None)
        _ft = int(getattr(_resil, "failure_threshold", 5)) if _resil else 5
        _rt = float(getattr(_resil, "reset_timeout", 60.0)) if _resil else 60.0
        self._provider_breaker: Optional[CircuitBreaker] = (
            CircuitBreaker(
                name=f"provider:{self._provider_name}",
                failure_threshold=_ft,
                reset_timeout=_rt,
            )
            if _ft > 0
            else None
        )

        # Initialize knowledge gap cache for deduplication
        self._gap_cache = KnowledgeGapCache(timeout_seconds=300.0)

        # Initialize task timer orchestrator for tracking user question timeouts
        self._task_timer = get_task_timer_orchestrator()
        self._current_task_id: Optional[str] = None

        # ── Intelligent Routing ──────────────────────────────────────────────
        from .backends.router import IntelligentRouter

        self._router = IntelligentRouter(config)

        # ── Hardware Awareness & Optimization Check ──────────────────────────
        # PERF FIX (2026-04-23): Hardware probes block startup by 100-500ms.
        # Defer to a background thread so __init__ returns immediately.
        def _deferred_hardware_check():
            try:
                from .backends.hardware import get_system_metrics, get_optimization_suggestions

                metrics = get_system_metrics()
                suggestions = get_optimization_suggestions(metrics)
                if suggestions:
                    for s in suggestions:
                        logger.info(f"System Optimization Note: {s}")
            except Exception as e:
                logger.debug(f"Hardware optimization check skipped: {e}")

        threading.Thread(target=_deferred_hardware_check, daemon=True).start()

    def _handle_task_status_update(self, update) -> None:
        """Handle status updates from the task timer."""
        # Format and print the status update to console
        timestamp = datetime.fromtimestamp(update.timestamp).strftime("%H:%M:%S")
        elapsed = f"[{update.elapsed_seconds:.1f}s]"

        print(
            f"\n{TimerColors.TIMESTAMP}[{timestamp}]{TimerColors.RESET} "
            f"{TimerColors.METRIC}{elapsed}{TimerColors.RESET}",
            end="",
        )

        if update.threshold:
            print(
                f" {TimerColors.WARNING}[THRESHOLD: {update.threshold}s]{TimerColors.RESET}", end=""
            )
        print()

        if update.step_name:
            print(f"  Step: {TimerColors.STEP_NAME}{update.step_name}{TimerColors.RESET}")

        print(f"  {update.color}{update.message}{TimerColors.RESET}")

        # If it's the max timeout failure, the task is already being terminated
        if update.threshold == 300 and update.message_type == "fatal":
            logger.critical(f"Task {update.task_id} reached maximum timeout")

    def _track_preload(self, task: asyncio.Task) -> None:
        """Safely track a preload task with bounded memory.

        PERF FIX (2026-04-23): Evicts oldest completed tasks if the set
        exceeds _max_pending_preloads to prevent unbounded growth.
        """
        # Prune completed tasks if we're near the limit
        if len(self._pending_preloads) >= self._max_pending_preloads:
            completed = {t for t in self._pending_preloads if t.done()}
            self._pending_preloads.difference_update(completed)
            # If still at limit, evict oldest by removing arbitrary tasks
            while len(self._pending_preloads) >= self._max_pending_preloads:
                try:
                    self._pending_preloads.pop()
                except KeyError:
                    break
        self._pending_preloads.add(task)

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
        if not skip_cache and hasattr(self, "_gap_cache"):
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
        if hasattr(self, "_gap_cache"):
            self._gap_cache.clear()

    def set_gap_cache_enabled(self, enabled: bool) -> None:
        """Enable or disable gap cache (test hook).

        Args:
            enabled: Whether to enable deduplication caching
        """
        self._knowledge_gap_cache_enabled = enabled
        if hasattr(self, "_gap_cache"):
            self._gap_cache.set_enabled(enabled)

    # ── Automatic Knowledge Extraction ───────────────────────────────────────

    @staticmethod
    def _should_extract_knowledge(user_message: str, response: str) -> bool:
        """Heuristic to decide if an interaction is worth auto-extracting knowledge from.

        Returns True only for exchanges that contain concrete, memorable information
        (IPs, URLs, config, tech terms, preferences, long substantive text).
        Prevents spurious KB entries from trivial chit-chat or yes/no replies.
        """
        # Skip very short exchanges — almost certainly trivial
        if len(user_message.strip()) < 30 and len(response.strip()) < 80:
            return False

        # Skip common non-informational openers
        _trivial_re = re.compile(
            r"^\s*(hi|hello|hey|thanks?|thank you|ok|okay|yes|no|sure|great|"
            r"cool|bye|goodbye|good morning|good night|lol|haha|got it|noted)\W*$",
            re.IGNORECASE,
        )
        if _trivial_re.match(user_message.strip()):
            return False

        # Positive signals — any of these make the exchange knowledge-worthy
        _knowledge_signals = [
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",  # IP address
            r"https?://\S+",  # URL
            r"[A-Za-z]:\\[^\s]+|(?:/[a-z][^\s]+){2,}",  # file path
            r"\bversion\s+\d",  # version number
            r"\b(?:config|password|token|api[_\-]?key|secret|endpoint|"  # tech config
            r"server|host(?:name)?|port|user(?:name)?|database|schema|"  # infra
            r"install|docker|container|cluster|namespace|region)\b",
            r"\b(?:i prefer|i always|i use|i like|my \w+ is|"  # user preferences
            r"i'm using|we use|we're using|our \w+ is)\b",
            r"\b(?:install|configure|setup|deploy|enable|disable|connect)\b",  # config actions
            r"\b(?:remember|note that|keep in mind|important:|fyi:)\b",  # explicit memory cues
        ]
        combined = user_message + " " + response
        for pattern in _knowledge_signals:
            if re.search(pattern, combined, re.IGNORECASE):
                return True

        # Long substantive exchanges are likely meaningful
        if len(user_message) > 120 and len(response) > 300:
            return True

        return False

    async def _extract_and_save_knowledge(
        self,
        user_message: str,
        response: str,
        user_id: str,
    ) -> None:
        """Background task: use the LLM to extract facts from an interaction and save them.

        Sends a short focused extraction prompt and writes any discovered facts to the
        knowledge base automatically.  Runs fire-and-forget via asyncio.create_task();
        errors are silently logged so they never disrupt the main pipeline.
        """
        try:
            extraction_system = (
                "You are a knowledge extraction assistant. "
                "Analyse the conversation exchange below and extract ONLY concrete, "
                "factual information worth storing permanently "
                "(IP addresses, hostnames, credentials placeholders, configurations, "
                "file paths, user preferences, decisions, important facts). "
                "Return a JSON array of objects: "
                '[{"title": "<short title>", "content": "<full detail>", "tags": "tag1,tag2"}]. '
                "Use specific, searchable titles. "
                "Return ONLY the JSON array — no explanation, no markdown fences. "
                "Return [] if nothing is concretely worth saving."
            )
            exchange = f"User: {user_message[:800]}\n\nAssistant: {response[:1200]}"
            messages = [
                {"role": "system", "content": extraction_system},
                {"role": "user", "content": exchange},
            ]

            raw, _ = await self._provider_chat(messages, self.model)

            # Strip optional markdown code fences
            raw = raw.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

            facts = json.loads(raw)
            if not isinstance(facts, list):
                return

            saved = 0
            for fact in facts[:5]:  # Hard cap: max 5 entries per turn
                if not isinstance(fact, dict):
                    continue
                title = str(fact.get("title", "")).strip()
                content = str(fact.get("content", "")).strip()
                tags_raw = str(fact.get("tags", "")).strip()
                if not title or not content:
                    continue
                tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
                tag_list.append("auto-extracted")
                try:
                    write_note(
                        name=title,
                        title=title,
                        content=content,
                        tags=tag_list,
                        user_id=user_id,
                    )
                    saved += 1
                    logger.info(f"[KB-AUTO] Saved: '{title}' for user '{user_id}'")
                except Exception as write_err:
                    logger.debug(f"[KB-AUTO] Could not save '{title}': {write_err}")

            if saved:
                logger.info(
                    f"[KB-AUTO] Auto-extraction saved {saved} entr"
                    f"{'y' if saved == 1 else 'ies'} from interaction."
                )

        except Exception as exc:
            # Never crash the main pipeline
            logger.debug(f"[KB-AUTO] Extraction skipped: {exc}")

    async def _load_system_prompt(self) -> str:
        """Lazy load system prompt with async file I/O.

        Loads the agent profile and the optional user-dialectic profile in
        parallel. Both are filesystem reads, so overlapping them roughly
        halves the prompt-load latency on cold cache.
        """
        if self._system_prompt_loaded:
            return self._system_prompt

        # Determine which profile path to use (local workspace wins).
        local_profile = self._local_profiles_dir / f"{self.name}.md"
        if local_profile.exists():
            profile_path: Optional[Path] = local_profile
        else:
            self._user_profiles_dir.mkdir(parents=True, exist_ok=True)
            home_profile = self._user_profiles_dir / f"{self.name}.md"
            profile_path = home_profile if home_profile.exists() else None

        dialectic_path = self._local_profiles_dir / "user_dialectic.md"
        dialectic_exists = dialectic_path.exists()

        async def _load_main() -> str:
            if profile_path is not None:
                return await _load_profile_cached_async(self.name, profile_path)
            return self._custom_system_prompt or SYSTEM_PROMPT

        async def _load_dialectic() -> str:
            if not dialectic_exists:
                return ""
            content = await asyncio.to_thread(dialectic_path.read_text, encoding="utf-8")
            return content.strip()

        # Overlap the two reads. asyncio.gather schedules them concurrently
        # so the dialectic file's stat+read overlaps the profile load.
        prompt, dialectic_content = await asyncio.gather(_load_main(), _load_dialectic())

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
            "implement",
            "debug",
            "fix",
            "refactor",
            "architect",
            "analyze",
            "compare",
            "optimize",
            "write a program",
            "complex",
            "threading",
            "asyncio",
            "database schema",
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

    async def _provider_chat(self, messages, model, stream: bool = False):
        """Call provider.chat() with circuit breaker + optional offline fallback.

        Pipeline:

        1. **Tracing span** (``provider.chat``) when OTel is active —
           no-op otherwise.
        2. **Circuit breaker** wraps the primary call. After
           ``failure_threshold`` consecutive failures the breaker opens and
           subsequent requests skip the primary call entirely (failing fast
           into the fallback path) for ``reset_timeout`` seconds.
        3. **Offline fallback** kicks in when the primary fails (or is
           short-circuited) AND ``offline_mode`` is enabled. Routes through
           ``offline.FallbackChatWrapper`` which tries local providers in
           sequence (Ollama → LM Studio → llama.cpp).

        Errors that aren't connection-shaped propagate immediately when
        offline_mode is off, matching the historical behavior.
        """
        from .observability import span
        from .resilience import CircuitBreakerError

        async def _primary():
            with span("provider.chat", provider=self._provider_name, model=model):
                return await self.provider.chat(messages, model, stream=stream)

        # Fast path: no breaker, no fallback — direct call.
        if self._provider_breaker is None and not self._offline_mode:
            return await _primary()

        # Try the primary call (through the breaker if configured).
        try:
            if self._provider_breaker is not None:
                return await self._provider_breaker.call(_primary)
            return await _primary()
        except CircuitBreakerError as e:
            # Breaker is OPEN — skip the primary call but still try the
            # fallback (the local providers may be reachable even when the
            # remote one is flapping).
            if not self._offline_mode:
                raise
            logger.info("Provider circuit %s open; using offline fallback", self._provider_name)
        except (ConnectionError, TimeoutError, OSError) as e:
            if not self._offline_mode:
                raise
            logger.warning(f"Primary provider failed: {e}. Trying offline fallback...")
        except Exception as e:
            if not self._offline_mode:
                raise
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("connection", "timeout", "unreachable", "refused")):
                logger.warning(f"Primary provider failed: {e}. Trying offline fallback...")
            else:
                raise

        # Initialize fallback wrapper on first need.
        if self._fallback_wrapper is None:
            from .offline import FallbackChatWrapper
            self._fallback_wrapper = FallbackChatWrapper(self.provider, self.config)

        with span("provider.fallback_chat", provider=self._provider_name, model=model):
            return await self._fallback_wrapper.chat(messages, model, stream=stream)

    def _extract_suggested_topics(self, message: str) -> List[str]:
        """Extract suggested topics from a message for knowledge gap guidance.

        Args:
            message: The user's message to analyze

        Returns:
            List of suggested topic keywords (words > 3 chars, bigrams)
        """
        cleaned = re.sub(r"[^\w\s]", " ", message.lower())
        words = [w for w in cleaned.split() if len(w) > 3]

        topics = words[:5]  # Top single words

        # Add bigrams for more context
        if len(words) >= 2:
            bigrams = [f"{words[i]} {words[i + 1]}" for i in range(min(len(words) - 1, 3))]
            topics.extend(bigrams)

        return list(dict.fromkeys(topics))  # Remove duplicates, preserve order

    async def _background_summarize_context(
        self, history: List[Dict[str, str]], user_id: str, mem: Memory
    ) -> None:
        """Summarize conversation context in the background after responding.

        This moves the LLM-based summarization off the hot path so the user
        response is not blocked. The summary is written to the knowledge base
        as a session note for future context retrieval.

        Args:
            history: Full conversation history before truncation
            user_id: User identifier
            mem: Memory instance for this user
        """
        try:
            # Exclude the most recent 5 messages from summarization
            to_summarize = history[:-5] if len(history) > 5 else history
            if not to_summarize:
                return

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

            summary_text, _ = await self._provider_chat(summary_msgs, self.model)
            original_len = sum(len(m["content"]) for m in to_summarize)
            compressed_len = len(summary_text)
            compression_ratio = (1 - compressed_len / original_len) * 100 if original_len > 0 else 0
            logger.debug(
                f"Background trajectory compressed: {original_len} -> {compressed_len} chars "
                f"({compression_ratio:.1f}% reduction)"
            )

            # Store summary in knowledge base for future retrieval
            from .knowledge import write_note

            await asyncio.to_thread(
                write_note,
                name=f"session-summary-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                title=f"Session Summary ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})",
                content=summary_text,
                user_id=user_id,
                tags=["session_summary", "auto-generated"],
            )
        except Exception as e:
            logger.error(f"Background summarization error: {e}")

    def _search_knowledge_context_sync(
        self, message: str, user_id: str, max_results: int = 3, return_structured: bool = False
    ) -> Union[str, KnowledgeSearchResult]:
        """Synchronous implementation of knowledge context search.

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
            "max_results": max_results,
        }

        try:
            # Resolve explicit memory:// references
            memory_refs = re.findall(r"memory://([\w\-]+)", message)

            # Clean the message for keyword extraction
            cleaned = re.sub(r"[^\w\s]", " ", message.lower())
            words = [w for w in cleaned.split() if len(w) > 3]

            notes = []
            if words:
                # Strategy 1: search with full message text (FTS5 ranked)
                notes = search_notes(message, user_id, limit=max_results)

                if not notes:
                    # Strategy 2: bigram + single-keyword OR query
                    bigrams = [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
                    candidates = bigrams[:3] + words[:5]
                    query = " OR ".join(f'"{t}"' if " " in t else t for t in candidates)
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

                guidance_lines.extend(
                    [
                        "\n**You can:**",
                        f"1. Create a new knowledge entry: `write_to_knowledge(title='Your Topic', content='Details...')`",
                        f"2. Browse existing entries: `list_knowledge()`",
                        f"3. Try different keywords in your search",
                        "\n---\n",
                    ]
                )

                context = "\n".join(guidance_lines)

                result = KnowledgeSearchResult(
                    context=context,
                    has_results=False,
                    suggested_topics=suggested_topics,
                    gap_logged=gap_logged,
                    metadata=metadata,
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
                    context_lines.append(
                        full_context[:500] + "..." if len(full_context) > 500 else full_context
                    )

            context_lines.append("\n---\n")
            context = "\n".join(context_lines)

            result = KnowledgeSearchResult(
                context=context,
                has_results=True,
                suggested_topics=[],
                gap_logged=False,
                metadata={**metadata, "results_count": len(notes)},
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
                metadata=metadata,
            )
            return result if return_structured else ""

    async def _search_knowledge_context(
        self, message: str, user_id: str, max_results: int = 3, return_structured: bool = False
    ) -> Union[str, KnowledgeSearchResult]:
        """Async wrapper for knowledge context search.

        Runs the synchronous FTS5 search on a **dedicated** thread pool
        (``_kb_search_executor``) so it doesn't compete with the default
        ``asyncio.to_thread`` pool used by everything else (file I/O,
        profile loads, blocking provider calls). At 5+ concurrent users
        the shared default pool was the bottleneck — KB queries waited
        behind unrelated I/O.

        The executor is module-global, lazily created, and survives the
        Agent lifecycle. Tests can monkey-patch ``_get_kb_search_executor``
        if they need a different concurrency model.
        """
        loop = asyncio.get_running_loop()
        executor = _get_kb_search_executor()
        from .observability import span as _span
        with _span("kb.search", user=user_id, max_results=max_results):
            return await loop.run_in_executor(
                executor,
                self._search_knowledge_context_sync,
                message,
                user_id,
                max_results,
                return_structured,
            )

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

    # ── Tool-result Knowledge Extraction ────────────────────────────────────

    # Tools whose output is worth persisting in the knowledge base
    _KB_WORTHY_TOOLS: frozenset = frozenset(
        {
            "shell",
            "browse",
            "read_file",
            "write_file",
            "download_file",
            "delegate",
        }
    )

    @staticmethod
    def _should_save_tool_result(tool_name: str, tool_output: str) -> bool:
        """Return True when a tool's output is substantial enough to save to the KB.

        Checks both the tool whitelist and a minimum length threshold so that
        trivial confirmations (e.g. 'File written.') are silently skipped.
        """
        return tool_name in Agent._KB_WORTHY_TOOLS and len(tool_output.strip()) > 120

    async def _save_tool_result_to_kb(
        self,
        tool_name: str,
        args: dict,
        tool_output: str,
        user_message: str,
        user_id: str,
    ) -> None:
        """Background task: extract and save knowledge from a completed tool execution.

        Sends a focused extraction prompt to the LLM asking it to identify concrete
        facts in the tool output (file contents, command results, downloaded data, etc.)
        and writes each fact to the KB via write_note().  Runs fire-and-forget;
        errors are silently logged so the main pipeline is never disrupted.
        """
        try:
            extraction_system = (
                "You are a knowledge extraction assistant. "
                "Review the following tool execution result and extract ONLY concrete, "
                "factual information worth storing permanently "
                "(file contents, configuration values, command output facts, research results, "
                "server details, hostnames, paths, versions, credentials placeholders). "
                "Return a JSON array of objects: "
                '[{"title": "<short searchable title>", "content": "<full detail>", "tags": "tag1,tag2"}]. '
                "Return ONLY the JSON array — no explanation, no markdown fences. "
                "Return [] if nothing is concretely worth saving."
            )
            context = (
                f"Tool: {tool_name}\n"
                f"Arguments: {json.dumps(args, default=str)[:300]}\n"
                f"User task context: {user_message[:200]}\n\n"
                f"Tool output:\n{tool_output[:1500]}"
            )
            messages = [
                {"role": "system", "content": extraction_system},
                {"role": "user", "content": context},
            ]

            raw, _ = await self._provider_chat(messages, self.model)
            raw = raw.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

            facts = json.loads(raw)
            if not isinstance(facts, list):
                return

            saved = 0
            for fact in facts[:3]:  # Hard cap: max 3 entries per tool call
                if not isinstance(fact, dict):
                    continue
                title = str(fact.get("title", "")).strip()
                content = str(fact.get("content", "")).strip()
                tags_raw = str(fact.get("tags", "")).strip()
                if not title or not content:
                    continue
                tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
                tag_list.extend(["auto-extracted", f"tool-{tool_name}"])
                try:
                    write_note(
                        name=title,
                        title=title,
                        content=content,
                        tags=tag_list,
                        user_id=user_id,
                    )
                    saved += 1
                    logger.info(f"[KB-TOOL] Saved from {tool_name}: '{title}'")
                except Exception as we:
                    logger.debug(f"[KB-TOOL] Could not save '{title}': {we}")

            if saved:
                logger.info(
                    f"[KB-TOOL] Saved {saved} "
                    f"{'entry' if saved == 1 else 'entries'} from {tool_name} result."
                )

        except Exception as exc:
            logger.debug(f"[KB-TOOL] Tool result extraction skipped for {tool_name}: {exc}")

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
        topic = (
            re.sub(r"https?://[^/]*", "", url).strip("/").replace("-", "+").replace("_", "+")[:60]
        )
        if not topic:
            # Fall back to first 4 meaningful words from the user message
            topic = "+".join(w for w in re.sub(r"[^\w\s]", "", user_message).split() if len(w) > 2)[
                :60
            ]
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
            recovered, _ = await self._provider_chat(recovery_messages, self.model)
            if not self._is_empty_response(recovered):
                logger.info("Empty-response recovery succeeded.")
                return recovered
        except Exception as e:
            logger.error(f"Recovery LLM call failed: {e}")

        # Build a meaningful fallback
        fallback_parts = ["I'm sorry — I wasn't able to generate a useful response right now."]
        if not had_kb_results:
            fallback_parts.append(
                f"\n\nI also noticed my knowledge base has no entries related to your query. "
                f"You can add information with:\n"
                f'  `write_to_knowledge(title="<topic>", content="<details>")`'
            )
        return " ".join(fallback_parts)

    # -- Sub-method: Route message ------------------------------------------------

    async def _route_message(self, user_message: str, user_id: str, _depth: int) -> tuple:
        """Delegate to ``agent_internals.router.route_message``.

        See :mod:`myclaw.agent_internals.router` for the implementation.
        Kept here as a thin wrapper so the public method name on ``Agent``
        is unchanged and existing tests / callers keep working.
        """
        from .agent_internals import route_message
        return await route_message(self, user_message, user_id, _depth)

    # -- Sub-method: Build context ------------------------------------------------

    async def _build_context(
        self, user_message: str, user_id: str, mem: Memory, history: list, request_model: str
    ) -> tuple:
        """Delegate to ``agent_internals.context_builder.build_message_context``.

        See :mod:`myclaw.agent_internals.context_builder` for the implementation.
        """
        from .agent_internals import build_message_context
        return await build_message_context(
            self, user_message, user_id, mem, history, request_model
        )

    # -- Sub-method: Execute tools ------------------------------------------------

    async def _execute_tools(
        self,
        tool_calls: list,
        messages: list,
        user_message: str,
        user_id: str,
        mem: Memory,
        _depth: int,
        had_kb_results: bool,
    ) -> str:
        """Delegate to ``agent_internals.tool_executor.execute_tools``.

        See :mod:`myclaw.agent_internals.tool_executor` for the implementation.
        Kept as a wrapper so the existing call site in ``think()`` (and any
        external callers) need no change.
        """
        from .agent_internals import execute_tools
        return await execute_tools(
            self, tool_calls, messages, user_message, user_id, mem, _depth, had_kb_results
        )

    # ── _execute_tools body moved to agent_internals/tool_executor.py ──

    # -- Sub-method: Handle summarization & cleanup -------------------------------

    async def _handle_summarization(
        self,
        user_message: str,
        response: str,
        user_id: str,
        mem: Memory,
        _full_history_for_bg: Optional[list],
    ) -> None:
        """Delegate to ``agent_internals.ResponseHandler``.

        Sprint 9 split this 30-line method into a real class with explicit
        dependencies. The wrapper stays so existing call sites in
        ``think()`` / ``stream_think()`` need no change.
        """
        from .agent_internals import ResponseHandler
        await ResponseHandler(self).handle(
            user_message, response, user_id, mem, _full_history_for_bg
        )

    # -- Structured-output helper (Sprint 4 integration) -------------------------

    async def complete_structured(
        self,
        messages: list,
        schema: Any,
        model: Optional[str] = None,
        max_repair_attempts: int = 1,
    ) -> Any:
        """Get a schema-validated structured response from the LLM.

        Wraps :func:`myclaw.structured_output.repair_json` around the same
        provider call ``think()`` uses. When the model returns invalid
        JSON, a focused repair prompt with the schema and validation
        errors is sent back as a follow-up call. Up to ``max_repair_attempts``
        rounds are attempted before giving up.

        Args:
            messages: OpenAI-style ``[{"role": ..., "content": ...}, ...]`` list.
            schema: Either a Pydantic v2 ``BaseModel`` subclass (preferred —
                richer type coercion and error paths) or a JSON-schema dict.
            model: Optional override; otherwise uses the agent's configured model.
            max_repair_attempts: Repair rounds. Each costs one LLM call.

        Returns:
            A :class:`~myclaw.structured_output.ValidationResult`. Check
            ``result.ok``; on success ``result.data`` is the parsed object
            (a Pydantic instance when a model was passed in).
        """
        from .structured_output import repair_json

        target_model = model or self.model

        async def _llm_call(msgs: list) -> str:
            response, _tool_calls = await self._provider_chat(msgs, target_model)
            return response or ""

        # Initial completion.
        first_response, _ = await self._provider_chat(messages, target_model)
        return await repair_json(
            text=first_response or "",
            schema=schema,
            llm_call=_llm_call,
            max_attempts=max_repair_attempts,
        )

    # -- Main think() orchestrator ------------------------------------------------

    async def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.

        Orchestrates the pipeline via sub-methods:
            1. _route_message()    -- routing, timer, guardrails
            2. _build_context()    -- knowledge search, system prompt
            3. LLM call            -- primary reasoning
            4. _execute_tools()    -- tool execution (if any)
            5. _handle_summarization() -- background cleanup

        Wrapped in an ``agent.think`` span so distributed traces can show
        the full request fan-out into provider/tool/KB child spans. The
        span is a no-op when OpenTelemetry is not installed/enabled.
        """
        from .observability import span as _span
        with _span("agent.think", agent=self.name, user=user_id, depth=_depth):
            return await self._think_impl(user_message, user_id, _depth)

    async def _think_impl(
        self, user_message: str, user_id: str = "default", _depth: int = 0
    ) -> str:
        # 1. Route message
        route_result = await self._route_message(user_message, user_id, _depth)
        if route_result is None:
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."
        request_model, mem, history, _full_history_for_bg = route_result

        # 2. Build context
        context_result = await self._build_context(
            user_message, user_id, mem, history, request_model
        )
        if context_result is None:
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."
        messages, had_kb_results, kb_gap_hint = context_result

        # 3. LLM call
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "llm_call", 3, 5)

        try:
            import httpx

            response, tool_calls = await self._provider_chat(messages, request_model)
        except httpx.TimeoutException as e:
            logger.error(f"LLM provider timeout: {e}")
            error_msg = "Sorry, the LLM service timed out. Please try again."
            if self._current_task_id:
                await self._task_timer.complete_task(
                    self._current_task_id, success=False, error_message=error_msg
                )
                self._current_task_id = None
            return error_msg
        except (httpx.ConnectError, ConnectionError) as e:
            logger.error(f"LLM provider connection error: {e}")
            error_msg = "Sorry, I cannot connect to the LLM service. Please check your connection."
            if self._current_task_id:
                await self._task_timer.complete_task(
                    self._current_task_id, success=False, error_message=error_msg
                )
                self._current_task_id = None
            return error_msg
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM provider HTTP error: {e}")
            error_msg = f"Sorry, the LLM service returned an error: {e.response.status_code}"
            if self._current_task_id:
                await self._task_timer.complete_task(
                    self._current_task_id, success=False, error_message=error_msg
                )
                self._current_task_id = None
            return error_msg
        except Exception as e:
            logger.exception(f"Unexpected LLM provider error: {e}")
            error_msg = f"Sorry, an unexpected error occurred: {e}"
            if self._current_task_id:
                await self._task_timer.complete_task(
                    self._current_task_id, success=False, error_message=error_msg
                )
                self._current_task_id = None
            return error_msg

        # Trigger post_llm_call hooks
        hook_results = trigger_hook("post_llm_call", response, tool_calls)
        for result in hook_results:
            if result and isinstance(result, tuple) and len(result) == 2:
                response, tool_calls = result

        # 4. Execute tools (if any) or handle direct response
        if tool_calls:
            final_response = await self._execute_tools(
                tool_calls, messages, user_message, user_id, mem, _depth, had_kb_results
            )
            if final_response.startswith("Tool executed but error"):
                return final_response
            # 5. Handle summarization & cleanup
            await self._handle_summarization(
                user_message, final_response, user_id, mem, _full_history_for_bg
            )
            return final_response

        # No tool calls: validate response is non-empty
        if self._is_empty_response(response):
            response = await self._recover_empty_response(
                messages, user_message, user_id, had_kb_results
            )

        await mem.add("assistant", response)

        # 5. Handle summarization & cleanup
        await self._handle_summarization(user_message, response, user_id, mem, _full_history_for_bg)
        return response

    async def stream_think(
        self, user_message: str, user_id: str = "default", _depth: int = 0
    ) -> AsyncIterator[str]:
        """Process a user message and yield response chunks in real-time.

        Streaming tool call flow:
            1. Stream initial LLM reasoning
            2. If tool calls detected → yield markers, execute tools, stream follow-up
            3. If no tool calls → save response and finish
        """
        global _LAST_ACTIVE_TIME
        _LAST_ACTIVE_TIME = time.time()

        mem = await self._get_memory(user_id)
        await mem.add("user", user_message)

        # Trigger on_session_start hook
        trigger_hook("on_session_start", user_id, self.name)

        history = await mem.get_history()

        # Feature: Context Summarization moved off hot path
        threshold = getattr(self.config.agents, "summarization_threshold", 10)
        _should_summarize_after_stream = len(history) > threshold
        _full_history_for_bg_stream = history.copy() if _should_summarize_after_stream else None

        # Search knowledge base for relevant context
        knowledge_context = await self._search_knowledge_context(user_message, user_id)
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

        # Step 1: Stream initial LLM response
        stream_result = await self._provider_chat(messages, self.model, stream=True)

        # Providers now return (iterator, tool_calls_collector) for streaming
        if isinstance(stream_result, tuple) and len(stream_result) == 2:
            stream_iterator, tool_calls_collector = stream_result
        else:
            stream_iterator = stream_result
            tool_calls_collector = []

        response_parts = []

        try:
            async for chunk in stream_iterator:
                if chunk:
                    response_parts.append(chunk)
                    yield chunk
        except Exception as e:
            logger.error(f"Error in initial streaming: {e}")
            yield f"\n\n[Error streaming response: {e}]"
            trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
            return

        full_response = "".join(response_parts)

        # Empty-response recovery for streaming
        if self._is_empty_response(full_response):
            logger.warning("Streaming produced empty response — emitting fallback.")
            fallback = "I'm sorry — I wasn't able to generate a response."
            if not had_kb_results:
                fallback += (
                    " My knowledge base has no entries on this topic. "
                    'You can add information with: write_to_knowledge(title="<topic>", content="<details>")'
                )
            yield fallback
            full_response = fallback

        # Trigger post_llm_call hooks for initial response
        trigger_hook("post_llm_call", full_response, None)

        # Step 2: Check for tool calls collected during streaming
        tool_calls = tool_calls_collector if tool_calls_collector else None

        if tool_calls:
            # ── Tool calls detected — execute and stream follow-up ──────────────
            logger.info(
                f"Streaming tool calls detected: {[tc['function']['name'] for tc in tool_calls]}"
            )

            # Yield tool call markers for frontend visibility
            yield "__TOOL_CALLS_START__"
            for tc in tool_calls:
                yield json.dumps(
                    {
                        "tool": tc["function"]["name"],
                        "status": "running",
                        "args": tc["function"].get("arguments", {}),
                    }
                )
            yield "__TOOL_CALLS_END__"

            # Execute tools using existing logic (non-streaming follow-up for now)
            try:
                final_response = await self._execute_tools(
                    tool_calls, messages, user_message, user_id, mem, _depth, had_kb_results
                )

                # Stream the final response in chunks for UX consistency
                yield "__STREAM_START__"
                # Split final response into word chunks to simulate streaming
                words = final_response.split(" ")
                for i, word in enumerate(words):
                    prefix = " " if i > 0 else ""
                    yield prefix + word
                yield "__STREAM_END__"

                # Background cleanup after tool-using response
                await self._handle_summarization(
                    user_message, final_response, user_id, mem, _full_history_for_bg_stream
                )

            except Exception as e:
                logger.error(f"Tool execution error during streaming: {e}")
                yield f"\n\n[Error executing tools: {e}]"
                yield "__STREAM_END__"

            trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
            return

        # ── No tool calls — finalize and cleanup ──────────────────────────────
        await mem.add("assistant", full_response)

        # Background KB auto-extraction (fire-and-forget)
        if self._kb_auto_extract and self._should_extract_knowledge(user_message, full_response):
            _kb_task = asyncio.create_task(
                self._extract_and_save_knowledge(user_message, full_response, user_id)
            )
            self._track_preload(_kb_task)

        # Background context summarization (fire-and-forget, off hot path)
        if _full_history_for_bg_stream:
            _summarize_task = asyncio.create_task(
                self._background_summarize_context(_full_history_for_bg_stream, user_id, mem)
            )
            self._track_preload(_summarize_task)

        # Trigger on_session_end hook
        trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))

        yield "[TOOL_CALLS_NONE]"  # Signal no tools were invoked


# ── Public API surface ───────────────────────────────────────────────
# Listing __all__ explicitly so `from this_module import *` doesn't leak
# internal helpers (e.g. _profile_cache, _LAST_ACTIVE_TIME). Names that
# aren't here are still importable by direct attribute access — they
# just don't participate in star imports.
__all__ = ['Agent', 'KnowledgeSearchResult', 'KnowledgeGapCache', 'GAP_FILE', 'SYSTEM_PROMPT', 'get_last_active_time', 'shutdown_kb_search_executor']
