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
    
    def __init__(self, user_id: str = "default", auto_cleanup_days: int = 30, auto_cleanup_enabled: bool = True):
        # Each user gets their own DB file — full disk-level session isolation
        db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
        self.db = db_path
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.auto_cleanup_days = auto_cleanup_days
        self.auto_cleanup_enabled = auto_cleanup_enabled

        # VACUUM optimization: track cleanups and only VACUUM periodically
        self.cleanup_count = 0
        self.vacuum_interval = 100  # Run VACUUM every 100 cleanups

        # LRU cache for history retrieval
        self._history_cache = {}  # Simple cache: (limit) -> result
        self._cache_max_size = 5  # Number of different limit values to cache

        # Batch writing configuration
        self._pending_messages = []  # Buffer for pending messages
        self._batch_size = 10  # Number of messages to batch before commit
        self._batch_timeout = 1.0  # Max seconds to wait before auto-flush
        self._last_flush = datetime.now()  # Track last flush time

        # Chunked cleanup configuration
        self._cleanup_chunk_size = 100  # Delete messages in chunks of 100

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
        self.conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            content=messages,
            content_rowid=id
        )""")
        
        # Trigger for INSERT
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        
        # Trigger for DELETE
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
        END""")
        
        # Trigger for UPDATE
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        
        self.conn.commit()
        # 6.2: Only run cleanup if enabled
        if self.auto_cleanup_enabled:
            self.cleanup(self.auto_cleanup_days)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the database connection."""
        # Flush any pending messages first
        self.flush()
        if hasattr(self, 'conn') and self.conn:
            try:
                SQLitePool.release_connection(self.db)
                logger.info(f"Database connection released: {self.db.name}")
            except Exception as e:
                logger.error(f"Error releasing database connection: {e}")

    def flush(self) -> int:
        """Flush any pending messages to the database. Returns count of flushed messages."""
        if not self._pending_messages:
            return 0
        
        try:
            # Use executemany for batch insert
            self.conn.executemany(
                "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                self._pending_messages
            )
            self.conn.commit()
            
            # Clear pending and update timestamp
            count = len(self._pending_messages)
            self._pending_messages = []
            self._last_flush = datetime.now()
            
            # Clear history cache since we added new messages
            if hasattr(self, '_history_cache'):
                self._history_cache.clear()
            
            logger.info(f"Flushed {count} messages to database")
            return count
        except Exception as e:
            logger.error(f"Error flushing messages: {e}")
            return 0

    def add(self, role: str, content: str):
        """Add a message to the conversation history (with batching)."""
        # Add to pending batch
        self._pending_messages.append((role, content, datetime.now().isoformat()))
        
        # Check if we should flush
        should_flush = (
            len(self._pending_messages) >= self._batch_size or
            (datetime.now() - self._last_flush).total_seconds() >= self._batch_timeout
        )
        
        if should_flush:
            self.flush()

    def get_history(self, limit: int = 20, columns: Optional[List[str]] = None) -> list:
        """Get the last N messages in chronological order, efficiently with caching.

        Args:
            limit: Maximum number of messages to retrieve.
            columns: List of column names to retrieve. If None, defaults to ["role", "content"].
                     Allowed columns: "id", "role", "content", "timestamp".

        Returns:
            List of dictionaries, each representing a message with the requested columns.
        """
        # Caching check (only if columns is default)
        is_default_columns = columns is None or columns == ["role", "content"]
        if is_default_columns:
            cache_key = limit
            if hasattr(self, '_history_cache') and cache_key in self._history_cache:
                return self._history_cache[cache_key]

        try:
            if columns is None:
                columns = ["role", "content"]
            allowed_columns = {"id", "role", "content", "timestamp"}
            if not set(columns).issubset(allowed_columns):
                raise ValueError(f"Invalid column(s). Allowed columns are: {allowed_columns}")

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

            # Cache the result if default columns
            if is_default_columns and hasattr(self, '_history_cache'):
                if len(self._history_cache) >= self._cache_max_size:
                    oldest_key = next(iter(self._history_cache))
                    del self._history_cache[oldest_key]
                self._history_cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    def cleanup(self, days: int = None) -> int:
        """Delete messages older than specified days in chunks. Returns count of deleted messages."""
        if days is None:
            days = self.auto_cleanup_days

        total_deleted = 0
        
        try:
            cutoff = datetime.now() - timedelta(days=days)

            
            while True:
                # Delete in chunks to avoid locking for long periods
                cursor = self.conn.execute(
                    "DELETE FROM messages WHERE timestamp < ? LIMIT ?",
                    (cutoff.isoformat(), self._cleanup_chunk_size)
                )
                self.conn.commit()
                deleted = cursor.rowcount
                
                if deleted == 0:
                    break
                    
                total_deleted += deleted
                logger.debug(f"Cleaned up chunk of {deleted} messages (total: {total_deleted})")
            
            if total_deleted > 0:
                # Increment cleanup counter and run VACUUM periodically
                self.cleanup_count += 1
                if self.cleanup_count >= self.vacuum_interval:
                    self.conn.execute("VACUUM")
                    self.cleanup_count = 0
                    logger.info(f"VACUUM performed after {self.vacuum_interval} cleanups")
                
                # Clear history cache after cleanup
                if hasattr(self, '_history_cache'):
                    self._history_cache.clear()
                logger.info(f"Cleaned up {total_deleted} old messages")
            
            return total_deleted

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return total_deleted

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

    def search(self, query: str, limit: int = 20, boost_recent: bool = True) -> list:
        """Search messages using full-text search with intelligent query processing.
        
        Args:
            query: Search query string. Supports FTS5 syntax (AND, OR, NOT, *).
                   Phrases in quotes are matched exactly.
            limit: Maximum number of results (default: 20)
            boost_recent: Whether to boost recent messages in ranking (default: True)
            
        Returns:
            List of message dicts with role, content, and timestamp.
        """
        try:
            import re
            
            # Preprocess query for better FTS5 matching
            processed_query = query
            
            # Detect if user used quotes (exact phrase)
            exact_phrases = re.findall(r'"([^"]+)"', processed_query)
            if exact_phrases:
                # Replace quoted phrases with FTS5 phrase syntax
                for phrase in exact_phrases:
                    processed_query = processed_query.replace(f'"{phrase}"', f'"{phrase}"')
            
            # If query has no operators, try prefix matching on each word
            if not any(op in processed_query.upper() for op in ['AND', 'OR', 'NOT', '*']):
                words = processed_query.split()
                if len(words) == 1:
                    processed_query = f"{processed_query}*"
                elif len(words) <= 3:
                    processed_query = " ".join(f"{w}*" for w in words)
            
            # Build the SQL query with optional recency boosting
            if boost_recent:
                sql = """
                    SELECT m.role, m.content, m.timestamp,
                           bm25(messages_fts) as score,
                           CAST(m.id as real) / 1000000 as recency_score
                    FROM messages m
                    JOIN messages_fts fts ON m.id = fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY bm25(messages_fts) + (recency_score * 0.1) DESC
                    LIMIT ?
                """
            else:
                sql = """
                    SELECT m.role, m.content, m.timestamp
                    FROM messages m
                    JOIN messages_fts fts ON m.id = fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
            
            cur = self.conn.execute(sql, (processed_query, limit))
            
            if boost_recent:
                return [
                    {"role": r, "content": c, "timestamp": t, "score": s}
                    for r, c, t, s, _ in cur.fetchall()
                ]
            else:
                return [{"role": r, "content": c, "timestamp": t} for r, c, t in cur.fetchall()]
                
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []