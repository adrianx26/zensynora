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
import itertools
from dataclasses import dataclass, field
from datetime import datetime
import time

from .memory import Memory
from .provider import get_provider, SUPPORTED_PROVIDERS
from .exceptions import ProviderUnavailableError
from .tools import TOOLS, trigger_hook, _HOOKS, get_parallel_executor, is_tool_independent
from .knowledge import search_notes, build_context, write_note
from .knowledge.extract import (  # noqa: E402  (2026-10-23)
    FORMAT as KB_FORMAT, existing_titles as kb_existing_titles, known_notes_hint as kb_known_hint,
    parse_json_array as kb_parse_json_array, save_items as kb_save_items,
)
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

# 2026-10-18: wait this long before the one retry of a rate-limited model
# (only when no rotation list is set; with rotation the next model answers).
RATE_LIMIT_RETRY_SECONDS = 5

# v0.7.1: shared counter for the "round_robin" rotation strategy.
_ROUND_ROBIN = itertools.count()
# v0.7.3: (provider, model) -> time until which rotation skips it (recent hard failure).
# Shared with utility calls (provider.complete_with_fallback).
from .llm_errors import MODEL_COOLDOWN as _MODEL_COOLDOWN  # noqa: E402

# v0.7.1: conversation-memory scope (set by the chat engine per channel/agent).
import contextvars  # noqa: E402
MEMORY_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar("zensynora_memory_scope", default="")

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
    "You are ZenSynora, a personal AI agent with access to a knowledge base, TOOLBOX, and Agent Swarms. "
    "Use the provided function-calling tools. If you cannot call functions natively, call a tool "
    "by responding ONLY with JSON, nothing else: "
    '{"tool": "<name>", "args": {<key>: <value>}} — you then get the result and answer in normal text. '
    "Core tools: shell(cmd), read_file(path), write_file(path, content), "
    "browse(url, max_length), download_file(url, path), delegate(agent_name, task). "
    "Tool management: list_tools(), register_tool(name, code, documentation), "
    "list_toolbox(), get_tool_documentation(name). "
    "Scheduling: schedule(task, delay, every, user_id), edit_schedule(job_id, new_task, delay, every), "
    "split_schedule(job_id, sub_tasks_json), suspend_schedule(job_id), resume_schedule(job_id), "
    "cancel_schedule(job_id), list_schedules(). "
    "Knowledge: write_to_knowledge(title, content, tags, observations, relations), search_knowledge(query), "
    "read_knowledge(permalink), get_knowledge_context(permalink, depth), list_knowledge(), "
    "get_related_knowledge(permalink), sync_knowledge_base(), list_knowledge_tags(), "
    # 2026-10-24 (KB phase 2): quality tools
    "knowledge_conflicts(permalink), resolve_knowledge_conflict(conflict_id, keep), "
    "knowledge_categories(), find_duplicate_notes(). "
    # 2026-10-23 (KB phase 0): how notes link to each other.
    "A line `- relation [[Other note]]` in a note's text links it to another note. "
    "Agent Swarms: swarm_create(name, strategy, workers, coordinator, aggregation), "
    "swarm_assign(swarm_id, task), swarm_status(swarm_id), swarm_result(swarm_id), "
    "swarm_terminate(swarm_id), swarm_list(status), swarm_stats(). "
    "IMPORTANT: When creating tools with register_tool(), first use list_toolbox() to check if a similar tool exists. "
    "You can reference knowledge with memory://permalink. "
    # 2026-10-23 (KB phase 0): "credentials" removed — the knowledge-keeper
    # skill (and common sense) says secrets never go into the knowledge base.
    "KNOWLEDGE GROWTH MANDATE: After EVERY interaction where the user shares factual information, "
    "preferences, configurations, server details, hostnames, ports, file paths, "
    "important decisions, or any data worth remembering — you MUST proactively call "
    "write_to_knowledge() to persist it permanently. Do NOT wait to be asked. "
    "Every meaningful exchange must grow the knowledge base. "
    "Never store passwords, API keys, tokens or other secrets in the knowledge base. "
    "For all other responses, reply in plain text."
)


# 2026-10-23 (KB phase 0): size of the automatic "## Relevant Knowledge" block.
KB_CONTEXT_BUDGET = 2000       # characters for the whole block
KB_CONTEXT_NOTE_TEXT = 500     # characters of text per note
KB_CONTEXT_FACTS = 3           # facts per note
# One session-summary note per day; its text keeps the newest characters.
SESSION_SUMMARY_MAX_CHARS = 20000
_SESSION_SUMMARY_LOCK = threading.Lock()


class Agent:
    """Personal AI agent with per-user memory, native tool calling, multi-agent delegation."""

    @property
    def _kb_enabled(self) -> bool:
        """Is the knowledge base switched on (``config.knowledge.enabled``)?

        2026-10-23 (KB phase 0): the setting was ignored. When it is off, the
        automatic parts (context search, extraction, tool-result saving,
        session summaries, gap hints) stay off; the explicit tools still work.
        Unreadable config → True (the default).
        """
        try:
            value = getattr(self.config.knowledge, "enabled", True)
        except Exception:
            return True
        return True if value is None else bool(value)

    @property
    def _kb_auto_extract(self) -> bool:
        """Read-once helper: is automatic KB extraction enabled in config?"""
        if not self._kb_enabled:  # 2026-10-23 (KB phase 0)
            return False
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
        key: str = None,
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
        # v0.7.5: which API key of the provider (None = its default key). An
        # agent without its own provider also takes the default agent's key.
        self._key: Optional[str] = self._resolve_key(config, key, inherit=provider_name is None)
        self.last_key_used: Optional[str] = None

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
        # (provider, model) that produced the last answer — set by rotation (v0.7.1).
        self.last_model_used: Optional[tuple] = None
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
        if update.message_type == "fatal":
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
        # 2026-10-23 (KB phase 0): remember the newest gap — the hint used an
        # arbitrary (often old) entry of the set.
        self.__dict__.setdefault("_last_kb_gap", {})[user_id] = topic[:120]
        logger.debug(f"KB gap recorded for user '{user_id}': {topic[:60]}")
        return True

    def _latest_kb_gap(self, user_id: str) -> str:
        """The gap recorded last for this user ("" when none)."""
        last = self.__dict__.get("_last_kb_gap", {}).get(user_id)
        if last:
            return last
        gaps = self._kb_gaps.get(user_id)
        return next(iter(gaps)) if gaps else ""

    def _kb_gap_hint(self, user_id: str, had_kb_results: bool) -> str:
        """One-line hint for the prompt when the knowledge base had nothing.

        2026-10-23 (KB phase 0): shared by think() and stream_think(); also
        writes the gap to GAP_FILE for the gap researcher (once per topic
        and 5 minutes). That write used to be skipped almost always: it was
        de-duplicated with the same cache _record_kb_gap had just filled.
        Off when knowledge.enabled is False.
        """
        if had_kb_results or not getattr(self, "_kb_enabled", True):
            return ""
        last_gap = self._latest_kb_gap(user_id)
        if not last_gap:
            return ""
        self._log_kb_gap(user_id, last_gap)
        return (
            f"\n\n[Note: The knowledge base has no entries related to '{last_gap[:60]}'. "
            "Consider using write_to_knowledge() to store useful information for future queries.]"
        )

    def _log_kb_gap(self, user_id: str, topic: str) -> bool:
        """Structured log event + one GAP_FILE line for a gap (de-duplicated)."""
        cache = self.__dict__.get("_gap_log_cache")
        if cache is None:
            cache = KnowledgeGapCache(timeout_seconds=300.0)
            self.__dict__["_gap_log_cache"] = cache
        if cache.is_duplicate(topic, user_id):
            return False
        gap_data = {
            "event": "knowledge_gap_detected",
            "query": topic,
            "description": "No knowledge base entries found for query",
            "user_id": user_id,
            "session_context": (
                "System will preserve context to avoid redundant empty "
                "searches in this session"
            ),
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": "Use write_to_knowledge() to create a new entry for future queries",
        }
        kb_gap_logger.info(gap_data)
        try:
            GAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(GAP_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(gap_data) + "\n")
        except Exception as e:
            logger.warning(f"Failed to record gap to file: {e}")
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
        if not self._kb_enabled:  # 2026-10-23 (KB phase 0): knowledge.enabled = false
            return
        try:
            extraction_system = (
                "You are a knowledge extraction assistant. "
                "Analyse the conversation exchange below and extract ONLY concrete, "
                "factual information worth storing permanently "
                "(IP addresses, hostnames, credentials placeholders, configurations, "
                "file paths, user preferences, decisions, important facts). "
                + KB_FORMAT +
                "Use specific, searchable titles. "
                "Return ONLY the JSON array — no explanation, no markdown fences. "
                "Return [] if nothing is concretely worth saving. "
                # 2026-10-23: status snapshots were stored and later treated as current.
                "Do NOT save the current state of this computer or of ZenSynora (health or error "
                "reports, crash/restart counts, failing checks, recovery progress, file listings, "
                "timeouts, rate limits) — it is out of date minutes later."
            )
            exchange = f"User: {user_message[:800]}\n\nAssistant: {response[:1200]}"
            known = await asyncio.to_thread(kb_existing_titles, user_message, user_id)
            messages = [
                {"role": "system", "content": extraction_system},
                {"role": "user", "content": f"{exchange}\n\n{kb_known_hint(known)}".strip()},
            ]

            raw, _ = await self._provider_chat(messages, self.model)
            facts = kb_parse_json_array(raw)
            if not facts:
                return
            # max 5 notes per turn, each with tags, facts and links (2026-10-23)
            # 2026-10-24 (KB phase 2): every fact records where it came from
            from .knowledge.quality import chat_source
            saved = await asyncio.to_thread(kb_save_items, facts, user_id, ["auto-extracted"], 5, "[KB-AUTO]",
                                            chat_source())

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

        # v0.7.5: the agent's skills (names + descriptions; use_skill loads one)
        try:
            from .agent_skills import prompt_section, skills_for

            section = await asyncio.to_thread(prompt_section, skills_for(self.config, self.name))
            if section:
                prompt = f"{prompt}\n\n{section}"
            from .agent_team import team_section
            from .config import load_config as _load_config

            team = team_section(_load_config(), self.name)  # v0.7.5: who else can do the work
            if team:
                prompt = f"{prompt}\n\n{team}"
        except Exception as exc:  # a broken skill file must not stop the agent
            logger.warning("Could not load skills for agent %s: %s", self.name, exc)

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
        # v0.7.1: chat channels give every (channel, agent) its own history
        # while the knowledge base stays the user's (it is keyed by user_id).
        scope = MEMORY_SCOPE.get()
        key = f"{user_id}__{scope}" if scope else user_id
        if key not in self._memories:
            mem = Memory(user_id=key)
            await mem.initialize()
            self._memories[key] = mem
        return self._memories[key]

    def _resolve_key(self, config, key: Optional[str], inherit: bool) -> Optional[str]:
        """Key id to use, or None for the default key; a missing key falls back with a warning."""
        from .provider_keys import has_key, normalize_key_id

        if key is None and inherit:
            try:
                key = getattr(config.agents.defaults, "key", "") or ""
            except AttributeError:
                key = ""
        kid = normalize_key_id(key if isinstance(key, str) else "")
        if kid is None:
            return None
        try:
            ok = has_key(config, self._provider_name, kid)
        except Exception:  # unusual config objects (tests, mocks)
            ok = False
        if not ok:
            logger.warning("Agent %s uses key '%s' of %s, which no longer exists — using the default key.",
                           self.name, kid, self._provider_name)
            return None
        return kid

    def _get_provider_for(self, provider_name: str, key: Optional[str] = None):
        """get_provider for one key (the plain two-argument call for the default key)."""
        if key:
            return get_provider(self._config, provider_name, key_id=key)
        return get_provider(self._config, provider_name)

    @property
    def provider(self):
        """Lazy provider initialization - initializes on first access.

        This improves startup performance by deferring provider initialization
        until the provider is actually needed (e.g., on first think() call).
        """
        if self._provider is None:
            try:
                self._provider = self._get_provider_for(self._provider_name, self._key)
            except Exception as e:
                # Do NOT silently switch to Ollama: the user picked this
                # provider on purpose, and quietly sending their chat to
                # localhost:11434 produced confusing "404 /api/chat" errors
                # (e.g. llama.cpp selected but the `openai` SDK missing).
                logger.error("Could not initialise provider '%s': %s", self._provider_name, e)
                raise ProviderUnavailableError(
                    f"The '{self._provider_name}' provider could not be started: {e}"
                ) from e
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
        from .context_budget import CONTEXT_LENGTH, agent_context_length
        from .config import load_config as _load_cfg

        # The agent's context length (Ollama sends it as num_ctx).
        token = CONTEXT_LENGTH.set(agent_context_length(_load_cfg(), self.name))
        try:
            from .observability import span
            from .resilience import CircuitBreakerError

            # v0.7.1: default-LLM rotation / fallback list (Settings → Model).
            candidates = self._rotation_candidates()
            if not candidates and self._key_fallback_on():
                # v0.7.5: no model rotation, but other keys of this provider may answer.
                candidates = [self._own_candidate(model)]
            if candidates:
                return await self._rotating_chat(candidates, messages, stream)

            async def _primary():
                from .llm_errors import is_rate_limit

                with span("provider.chat", provider=self._provider_name, model=model):
                    try:
                        return await self.provider.chat(messages, model, stream=stream)
                    except Exception as e:
                        # 2026-10-18: free models are often "temporarily rate-limited
                        # upstream"; without rotation the turn (and delegations)
                        # failed at once. Wait a moment and try once more.
                        if not is_rate_limit(e):
                            raise
                        logger.warning("%s/%s is rate-limited; retrying once in %ss",
                                       self._provider_name, model, RATE_LIMIT_RETRY_SECONDS)
                        await asyncio.sleep(RATE_LIMIT_RETRY_SECONDS)
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
                self._fallback_wrapper = FallbackChatWrapper(
                    self.provider, self.config, primary_name=self._provider_name
                )

            with span("provider.fallback_chat", provider=self._provider_name, model=model):
                return await self._fallback_wrapper.chat(messages, model, stream=stream)
        finally:
            CONTEXT_LENGTH.reset(token)

    # ── Default-LLM rotation (v0.7.1) ────────────────────────────────────────
    def _follows_default_model(self) -> bool:
        """True for the default agent and named agents that use the default model."""
        if self.name == "default":
            return True
        try:
            d = self.config.agents.defaults
        except AttributeError:
            return False
        return self._provider_name == d.provider and self.model == d.model

    def _own_candidate(self, model: Optional[str] = None) -> tuple:
        """(provider, model) or, with a named key, (provider, model, key)."""
        base = (self._provider_name, model or self.model)
        return base + (self._key,) if self._key else base

    def _key_fallback_on(self) -> bool:
        """True when the agent's provider tries its other keys on rate limits (rotate_keys)."""
        try:
            from .provider_keys import rotate_keys_enabled

            return rotate_keys_enabled(self.config, self._provider_name)
        except Exception:
            return False

    def _rotation_candidates(self) -> List[tuple]:
        """Ordered (provider, model[, key]) tuples to try, or [] when rotation is off.

        The key part is only present for a named key (v0.7.5), so setups with
        default keys keep plain (provider, model) pairs.
        """
        from .provider_keys import normalize_key_id

        try:
            rot = self.config.agents.rotation
        except AttributeError:
            return []
        if not (rot.enabled and rot.entries) or not self._follows_default_model():
            return []
        pairs = [self._own_candidate()]
        for e in rot.entries:
            kid = normalize_key_id(getattr(e, "key", "") or "")
            pair = (e.provider, e.model) + ((kid,) if kid else ())
            if e.provider and e.model and pair not in pairs:
                pairs.append(pair)
        if rot.strategy == "round_robin" and len(pairs) > 1:
            start = next(_ROUND_ROBIN) % len(pairs)
            pairs = pairs[start:] + pairs[:start]
        return pairs

    async def _rotating_chat(self, candidates: List[tuple], messages, stream: bool):
        """Try each (provider, model) until one answers; remember which one did.

        v0.7.3: a model that just failed in a way that will not fix itself soon
        (refused for this app, unknown, bad key) or is rate-limited is skipped
        for a while (see llm_errors.cooldown_for), so every request does not
        first wait for the same failure again.
        """
        from .llm_errors import cooldown_for, cooldown_key, describe_llm_error, is_key_error
        from .provider_keys import key_chain

        # v0.7.5: each candidate may carry a key id; with the provider's
        # rotate_keys on, its other keys are tried for the same model first
        # (rate limit / quota / refused key). Cooldowns are per (provider, model, key).
        def _chain(prov_name, key):
            try:
                return key_chain(self.config, prov_name, key)
            except Exception:
                return [key]

        now = time.time()
        plan = [(c[0], c[1], _chain(c[0], c[2] if len(c) > 2 else None)) for c in candidates]
        usable = []
        for p, m, keys in plan:
            ready = [k for k in keys if _MODEL_COOLDOWN.get(cooldown_key(p, m, k), 0) <= now]
            if ready:
                usable.append((p, m, ready))
        if usable and len(usable) < len(plan):
            skipped = {(p, m) for p, m, _ in plan} - {(p, m) for p, m, _ in usable}
            logger.info("Rotation: skipping %s (failed recently)", ", ".join(f"{p}/{m}" for p, m in skipped))
        errors: List[str] = []
        for prov_name, model, keys in (usable or plan):
            for i, key in enumerate(keys):
                try:
                    return await self._chat_once(prov_name, model, key, messages, stream, errors)
                except Exception as e:  # rate limit, server down, missing model, bad key…
                    wait = cooldown_for(e)
                    if wait:
                        _MODEL_COOLDOWN[cooldown_key(prov_name, model, key)] = time.time() + wait
                    where = f"{prov_name}/{model}" + (f" (key {key})" if key else "")
                    errors.append(describe_llm_error(e, where))
                    if i + 1 < len(keys) and is_key_error(e):
                        logger.info("Rotation: %s failed (%s); trying %s with another key",
                                    where, e, model)
                        continue
                    logger.info("Rotation: %s failed (%s); trying the next model", where, e)
                    break
        raise ProviderUnavailableError("All models in the rotation failed — " + " | ".join(errors))

    async def _chat_once(self, prov_name: str, model: str, key: Optional[str], messages,
                         stream: bool, errors: List[str]):
        """One rotation attempt; remembers which provider/model/key answered."""
        prov = self._get_provider_for(prov_name, key)
        if not stream:
            result = await prov.chat(messages, model, stream=False)
            self.last_model_used = (prov_name, model)
            self.last_key_used = key
            return result
        gen, collector = await prov.chat(messages, model, stream=True)
        it = gen.__aiter__()
        try:
            first = await it.__anext__()
        except StopAsyncIteration:
            first = None  # empty text is fine (e.g. a tool-call-only reply)

        async def _chained(first=first, it=it):
            if first is not None:
                yield first
            async for chunk in it:
                yield chunk

        self.last_model_used = (prov_name, model)
        self.last_key_used = key
        if errors:
            logger.warning("Rotation: answered by %s/%s after: %s", prov_name, model, "; ".join(errors))
        return _chained(), collector

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
        if not getattr(self, "_kb_enabled", True):  # 2026-10-23 (KB phase 0)
            return
        try:
            # Exclude the most recent 5 messages from summarization
            to_summarize = history[:-5] if len(history) > 5 else history
            if not to_summarize:
                return

            # Fixed 2026-10-14 (v0.7.3): entries without text (tool steps) made
            # this crash ("'NoneType' object is not subscriptable"), and the
            # model was asked WITH the agent's tools, so it often answered with
            # a tool call — an empty "Session Summary" note was then saved.
            # Only user/assistant text is summarized, without tools, and an
            # empty summary is not stored.
            turns = [m for m in to_summarize
                     if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
            if not turns:
                return
            summary_parts = [
                "Summarize the following conversation context. Focus on key decisions, "
                "important facts, and user preferences. Preserve information that would be "
                "important for future context:\n"
            ]
            for m in turns:
                summary_parts.append(f"{m['role']}: {str(m.get('content') or '')[:200]}\n")
            summary_prompt = "".join(summary_parts)
            summary_msgs = [
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": summary_prompt},
            ]

            from .provider import complete_text

            summary_text = await complete_text(self.provider, summary_msgs, self.model)
            if not (summary_text or "").strip():
                logger.info("Background summarization: the model returned no summary; nothing stored.")
                return
            original_len = sum(len(str(m.get("content") or "")) for m in turns)
            compressed_len = len(summary_text)
            compression_ratio = (1 - compressed_len / original_len) * 100 if original_len > 0 else 0
            logger.debug(
                f"Background trajectory compressed: {original_len} -> {compressed_len} chars "
                f"({compression_ratio:.1f}% reduction)"
            )

            # Store summary in knowledge base for future retrieval
            await asyncio.to_thread(self._store_session_summary, summary_text, user_id)
        except Exception as e:
            logger.error(f"Background summarization error: {e}")

    @staticmethod
    def _store_session_summary(summary_text: str, user_id: str,
                               now: Optional[datetime] = None) -> str:
        """Append a summary to today's session-summary note; returns its permalink.

        2026-10-23 (KB phase 0): every turn past the summarization threshold
        wrote a new ``session-summary-YYYYMMDD-HHMMSS`` note — hundreds of
        notes. Now there is ONE note per day and knowledge base
        (``session-summary-YYYYMMDD``, UTC), each summary a ``### HH:MM UTC``
        section; the text keeps the newest SESSION_SUMMARY_MAX_CHARS characters.
        """
        from .knowledge import read_note, write_note
        from .knowledge.parser import extract_body

        now = now or datetime.utcnow()
        name = f"session-summary-{now.strftime('%Y%m%d')}"
        section = f"### {now.strftime('%H:%M')} UTC\n\n{summary_text.strip()}"
        tags = ["session_summary", "auto-generated"]
        with _SESSION_SUMMARY_LOCK:  # two summaries at once must not lose one
            old = None
            try:
                old = read_note(name, user_id)
            except Exception as e:
                logger.debug("Session summary note %s unreadable: %s", name, e)
            observations, relations = [], []
            text = section
            if old is not None:
                old_body = extract_body(old.content if isinstance(old.content, str) else "")
                if old_body:
                    text = f"{old_body}\n\n{section}"
                tags = list(dict.fromkeys(list(old.tags or []) + tags))
                observations, relations = list(old.observations), list(old.relations)
            if len(text) > SESSION_SUMMARY_MAX_CHARS:
                text = text[-SESSION_SUMMARY_MAX_CHARS:]
                cut = text.find("\n### ")
                text = text[cut + 1:] if cut != -1 else text
            return write_note(
                name=name,
                title=f"Session summaries {now.strftime('%Y-%m-%d')}",
                content=text,
                user_id=user_id,
                tags=tags,
                observations=observations,
                relations=relations,
            )

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

        # 2026-10-23 (KB phase 0): knowledge.enabled = false → no automatic search.
        if not getattr(self, "_kb_enabled", True):
            result = KnowledgeSearchResult(
                context="", has_results=False, suggested_topics=[], gap_logged=False,
                metadata={**metadata, "disabled": True},
            )
            return result if return_structured else ""

        try:
            # Resolve explicit memory:// references
            memory_refs = re.findall(r"memory://([\w\-]+)", message)

            # Clean the message for keyword extraction
            cleaned = re.sub(r"[^\w\s]", " ", message.lower())
            words = [w for w in cleaned.split() if len(w) > 3]

            notes = []
            # 2026-10-24 (KB phase 2): with the hub penalty a few more candidates
            # are fetched, so a very linked note can be ranked below a specific one.
            from .knowledge.quality import hub_threshold, rank_with_hub_penalty
            fetch = max_results + 3 if hub_threshold() is not None else max_results
            if words:
                # Strategy 1: search with full message text (FTS5 ranked)
                notes = search_notes(message, user_id, limit=fetch)

                if not notes:
                    # Strategy 2: bigram + single-keyword OR query
                    bigrams = [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
                    candidates = bigrams[:3] + words[:5]
                    query = " OR ".join(f'"{t}"' if " " in t else t for t in candidates)
                    # 2026-10-23 (KB phase 0): boolean=True so OR means OR
                    # (as free text every word had to match).
                    notes = search_notes(query, user_id, limit=fetch, boolean=True)
            if fetch != max_results and notes:
                notes = rank_with_hub_penalty(notes, user_id)[:max_results]

            # 2026-10-23 (KB phase 0): memory://permalink references were
            # only used when the search happened to find that note.
            if memory_refs:
                found = {getattr(n, "permalink", None) for n in notes}
                from .knowledge import read_note as _read_note

                for ref in dict.fromkeys(memory_refs):
                    if ref in found:
                        continue
                    try:
                        ref_note = _read_note(ref, user_id)
                    except Exception:
                        ref_note = None
                    if ref_note is not None:
                        notes = [ref_note] + list(notes)
                        found.add(ref)

            if not notes:
                # Record gap so the agent can suggest KB creation.
                # 2026-10-23 (KB phase 0): not for messages without searchable
                # words ("hi", "ok") — they are no knowledge gap; and an empty
                # "## Relevant Knowledge" block is no longer sent when only an
                # unknown memory:// reference was given.
                gap_logged = (self._record_kb_gap(user_id, message[:120])
                              if words and not memory_refs else False)
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

            # Build context from found notes.
            # 2026-10-23 (KB phase 0): each note with its (shortened) text and
            # up to 3 facts, within KB_CONTEXT_BUDGET characters for the block —
            # agents only saw titles and facts, and most notes are plain text.
            from .knowledge.graph import note_text, shorten_text

            context_lines = ["## Relevant Knowledge"]
            used = len(context_lines[0])
            budget = KB_CONTEXT_BUDGET

            def _add(line: str) -> bool:
                nonlocal used
                if used + len(line) + 1 > budget:
                    return False
                context_lines.append(line)
                used += len(line) + 1
                return True

            for i, note in enumerate(notes):
                header = f"\n**{note.title}** ({note.permalink}):"
                if not _add(header):
                    if i == 0:  # always show the best match
                        context_lines.append(header)
                        used += len(header) + 1
                    else:
                        break
                room = budget - used - 1
                text = shorten_text(note_text(note), min(KB_CONTEXT_NOTE_TEXT, room))
                if text and len(text) > 20:
                    _add(text)

                for obs in (note.observations or [])[:KB_CONTEXT_FACTS]:
                    if not _add(f"- [{obs.category}] {shorten_text(str(obs.content), 200)}"):
                        break

                if note.permalink in memory_refs:
                    room = budget - used - len("\nRelated context:") - 2
                    if room > 100:
                        full_context = build_context(note.permalink, user_id, depth=1)
                        _add("\nRelated context:")
                        _add(shorten_text(full_context, min(500, room), collapse=False))

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

    # ── Medic Diagnosis ───────────────────────────────────────────────────────

    async def _run_medic_diagnosis(self, error: Exception, user_id: str) -> tuple[str, str]:
        """
        Runs a series of diagnostic checks using the medic_agent when an
        unexpected error occurs.
        Returns a tuple of (user_facing_summary, detailed_log_message).
        """
        logger.info("An unexpected error occurred. Running medic diagnosis...")
        try:
            # Lazily import medic tools to avoid circular dependencies and overhead
            # myclaw.tools loads these lazily; import them from where they live.
            from .agents.medic_agent import check_memory_usage, check_system_health, get_health_report

            # 1. Check basic system health
            health_status = await asyncio.to_thread(check_system_health)

            # 2. Check memory usage
            memory_status = await asyncio.to_thread(check_memory_usage)

            # 3. Get a more detailed report
            health_report = await asyncio.to_thread(get_health_report)

            # 4. Formulate a diagnosis report
            detailed_log = (
                f"--- Medic Agent Diagnosis ---\n"
                f"Triggering Error: {type(error).__name__}: {error}\n"
                f"User ID: {user_id}\n\n"
                f"System Health Status:\n{health_status}\n\n"
                f"Memory Status:\n{memory_status}\n\n"
                f"Detailed Health Report:\n{health_report}\n"
                "--- End of Diagnosis ---"
            )

            user_summary = "\n\n[Medic Agent Note: A system health check was performed due to an unexpected error. The issue has been logged.]"

            logger.warning(f"Medic diagnosis complete. {detailed_log}")
            return user_summary, detailed_log

        except Exception as diag_error:
            logger.error(f"Medic diagnosis itself failed: {diag_error}")
            return "\n\n[Medic Agent Diagnosis failed to run.]", f"Medic diagnosis failed: {diag_error}"

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
    # 2026-10-23: "shell" and "write_file" removed — directory listings,
    # process lists and the agents' own (sometimes invented) status reports
    # were saved as permanent facts ("system crash loop", "25 files still to
    # recover" …) and later read back as if they were current.
    _KB_WORTHY_TOOLS: frozenset = frozenset(
        {
            "browse",
            "read_file",
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
        out = tool_output.strip()
        return (tool_name in Agent._KB_WORTHY_TOOLS and len(out) > 120
                and not out.startswith(("Error", "[Error")))

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
        if not self._kb_enabled:  # 2026-10-23 (KB phase 0): knowledge.enabled = false
            return
        try:
            extraction_system = (
                "You are a knowledge extraction assistant. "
                "Review the following tool execution result and extract ONLY concrete, "
                "factual information worth storing permanently "
                "(file contents, configuration values, command output facts, research results, "
                "server details, hostnames, paths, versions, credentials placeholders). "
                + KB_FORMAT +
                "Return ONLY the JSON array — no explanation, no markdown fences. "
                "Return [] if nothing is concretely worth saving. "
                # 2026-10-23: snapshots of the system's state go stale at once.
                "Do NOT save the current state of this computer or of ZenSynora: health or error "
                "reports, crash/restart counts, failing checks, recovery progress, directory or file "
                "listings, process lists, log excerpts, timeouts or rate limits — they are out of date "
                "minutes later and mislead future answers."
            )
            context = (
                f"Tool: {tool_name}\n"
                f"Arguments: {json.dumps(args, default=str)[:300]}\n"
                f"User task context: {user_message[:200]}\n\n"
                f"Tool output:\n{tool_output[:1500]}"
            )
            known = await asyncio.to_thread(kb_existing_titles, f"{user_message} {tool_output[:300]}", user_id)
            messages = [
                {"role": "system", "content": extraction_system},
                {"role": "user", "content": f"{context}\n\n{kb_known_hint(known)}".strip()},
            ]

            raw, _ = await self._provider_chat(messages, self.model)
            facts = kb_parse_json_array(raw)
            if not facts:
                return
            # max 3 notes per tool call, each with tags, facts and links (2026-10-23)
            # 2026-10-24 (KB phase 2): facts record the tool and its URL/path
            from .knowledge.quality import tool_source
            saved = await asyncio.to_thread(kb_save_items, facts, user_id,
                                            ["auto-extracted", f"tool-{tool_name}"], 3, "[KB-TOOL]",
                                            tool_source(tool_name, args))

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
        # v0.7.3: no empty assistant turn here — several providers reject
        # messages with empty content.
        recovery_messages = messages + [{"role": "user", "content": recovery_prompt}]

        last_error = None
        try:
            recovered, _ = await self._provider_chat(recovery_messages, self.model)
            if not self._is_empty_response(recovered):
                logger.info("Empty-response recovery succeeded.")
                return recovered
        except Exception as e:
            logger.error(f"Recovery LLM call failed: {e}")
            last_error = e
        if last_error is not None:
            # v0.7.3: say what really went wrong instead of "empty answer".
            from .llm_errors import describe_llm_error

            return f"[Error] {describe_llm_error(last_error)}"

        # Build a meaningful fallback.
        # Changed 2026-10-13 (v0.7.1): the old text blamed the knowledge base
        # ("no entries on this topic"), which is almost never the cause — an
        # empty answer comes from the model (overloaded free tier, a reasoning
        # model running out of tokens, a model without tool support…).
        used = self.last_model_used or (self._provider_name, self.model)
        fallback_parts = [
            f"I'm sorry — the model ({used[0]}/{used[1]}) returned an empty answer twice.",
            "Please try again. If it keeps happening, pick another model on the Models page,",
            "or turn on rotation there so another model answers automatically.",
        ]
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
        from .knowledge.scope import KB_USER, for_turn
        from .observability import span as _span

        kb_token = KB_USER.set(for_turn(user_id))  # v0.7.5: tools use this turn's knowledge base
        try:
            with _span("agent.think", agent=self.name, user=user_id, depth=_depth):
                return await self._think_impl(user_message, user_id, _depth)
        finally:
            KB_USER.reset(kb_token)

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
            if not tool_calls:
                # v0.7.2: a tool call written as text ({"tool": …, "args": …}).
                from .agent_internals.text_tools import parse_text_tool_calls

                tool_calls = parse_text_tool_calls(response)
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
            from .llm_errors import describe_llm_error, is_provider_error  # v0.7.3: say what went wrong

            if is_provider_error(e):
                # 2026-10-18: a rate limit / timeout / unknown model is the model
                # server's answer, not a crash — no traceback, no medic run.
                logger.warning("LLM error (%s/%s): %s", self._provider_name, request_model, describe_llm_error(e))
                user_summary = ""
            else:
                logger.exception(f"Unexpected error in think loop: {e}")
                user_summary, _ = await self._run_medic_diagnosis(e, user_id)

            error_msg = f"[Error] {describe_llm_error(e)}{user_summary}"

            if self._current_task_id:
                await self._task_timer.complete_task(
                    self._current_task_id, success=False, error_message=str(e)
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
            if final_response.startswith(("Tool executed but error", "[Error] The tool ran")):
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
        """Streaming answer; the tools it calls use this turn's knowledge base (v0.7.5)."""
        from .knowledge.scope import KB_USER, for_turn

        kb_token = KB_USER.set(for_turn(user_id))
        try:
            async for chunk in self._stream_think_impl(user_message, user_id, _depth):
                yield chunk
        finally:
            try:
                KB_USER.reset(kb_token)
            except ValueError:  # generator closed from another context
                pass

    async def _stream_think_impl(
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

        # KB gap hint for streaming mode (2026-10-23 (KB phase 0): shared with
        # think(); also logs the gap for the researcher, off when the KB is off)
        kb_gap_hint = self._kb_gap_hint(user_id, had_kb_results)

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
        # v0.7.2: an answer that starts like JSON may be a tool call written
        # as text — hold it back until the stream ends instead of showing it.
        from .agent_internals.text_tools import looks_like_text_tool_call, parse_text_tool_calls

        held: List[str] = []
        holding: Optional[bool] = None  # None = undecided, True = holding, False = passing through

        try:
            async for chunk in stream_iterator:
                if not chunk:
                    continue
                response_parts.append(chunk)
                if holding is False:
                    yield chunk
                    continue
                held.append(chunk)
                head = "".join(held)
                if not head.strip():
                    continue
                if looks_like_text_tool_call(head):
                    holding = True
                    continue
                holding = False
                held = []
                yield head
        except Exception as e:
            from .llm_errors import describe_llm_error, is_provider_error  # v0.7.3: readable reason

            if is_provider_error(e):
                logger.warning("LLM error while streaming (%s): %s", self._provider_name, describe_llm_error(e))
            else:
                logger.error(f"Error in initial streaming: {e}")

            yield f"\n\n[Error] {describe_llm_error(e)}"
            trigger_hook("on_session_end", user_id, self.name, len(await mem.get_history()))
            return

        full_response = "".join(response_parts)
        # The provider fills the collector while the stream is consumed, so it
        # is complete only now.
        tool_calls = tool_calls_collector if tool_calls_collector else None
        if not tool_calls:
            tool_calls = parse_text_tool_calls(full_response)
        if held and not tool_calls:
            yield "".join(held)  # it was ordinary text after all (e.g. JSON you asked for)

        # Empty-response recovery for streaming.
        # Fixed 2026-10-13 (v0.7.1): this ran before the tool-call check, so a
        # reply that consisted only of a tool call (no text — normal for
        # agents) was answered with "I wasn't able to generate a response. My
        # knowledge base has no entries on this topic…" even though the agent
        # was working. Now: tool calls → run them below; truly empty → ask the
        # model once more (like think() does), and only then explain.
        if not tool_calls and self._is_empty_response(full_response):
            logger.warning("Streaming produced an empty response — retrying once without streaming.")
            full_response = await self._recover_empty_response(
                messages, user_message, user_id, had_kb_results
            )
            yield full_response

        # Trigger post_llm_call hooks for initial response
        trigger_hook("post_llm_call", full_response, None)

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
        if _full_history_for_bg_stream and self._kb_enabled:
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
