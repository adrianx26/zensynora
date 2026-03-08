"""
SQLite database operations for knowledge storage.

Uses FTS5 for full-text search.
Per-user database files for isolation.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .parser import Note, Observation, Relation

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """Database entity representation."""
    id: int
    name: str
    permalink: str
    file_path: str
    created_at: datetime
    updated_at: datetime


@dataclass
class EntityWithData:
    """Entity with observations and relations."""
    entity: Entity
    observations: List[Observation] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)


class KnowledgeDB:
    """
    SQLite database for knowledge storage with FTS5 search.
    
    Uses per-user database files for isolation:
    - ~/.myclaw/knowledge_{user_id}.db
    """
    
    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.db_path = Path.home() / ".myclaw" / f"knowledge_{user_id}.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        
        # Core entities table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                permalink TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Observations (facts about entities)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY,
                entity_id INTEGER NOT NULL,
                category TEXT,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
            )
        """)
        
        # Relations between entities (knowledge graph)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY,
                from_entity_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                to_entity_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (from_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (to_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                UNIQUE(from_entity_id, relation_type, to_entity_id)
            )
        """)
        
        # FTS5 for full-text search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                name,
                content,
                content='entities',
                content_rowid='id'
            )
        """)
        
        # Triggers to keep FTS index in sync
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_ai AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, content)
                VALUES (new.id, new.name, new.file_path);
            END
        """)
        
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_ad AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, content)
                VALUES ('delete', old.id, old.name, old.file_path);
            END
        """)
        
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_au AFTER UPDATE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, content)
                VALUES ('delete', old.id, old.name, old.file_path);
                INSERT INTO entities_fts(rowid, name, content)
                VALUES (new.id, new.name, new.file_path);
            END
        """)
        
        # Indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_permalink ON entities(permalink)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)")
        
        conn.commit()
        logger.info(f"Knowledge DB initialized: {self.db_path}")
    
    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Knowledge DB connection closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def create_entity(self, name: str, permalink: str, file_path: str) -> int:
        """
        Create a new entity.
        
        Args:
            name: Display name
            permalink: Unique identifier (URL-friendly)
            file_path: Path to markdown file
            
        Returns:
            Entity ID
        """
        conn = self._get_connection()
        now = datetime.now().isoformat()
        
        try:
            cursor = conn.execute(
                """
                INSERT INTO entities (name, permalink, file_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, permalink, str(file_path), now, now)
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError as e:
            logger.error(f"Entity already exists: {permalink}")
            raise ValueError(f"Entity with permalink '{permalink}' already exists") from e
    
    def get_entity_by_permalink(self, permalink: str) -> Optional[Entity]:
        """Get entity by permalink."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM entities WHERE permalink = ?",
            (permalink,)
        ).fetchone()
        
        if row:
            return Entity(
                id=row['id'],
                name=row['name'],
                permalink=row['permalink'],
                file_path=row['file_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at'])
            )
        return None
    
    def get_entity_by_id(self, entity_id: int) -> Optional[Entity]:
        """Get entity by ID."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM entities WHERE id = ?",
            (entity_id,)
        ).fetchone()
        
        if row:
            return Entity(
                id=row['id'],
                name=row['name'],
                permalink=row['permalink'],
                file_path=row['file_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at'])
            )
        return None
    
    def update_entity_timestamp(self, entity_id: int):
        """Update the updated_at timestamp."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (now, entity_id)
        )
        conn.commit()
    
    def delete_entity(self, permalink: str) -> bool:
        """Delete entity and all related data (cascades)."""
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM entities WHERE permalink = ?",
            (permalink,)
        )
        conn.commit()
        return cursor.rowcount > 0
    
    def add_observation(self, entity_id: int, category: str, content: str, tags: List[str] = None):
        """Add an observation to an entity."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        tags_json = json.dumps(tags or [])
        
        conn.execute(
            """
            INSERT INTO observations (entity_id, category, content, tags, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity_id, category, content, tags_json, now)
        )
        conn.commit()
    
    def get_observations(self, entity_id: int) -> List[Observation]:
        """Get all observations for an entity."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at",
            (entity_id,)
        ).fetchall()
        
        observations = []
        for row in rows:
            tags = json.loads(row['tags'] or '[]')
            observations.append(Observation(
                category=row['category'] or '',
                content=row['content'],
                tags=tags
            ))
        return observations
    
    def clear_observations(self, entity_id: int):
        """Clear all observations for an entity."""
        conn = self._get_connection()
        conn.execute("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
        conn.commit()
    
    def add_relation(self, from_entity_id: int, relation_type: str, to_entity_id: int):
        """Add a relation between entities."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        
        try:
            conn.execute(
                """
                INSERT INTO relations (from_entity_id, relation_type, to_entity_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_entity_id, relation_type, to_entity_id, now)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Relation already exists, ignore
            pass
    
    def get_relations_from(self, entity_id: int) -> List[tuple]:
        """
        Get all relations where entity is the source.
        
        Returns:
            List of (relation_type, target_permalink, target_name) tuples
        """
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT r.relation_type, e.permalink, e.name
            FROM relations r
            JOIN entities e ON r.to_entity_id = e.id
            WHERE r.from_entity_id = ?
            """,
            (entity_id,)
        ).fetchall()
        
        return [(row['relation_type'], row['permalink'], row['name']) for row in rows]
    
    def get_relations_to(self, entity_id: int) -> List[tuple]:
        """
        Get all relations where entity is the target.
        
        Returns:
            List of (relation_type, source_permalink, source_name) tuples
        """
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT r.relation_type, e.permalink, e.name
            FROM relations r
            JOIN entities e ON r.from_entity_id = e.id
            WHERE r.to_entity_id = ?
            """,
            (entity_id,)
        ).fetchall()
        
        return [(row['relation_type'], row['permalink'], row['name']) for row in rows]
    
    def clear_relations(self, entity_id: int):
        """Clear all relations for an entity (both directions)."""
        conn = self._get_connection()
        conn.execute(
            "DELETE FROM relations WHERE from_entity_id = ? OR to_entity_id = ?",
            (entity_id, entity_id)
        )
        conn.commit()
    
    def search_fts(self, query: str, limit: int = 10) -> List[Entity]:
        """
        Full-text search using FTS5.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of matching entities
        """
        conn = self._get_connection()
        
        # Use FTS5 match syntax
        rows = conn.execute(
            """
            SELECT e.* FROM entities e
            JOIN entities_fts fts ON e.id = fts.rowid
            WHERE entities_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit)
        ).fetchall()
        
        entities = []
        for row in rows:
            entities.append(Entity(
                id=row['id'],
                name=row['name'],
                permalink=row['permalink'],
                file_path=row['file_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at'])
            ))
        return entities
    
    def search_by_tag(self, tag: str) -> List[Entity]:
        """Search entities by tag in observations."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT DISTINCT e.* FROM entities e
            JOIN observations o ON e.id = o.entity_id
            WHERE json_extract(o.tags, '$') LIKE ?
            """,
            (f'%"{tag}"%',)
        ).fetchall()
        
        entities = []
        for row in rows:
            entities.append(Entity(
                id=row['id'],
                name=row['name'],
                permalink=row['permalink'],
                file_path=row['file_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at'])
            ))
        return entities
    
    def list_all_entities(self) -> List[Entity]:
        """List all entities."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY updated_at DESC"
        ).fetchall()
        
        entities = []
        for row in rows:
            entities.append(Entity(
                id=row['id'],
                name=row['name'],
                permalink=row['permalink'],
                file_path=row['file_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at'])
            ))
        return entities
    
    def get_stats(self) -> Dict[str, int]:
        """Get database statistics."""
        conn = self._get_connection()
        
        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        observation_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        relation_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        
        return {
            "entities": entity_count,
            "observations": observation_count,
            "relations": relation_count
        }
    
    def sync_entity_from_note(self, note: Note) -> int:
        """
        Sync a Note object to the database.
        
        Args:
            note: Parsed Note object
            
        Returns:
            Entity ID
        """
        # Check if entity exists
        existing = self.get_entity_by_permalink(note.permalink)
        
        if existing:
            entity_id = existing.id
            # Clear old data
            self.clear_observations(entity_id)
            self.clear_relations(entity_id)
            # Update timestamp
            self.update_entity_timestamp(entity_id)
        else:
            # Create new entity
            entity_id = self.create_entity(
                name=note.name,
                permalink=note.permalink,
                file_path=str(note.file_path) if note.file_path else f"{note.permalink}.md"
            )
        
        # Add observations
        for obs in note.observations:
            self.add_observation(entity_id, obs.category, obs.content, obs.tags)
        
        # Add relations (resolve permalinks to IDs)
        for rel in note.relations:
            target = self.get_entity_by_permalink(rel.target)
            if target:
                self.add_relation(entity_id, rel.relation_type, target.id)
            else:
                # Create placeholder for missing target
                logger.warning(f"Relation target not found: {rel.target}")
        
        return entity_id
