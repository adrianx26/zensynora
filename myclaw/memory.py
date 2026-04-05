"""
Memory - SQLite-backed conversation memory system.

Provides persistent storage for conversation history with per-user isolation,
full-text search via FTS5, and automatic knowledge extraction.

Key Components:
    - Memory: Async conversation memory with batching and cleanup
    - AsyncSQLitePool: Connection pool for async SQLite operations
    - SQLitePool: Synchronous connection pool with idle timeout
    - Knowledge Extraction: Automatic entity/relation extraction from messages

Features:
    - Per-user database isolation (memory_{user_id}.db)
    - FTS5 full-text search with BM25 ranking
    - Automatic message cleanup (configurable retention)
    - Knowledge extraction to knowledge base
    - WAL mode for better concurrency

Usage:
    from myclaw.memory import Memory

    mem = Memory(user_id="default")
    await mem.initialize()

    # Store message
    await mem.add(role="user", content="Hello!")

    # Retrieve history
    history = await mem.get_history(limit=10)

    # Search messages
    results = await mem.search("machine learning")
"""

import sqlite3
import aiosqlite
import json
import logging
import re
import threading
import asyncio
import time
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

# ── Configuration Constants ────────────────────────────────────────────────────
DEFAULT_BATCH_SIZE = 10
DEFAULT_CACHE_SIZE = 5
MAX_DELEGATION_DEPTH = 10
VACUUM_INTERVAL = 100
DEFAULT_CLEANUP_DAYS = 30
DEFAULT_HISTORY_LIMIT = 20
CLEANUP_CHUNK_SIZE = 100
CACHE_TTL_SECONDS = 1.0

# ── Async SQLite Connection Pool (Optimization #1) ─────────────────────────────

class AsyncSQLitePool:
    """Async connection pool for SQLite databases using aiosqlite."""
    
    _pools: dict[str, aiosqlite.Connection] = {}
    _locks: dict[str, asyncio.Lock] = {}
    _refcounts: dict[str, int] = {}
    _pool_lock = asyncio.Lock()
    
    @classmethod
    async def get_connection(cls, db_path: Path) -> aiosqlite.Connection:
        """Get or create a pooled async connection."""
        key = str(db_path)
        
        async with cls._pool_lock:
            if key not in cls._locks:
                cls._locks[key] = asyncio.Lock()
                cls._refcounts[key] = 0
        
        await cls._locks[key].acquire()
        
        if key not in cls._pools:
            conn = await aiosqlite.connect(db_path, check_same_thread=False)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            cls._pools[key] = conn
        
        async with cls._pool_lock:
            cls._refcounts[key] += 1
            
        return cls._pools[key]
    
    @classmethod
    async def release_connection(cls, db_path: Path):
        """Release a connection back to the pool."""
        key = str(db_path)
        async with cls._pool_lock:
            cls._refcounts[key] -= 1
        
        if key in cls._locks:
            cls._locks[key].release()
    
    @classmethod
    async def close_all(cls):
        """Close all pooled connections."""
        async with cls._pool_lock:
            for conn in cls._pools.values():
                try:
                    await conn.close()
                except Exception:
                    pass
            cls._pools.clear()
            cls._refcounts.clear()


# ── Sync SQLite Connection Pool (legacy, for backwards compatibility) ─────────

class SQLitePool:
    """Simple connection pool for SQLite databases with idle timeout."""
    
    _pools: dict[str, sqlite3.Connection] = {}
    _locks: dict[str, threading.Lock] = {}
    _refcounts: dict[str, int] = {}
    _last_used: dict[str, float] = {}
    _pool_lock = threading.Lock()
    IDLE_TIMEOUT = 300  # 5 minutes
    
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
            cls._last_used[key] = time.time()
            
        return cls._pools[key]
    
    @classmethod
    def release_connection(cls, db_path: Path):
        """Release a connection back to the pool."""
        key = str(db_path)
        with cls._pool_lock:
            cls._refcounts[key] -= 1
            if cls._refcounts[key] <= 0:
                cls._last_used[key] = time.time()
        
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
            cls._last_used.clear()
    
    @classmethod
    def cleanup_idle(cls):
        """Close connections idle for longer than IDLE_TIMEOUT."""
        now = time.time()
        with cls._pool_lock:
            idle_keys = [
                key for key, last in cls._last_used.items()
                if cls._refcounts.get(key, 0) <= 0 and (now - last) > cls.IDLE_TIMEOUT
            ]
            for key in idle_keys:
                try:
                    cls._pools[key].close()
                except Exception:
                    pass
                cls._pools.pop(key, None)
                cls._refcounts.pop(key, None)
                cls._last_used.pop(key, None)
                cls._locks.pop(key, None)


