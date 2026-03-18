"""
Storage layer for the Agent Swarm system.

Provides persistent storage for swarm state, tasks, messages, and results
using SQLite. Includes in-memory result caching with TTL for optimization 4.3.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Union

from .models import (
    SwarmInfo, SwarmTask, SwarmResult, SwarmMessage,
    TaskStatus, MessageType, SwarmStrategy, AggregationMethod,
    AgentResult, ActiveExecution
)

# Import SQLitePool for connection pooling (Optimization 4.2)
try:
    from myclaw.memory import SQLitePool
except ImportError:
    SQLitePool = None

logger = logging.getLogger(__name__)

# Database path
SWARM_DB_DIR = Path.home() / ".myclaw"
SWARM_DB_PATH = SWARM_DB_DIR / "swarm.db"

# Cache TTL in seconds (1 hour)
RESULT_CACHE_TTL = 3600


class ResultCache:
    """Thread-safe in-memory cache for swarm results with TTL.
    
    Optimization 4.3: Swarm result caching
    
    Caches swarm results keyed by swarm_id + input_hash to avoid
    re-computing results for identical inputs.
    """
    
    def __init__(self, ttl_seconds: int = RESULT_CACHE_TTL):
        """Initialize cache with specified TTL.
        
        Args:
            ttl_seconds: Time-to-live for cache entries in seconds.
                        Default is 3600 (1 hour).
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
    
    def _make_key(self, swarm_id: str, input_hash: str) -> str:
        """Generate cache key from swarm_id and input_hash."""
        return f"{swarm_id}:{input_hash}"
    
    def get(self, swarm_id: str, input_hash: str) -> Optional['SwarmResult']:
        """Get cached result if present and not expired.
        
        Args:
            swarm_id: The swarm identifier
            input_hash: Hash of the input data
            
        Returns:
            Cached SwarmResult if found and not expired, None otherwise.
        """
        key = self._make_key(swarm_id, input_hash)
        
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            
            # Check if expired
            if time.time() - entry['cached_at'] > self._ttl:
                # Remove expired entry
                del self._cache[key]
                return None
            
            return entry['result']
    
    def set(self, swarm_id: str, input_hash: str, result: 'SwarmResult'):
        """Store result in cache with current timestamp.
        
        Args:
            swarm_id: The swarm identifier
            input_hash: Hash of the input data
            result: The SwarmResult to cache
        """
        key = self._make_key(swarm_id, input_hash)
        
        with self._lock:
            self._cache[key] = {
                'result': result,
                'cached_at': time.time()
            }
    
    def invalidate(self, swarm_id: str, input_hash: Optional[str] = None):
        """Invalidate cache entries for a swarm.
        
        Args:
            swarm_id: The swarm identifier
            input_hash: Optional specific input hash to invalidate.
                       If None, invalidates all entries for the swarm.
        """
        with self._lock:
            if input_hash:
                key = self._make_key(swarm_id, input_hash)
                self._cache.pop(key, None)
            else:
                # Remove all entries for this swarm
                keys_to_remove = [
                    k for k in self._cache.keys()
                    if k.startswith(f"{swarm_id}:")
                ]
                for k in keys_to_remove:
                    del self._cache[k]
    
    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        """Remove all expired entries from cache.
        
        Returns:
            Number of entries removed.
        """
        now = time.time()
        removed = 0
        
        with self._lock:
            keys_to_remove = []
            for key, entry in self._cache.items():
                if now - entry['cached_at'] > self._ttl:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self._cache[key]
                removed += 1
        
        return removed
    
    def size(self) -> int:
        """Return current number of cached entries."""
        with self._lock:
            return len(self._cache)


