"""
SQLite database operations for knowledge storage.

Uses FTS5 for full-text search with BM25 ranking.
Per-user database files for isolation.
"""

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable, Tuple, Iterable

from .parser import Note, Observation, Relation

logger = logging.getLogger(__name__)

# BM25 default parameters for relevance tuning
BM25_DEFAULT_K1: float = 1.2  # Term frequency saturation
BM25_DEFAULT_B: float = 0.75  # Field length normalization


def _row_get(row, key, default=None):
    """Safely get a value from a sqlite3.Row (which lacks .get())."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


# ── FTS5 Query Sanitization (Phase 1.5) ──────────────────────────────────────

_FTS_RESERVED = {"NOT", "NEAR", "AND", "OR"}
_FTS_RESERVED_RE = re.compile(r"\b(NOT|NEAR|AND|OR)\b", re.IGNORECASE)


def sanitize_fts_query(query: str, any_term: bool = False, boolean: bool = False) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Fixed 2026-10-13 (v0.7.1): the old version only removed quotes and the
    words AND/OR/NOT/NEAR, so ordinary text could still be read as FTS5
    syntax — ``write: a file`` became a column filter ("no such column:
    write"), ``a-b`` too, and ``c++`` or brackets were syntax errors. Now every
    word is quoted as a plain term, so no user text is ever parsed as syntax:

        write: a file   ->  "write" "a" "file"
        how-to (C++)    ->  "how" "to" "C"
        pyth*           ->  "pyth"*        (a trailing * still means prefix)

    Terms are combined with FTS5's implicit AND, as before; with
    ``any_term=True`` they are joined with OR (used as a fallback for long
    sentences, ranked by relevance).

    Returns:
        The MATCH expression, or ``"*"`` when there is nothing to search for
        (callers treat ``"*"`` as "no text filter"; it is not valid in MATCH).
    """
    if not query or not query.strip():
        return "*"
    if boolean:
        return _sanitize_boolean_query(query)
    terms = []
    for m in _FTS_TERM_RE.finditer(query):
        word, star = m.group(1), m.group(2)
        if _FTS_RESERVED_RE.fullmatch(word):
            continue  # a bare AND/OR/NOT/NEAR adds nothing as a search term
        terms.append(f'"{word}"' + ("*" if star else ""))
    terms = terms[:_FTS_MAX_TERMS]
    return (" OR " if any_term else " ").join(terms) if terms else "*"


# Words = runs of letters/digits/underscore in any script; optional trailing *.
_FTS_TERM_RE = re.compile(r"(\w+)(\*)?", re.UNICODE)
_FTS_MAX_TERMS = 32

# 2026-10-23 (KB phase 0): boolean search syntax. A token is a "quoted phrase"
# (an unbalanced quote runs to the end of the text) or a word; both may end in *.
_FTS_BOOL_TOKEN_RE = re.compile(r'"([^"]*)(?:"|$)(\*)?|(\w+)(\*)?', re.UNICODE)
_FTS_WORD_RE = re.compile(r"\w+", re.UNICODE)
_FTS_BOOL_OPS = ("AND", "OR", "NOT")


def _sanitize_boolean_query(query: str) -> str:
    """``sanitize_fts_query(..., boolean=True)``: keep AND / OR / NOT and phrases.

    Only UPPERCASE ``AND``/``OR``/``NOT`` between two terms are operators
    (lowercase ones are dropped like in free text). Every word and phrase is
    re-quoted from its word characters only, so nothing else can be read as
    FTS5 syntax. Operators with no term on one side are dropped; a run of
    operators keeps the first one (``AND NOT`` becomes ``NOT``, the FTS5 form).
    A leading ``NOT x`` drops ``x`` too — FTS5 has no unary NOT, and searching
    for the excluded word would return the opposite of what was asked.
    """
    items: List[Tuple[str, str]] = []  # ("op", "AND") or ("term", '"word"*')
    for m in _FTS_BOOL_TOKEN_RE.finditer(query):
        phrase, phrase_star, word, word_star = m.group(1), m.group(2), m.group(3), m.group(4)
        if word is not None:
            if word in _FTS_BOOL_OPS:
                items.append(("op", word))
            elif not _FTS_RESERVED_RE.fullmatch(word):
                items.append(("term", f'"{word}"' + ("*" if word_star else "")))
            continue
        words = _FTS_WORD_RE.findall(phrase or "")
        if words:
            items.append(("term", '"' + " ".join(words) + '"' + ("*" if phrase_star else "")))

    out: List[str] = []
    pending_op: Optional[str] = None
    skip_term = False
    n_terms = 0
    for kind, value in items:
        if kind == "op":
            if not out:  # leading operator
                if value == "NOT":
                    skip_term = True
                continue
            if pending_op is None:
                pending_op = value
            elif pending_op == "AND" and value == "NOT":
                pending_op = "NOT"
            continue
        if skip_term:
            skip_term = False
            pending_op = None
            continue
        if n_terms >= _FTS_MAX_TERMS:
            break
        if out and pending_op:
            out.append(pending_op)
        out.append(value)
        n_terms += 1
        pending_op = None  # a trailing operator is never emitted
    return " ".join(out) if out else "*"


_BOOLEAN_SYNTAX_RE = re.compile(r'"|\b(?:AND|OR|NOT|NEAR)\b')


def _has_boolean_syntax(query: str) -> bool:
    """An UPPERCASE AND/OR/NOT/NEAR or a double quote (2026-10-24)."""
    return bool(query) and bool(_BOOLEAN_SYNTAX_RE.search(query))


# ── Link-target helpers (2026-10-23, KB phase 0) ─────────────────────────────

def _fold(text: Any) -> str:
    """Case-insensitive key for names, titles, aliases and link targets."""
    return str(text).strip().casefold() if text is not None else ""


def _slug(text: Any) -> str:
    """Same rules as ``storage.validate_permalink`` (re-implemented: no circular import)."""
    cleaned = str(text or "").lower().strip()
    cleaned = re.sub(r"[^\w\-]", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-")


def _link_candidates(name: Any) -> List[str]:
    """The texts a link target may mean, most literal first.

    ``[[Target|label]]`` (Obsidian display text), ``[[note.md]]`` and
    ``[[Note#Section]]`` point at ``Target`` / ``note`` / ``Note``.
    """
    raw = str(name or "").strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2].strip()
    out: List[str] = []

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in out:
            out.append(text)

    add(raw)
    base = raw.split("|", 1)[0].strip()
    add(base)
    if base.lower().endswith(".md"):
        base = base[:-3]
        add(base)
    # 2026-10-24 (KB phase 0 review): only "Note#Heading" (no space on either
    # side of the first "#") is a section link — "C# tips" and "C#" are names.
    head, sep, section = base.partition("#")
    if sep and head.strip() and section and not head[-1].isspace() \
            and not section[0].isspace():
        add(head)
    return out


# Characters that carry meaning in a name: a target containing one is never
# matched by its slug ("C#" / "C++" must not become note "c"). 2026-10-24.
_SLUG_UNSAFE = set("#+@&%$*=<>|~^")


def _target_slug(text: Any) -> str:
    """The slug of a link target, or "" when slugging would change its meaning."""
    text = str(text or "")
    if any(ch in _SLUG_UNSAFE for ch in text):
        return ""
    return _slug(text)


def _link_keys(to_name: Any) -> Tuple[str, str, str]:
    """(key, alt, slug) of a link text, for indexed lookups of links.

    key = folded target (``[[X|label]]`` → x), alt = folded target without
    ".md" / "#Heading", slug = slug of the target ("" if unsafe).
    """
    raw = str(to_name or "").strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2]
    base = raw.split("|", 1)[0].strip()
    cands = _link_candidates(base) if base else [base]
    return _fold(base), _fold(cands[-1]), _target_slug(base)


def _as_str_list(value: Any, strip_hash: bool = True) -> List[str]:
    """Frontmatter lists may be a list or a single "a, b" string."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    out: List[str] = []
    try:
        for v in value:
            text = str(v).strip() if v is not None else ""
            if strip_hash:
                text = text.lstrip("#").strip()
            if text and text not in out:
                out.append(text)
    except TypeError:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _sql_fold(value: Any) -> Optional[str]:
    return value.strip().casefold() if isinstance(value, str) else value


# search_by_metadata keys: names, dotted paths allowed (2026-10-23, KB phase 0)
_METADATA_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


@dataclass
class EntityAlias:
    """Alternative name for an entity."""
    id: int
    entity_id: int
    alias: str
    created_at: datetime


@dataclass
class Entity:
    """Database entity representation."""

    id: int
    name: str
    permalink: str
    file_path: str
    created_at: datetime
    updated_at: datetime
    checksum: Optional[str] = None
    mtime: Optional[float] = None
    size: Optional[int] = None
    entity_metadata: Optional[Dict[str, Any]] = None  # Phase 3: frontmatter metadata for search_by_metadata
    # 2026-10-23 (KB phase 0): the note title, so links by title ("[[Home server]]") resolve.
    # None = indexed by an older version (the next sync re-indexes the note).
    title: Optional[str] = None


def _row_to_entity(row) -> Entity:
    """One sqlite3.Row of ``entities`` → Entity (2026-10-23: shared by every query)."""
    raw_meta = _row_get(row, "entity_metadata")
    meta: Optional[Dict[str, Any]] = None
    if raw_meta:
        try:
            loaded = json.loads(raw_meta)
            meta = loaded if isinstance(loaded, dict) else None
        except (TypeError, ValueError):
            meta = None  # hand-edited/broken JSON must not break listing notes
    return Entity(
        id=row["id"],
        name=row["name"],
        permalink=row["permalink"],
        file_path=row["file_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        checksum=_row_get(row, "checksum"),
        mtime=_row_get(row, "mtime"),
        size=_row_get(row, "size"),
        entity_metadata=meta,
        title=_row_get(row, "title"),
    )


@dataclass
class EntityWithData:
    """Entity with observations and relations."""

    entity: Entity
    observations: List[Observation] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)


_TEXT_BACKFILLED: set = set()  # db files whose old notes were indexed (v0.7.5)


class KnowledgeDB:
    """
    SQLite database for knowledge storage with FTS5 search.

    Uses per-user database files for isolation:
    - ~/.myclaw/knowledge_{user_id}.db
    """

    def __init__(self, user_id: str = "default", db_path: Optional[Path] = None):
        from .scope import kb_user, migrate_internal_scopes

        user_id = kb_user(user_id)  # v0.7.5: internal ids share one knowledge base
        if db_path is None:
            try:
                migrate_internal_scopes()  # once: old 'webui' notes into the shared base
            except Exception as e:
                logger.warning("Knowledge: could not move old notes: %s", e)
        self.user_id = user_id
        self.db_path = db_path or (Path.home() / ".myclaw" / f"knowledge_{user_id}.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: Optional[sqlite3.Connection] = None
        self._batch_depth = 0  # 2026-10-24: >0 inside batch() (one big transaction)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            self._conn.row_factory = sqlite3.Row
            # 2026-10-23 (KB phase 0): wait for a writer (sync, web UI, agent)
            # instead of failing at once with "database is locked".
            self._conn.execute("PRAGMA busy_timeout=30000")
            # WAL + NORMAL: no corruption on power loss, far fewer fsyncs (one
            # commit per note re-indexed).
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # Unicode case folding for names/titles/aliases/tags (queries only —
            # never used in triggers or indexes, so other tools can open the file).
            self._conn.create_function("kb_fold", 1, _sql_fold, deterministic=True)
            # Enable WAL mode for better concurrent access
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # Configure auto-checkpoint to happen less frequently (every 1000 pages)
            # We will manually checkpoint periodically for better control
            self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        return self._conn

    def checkpoint_wal(self, mode: str = "PASSIVE") -> tuple:
        """
        Perform WAL checkpoint to prevent unbounded WAL file growth.

        Args:
            mode: Checkpoint mode - PASSIVE (default), FULL, RESTART, or TRUNCATE

        Returns:
            Tuple of (return_code, pages_checkpointed, pages_in_wal)
            return_code: 0=checkpointed, 1=busy, 2=locked, 3=read-only
        """
        conn = self._get_connection()
        result = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return (result[0], result[1], result[2])

    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()

        # Core entities table
        # Phase 1 (MemoPad import): added checksum, mtime, size for robust change detection
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                permalink TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT,
                mtime REAL,
                size INTEGER
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

        # Phase 1 (MemoPad import): migrate existing databases by adding new columns
        try:
            conn.execute("ALTER TABLE entities ADD COLUMN checksum TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE entities ADD COLUMN mtime REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE entities ADD COLUMN size INTEGER")
        except sqlite3.OperationalError:
            pass
        # Phase 3 (MemoPad import): metadata column for structured search
        try:
            conn.execute("ALTER TABLE entities ADD COLUMN entity_metadata TEXT")
        except sqlite3.OperationalError:
            pass
        # 2026-10-23 (KB phase 0): the note title (links by title resolve) and
        # the link text of each relation (kept when the target note is deleted).
        try:
            conn.execute("ALTER TABLE entities ADD COLUMN title TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE relations ADD COLUMN to_name TEXT")
        except sqlite3.OperationalError:
            pass
        # 2026-10-23 (KB phase 0): links whose target note does not exist yet
        # (forward links). Resolved into relations when the target is indexed.
        # to_key / to_slug: folded / slugged link text; to_alt: folded text
        # without ".md" / "#Section" — all indexed, for lookup.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_relations (
                id INTEGER PRIMARY KEY,
                from_entity_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                to_name TEXT NOT NULL,
                to_key TEXT NOT NULL DEFAULT '',
                to_slug TEXT NOT NULL DEFAULT '',
                to_alt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (from_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                UNIQUE(from_entity_id, relation_type, to_name)
            )
        """)
        for col in ("to_key", "to_slug", "to_alt"):  # tables made by earlier drafts
            try:
                conn.execute(f"ALTER TABLE pending_relations ADD COLUMN {col} "
                             "TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_from ON pending_relations(from_entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_key ON pending_relations(to_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_slug ON pending_relations(to_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_alt ON pending_relations(to_alt)")

        # Create indexes for better query performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_permalink ON entities(permalink)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")
        # Phase 1 (MemoPad import): indexes for change detection fields
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_checksum ON entities(checksum)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_file_path ON entities(file_path)")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_entity_id ON observations(entity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_category ON observations(category)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_entity_id)")

        # Optimization 3.2: Composite indexes for graph queries
        # Note: idx_entities_name already created on entities(name) above
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_from_type ON relations(from_entity_id, relation_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_to_type ON relations(to_entity_id, relation_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_entity_category ON observations(entity_id, category)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)")

        # FTS5 for full-text search on entities
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                name,
                content,
                content='entities',
                content_rowid='id'
            )
        """)

        # FTS5 for full-text search on observations (optimization 3.1)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
                content,
                category,
                content='observations',
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

        # v0.7.5: the note text itself. entities_fts indexes the name and the
        # file path only, so a note could be found by its title or its
        # observations but never by what it says (most notes agents write are
        # plain text) — agents concluded the knowledge base was empty.
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_text_fts USING fts5(title, body, tags)
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_text_ad AFTER DELETE ON entities BEGIN
                DELETE FROM notes_text_fts WHERE rowid = old.id;
            END
        """)

        # Triggers to keep observations FTS index in sync (optimization 3.1)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END
        """)

        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
            END
        """)

        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
                INSERT INTO observations_fts(rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END
        """)

        # Phase 2 (MemoPad import): EntityAlias table for alternative names
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY,
                entity_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                UNIQUE(entity_id, alias)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity_id ON entity_aliases(entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias ON entity_aliases(alias)")
        # 2026-10-24 (KB phase 0 review): Unicode case-folded copies of the
        # names a link can use, indexed (kb_fold scans were quadratic), and
        # the folded link text of each relation (to re-resolve links when a
        # note is renamed / retitled / added). Filled by the Python code.
        for table, col in (("entities", "permalink_fold"), ("entities", "name_fold"),
                           ("entities", "title_fold"), ("entity_aliases", "alias_fold"),
                           ("relations", "to_key"), ("relations", "to_alt"),
                           ("relations", "to_slug")):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        for name, table, col in (
                ("idx_entities_permalink_fold", "entities", "permalink_fold"),
                ("idx_entities_name_fold", "entities", "name_fold"),
                ("idx_entities_title_fold", "entities", "title_fold"),
                ("idx_entity_aliases_alias_fold", "entity_aliases", "alias_fold"),
                ("idx_relations_to_key", "relations", "to_key"),
                ("idx_relations_to_alt", "relations", "to_alt"),
                ("idx_relations_to_slug", "relations", "to_slug")):
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({col})")
        # Rows written before these columns existed (or by an older version).
        conn.execute(
            "UPDATE entities SET permalink_fold = kb_fold(permalink), name_fold = kb_fold(name), "
            "title_fold = kb_fold(title) WHERE permalink_fold IS NULL OR name_fold IS NULL "
            "OR (title IS NOT NULL AND title_fold IS NULL)")
        conn.execute("UPDATE entity_aliases SET alias_fold = kb_fold(alias) "
                     "WHERE alias_fold IS NULL")
        for row in conn.execute("SELECT id, to_name FROM relations WHERE to_name IS NOT NULL "
                                "AND to_key IS NULL").fetchall():
            conn.execute("UPDATE relations SET to_key = ?, to_alt = ?, to_slug = ? WHERE id = ?",
                         (*_link_keys(row["to_name"]), row["id"]))

        # Phase 2 (MemoPad import): add context and provenance_path to observations
        try:
            conn.execute("ALTER TABLE observations ADD COLUMN context TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE observations ADD COLUMN provenance_path TEXT")
        except sqlite3.OperationalError:
            pass

        # Phase 4 (MemoPad import): sync_meta table for watermark-based incremental scanning
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.commit()
        # v0.7.5: index the text of notes written before the text index existed
        key = str(self.db_path)
        if key not in _TEXT_BACKFILLED:
            _TEXT_BACKFILLED.add(key)
            n = self.backfill_note_text()
            if n:
                logger.info(f"Knowledge: indexed the text of {n} existing note(s)")
        logger.info(f"Knowledge DB initialized: {self.db_path}")

    def get_sync_meta(self, key: str) -> Optional[str]:
        """Get sync metadata value."""
        conn = self._get_connection()
        row = conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_sync_meta(self, key: str, value: str):
        """Set sync metadata value."""
        conn = self._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    def rebuild_fts_index(self):
        """
        Rebuild FTS5 indexes for existing data.

        This should be called when upgrading existing databases to ensure
        the observations FTS table is populated.
        """
        conn = self._get_connection()

        # Rebuild entities FTS
        conn.execute("INSERT INTO entities_fts(entities_fts) VALUES('rebuild')")

        # Rebuild observations FTS (optimization 3.1)
        conn.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")

        conn.commit()
        logger.info("FTS indexes rebuilt")

    def migrate_add_meta_type(self) -> bool:
        """Add a meta_type TEXT column backed by json_extract(entity_metadata, '$.type').

        Uses a trigger-based approach for broad SQLite compatibility (works on
        SQLite 3.7.17+, the minimum version many environments ship). The column
        plus an index lets callers filter by ``type`` without scanning the full
        JSON blob on every query.

        Returns True if a migration was applied, False if already up-to-date.
        """
        conn = self._get_connection()
        row = conn.execute(
            "SELECT sql FROM pragma_table_info('entities') WHERE name = 'meta_type'"
        ).fetchone()
        if row:
            return False

        conn.execute("ALTER TABLE entities ADD COLUMN meta_type TEXT")

        # Keep the column in sync on INSERT and UPDATE.
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_meta_type_ai
            AFTER INSERT ON entities FOR EACH ROW
            BEGIN
                UPDATE entities
                SET meta_type = json_extract(NEW.entity_metadata, '$.type')
                WHERE id = NEW.id;
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS entities_meta_type_au
            AFTER UPDATE OF entity_metadata ON entities FOR EACH ROW
            BEGIN
                UPDATE entities
                SET meta_type = json_extract(NEW.entity_metadata, '$.type')
                WHERE id = NEW.id;
            END
        """)
        # Back-fill existing rows.
        conn.execute("""
            UPDATE entities
            SET meta_type = json_extract(entity_metadata, '$.type')
            WHERE entity_metadata IS NOT NULL
              AND json_extract(entity_metadata, '$.type') IS NOT NULL
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entities_meta_type ON entities(meta_type)"
        )
        conn.commit()
        logger.info("meta_type column and index applied to entities table")
        return True

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

    def create_entity(self, name: str, permalink: str, file_path: str,
                      checksum: Optional[str] = None, mtime: Optional[float] = None,
                      size: Optional[int] = None, entity_metadata: Optional[Dict[str, Any]] = None) -> int:
        """
        Create a new entity.

        Args:
            name: Display name
            permalink: Unique identifier (URL-friendly)
            file_path: Path to markdown file
            checksum: File checksum for change detection (Phase 1 import)
            mtime: File modification time (Phase 1 import)
            size: File size in bytes (Phase 1 import)

        Returns:
            Entity ID
        """
        conn = self._get_connection()
        now = datetime.now().isoformat()

        try:
            cursor = conn.execute(
                """
                INSERT INTO entities (name, permalink, file_path, created_at, updated_at,
                                     checksum, mtime, size, entity_metadata,
                                     permalink_fold, name_fold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, permalink, str(file_path), now, now,
                 checksum, mtime, size, json.dumps(entity_metadata) if entity_metadata else None,
                 _fold(permalink), _fold(name)),
            )
            if self._batch_depth == 0:
                conn.commit()
            return int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as e:
            # 2026-10-23 (KB phase 0): caused by the caller's input → WARNING
            logger.warning(f"Entity already exists: {permalink}")
            raise ValueError(f"Entity with permalink '{permalink}' already exists") from e

    # ── Transactions (2026-10-24, KB phase 0 review) ─────────────────────────
    @contextmanager
    def _write_txn(self):
        """One write transaction, taken with BEGIN IMMEDIATE.

        The write lock is held from the first read, so two processes indexing
        the same note cannot both decide "it does not exist yet" (that gave
        "UNIQUE constraint failed"). Inside ``batch()`` it is a SAVEPOINT, so
        one failing note is undone without undoing the batch.
        """
        conn = self._get_connection()
        if self._batch_depth > 0:
            name = f"kb_sp_{self._batch_depth}"
            self._batch_depth += 1
            conn.execute(f"SAVEPOINT {name}")
            try:
                yield conn
            except BaseException:
                conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                raise
            else:
                conn.execute(f"RELEASE {name}")
            finally:
                self._batch_depth -= 1
            return
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

    @contextmanager
    def batch(self):
        """Run several index writes as ONE transaction (all or nothing).

        Readers keep seeing the previous index until the end (used by the
        full re-index). Inside, ``sync_entity_from_note``, ``delete_entity``
        and ``clear_all_data`` use savepoints instead of committing; do not
        call the other single-item methods (they commit).
        """
        conn = self._get_connection()
        if self._batch_depth > 0:
            yield self
            return
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        self._batch_depth = 1
        try:
            yield self
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            self._batch_depth = 0

    def get_entity_by_permalink(self, permalink: str) -> Optional[Entity]:
        """Get entity by permalink."""
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM entities WHERE permalink = ?", (permalink,)).fetchone()

        if row:
            return _row_to_entity(row)
        return None

    def get_entities_by_permalinks(self, permalinks: List[str]) -> Dict[str, Entity]:
        """
        Batch fetch multiple entities by their permalinks.

        Args:
            permalinks: List of entity permalinks to fetch

        Returns:
            Dict mapping permalink to Entity (only found entities)

        Performance: O(1) query instead of O(N) for N permalinks.
        """
        if not permalinks:
            return {}

        conn = self._get_connection()
        # Create placeholders for IN clause
        placeholders = ",".join("?" * len(permalinks))
        rows = conn.execute(
            f"SELECT * FROM entities WHERE permalink IN ({placeholders})", tuple(permalinks)
        ).fetchall()

        return {
            row["permalink"]: _row_to_entity(row)
            for row in rows
        }

    def get_entity_by_id(self, entity_id: int) -> Optional[Entity]:
        """Get entity by ID."""
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()

        if row:
            return _row_to_entity(row)
        return None

    def get_entity_by_file_path(self, file_path: str) -> Optional[Entity]:
        """Get entity by file path (for move detection)."""
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM entities WHERE file_path = ?", (file_path,)).fetchone()

        if row:
            return _row_to_entity(row)
        return None

    def get_entities_by_checksum(self, checksum: str) -> List[Entity]:
        """Find entities by checksum (for move/copy detection)."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM entities WHERE checksum = ?", (checksum,)
        ).fetchall()

        return [
            _row_to_entity(row)
            for row in rows
        ]

    def update_entity_timestamp(self, entity_id: int):
        """Update the updated_at timestamp."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        conn.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now, entity_id))
        conn.commit()

    def update_entity_metadata(self, entity_id: int, checksum: Optional[str] = None,
                                mtime: Optional[float] = None, size: Optional[int] = None):
        """Update file metadata for an entity (Phase 1 import from MemoPad)."""
        conn = self._get_connection()
        updates = {}
        if checksum is not None:
            updates["checksum"] = checksum
        if mtime is not None:
            updates["mtime"] = mtime
        if size is not None:
            updates["size"] = size
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [entity_id]
        conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
        conn.commit()

    def update_entity_path(self, entity_id: int, new_path: str, checksum: str, mtime: float, size: int):
        """
        Update the file path and metadata for an entity, used for move detection.
        """
        conn = self._get_connection()
        now = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE entities
            SET file_path = ?, checksum = ?, mtime = ?, size = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(new_path), checksum, mtime, size, now, entity_id),
        )
        conn.commit()

    def delete_entity(self, permalink: str) -> bool:
        """Delete entity and all related data (cascades).

        2026-10-23 (KB phase 0): links from other notes to the deleted note are
        kept as unresolved links (``get_unresolved_relations``), so they come
        back when the note is restored or re-created — the same state a full
        re-index from the files gives. A link that another note also matches
        (e.g. the same title) moves to that note, as a re-index would do.
        """
        with self._write_txn() as conn:
            row = conn.execute(
                "SELECT id FROM entities WHERE permalink = ?", (permalink,)).fetchone()
            if row is None:
                return False
            eid = row["id"]
            folds, plinks = self._entity_keys(conn, eid)
            incoming = conn.execute(
                "SELECT from_entity_id, relation_type, to_name FROM relations "
                "WHERE to_entity_id = ? AND from_entity_id != ?", (eid, eid)).fetchall()
            for rel in incoming:
                self._add_pending(conn, rel["from_entity_id"], rel["relation_type"],
                                  rel["to_name"] or permalink)
            cursor = conn.execute("DELETE FROM entities WHERE id = ?", (eid,))
            self._relink_for_keys(conn, folds, plinks)
        return cursor.rowcount > 0

    def clear_all_data(self, keep_file_paths: Optional[Iterable[str]] = None) -> None:
        """Remove every entity, observation, relation and alias in one transaction.

        Used by ``SyncEngine.sync(force=True)`` (``knowledge sync --force``).
        Child tables are cleared first so this works with or without
        ``PRAGMA foreign_keys``; the FTS delete triggers keep the full-text
        indexes in step.

        2026-10-24 (KB phase 0 review): ``keep_file_paths`` — entries of these
        files are kept (a note file that cannot be read now keeps its entry,
        as in an incremental sync); their links to removed notes become
        unresolved links, resolved again when those notes are re-added.
        """
        keep = {str(p) for p in (keep_file_paths or [])}
        with self._write_txn() as conn:
            kept: List[int] = []
            if keep:
                kept = [r["id"] for r in conn.execute("SELECT id, file_path FROM entities")
                        if r["file_path"] in keep]
            if not kept:
                conn.execute("DELETE FROM relations")
                conn.execute("DELETE FROM pending_relations")
                conn.execute("DELETE FROM observations")
                conn.execute("DELETE FROM entity_aliases")
                conn.execute("DELETE FROM entities")
                return
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS kb_keep (id INTEGER PRIMARY KEY)")
            conn.execute("DELETE FROM kb_keep")
            conn.executemany("INSERT INTO kb_keep (id) VALUES (?)", [(i,) for i in kept])
            for rel in conn.execute(
                    "SELECT r.from_entity_id, r.relation_type, "
                    "COALESCE(r.to_name, e.permalink) AS to_name FROM relations r "
                    "JOIN entities e ON e.id = r.to_entity_id "
                    "WHERE r.from_entity_id IN (SELECT id FROM kb_keep) "
                    "AND r.to_entity_id NOT IN (SELECT id FROM kb_keep)").fetchall():
                self._add_pending(conn, rel["from_entity_id"], rel["relation_type"],
                                  rel["to_name"])
            conn.execute("DELETE FROM relations WHERE from_entity_id NOT IN (SELECT id FROM kb_keep) "
                         "OR to_entity_id NOT IN (SELECT id FROM kb_keep)")
            for table, col in (("pending_relations", "from_entity_id"),
                               ("observations", "entity_id"),
                               ("entity_aliases", "entity_id"), ("entities", "id")):
                conn.execute(f"DELETE FROM {table} WHERE {col} NOT IN (SELECT id FROM kb_keep)")
            conn.execute("DELETE FROM kb_keep")

    def add_observation(self, entity_id: int, category: str, content: str, tags: List[str] = None,
                        context: Optional[str] = None, provenance_path: Optional[str] = None):
        """Add an observation to an entity."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        tags_json = json.dumps(tags or [])

        conn.execute(
            """
            INSERT INTO observations (entity_id, category, content, tags, created_at, context, provenance_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, category, content, tags_json, now, context, provenance_path),
        )
        conn.commit()

    def get_observations(self, entity_id: int) -> List[Observation]:
        """Get all observations for an entity."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at", (entity_id,)
        ).fetchall()

        observations = []
        for row in rows:
            tags = json.loads(row["tags"] or "[]")
            observations.append(
                Observation(
                    category=row["category"] or "",
                    content=row["content"],
                    tags=tags,
                    context=_row_get(row, "context"),
                    provenance_path=_row_get(row, "provenance_path"),
                )
            )
        return observations

    def clear_observations(self, entity_id: int):
        """Clear all observations for an entity."""
        conn = self._get_connection()
        conn.execute("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
        conn.commit()

    # Phase 2 (MemoPad import): EntityAlias methods
    def add_entity_alias(self, entity_id: int, alias: str) -> int:
        """Add an alias for an entity."""
        conn = self._get_connection()
        now = datetime.now().isoformat()
        try:
            cursor = conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias, created_at, alias_fold) "
                "VALUES (?, ?, ?, ?)",
                (entity_id, alias, now, _fold(alias)),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError:
            return -1

    def get_aliases_for_entity(self, entity_id: int) -> List[str]:
        """Get all aliases for an entity."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        return [row["alias"] for row in rows]

    def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
        """Find entity by alias (2026-10-23, KB phase 0: case-insensitive)."""
        conn = self._get_connection()
        row = conn.execute(
            """SELECT e.* FROM entities e
               JOIN entity_aliases ea ON e.id = ea.entity_id
               WHERE ea.alias = ?
               ORDER BY e.file_path, e.id LIMIT 1""",
            (alias,),
        ).fetchone()
        if row is None and alias:
            row = conn.execute(
                """SELECT e.* FROM entities e
                   JOIN entity_aliases ea ON e.id = ea.entity_id
                   WHERE ea.alias_fold = ?
                   ORDER BY e.file_path, e.id LIMIT 1""",
                (_fold(alias),),
            ).fetchone()
        if row:
            return _row_to_entity(row)
        return None

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
                (from_entity_id, relation_type, to_entity_id, now),
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
            (entity_id,),
        ).fetchall()

        return [(row["relation_type"], row["permalink"], row["name"]) for row in rows]

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
            (entity_id,),
        ).fetchall()

        return [(row["relation_type"], row["permalink"], row["name"]) for row in rows]

    def clear_relations(self, entity_id: int):
        """Clear all relations for an entity (both directions).

        Do not use this to re-index a note: it also deletes the links of other
        notes to this one. Use ``clear_outgoing_relations``.
        """
        conn = self._get_connection()
        conn.execute(
            "DELETE FROM relations WHERE from_entity_id = ? OR to_entity_id = ?",
            (entity_id, entity_id),
        )
        conn.commit()

    def clear_outgoing_relations(self, entity_id: int) -> None:
        """Remove the links *from* this note (resolved and unresolved).

        2026-10-23 (KB phase 0): re-indexing a note used ``clear_relations``,
        which also deleted every link from other notes to it (lost backlinks).
        """
        with self._write_txn() as conn:
            conn.execute("DELETE FROM relations WHERE from_entity_id = ?", (entity_id,))
            conn.execute("DELETE FROM pending_relations WHERE from_entity_id = ?", (entity_id,))

    # ── Link resolution (2026-10-23, KB phase 0) ─────────────────────────────
    def resolve_target(self, name: str) -> Optional[Entity]:
        """The note a link target points to, or None.

        Tried in order, each case-insensitive: exact permalink, the slug of the
        target (``"Home server"`` → ``home-server``), note name, note title,
        alias. ``[[Target|label]]``, ``[[note.md]]`` and ``[[Note#Section]]``
        are understood (``[[C# tips]]`` is a name, not a section link). No
        fuzzy matching: a wrong link is worse than none. When two notes match
        at the same level, the one with the first file path wins (the same
        answer however the index was built).
        """
        row = self._resolve_target_row(self._get_connection(), name)
        return _row_to_entity(row) if row is not None else None

    @staticmethod
    def _resolve_target_row(conn: sqlite3.Connection, name: Any):
        # 2026-10-24 (KB phase 0 review): indexed *_fold columns instead of
        # kb_fold() scans (quadratic for non-ASCII targets).
        order = " ORDER BY file_path, id LIMIT 1"
        for cand in _link_candidates(name):
            folded = _fold(cand)
            slug = _target_slug(cand)
            steps: List[Tuple[str, Any]] = [
                ("SELECT * FROM entities WHERE permalink = ? LIMIT 1", cand),
                ("SELECT * FROM entities WHERE permalink_fold = ?" + order, folded),
            ]
            if slug and slug != cand:
                steps.append(("SELECT * FROM entities WHERE permalink = ? LIMIT 1", slug))
            steps += [
                ("SELECT * FROM entities WHERE name_fold = ?" + order, folded),
                ("SELECT * FROM entities WHERE title_fold = ?" + order, folded),
                ("SELECT e.* FROM entities e JOIN entity_aliases a ON a.entity_id = e.id "
                 "WHERE a.alias_fold = ? ORDER BY e.file_path, e.id LIMIT 1", folded),
            ]
            for sql, param in steps:
                row = conn.execute(sql, (param,)).fetchone()
                if row is not None:
                    return row
        return None

    @staticmethod
    def _add_pending(conn: sqlite3.Connection, from_entity_id: int, relation_type: str,
                     to_name: str) -> None:
        key, alt, slug = _link_keys(to_name)
        conn.execute(
            "INSERT OR IGNORE INTO pending_relations "
            "(from_entity_id, relation_type, to_name, to_key, to_slug, to_alt, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (from_entity_id, relation_type, to_name, key, slug, alt,
             datetime.now().isoformat()),
        )

    @staticmethod
    def _insert_relation(conn: sqlite3.Connection, from_entity_id: int, relation_type: str,
                         to_entity_id: int, to_name: Optional[str]) -> None:
        key, alt, slug = _link_keys(to_name) if to_name else (None, None, None)
        conn.execute(
            "INSERT OR IGNORE INTO relations (from_entity_id, relation_type, to_entity_id, "
            "created_at, to_name, to_key, to_alt, to_slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (from_entity_id, relation_type, to_entity_id, datetime.now().isoformat(),
             to_name, key, alt, slug),
        )

    @staticmethod
    def _entity_keys(conn: sqlite3.Connection, entity_id: int) -> Tuple[set, set]:
        """(folded names a link may use, permalinks) of one note."""
        ent = conn.execute("SELECT permalink, name, title FROM entities WHERE id = ?",
                           (entity_id,)).fetchone()
        if ent is None:
            return set(), set()
        folds = {_fold(ent["permalink"]), _fold(ent["name"]), _fold(ent["title"])}
        folds |= {_fold(r["alias"]) for r in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,))}
        folds.discard("")
        return folds, {ent["permalink"]}

    def _relink_for_keys(self, conn: sqlite3.Connection, folds: set, permalinks: set) -> int:
        """Re-resolve every link (resolved or not) whose text may mean one of
        these names, with the normal priority rules.

        2026-10-24 (KB phase 0 review): called whenever a note is (re)indexed
        or deleted, with its old and new names, so the index does not depend
        on the order notes were indexed in: a retitled note loses links by
        its old title (they go to another note with that title, or become
        unresolved), and a new note with a better match (a permalink beats an
        alias) takes links over. Returns the number of links changed.
        """
        folds = {f for f in folds if f}
        permalinks = {p for p in permalinks if p}
        if not folds and not permalinks:
            return 0
        fph = ",".join("?" * len(folds)) or "NULL"
        pph = ",".join("?" * len(permalinks)) or "NULL"
        params = (*folds, *folds, *permalinks)
        changed = 0
        for table in ("relations", "pending_relations"):
            cols = ("id, from_entity_id, relation_type, to_name"
                    + (", to_entity_id" if table == "relations" else ""))
            rows = conn.execute(
                f"SELECT {cols} FROM {table} WHERE to_key IN ({fph}) "
                f"UNION SELECT {cols} FROM {table} WHERE to_alt IN ({fph}) "
                f"UNION SELECT {cols} FROM {table} WHERE to_slug IN ({pph})",
                params).fetchall()
            for r in rows:
                if not r["to_name"]:
                    continue
                target = self._resolve_target_row(conn, r["to_name"])
                if table == "relations":
                    if target is not None and target["id"] == r["to_entity_id"]:
                        continue
                    conn.execute("DELETE FROM relations WHERE id = ?", (r["id"],))
                    if target is None:
                        self._add_pending(conn, r["from_entity_id"], r["relation_type"],
                                          r["to_name"])
                    else:
                        self._insert_relation(conn, r["from_entity_id"], r["relation_type"],
                                              target["id"], r["to_name"])
                    changed += 1
                elif target is not None:
                    self._insert_relation(conn, r["from_entity_id"], r["relation_type"],
                                          target["id"], r["to_name"])
                    conn.execute("DELETE FROM pending_relations WHERE id = ?", (r["id"],))
                    changed += 1
        return changed

    def resolve_pending_relations(self) -> Dict[str, int]:
        """Resolve every unresolved link whose target note exists now.

        Returns ``{"resolved": n, "unresolved": m}``.
        """
        resolved = 0
        with self._write_txn() as conn:
            rows = conn.execute(
                "SELECT id, from_entity_id, relation_type, to_name FROM pending_relations"
            ).fetchall()
            for p in rows:
                target = self._resolve_target_row(conn, p["to_name"])
                if target is None:
                    continue
                self._insert_relation(conn, p["from_entity_id"], p["relation_type"],
                                      target["id"], p["to_name"])
                conn.execute("DELETE FROM pending_relations WHERE id = ?", (p["id"],))
                resolved += 1
            left = conn.execute("SELECT COUNT(*) FROM pending_relations").fetchone()[0]
        return {"resolved": resolved, "unresolved": left}

    def get_unresolved_relations(self, entity_id: int) -> List[Tuple[str, str]]:
        """``(relation_type, target_name)`` of this note's links whose target
        note does not exist yet (2026-10-23, KB phase 0)."""
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT relation_type, to_name FROM pending_relations "
            "WHERE from_entity_id = ? ORDER BY id", (entity_id,)).fetchall()
        return [(row["relation_type"], row["to_name"]) for row in rows]

    def get_relations_from_many(self, entity_ids: List[int]) -> Dict[int, List[tuple]]:
        """
        Batch fetch all outgoing relations for multiple entities in one query.

        Args:
            entity_ids: List of entity IDs to fetch relations for.

        Returns:
            Dict mapping entity_id to a list of (relation_type, target_permalink, target_name) tuples.
        """
        if not entity_ids:
            return {}

        conn = self._get_connection()
        placeholders = ",".join("?" * len(entity_ids))
        sql = f"""
            SELECT r.from_entity_id, r.relation_type, e.permalink, e.name
            FROM relations r
            JOIN entities e ON r.to_entity_id = e.id
            WHERE r.from_entity_id IN ({placeholders})
        """
        rows = conn.execute(sql, tuple(entity_ids)).fetchall()

        results: Dict[int, List[tuple]] = {eid: [] for eid in entity_ids}
        for row in rows:
            results[row['from_entity_id']].append(
                (row['relation_type'], row['permalink'], row['name'])
            )
        return results

    def search_fts(self, query: str, limit: int = 10, boolean: bool = False) -> List[Entity]:
        """
        Full-text search using FTS5 with BM25 ranking.

        Searches both entity names/paths and observation content for more relevant results.
        Uses rank_bm25() for configurable BM25 parameters.

        Args:
            query: Search query (will be sanitized for FTS5 safety)
            limit: Maximum results
            boolean: 2026-10-23 (KB phase 0) — keep AND / OR / NOT and
                "quoted phrases" as search syntax (see ``sanitize_fts_query``).
                The whole note (title, text, tags) is matched as one document,
                so ``a NOT b`` excludes notes that mention b anywhere.

        Returns:
            List of matching entities sorted by BM25 relevance
        """
        conn = self._get_connection()

        # 2026-10-24 (KB phase 0 review): without an operator or a quoted
        # phrase a "boolean" query is plain text (the agent often passes a
        # sentence) — same search, including the any-word fallback.
        if boolean and not _has_boolean_syntax(query):
            boolean = False

        # Phase 1.5: Sanitize user input to prevent FTS5 injection
        safe_query = sanitize_fts_query(query, boolean=boolean)
        if safe_query == "*":
            return []  # no searchable words (MATCH "*" is an FTS5 error)

        rows = self._match_entities(conn, safe_query, limit, boolean=boolean)
        # v0.7.1: a whole sentence (the agent searches with the user's
        # message) rarely has every word in one note. If the strict search
        # finds nothing, accept notes with any of the words, best match first.
        # Not for boolean queries: there the user chose AND/OR on purpose.
        if not boolean and not rows and safe_query.count(" ") >= 3:
            rows = self._match_entities(conn, sanitize_fts_query(query, any_term=True), limit)

        entities = []
        seen_ids = set()
        for row in rows:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                entities.append(_row_to_entity(row))
        return entities

    @staticmethod
    def _match_entities(conn, q: str, limit: int, boolean: bool = False):
        """Run the FTS5 entity search for an already-sanitized expression.

        ``boolean=True``: match the note text index (title + text + tags as one
        document) so NOT/AND apply to the whole note; notes without a text
        index row (created directly with ``create_entity``) are matched on
        their name/path instead.
        """
        if boolean:
            try:
                return conn.execute(
                    """
                    SELECT e.*, fts.rank AS match_rank
                    FROM entities e
                    JOIN notes_text_fts fts ON e.id = fts.rowid
                    WHERE notes_text_fts MATCH ?

                    UNION

                    SELECT e.*, fts.rank AS match_rank
                    FROM entities e
                    JOIN entities_fts fts ON e.id = fts.rowid
                    WHERE entities_fts MATCH ?
                      AND e.id NOT IN (SELECT rowid FROM notes_text_fts)

                    ORDER BY match_rank
                    LIMIT ?
                    """,
                    (q, q, limit),
                ).fetchall()
            except sqlite3.OperationalError as e:
                logger.debug("boolean note-text search unavailable: %s", e)
                # fall through to the entities-only search below
        # Try enhanced search with observations (optimization 3.1)
        # Fall back to entities-only search for backward compatibility
        try:
            if boolean:
                raise sqlite3.OperationalError("boolean: entities only")
            # PERF FIX (2026-04-23): Use UNION of two independent FTS5 subqueries
            # instead of LEFT JOINs. The old query produced a Cartesian product
            # when entities had many observations, causing exponential slowdown.
            rows = conn.execute(
                """
                SELECT e.*, fts.rank AS match_rank
                FROM entities e
                JOIN entities_fts fts ON e.id = fts.rowid
                WHERE entities_fts MATCH ?

                UNION

                SELECT e.*, fts.rank AS match_rank
                FROM entities e
                JOIN observations o ON e.id = o.entity_id
                JOIN observations_fts fts ON o.id = fts.rowid
                WHERE observations_fts MATCH ?

                UNION

                SELECT e.*, fts.rank AS match_rank
                FROM entities e
                JOIN notes_text_fts fts ON e.id = fts.rowid
                WHERE notes_text_fts MATCH ?

                ORDER BY match_rank
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fall back to entities-only search for existing databases
            # without observations FTS table
            # Optimization: Use rank column instead of bm25() function
            rows = conn.execute(
                """
                SELECT e.* FROM entities e
                JOIN entities_fts fts ON e.id = fts.rowid
                WHERE entities_fts MATCH ?
                ORDER BY fts.rank
                LIMIT ?
                """,
                (q, limit),
            ).fetchall()

        return rows

    def rank_bm25(
        self, fts_table: str, query: str, k1: float = BM25_DEFAULT_K1, b: float = BM25_DEFAULT_B
    ) -> float:
        """
        Calculate BM25 ranking score for a query against an FTS table.

        This is a helper method that can be used to understand BM25 scoring.
        The actual ranking is done in SQL for performance.

        Args:
            fts_table: Name of the FTS virtual table
            query: Search query
            k1: Term frequency saturation parameter (default 1.2)
            b: Field length normalization parameter (default 0.75)

        Returns:
            BM25 score (lower is better)
        """
        # This method exists for documentation purposes and potential future use
        # The actual BM25 calculation happens in the SQL queries above
        # where we use bm25(fts_table, k1, b) for custom parameters
        return 0.0

    def search_by_tag(self, tag: str) -> List[Entity]:
        """Notes with this tag: frontmatter ``tags:`` or an observation ``#tag``.

        2026-10-23 (KB phase 0): frontmatter tags were not searched, and the
        match was a substring of the JSON (``py`` found ``python``). Now the
        whole tag must match, case-insensitively; a leading ``#`` is ignored.
        """
        key = _fold(str(tag or "").strip().lstrip("#"))
        if not key:
            return []
        conn = self._get_connection()
        ids: set = set()
        ids.update(r[0] for r in conn.execute(
            """
            SELECT DISTINCT o.entity_id FROM observations o,
                   json_each(CASE WHEN json_valid(o.tags) THEN o.tags ELSE '[]' END) j
            WHERE j.type = 'text' AND kb_fold(ltrim(j.value, '#')) = ?
            """, (key,)))
        ids.update(r[0] for r in conn.execute(
            """
            SELECT DISTINCT e.id FROM entities e,
                   json_each(CASE WHEN json_valid(e.entity_metadata)
                             THEN e.entity_metadata ELSE '{}' END, '$.tags') j
            WHERE j.type = 'text' AND kb_fold(ltrim(j.value, '#')) = ?
            """, (key,)))
        # Notes indexed before their tags were stored in entity_metadata: use
        # the tags column of the note text index (space-separated tags).
        try:
            for r in conn.execute(
                """
                SELECT f.rowid, f.tags FROM notes_text_fts f
                JOIN entities e ON e.id = f.rowid
                WHERE e.entity_metadata IS NULL OR NOT json_valid(e.entity_metadata)
                   OR json_type(e.entity_metadata, '$.tags') IS NULL
                """):
                if key in {_fold(t.lstrip("#")) for t in (r["tags"] or "").split()}:
                    ids.add(r["rowid"])
        except sqlite3.OperationalError:
            pass  # FTS5 not available
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM entities WHERE id IN ({ph}) ORDER BY updated_at DESC",
            tuple(ids)).fetchall()
        return [_row_to_entity(row) for row in rows]

    def search_by_metadata(self, filters: Dict[str, Any]) -> List[Entity]:
        """Search entities by frontmatter metadata fields.

        Keys are field names or dotted paths (``status``, ``project.owner``).
        2026-10-23 (KB phase 0): the key was pasted into the SQL JSON path; now
        it is checked and passed as a parameter. Raises ``ValueError`` for a
        key that is not letters/digits/``_``/``-`` separated by dots.
        """
        conn = self._get_connection()
        where_clauses = []
        params: List[Any] = []
        for key, value in filters.items():
            key = str(key)
            if not _METADATA_KEY_RE.match(key):
                raise ValueError(
                    f"Invalid metadata key {key!r}: use letters, digits, '_' or '-', "
                    "with '.' between nested fields (e.g. 'status' or 'project.owner')")
            path = "$." + ".".join(f'"{part}"' for part in key.split("."))
            where_clauses.append(
                "(CASE WHEN json_valid(entity_metadata) "
                "THEN json_extract(entity_metadata, ?) END) = ?")
            params.extend([path, value])
        if not where_clauses:
            return self.list_all_entities()
        sql = f"SELECT * FROM entities WHERE {' AND '.join(where_clauses)}"
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_entity(row) for row in rows]

    def get_backlinks(self, entity_id: int) -> List[Entity]:
        """Find all entities that link TO the given entity."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT e.* FROM entities e
            JOIN relations r ON e.id = r.from_entity_id
            WHERE r.to_entity_id = ?
            """,
            (entity_id,),
        ).fetchall()
        entities = []
        for row in rows:
            entities.append(
                _row_to_entity(row)
            )
        return entities

    def list_all_entities(self) -> List[Entity]:
        """List all entities."""
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM entities ORDER BY updated_at DESC").fetchall()

        entities = []
        for row in rows:
            entities.append(
                _row_to_entity(row)
            )
        return entities

    def get_stats(self) -> Dict[str, int]:
        """Get database statistics."""
        conn = self._get_connection()

        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        observation_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        relation_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        # 2026-10-23 (KB phase 0): links whose target note does not exist (yet)
        unresolved = conn.execute("SELECT COUNT(*) FROM pending_relations").fetchone()[0]

        return {
            "entities": entity_count,
            "observations": observation_count,
            "relations": relation_count,
            "unresolved_relations": unresolved,
        }

    def sync_entity_from_note(self, note: Note,
                              checksum: Optional[str] = None,
                              mtime: Optional[float] = None,
                              size: Optional[int] = None,
                              entity_id: Optional[int] = None) -> int:
        """
        Sync a Note object to the database.

        2026-10-23 (KB phase 0): everything is replaced in ONE transaction
        (all or nothing). Only the note's own (outgoing) links are replaced —
        links from other notes to it stay. Link targets are resolved by
        permalink, slug, name, title or alias (``resolve_target``); a target
        that does not exist yet is kept as an unresolved link and becomes a
        relation as soon as that note is indexed. Title, aliases and
        frontmatter metadata (with ``tags``) are stored; for an existing note
        file path, name, title, checksum, mtime and size are updated too.

        Args:
            note: Parsed Note object
            checksum: File checksum for change detection (Phase 1 import)
            mtime: File modification time (Phase 1 import)
            size: File size in bytes (Phase 1 import)
            entity_id: update this entity (keeps its id and backlinks) even
                when the permalink changed — used for moved/renamed files.

        Returns:
            Entity ID
        """
        now = datetime.now().isoformat()
        title = note.title or note.name or note.permalink
        tags = _as_str_list(note.tags)
        aliases = _as_str_list(getattr(note, "aliases", None), strip_hash=False)
        raw_meta = getattr(note, "metadata", None)
        metadata: Dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        metadata["tags"] = tags
        metadata_json = json.dumps(metadata, default=str, ensure_ascii=False)
        file_path = str(note.file_path) if note.file_path else None

        # 2026-10-24 (KB phase 0 review): the lookup happens inside the write
        # transaction (BEGIN IMMEDIATE) — two processes indexing at once got
        # "UNIQUE constraint failed: entities.permalink".
        with self._write_txn() as conn:
            existing = None
            old_folds: set = set()
            old_plinks: set = set()
            if entity_id is not None:
                existing = conn.execute(
                    "SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
                if existing is not None and existing["permalink"] != note.permalink:
                    holder = conn.execute("SELECT id FROM entities WHERE permalink = ?",
                                          (note.permalink,)).fetchone()
                    if holder is not None:  # the new permalink is taken: update that one
                        existing = None
            if existing is None:
                existing = conn.execute(
                    "SELECT * FROM entities WHERE permalink = ?", (note.permalink,)).fetchone()

            if existing is not None:
                eid = existing["id"]
                old_folds, old_plinks = self._entity_keys(conn, eid)
                name = note.name or existing["name"]
                if name != existing["name"] and conn.execute(
                        "SELECT 1 FROM entities WHERE name = ? AND id != ?",
                        (name, eid)).fetchone():
                    name = existing["name"]  # name is UNIQUE: keep the old one
                sets: Dict[str, Any] = {
                    "name": name, "permalink": note.permalink, "title": title,
                    "entity_metadata": metadata_json, "updated_at": now,
                    "name_fold": _fold(name), "permalink_fold": _fold(note.permalink),
                    "title_fold": _fold(title),
                }
                if file_path:
                    sets["file_path"] = file_path
                if checksum is not None:
                    sets["checksum"] = checksum
                if mtime is not None:
                    sets["mtime"] = mtime
                if size is not None:
                    sets["size"] = size
                conn.execute(
                    f"UPDATE entities SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                    (*sets.values(), eid))
            else:
                name = note.name or note.permalink
                if conn.execute("SELECT 1 FROM entities WHERE name = ?", (name,)).fetchone():
                    # Another note already uses this name (UNIQUE): fall back to
                    # the permalink, so the note is indexed instead of failing.
                    alt = note.permalink
                    if conn.execute("SELECT 1 FROM entities WHERE name = ?", (alt,)).fetchone():
                        alt = f"{name} ({note.permalink})"
                    name = alt
                cursor = conn.execute(
                    """
                    INSERT INTO entities (name, permalink, file_path, created_at, updated_at,
                                          checksum, mtime, size, entity_metadata, title,
                                          name_fold, permalink_fold, title_fold)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, note.permalink, file_path or f"{note.permalink}.md", now, now,
                     checksum, mtime, size, metadata_json, title,
                     _fold(name), _fold(note.permalink), _fold(title)),
                )
                eid = int(cursor.lastrowid or 0)

            # Observations: replace
            conn.execute("DELETE FROM observations WHERE entity_id = ?", (eid,))
            for obs in note.observations:
                conn.execute(
                    """
                    INSERT INTO observations
                        (entity_id, category, content, tags, created_at, context, provenance_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (eid, obs.category, obs.content, json.dumps(obs.tags or []), now,
                     getattr(obs, "context", None), getattr(obs, "provenance_path", None)),
                )

            # Aliases: replace the set (before links, so a self-link by alias resolves)
            conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (eid,))
            seen_alias: set = set()
            for alias in aliases:
                if _fold(alias) in seen_alias:
                    continue
                seen_alias.add(_fold(alias))
                conn.execute(
                    "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, created_at, "
                    "alias_fold) VALUES (?, ?, ?, ?)", (eid, alias, now, _fold(alias)))

            # Outgoing links only: replace (resolved and unresolved)
            conn.execute("DELETE FROM relations WHERE from_entity_id = ?", (eid,))
            conn.execute("DELETE FROM pending_relations WHERE from_entity_id = ?", (eid,))
            for rel in note.relations:
                target_name = (rel.target or "").strip()
                if not target_name or not rel.relation_type:
                    continue
                target = self._resolve_target_row(conn, target_name)
                if target is not None:
                    self._insert_relation(conn, eid, rel.relation_type, target["id"],
                                          target_name)
                else:
                    logger.debug("Link target not found yet (kept as unresolved): %s",
                                 target_name)
                    self._add_pending(conn, eid, rel.relation_type, target_name)

            # Note text index
            self._index_note_text_conn(conn, eid, title, note.content or "", tags)

            # Links from other notes by this note's old or new names: waiting
            # ones resolve, existing ones move to the best match (2026-10-24).
            new_folds, new_plinks = self._entity_keys(conn, eid)
            self._relink_for_keys(conn, old_folds | new_folds, old_plinks | new_plinks)
        return eid

    # ── v0.7.5: full-text index of the note text ─────────────────────────────
    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                return text[end + 4:].lstrip("\n")
        return text

    def index_note_text(self, entity_id: int, title: str, body: str, tags: List[str]) -> None:
        conn = self._get_connection()
        self._index_note_text_conn(conn, entity_id, title, body, tags)
        conn.commit()

    def _index_note_text_conn(self, conn: sqlite3.Connection, entity_id: int, title: str,
                              body: str, tags: List[str]) -> None:
        """index_note_text without commit (2026-10-23: part of the sync transaction)."""
        try:
            conn.execute("DELETE FROM notes_text_fts WHERE rowid = ?", (entity_id,))
            conn.execute("INSERT INTO notes_text_fts(rowid, title, body, tags) VALUES (?, ?, ?, ?)",
                         (entity_id, title or "", self._strip_frontmatter(body or "")[:200_000],
                          " ".join(tags or [])))
        except sqlite3.OperationalError as e:  # FTS5 missing: names/observations still work
            logger.debug("note text not indexed: %s", e)

    def backfill_note_text(self) -> int:
        """Index the text of notes saved before 0.7.5 (once)."""
        conn = self._get_connection()
        try:
            missing = conn.execute(
                "SELECT e.id, e.name, e.file_path FROM entities e "
                "WHERE e.id NOT IN (SELECT rowid FROM notes_text_fts)").fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        for row in missing:
            try:
                text = Path(row["file_path"]).read_text(encoding="utf-8")
            except (OSError, TypeError, ValueError):
                text = ""
            title = row["name"]
            m = re.search(r"^title:\s*(.+)$", text[:2000], re.MULTILINE)
            if m:
                title = m.group(1).strip().strip('"')
            self.index_note_text(row["id"], title, text, [])
            n += 1
        return n