async def cleanup_on_shutdown():
    """Call this on application shutdown to clean up resources."""
    await AsyncSQLitePool.close_all()
    logger.info("Async memory pool shutdown complete")


class Memory:
    """Async SQLite-backed conversation memory with per-user isolation."""

    _cleanup_count: int = 0
    
    def __init__(self, user_id: str = "default", auto_cleanup_days: int = DEFAULT_CLEANUP_DAYS, auto_cleanup_enabled: bool = True):
        db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
        self.db = db_path
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.auto_cleanup_days = auto_cleanup_days
        self.auto_cleanup_enabled = auto_cleanup_enabled
        self.cleanup_count = 0
        self.vacuum_interval = VACUUM_INTERVAL
        self._history_cache = {}
        self._cache_max_size = DEFAULT_CACHE_SIZE
        self._pending_messages = []
        self._batch_size = DEFAULT_BATCH_SIZE
        self._batch_timeout = CACHE_TTL_SECONDS
        self._last_flush = datetime.now()
        self._cleanup_chunk_size = CLEANUP_CHUNK_SIZE
        self.conn: Optional[aiosqlite.Connection] = None
        self._initialized = False

    async def initialize(self):
        """Initialize async connection and schema. Call before using other methods."""
        if self._initialized:
            return
        self.conn = await AsyncSQLitePool.get_connection(self.db)
        
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )""")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
        await self.conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            content=messages,
            content_rowid=id
        )""")
        await self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        await self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
        END""")
        await self.conn.execute("""CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        await self.conn.commit()
        self._initialized = True
        if self.auto_cleanup_enabled:
            await self.cleanup(self.auto_cleanup_days)

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close the database connection."""
        await self.flush()
        if self.conn:
            try:
                await AsyncSQLitePool.release_connection(self.db)
                logger.info(f"Async database connection released: {self.db.name}")
            except Exception as e:
                logger.error(f"Error releasing database connection: {e}")
            self.conn = None
            self._initialized = False

    async def flush(self) -> int:
        """Flush any pending messages to the database."""
        if not self._pending_messages:
            return 0
        
        try:
            await self.conn.executemany(
                "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                self._pending_messages
            )
            await self.conn.commit()
            
            count = len(self._pending_messages)
            self._pending_messages = []
            self._last_flush = datetime.now()
            self._history_cache.clear()
            
            logger.info(f"Flushed {count} messages to database")
            return count
        except Exception as e:
            logger.error(f"Error flushing messages: {e}")
            return 0

    async def add(self, role: str, content: str):
        """Add a message to the conversation history (with batching)."""
        if not self._initialized:
            await self.initialize()
        
        self._pending_messages.append((role, content, datetime.now().isoformat()))
        
        should_flush = (
            len(self._pending_messages) >= self._batch_size or
            (datetime.now() - self._last_flush).total_seconds() >= self._batch_timeout
        )
        
        if should_flush:
            await self.flush()

    async def get_history(self, limit: int = DEFAULT_HISTORY_LIMIT, columns: Optional[List[str]] = None) -> list:
        """Get the last N messages in chronological order, efficiently with caching."""
        if not self._initialized:
            await self.initialize()
        
        is_default_columns = columns is None or columns == ["role", "content"]
        if is_default_columns:
            cache_key = limit
            if cache_key in self._history_cache:
                return self._history_cache[cache_key]

        try:
            if columns is None:
                columns = ["role", "content"]
            allowed_columns = {"id", "role", "content", "timestamp"}
            if not set(columns).issubset(allowed_columns):
                raise ValueError(f"Invalid column(s). Allowed columns are: {allowed_columns}")

            inner_columns = list(dict.fromkeys(columns + ["id"]))
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
            cur = await self.conn.execute(query, (limit,))
            rows = await cur.fetchall()
            
            result = [dict(zip(columns, row)) for row in rows]

            if is_default_columns:
                if len(self._history_cache) >= self._cache_max_size:
                    oldest_key = next(iter(self._history_cache))
                    del self._history_cache[oldest_key]
                self._history_cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    async def cleanup(self, days: int = None) -> int:
        """Delete messages older than specified days in chunks."""
        if not self._initialized:
            await self.initialize()
        
        if days is None:
            days = self.auto_cleanup_days

        total_deleted = 0
        
        try:
            cutoff = datetime.now() - timedelta(days=days)

            while True:
                cursor = await self.conn.execute(
                    "DELETE FROM messages WHERE timestamp < ? LIMIT ?",
                    (cutoff.isoformat(), self._cleanup_chunk_size)
                )
                await self.conn.commit()
                deleted = cursor.rowcount
                
                if deleted == 0:
                    break
                    
                total_deleted += deleted
                logger.debug(f"Cleaned up chunk of {deleted} messages (total: {total_deleted})")
            
            if total_deleted > 0:
                self.cleanup_count += 1
                if self.cleanup_count >= self.vacuum_interval:
                    await self.conn.execute("VACUUM")
                    self.cleanup_count = 0
                    logger.info(f"VACUUM performed after {self.vacuum_interval} cleanups")
                
                self._history_cache.clear()
                logger.info(f"Cleaned up {total_deleted} old messages")
            
            return total_deleted

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return total_deleted

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        if not self._initialized:
            await self.initialize()
        try:
            cur = await self.conn.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM messages")
            count, oldest, newest = await cur.fetchone()
            return {
                "total_messages": count,
                "oldest_message": oldest,
                "newest_message": newest
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}

    async def extract_knowledge_candidates(self, limit: int = 50) -> List[Dict]:
        """Extract potential knowledge entities from recent conversation history."""
        if not self._initialized:
            await self.initialize()
        
        try:
            cur = await self.conn.execute(
                "SELECT content FROM messages ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            messages = [row[0] async for row in cur.fetchall()]
            
            text = ' '.join(messages)
            
            candidates = []
            
            for pattern in ENTITY_PATTERNS:
                matches = re.findall(pattern, text)
                for match in matches:
                    if len(match) > 3:
                        count = text.count(match)
                        if count >= 2:
                            candidates.append({
                                'type': 'entity',
                                'name': match,
                                'mentions': count,
                                'confidence': min(count / 5, 1.0),
                                'source': 'pattern_match'
                            })
            
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
            
            seen = set()
            unique_candidates = []
            for c in candidates:
                name = c['name'].lower()
                if name not in seen:
                    seen.add(name)
                    unique_candidates.append(c)
            
            unique_candidates.sort(key=lambda x: x['confidence'], reverse=True)
            
            return unique_candidates[:20]
            
        except Exception as e:
            logger.error(f"Error extracting knowledge: {e}")
            return []

    async def save_extracted_knowledge(
        self,
        entity_name: str,
        observations: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        user_id: str = "default"
    ) -> Optional[str]:
        """Save an extracted knowledge entity to the knowledge base."""
        if not self._initialized:
            await self.initialize()
        
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

    async def search(self, query: str, limit: int = 20, boost_recent: bool = True) -> list:
        """Search messages using full-text search with intelligent query processing."""
        if not self._initialized:
            await self.initialize()
        
        try:
            # Security: Sanitize query - only allow alphanumeric and basic FTS operators
            # Remove potentially dangerous characters that could manipulate FTS queries
            sanitized_query = re.sub(r'[^\w\s"\*\-\(\)ANDORNOT]', '', query)
            processed_query = sanitized_query
            
            exact_phrases = re.findall(r'"([^"]+)"', processed_query)
            if exact_phrases:
                for phrase in exact_phrases:
                    processed_query = processed_query.replace(f'"{phrase}"', f'"{phrase}"')
            
            if not any(op in processed_query.upper() for op in ['AND', 'OR', 'NOT', '*']):
                words = processed_query.split()
                if len(words) == 1:
                    processed_query = f"{processed_query}*"
                elif len(words) <= 3:
                    processed_query = " ".join(f"{w}*" for w in words)
            
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
            
            cur = await self.conn.execute(sql, (processed_query, limit))
            
            if boost_recent:
                return [
                    {"role": r, "content": c, "timestamp": t, "score": s}
                    async for r, c, t, s, _ in cur.fetchall()
                ]
            else:
                return [{"role": r, "content": c, "timestamp": t} async for r, c, t in cur.fetchall()]
                
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []