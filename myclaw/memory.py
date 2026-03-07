import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class Memory:
    """SQLite-backed conversation memory with per-user isolation and context manager support."""

    def __init__(self, user_id: str = "default", auto_cleanup_days: int = 30):
        # Each user gets their own DB file — full disk-level session isolation
        db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
        self.db = db_path
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.auto_cleanup_days = auto_cleanup_days

        self.conn = sqlite3.connect(self.db, check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )""")
        # Index for fast timestamp-based queries and cleanup
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
            finally:
                self.conn = None

    def add(self, role: str, content: str):
        """Add a message to the conversation history."""
        try:
            self.conn.execute(
                "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                (role, content, datetime.now().isoformat())
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error adding message: {e}")
            raise

    def get_history(self, limit: int = 20) -> list:
        """Get the last N messages in chronological order, efficiently."""
        try:
            # Subquery approach: get last N by DESC, then re-order ASC in outer query.
            # Avoids Python-side reversal and is faster on large tables.
            cur = self.conn.execute(
                "SELECT role, content FROM "
                "(SELECT role, content, id FROM messages ORDER BY id DESC LIMIT ?) "
                "ORDER BY id ASC",
                (limit,)
            )
            return [{"role": r, "content": c} for r, c in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    def cleanup(self, days: int = None) -> int:
        """Delete messages older than specified days. Returns count of deleted messages."""
        if days is None:
            days = self.auto_cleanup_days

        try:
            cutoff = datetime.now() - timedelta(days=days)
            cursor = self.conn.execute(
                "DELETE FROM messages WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            self.conn.commit()
            deleted = cursor.rowcount

            if deleted > 0:
                self.conn.execute("VACUUM")
                logger.info(f"Cleaned up {deleted} old messages")

            return deleted
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