import sqlite3
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import List, Dict, Optional
from functools import lru_cache

from .exceptions import MemoryError, MemoryQueryError

logger = logging.getLogger(__name__)

# Import knowledge storage for extraction
from .knowledge.parser import Observation
from .knowledge.storage import write_note

# Patterns for knowledge extraction
ENTITY_PATTERNS = [
    r'\b([A-Z][a-z]+ (?:Project|System|API|Tool|Framework|Library|Database|Server))\b',
    r'\b([A-Z][a-zA-Z]+(?:\d+)?)\b',  # Capitalized words (potential proper nouns)
]

RELATION_KEYWORDS = [
    'uses', 'depends on', 'requires', 'integrates with', 'connects to',
    'implements', 'extends', 'inherits from', 'calls', 'triggers',
    'leads to', 'results in', 'causes', 'enables', 'blocks'
]


# ── SQLite Connection Pool ───────────────────────────────────────────────────────

class SQLitePool:
    """Simple connection pool for SQLite databases."""
    
    _pools: dict[str, sqlite3.Connection] = {}
    _locks: dict[str, threading.Lock] = {}
    _refcounts: dict[str, int] = {}
    _pool_lock = threading.Lock()
    
    @classmethod
    def get_connection(cls, db_path: Path) -> sqlite3.Connection:
        """Get or create a pooled connection."""
        key = str(db_path)
        
        with cls._pool_lock:
            if key not in cls._locks:
                cls._locks[key] = threading.Lock()
                cls._refcounts[key] = 0
        
        # Use lock for this specific DB
        cls._locks[key].acquire()
        
        if key not in cls._pools:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL for better concurrency
            conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety/speed
            cls._pools[key] = conn
        
        with cls._pool_lock:
            cls._refcounts[key] += 1
            
        return cls._pools[key]
    
    @classmethod
    def release_connection(cls, db_path: Path):
        """Release a connection back to the pool."""
        key = str(db_path)
        with cls._pool_lock:
            cls._refcounts[key] -= 1
        
        if key in cls._locks:
            cls._locks[key].release()
    
    @classmethod
    def close_all(cls):
        """Close all pooled connections."""
        with cls._pool_lock:
            for conn in cls._pools.values():
                try:
                    conn.close()
                except Exception:
                    pass
            cls._pools.clear()
            cls._refcounts.clear()


def cleanup_on_shutdown():
    """Call this on application shutdown to clean up resources."""
    SQLitePool.close_all()
    logger.info("Memory pool shutdown complete")

class Memory:
    """SQLite-backed conversation memory with per-user isolation and context manager support."""

    # Class-level tracking for VACUUM optimization
    _cleanup_count: int = 0
    
    # Class-level LRU cache for history (shared across instances with same user_id)
    _history_cache: dict = {}
    _history_cache_order: list = []
    _cache_max_size: int = 10
    _cache_lock = threading.Lock()
    
    def __init__(self, user_id: str = "default", auto_cleanup_days: int = 30, auto_cleanup_enabled: bool = True):
        # Each user gets their own DB file — full disk-level session isolation
        db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
        self.db = db_path
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.auto_cleanup_days = auto_cleanup_days
        self.auto_cleanup_enabled = auto_cleanup_enabled
        
        # Batch mode state
        self._batch_mode = False
        self._batch_size = 0

        # Use pooled connection
        self.conn = SQLitePool.get_connection(self.db)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )""")
        # Index for fast timestamp-based queries and cleanup
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
        
        # Create FTS5 virtual table for full-text search
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content=messages,
                content_rowid=id,
                tokenize='porter unicode61'
            )
        """)
        
        # Create triggers to keep FTS table in sync
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END
        """)
        
        self.conn.commit()
        # 6.2: Only run cleanup if enabled
        if self.auto_cleanup_enabled:
            self.cleanup(self.auto_cleanup_days)

    @classmethod
    def _get_cache_key(cls, db_path: Path, limit: int, columns: tuple) -> str:
        """Generate cache key for history query."""
        return f"{db_path}:{limit}:{columns}"
    
    @classmethod
    def _get_cached_history(cls, db_path: Path, limit: int, columns: tuple) -> Optional[list]:
        """Get cached history if available."""
        key = cls._get_cache_key(db_path, limit, columns)
        with cls._cache_lock:
            return cls._history_cache.get(key)
    
    @classmethod
    def _set_cached_history(cls, db_path: Path, limit: int, columns: tuple, value: list):
        """Set cached history with LRU eviction."""
        key = cls._get_cache_key(db_path, limit, columns)
        with cls._cache_lock:
            # Evict oldest if at capacity
            if len(cls._history_cache_order) >= cls._cache_max_size:
                oldest_key = cls._history_cache_order.pop(0)
                cls._history_cache.pop(oldest_key, None)
            
            cls._history_cache[key] = value
            cls._history_cache_order.append(key)
    
    @classmethod
    def _invalidate_cache(cls, db_path: Path):
        """Invalidate all cache entries for a specific database."""
        with cls._cache_lock:
            keys_to_remove = [k for k in cls._history_cache if k.startswith(str(db_path))]
            for key in keys_to_remove:
                cls._history_cache.pop(key, None)
                if key in cls._history_cache_order:
                    cls._history_cache_order.remove(key)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                SQLitePool.release_connection(self.db)
                logger.info(f"Database connection released: {self.db.name}")
            except Exception as e:
                logger.error(f"Error releasing database connection: {e}")

    @contextmanager
    def batch_mode(self):
        """Context manager for batch writes - delays commit until exit.
        
        Usage:
            with mem.batch_mode():
                mem.add("user", "message 1")
                mem.add("user", "message 2")
                mem.add("user", "message 3")
            # All messages committed at once
        """
        self._batch_mode = True
        self._batch_size = 0
        try:
            yield self
        finally:
            if self._batch_size > 0:
                self.conn.commit()
                self._invalidate_cache(self.db)
            self._batch_mode = False
            self._batch_size = 0

    def add(self, role: str, content: str):
        """Add a message to the conversation history."""
        try:
            self.conn.execute(
                "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                (role, content, datetime.now().isoformat())
            )
            
            # Check if in batch mode
            if hasattr(self, '_batch_mode') and self._batch_mode:
                self._batch_size += 1
            else:
                self.conn.commit()
                # Invalidate cache when new messages are added
                self._invalidate_cache(self.db)
        except Exception as e:
            logger.error(f"Error adding message: {e}")
            raise

    def get_history(self, limit: int = 20, columns: Optional[List[str]] = None, use_cache: bool = True) -> list:
        """Get the last N messages in chronological order, efficiently.

        Args:
            limit: Maximum number of messages to retrieve.
            columns: List of column names to retrieve. If None, defaults to ["role", "content"].
                     Allowed columns: "id", "role", "content", "timestamp".
            use_cache: Whether to use the LRU cache (default True).

        Returns:
            List of dictionaries, each representing a message with the requested columns.
        """
        try:
            if columns is None:
                columns = ["role", "content"]
            allowed_columns = {"id", "role", "content", "timestamp"}
            if not set(columns).issubset(allowed_columns):
                from .exceptions import MemoryValidationError
                raise MemoryValidationError(
                    f"Invalid column(s). Allowed columns are: {allowed_columns}",
                    column=", ".join(set(columns) - set(allowed_columns)),
                    allowed_values=allowed_columns
                )

            # Check cache first
            columns_tuple = tuple(columns)
            if use_cache:
                cached = self._get_cached_history(self.db, limit, columns_tuple)
                if cached is not None:
                    return cached

            # Ensure we have the id for ordering in the inner query, but avoid duplicating in the inner select
            inner_columns = list(dict.fromkeys(columns + ["id"]))  # preserves order and removes duplicates

            # Build the query
            inner_select = ", ".join(inner_columns)
            outer_select = ", ".join(columns)
            query = f"""
                SELECT {outer_select} 
                FROM (
                    SELECT {inner_select} 
                    FROM messages 
                    ORDER BY id DESC 
                    LIMIT ?
                ) 
                ORDER BY id ASC
            """
            cur = self.conn.execute(query, (limit,))
            rows = cur.fetchall()
            # Build list of dictionaries
            result = [dict(zip(columns, row)) for row in rows]
            
            # Cache the result
            if use_cache:
                self._set_cached_history(self.db, limit, columns_tuple, result)
            
            return result
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    def cleanup(self, days: int = None, incremental: bool = True, chunk_size: int = 1000) -> int:
        """Delete messages older than specified days. Returns count of deleted messages.
        
        Args:
            days: Number of days to keep (defaults to auto_cleanup_days)
            incremental: If True, deletes in chunks to avoid long locks (default True)
            chunk_size: Number of messages to delete per chunk (default 1000)
        """
        if days is None:
            days = self.auto_cleanup_days

        try:
            cutoff = datetime.now() - timedelta(days=days)
            total_deleted = 0
            
            if incremental:
                # Incremental cleanup - delete in chunks
                while True:
                    cursor = self.conn.execute(
                        "DELETE FROM messages WHERE timestamp < ? LIMIT ?",
                        (cutoff.isoformat(), chunk_size)
                    )
                    self.conn.commit()
                    deleted = cursor.rowcount
                    total_deleted += deleted
                    
                    if deleted < chunk_size:
                        break
                    
                    logger.debug(f"Incremental cleanup: deleted {deleted} messages")
            else:
                # Bulk delete (original behavior)
                cursor = self.conn.execute(
                    "DELETE FROM messages WHERE timestamp < ?",
                    (cutoff.isoformat(),)
                )
                self.conn.commit()
                total_deleted = cursor.rowcount

            if total_deleted > 0:
                # Increment cleanup counter and run VACUUM every 100 cleanups
                Memory._cleanup_count += 1
                if Memory._cleanup_count >= 100:
                    self.conn.execute("VACUUM")
                    Memory._cleanup_count = 0
                    logger.info("VACUUM performed after 100 cleanups")
                logger.info(f"Cleaned up {total_deleted} old messages")
                # Invalidate cache after cleanup
                self._invalidate_cache(self.db)

            return total_deleted
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return 0

    def get_stats(self) -> dict:
        """Get memory statistics."""
        try:
            cur = self.conn.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM messages")
            count, oldest, newest = cur.fetchone()
            return {
                "total_messages": count,
                "oldest_message": oldest,
                "newest_message": newest
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}

    def search(self, query: str, limit: int = 20) -> list:
        """
        Search messages using full-text search (FTS5).
        
        Args:
            query: Search query (supports FTS5 query syntax: AND, OR, NOT, *, etc.)
            limit: Maximum number of results to return.
            
        Returns:
            List of matching messages with relevance ranking.
        """
        try:
            # Use FTS5 MATCH for full-text search with ranking
            fts_query = f"""
                SELECT m.id, m.role, m.content, m.timestamp,
                       bm25(messages_fts) as rank
                FROM messages m
                JOIN messages_fts fts ON m.id = fts.rowid
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            cur = self.conn.execute(fts_query, (query, limit))
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "role": row[1],
                    "content": row[2],
                    "timestamp": row[3],
                    "rank": row[4]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error searching messages: {e}")
            return []

    def extract_knowledge_candidates(self, limit: int = 50) -> List[Dict]:
        """
        Extract potential knowledge entities from recent conversation history.
        
        This is a simple extraction that looks for:
        - Capitalized phrases (potential named entities)
        - Technical terms
        - Recurring topics
        
        Args:
            limit: Number of recent messages to analyze
            
        Returns:
            List of candidate knowledge items with confidence scores
        """
        try:
            # Get recent messages
            cur = self.conn.execute(
                "SELECT content FROM messages ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            messages = [row[0] for row in cur.fetchall()]
            
            # Combine all messages
            text = ' '.join(messages)
            
            candidates = []
            
            # Extract capitalized phrases (potential entities)
            for pattern in ENTITY_PATTERNS:
                matches = re.findall(pattern, text)
                for match in matches:
                    if len(match) > 3:  # Filter out short matches
                        # Count occurrences as confidence indicator
                        count = text.count(match)
                        if count >= 2:  # Must appear at least twice
                            candidates.append({
                                'type': 'entity',
                                'name': match,
                                'mentions': count,
                                'confidence': min(count / 5, 1.0),  # Cap at 1.0
                                'source': 'pattern_match'
                            })
            
            # Look for "X is Y" patterns (definitions/facts)
            definition_pattern = r'([A-Z][\w\s]+)\s+is\s+(?:a|an|the)\s+([\w\s]+)'
            definitions = re.findall(definition_pattern, text)
            for entity, definition in definitions:
                entity_clean = entity.strip()
                if len(entity_clean) > 3:
                    candidates.append({
                        'type': 'definition',
                        'name': entity_clean,
                        'definition': definition.strip(),
                        'confidence': 0.7,
                        'source': 'definition_pattern'
                    })
            
            # Deduplicate by name
            seen = set()
            unique_candidates = []
            for c in candidates:
                name = c['name'].lower()
                if name not in seen:
                    seen.add(name)
                    unique_candidates.append(c)
            
            # Sort by confidence
            unique_candidates.sort(key=lambda x: x['confidence'], reverse=True)
            
            return unique_candidates[:20]  # Return top 20
            
        except Exception as e:
            logger.error(f"Error extracting knowledge: {e}")
            return []

    def save_extracted_knowledge(
        self,
        entity_name: str,
        observations: List[str] = None,
        tags: List[str] = None,
        user_id: str = "default"
    ) -> Optional[str]:
        """
        Save an extracted knowledge entity to the knowledge base.
        
        Args:
            entity_name: Name of the entity
            observations: List of observations/facts
            tags: List of tags
            user_id: User ID for isolation
            
        Returns:
            Permalink if successful, None otherwise
        """
        try:
            obs_objects = []
            if observations:
                for obs in observations:
                    obs_objects.append(Observation(
                        category='extracted',
                        content=obs,
                        tags=[]
                    ))
            
            permalink = write_note(
                name=entity_name,
                title=entity_name,
                observations=obs_objects,
                tags=tags or ['auto-extracted'],
                user_id=user_id
            )
            
            logger.info(f"Saved extracted knowledge: {entity_name} ({permalink})")
            return permalink
            
        except Exception as e:
            logger.error(f"Error saving extracted knowledge: {e}")
            return None