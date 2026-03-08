import sqlite3
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import List, Dict, Optional

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
        self.cleanup(self.auto_cleanup_days)

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