class SwarmStorage:
    """SQLite storage for swarm data.
    
    Manages persistence of:
    - Swarm configurations and status
    - Individual tasks within swarms
    - Inter-agent messages
    - Final results
    
    Each user has isolated data via user_id field.
    
    Optimizations:
    - 4.2: Supports shared connection pool via SQLitePool parameter
    - 4.3: In-memory result caching with TTL (1 hour) for swarm results
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        pool: Optional[object] = None,
        enable_cache: bool = True
    ):
        """Initialize storage with optional custom path and connection pool.
        
        Args:
            db_path: Optional custom database path. Defaults to ~/.myclaw/swarm.db
            pool: Optional SQLitePool instance for shared connections.
                  If provided, uses pooled connections for better performance.
            enable_cache: Enable in-memory result caching (Optimization 4.3).
                         Default is True. Set to False to disable caching.
        """
        self.db_path = db_path or SWARM_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use provided pool or fall back to creating new connections
        self._pool = pool
        self._use_pool = pool is not None and SQLitePool is not None
        
        # Initialize result cache (Optimization 4.3)
        self._cache = ResultCache() if enable_cache else None
        self._enable_cache = enable_cache
        
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with row factory.
        
        Uses connection pool if available, otherwise creates new connection.
        """
        if self._use_pool:
            # Use pooled connection
            conn = self._pool.get_connection(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                self._pool.release_connection(self.db_path)
        else:
            # Fall back to creating new connection (legacy behavior)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Main swarms table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swarms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    coordinator_agent TEXT,
                    worker_agents TEXT NOT NULL,  -- JSON array
                    aggregation_method TEXT DEFAULT 'synthesis',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_id TEXT NOT NULL,
                    max_iterations INTEGER DEFAULT 1,
                    timeout_seconds INTEGER DEFAULT 300
                )
            """)
            
            # Tasks within swarms
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swarm_tasks (
                    id TEXT PRIMARY KEY,
                    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                    description TEXT NOT NULL,
                    assigned_agents TEXT,  -- JSON array
                    status TEXT DEFAULT 'pending',
                    input_data TEXT,  -- JSON
                    output_data TEXT,  -- JSON
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            
            # Inter-agent messages
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swarm_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT,  -- NULL = broadcast
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            
            # Results
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swarm_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    swarm_id TEXT NOT NULL UNIQUE REFERENCES swarms(id) ON DELETE CASCADE,
                    aggregation_method TEXT NOT NULL,
                    individual_results TEXT NOT NULL,  -- JSON
                    final_result TEXT NOT NULL,
                    confidence_score REAL DEFAULT 0.0,
                    execution_time_seconds REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    metadata TEXT  -- JSON
                )
            """)
            
            # Active executions (Optimization 4.4: Persistent active execution tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_executions (
                    execution_id TEXT PRIMARY KEY,
                    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                    task_description TEXT NOT NULL,
                    input_data TEXT,  -- JSON
                    status TEXT DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Create indexes for performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_swarms_user ON swarms(user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_swarms_status ON swarms(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_swarm ON swarm_tasks(swarm_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_swarm ON swarm_messages(swarm_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON swarm_messages(timestamp)
            """)
            
            conn.commit()
            logger.info("Swarm database initialized")
    
    # ── Swarm Operations ─────────────────────────────────────────────────────
    
    def create_swarm(
        self,
        name: str,
        strategy: SwarmStrategy,
        workers: List[str],
        coordinator: Optional[str] = None,
        aggregation_method: AggregationMethod = AggregationMethod.SYNTHESIS,
        user_id: str = "default",
        max_iterations: int = 1,
        timeout_seconds: int = 300
    ) -> str:
        """Create a new swarm and return its ID."""
        swarm_id = f"swarm_{uuid.uuid4().hex[:12]}"
        
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO swarms 
                (id, name, strategy, status, coordinator_agent, worker_agents,
                 aggregation_method, created_at, user_id, max_iterations, timeout_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    swarm_id,
                    name,
                    strategy.value,
                    TaskStatus.PENDING.value,
                    coordinator,
                    json.dumps(workers),
                    aggregation_method.value,
                    datetime.now().isoformat(),
                    user_id,
                    max_iterations,
                    timeout_seconds
                )
            )
            conn.commit()
        
        logger.info(f"Created swarm {swarm_id} ({name}) for user {user_id}")
        return swarm_id
    
    def get_swarm(self, swarm_id: str) -> Optional[SwarmInfo]:
        """Get swarm information by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM swarms WHERE id = ?",
                (swarm_id,)
            ).fetchone()
            
            if not row:
                return None
            
            return SwarmInfo(
                id=row["id"],
                name=row["name"],
                strategy=SwarmStrategy(row["strategy"]),
                status=TaskStatus(row["status"]),
                coordinator=row["coordinator_agent"],
                workers=json.loads(row["worker_agents"]),
                aggregation_method=AggregationMethod(row["aggregation_method"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                user_id=row["user_id"]
            )
    
    def update_swarm_status(self, swarm_id: str, status: TaskStatus):
        """Update swarm status."""
        completed_at = None
        if status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TERMINATED]:
            completed_at = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            if completed_at:
                conn.execute(
                    "UPDATE swarms SET status = ?, completed_at = ? WHERE id = ?",
                    (status.value, completed_at, swarm_id)
                )
            else:
                conn.execute(
                    "UPDATE swarms SET status = ? WHERE id = ?",
                    (status.value, swarm_id)
                )
            conn.commit()
        
        logger.debug(f"Updated swarm {swarm_id} status to {status.value}")
    
    def list_swarms(
        self,
        user_id: str = "default",
        status: Optional[TaskStatus] = None
    ) -> List[SwarmInfo]:
        """List swarms for a user, optionally filtered by status."""
        with self._get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM swarms WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                    (user_id, status.value)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM swarms WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,)
                ).fetchall()
            
            return [
                SwarmInfo(
                    id=row["id"],
                    name=row["name"],
                    strategy=SwarmStrategy(row["strategy"]),
                    status=TaskStatus(row["status"]),
                    coordinator=row["coordinator_agent"],
                    workers=json.loads(row["worker_agents"]),
                    aggregation_method=AggregationMethod(row["aggregation_method"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                    user_id=row["user_id"]
                )
                for row in rows
            ]
    
    def count_active_swarms(self, user_id: str = "default") -> int:
        """Count active (pending/running) swarms for a user."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as count FROM swarms 
                WHERE user_id = ? AND status IN ('pending', 'running')
                """,
                (user_id,)
            ).fetchone()
            return row["count"] if row else 0
    
    def delete_swarm(self, swarm_id: str) -> bool:
        """Delete a swarm and all its data."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM swarms WHERE id = ?", (swarm_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted swarm {swarm_id}")
            return deleted
    
    # ── Task Operations ──────────────────────────────────────────────────────
    
    def create_task(
        self,
        swarm_id: str,
        description: str,
        assigned_agents: Optional[List[str]] = None,
        input_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create a new task within a swarm."""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO swarm_tasks
                (id, swarm_id, description, assigned_agents, input_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    swarm_id,
                    description,
                    json.dumps(assigned_agents or []),
                    json.dumps(input_data or {}),
                    datetime.now().isoformat()
                )
            )
            conn.commit()
        
        return task_id
    
    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        output_data: Optional[Dict[str, Any]] = None
    ):
        """Update task status and optionally output data."""
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            if status == TaskStatus.RUNNING:
                conn.execute(
                    "UPDATE swarm_tasks SET status = ?, started_at = ? WHERE id = ?",
                    (status.value, now, task_id)
                )
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                conn.execute(
                    "UPDATE swarm_tasks SET status = ?, completed_at = ?, output_data = ? WHERE id = ?",
                    (status.value, now, json.dumps(output_data or {}), task_id)
                )
            else:
                conn.execute(
                    "UPDATE swarm_tasks SET status = ? WHERE id = ?",
                    (status.value, task_id)
                )
            conn.commit()
    
    def get_swarm_tasks(self, swarm_id: str) -> List[SwarmTask]:
        """Get all tasks for a swarm."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM swarm_tasks WHERE swarm_id = ? ORDER BY created_at",
                (swarm_id,)
            ).fetchall()
            
            return [
                SwarmTask(
                    id=row["id"],
                    swarm_id=row["swarm_id"],
                    description=row["description"],
                    input_data=json.loads(row["input_data"]) if row["input_data"] else {},
                    status=TaskStatus(row["status"]),
                    assigned_agents=json.loads(row["assigned_agents"]) if row["assigned_agents"] else [],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                    completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                )
                for row in rows
            ]
    
    # ── Message Operations ───────────────────────────────────────────────────
    
    def add_message(
        self,
        swarm_id: str,
        from_agent: str,
        content: str,
        to_agent: Optional[str] = None,
        message_type: MessageType = MessageType.BROADCAST
    ) -> int:
        """Add a message to the swarm log."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO swarm_messages
                (swarm_id, from_agent, to_agent, message_type, content, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    swarm_id,
                    from_agent,
                    to_agent,
                    message_type.value,
                    content,
                    datetime.now().isoformat()
                )
            )
            conn.commit()
            return cursor.lastrowid
    
    def get_messages(
        self,
        swarm_id: str,
        to_agent: Optional[str] = None,
        limit: int = 100
    ) -> List[SwarmMessage]:
        """Get messages for a swarm, optionally filtered by recipient."""
        with self._get_connection() as conn:
            if to_agent:
                # Get messages addressed to specific agent or broadcasts
                rows = conn.execute(
                    """
                    SELECT * FROM swarm_messages 
                    WHERE swarm_id = ? AND (to_agent = ? OR to_agent IS NULL)
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (swarm_id, to_agent, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM swarm_messages 
                    WHERE swarm_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (swarm_id, limit)
                ).fetchall()
            
            return [
                SwarmMessage(
                    id=row["id"],
                    swarm_id=row["swarm_id"],
                    from_agent=row["from_agent"],
                    to_agent=row["to_agent"],
                    message_type=MessageType(row["message_type"]),
                    content=row["content"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                )
                for row in rows
            ]
    
    # ── Result Operations ────────────────────────────────────────────────────
    
    def _compute_input_hash(self, input_data: Dict[str, Any]) -> str:
        """Compute hash for input data.
        
        Args:
            input_data: Input data dictionary to hash
            
        Returns:
            SHA256 hash of the JSON-sorted input data
        """
        # Sort keys for consistent hashing
        data_str = json.dumps(input_data, sort_keys=True, default=str)
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]
    
    def save_result(
        self,
        result: SwarmResult,
        input_hash: Optional[str] = None
    ):
        """Save a swarm result and optionally cache it.
        
        Args:
            result: The swarm result to save
            input_hash: Optional hash of input data for caching.
                       If provided, result will be cached for 1 hour.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO swarm_results
                (swarm_id, aggregation_method, individual_results, final_result,
                 confidence_score, execution_time_seconds, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.swarm_id,
                    result.aggregation_method.value,
                    json.dumps({k: v.to_dict() for k, v in result.individual_results.items()}),
                    result.final_result,
                    result.confidence_score,
                    result.execution_time_seconds,
                    result.created_at.isoformat(),
                    json.dumps(result.metadata)
                )
            )
            conn.commit()
        
        # Cache the result if caching is enabled and input_hash provided (Optimization 4.3)
        if self._enable_cache and self._cache and input_hash:
            self._cache.set(result.swarm_id, input_hash, result)
            logger.debug(f"Cached result for swarm {result.swarm_id} with input_hash {input_hash}")
    
    def get_result(
        self,
        swarm_id: str,
        input_hash: Optional[str] = None
    ) -> Optional[SwarmResult]:
        """Get result for a swarm, optionally from cache.
        
        Args:
            swarm_id: The swarm identifier
            input_hash: Optional hash of input data for cache lookup.
                       If provided and cache is enabled, checks cache first.
        
        Returns:
            SwarmResult if found, None otherwise.
        """
        # Try cache first if enabled and input_hash provided (Optimization 4.3)
        if self._enable_cache and self._cache and input_hash:
            cached = self._cache.get(swarm_id, input_hash)
            if cached:
                logger.debug(f"Cache hit for swarm {swarm_id} with input_hash {input_hash}")
                return cached
            logger.debug(f"Cache miss for swarm {swarm_id} with input_hash {input_hash}")
        
        # Fall back to database
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM swarm_results WHERE swarm_id = ?",
                (swarm_id,)
            ).fetchone()
            
            if not row:
                return None
            
            individual_results = {
                k: AgentResult.from_dict(v)
                for k, v in json.loads(row["individual_results"]).items()
            }
            
            result = SwarmResult(
                swarm_id=row["swarm_id"],
                aggregation_method=AggregationMethod(row["aggregation_method"]),
                individual_results=individual_results,
                final_result=row["final_result"],
                confidence_score=row["confidence_score"],
                execution_time_seconds=row["execution_time_seconds"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else {}
            )
        
        # Cache the result if caching is enabled and input_hash provided
        if self._enable_cache and self._cache and input_hash:
            self._cache.set(swarm_id, input_hash, result)
        
        return result
    
    def invalidate_result_cache(
        self,
        swarm_id: str,
        input_hash: Optional[str] = None
    ):
        """Invalidate cached result for a swarm.
        
        Args:
            swarm_id: The swarm identifier
            input_hash: Optional specific input hash to invalidate.
                       If None, invalidates all entries for the swarm.
        """
        if self._cache:
            self._cache.invalidate(swarm_id, input_hash)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats including size and TTL.
        """
        if not self._enable_cache or not self._cache:
            return {"enabled": False}
        
        return {
            "enabled": True,
            "size": self._cache.size(),
            "ttl_seconds": self._cache._ttl
        }
    
    # ── Cleanup ───────────────────────────────────────────────────────────────
    
    def cleanup_old_swarms(self, days: int = 30) -> int:
        """Delete swarms older than specified days."""
        cutoff = datetime.now() - __import__('datetime').timedelta(days=days)
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM swarms WHERE created_at < ?",
                (cutoff.isoformat(),)
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old swarms")
            return deleted
    
    # ── Active Execution Operations (Optimization 4.4) ────────────────────────
    
    def save_execution_state(
        self,
        execution_id: str,
        swarm_id: str,
        task_description: str,
        input_data: Optional[Dict[str, Any]] = None
    ):
        """Save execution state to persistent storage.
        
        This is called when starting an async task execution to persist
        the execution state so it can be recovered after a restart.
        
        Args:
            execution_id: Unique identifier for this execution
            swarm_id: Reference to the swarm being executed
            task_description: Description of the task being executed
            input_data: Optional input data for the task
        """
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO active_executions
                (execution_id, swarm_id, task_description, input_data, status, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    swarm_id,
                    task_description,
                    json.dumps(input_data or {}),
                    TaskStatus.RUNNING.value,
                    now,
                    now
                )
            )
            conn.commit()
        
        logger.debug(f"Saved execution state for {execution_id} (swarm {swarm_id})")
    
    def update_execution_state(
        self,
        execution_id: str,
        status: Optional[TaskStatus] = None
    ):
        """Update execution state in persistent storage.
        
        Args:
            execution_id: Unique identifier for this execution
            status: Optional new status (e.g., completed, failed, terminated)
        """
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            if status:
                conn.execute(
                    "UPDATE active_executions SET status = ?, updated_at = ? WHERE execution_id = ?",
                    (status.value, now, execution_id)
                )
            else:
                conn.execute(
                    "UPDATE active_executions SET updated_at = ? WHERE execution_id = ?",
                    (now, execution_id)
                )
            conn.commit()
        
        logger.debug(f"Updated execution state for {execution_id}")
    
    def remove_execution_state(self, execution_id: str):
        """Remove execution state from persistent storage.
        
        This is called when an execution completes (success or failure).
        
        Args:
            execution_id: Unique identifier for this execution
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM active_executions WHERE execution_id = ?",
                (execution_id,)
            )
            conn.commit()
        
        logger.debug(f"Removed execution state for {execution_id}")
    
    def load_active_executions(self) -> List[ActiveExecution]:
        """Load all active executions from persistent storage.
        
        This is called on orchestrator startup to recover any executions
        that were running when the orchestrator last stopped.
        
        Returns:
            List of ActiveExecution objects for all running executions
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM active_executions WHERE status = ?",
                (TaskStatus.RUNNING.value,)
            ).fetchall()
            
            return [
                ActiveExecution(
                    execution_id=row["execution_id"],
                    swarm_id=row["swarm_id"],
                    task_description=row["task_description"],
                    input_data=json.loads(row["input_data"]) if row["input_data"] else {},
                    status=TaskStatus(row["status"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                for row in rows
            ]
    
    def recover_stale_executions(self, max_age_seconds: int = 3600) -> int:
        """Mark stale executions as failed/terminated.
        
        This should be called on startup to recover from crashes where
        the orchestrator was killed while executions were running.
        
        Args:
            max_age_seconds: Maximum age in seconds before an execution
                           is considered stale (default: 1 hour)
        
        Returns:
            Number of executions recovered
        """
        cutoff = datetime.now() - __import__('datetime').timedelta(seconds=max_age_seconds)
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            # Find stale executions
            cursor = conn.execute(
                """
                SELECT execution_id, swarm_id FROM active_executions
                WHERE status = ? AND updated_at < ?
                """,
                (TaskStatus.RUNNING.value, cutoff.isoformat())
            )
            stale = cursor.fetchall()
            
            # Mark them as terminated (they likely crashed)
            for row in stale:
                conn.execute(
                    "UPDATE active_executions SET status = ?, updated_at = ? WHERE execution_id = ?",
                    (TaskStatus.TERMINATED.value, now, row["execution_id"])
                )
                # Also update swarm status
                conn.execute(
                    "UPDATE swarms SET status = ?, completed_at = ? WHERE id = ?",
                    (TaskStatus.TERMINATED.value, now, row["swarm_id"])
                )
            
            conn.commit()
            
            if stale:
                logger.info(f"Recovered {len(stale)} stale executions")
            
            return len(stale)
