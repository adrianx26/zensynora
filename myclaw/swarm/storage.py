"""
Storage layer for the Agent Swarm system.

Provides persistent storage for swarm state, tasks, messages, and results
using SQLite.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .models import (
    SwarmInfo, SwarmTask, SwarmResult, SwarmMessage,
    TaskStatus, MessageType, SwarmStrategy, AggregationMethod,
    AgentResult
)

logger = logging.getLogger(__name__)

# Database path
SWARM_DB_DIR = Path.home() / ".myclaw"
SWARM_DB_PATH = SWARM_DB_DIR / "swarm.db"


class SwarmStorage:
    """SQLite storage for swarm data.
    
    Manages persistence of:
    - Swarm configurations and status
    - Individual tasks within swarms
    - Inter-agent messages
    - Final results
    
    Each user has isolated data via user_id field.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize storage with optional custom path."""
        self.db_path = db_path or SWARM_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
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
    
    def save_result(self, result: SwarmResult):
        """Save a swarm result."""
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
    
    def get_result(self, swarm_id: str) -> Optional[SwarmResult]:
        """Get result for a swarm."""
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
            
            return SwarmResult(
                swarm_id=row["swarm_id"],
                aggregation_method=AggregationMethod(row["aggregation_method"]),
                individual_results=individual_results,
                final_result=row["final_result"],
                confidence_score=row["confidence_score"],
                execution_time_seconds=row["execution_time_seconds"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else {}
            )
    
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
