import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class Memory:
    """SQLite-backed conversation memory with context manager support."""
    
    def __init__(self, db_path: str = None, auto_cleanup_days: int = 30):
        if db_path is None:
            db_path = Path.home() / ".myclaw" / "memory.db"
        self.db = Path(db_path)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.auto_cleanup_days = auto_cleanup_days
        
        self.conn = sqlite3.connect(self.db, check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )""")
        # Create index for faster timestamp queries
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
        self.conn.commit()

    def __enter__(self):
        """Support for 'with Memory() as m:' pattern."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure connection is closed on exit."""
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

    def get_history(self, limit=20):
        """Get conversation history with optional limit."""
        try:
            cur = self.conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", 
                (limit,)
            )
            return [{"role": r, "content": c} for r, c in cur.fetchall()][::-1]
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
            
            # Vacuum to reclaim space
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