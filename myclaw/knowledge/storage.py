"""
File storage operations for knowledge notes.

Handles reading/writing Markdown files to the knowledge directory.
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .parser import (
    RESERVED_KEYS, Note, Observation, Relation, generate_markdown, parse_datetime,
    parse_frontmatter, parse_note, parse_observations, parse_relations,
)
from .db import KnowledgeDB

logger = logging.getLogger(__name__)


def compute_file_checksum(file_path: Path) -> str:
    """Compute MD5 checksum of a file (Phase 1 import from MemoPad)."""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def get_file_metadata(file_path: Path) -> Dict[str, Any]:
    """Get file metadata: mtime, size, checksum (Phase 1 import from MemoPad)."""
    stat = file_path.stat()
    return {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "checksum": compute_file_checksum(file_path),
    }


# 2026-10-23 (KB phase 0): ``knowledge.knowledge_dir`` from the config is
# honoured. None = the default ``~/.myclaw/knowledge`` (computed on every call,
# so tests that change HOME keep working).
_KNOWLEDGE_ROOT: Optional[Path] = None


def set_knowledge_root(path: Optional[Any]) -> None:
    """Use another folder for the knowledge bases (None = ~/.myclaw/knowledge)."""
    global _KNOWLEDGE_ROOT
    _KNOWLEDGE_ROOT = Path(str(path)).expanduser() if path else None


def knowledge_root() -> Path:
    """The folder that holds one sub-folder per knowledge base."""
    return _KNOWLEDGE_ROOT or (Path.home() / ".myclaw" / "knowledge")


def get_knowledge_dir(user_id: str = "default") -> Path:
    """Get the knowledge directory for a user (internal ids share 'default', v0.7.5)."""
    from .scope import kb_user, migrate_internal_scopes

    try:
        migrate_internal_scopes()
    except Exception as e:  # never block reading notes
        logger.warning("Knowledge: could not move old notes: %s", e)
    knowledge_dir = knowledge_root() / kb_user(user_id)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    return knowledge_dir


def _write_atomic(file_path: Path, text: str) -> None:
    """Write a note via a temporary file + ``os.replace``.

    2026-10-23 (KB phase 0): a crash or a full disk mid-write no longer leaves
    a half-written note (memopad's write_file_atomic). The temporary file
    starts with '.' and ends in '.tmp', so the sync never indexes it.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_name(f".{file_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, file_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _inside(path: Path, folder: Path) -> bool:
    """True if ``path`` is inside ``folder`` and not in a dot-folder (.trash…)."""
    try:
        rel = path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return not any(part.startswith(".") for part in rel.parts[:-1])


def _find_note_file(permalink: str, user_id: str, db_path: Optional[Path] = None,
                    knowledge_dir: Optional[Path] = None) -> Optional[Path]:
    """The file of a note: ``<base>/<permalink>.md``, else the indexed file.

    2026-10-23 (KB phase 0): notes in a sub-folder, or whose file name differs
    from their frontmatter permalink, were "not found". The index's
    ``file_path`` is used only when that file exists inside the knowledge
    folder. Never raises.
    """
    try:
        knowledge_dir = knowledge_dir or get_knowledge_dir(user_id)
        if not permalink or "/" in permalink or "\\" in permalink or permalink.startswith("."):
            direct = None
        else:
            direct = knowledge_dir / f"{permalink}.md"
        if direct is not None and direct.is_file():
            return direct
        with KnowledgeDB(user_id, db_path=db_path) as db:
            entity = db.get_entity_by_permalink(permalink)
        if entity is None or not entity.file_path:
            return None
        candidate = Path(entity.file_path)
        if not candidate.is_absolute():
            candidate = knowledge_dir / candidate
        if candidate.is_file() and candidate.suffix == ".md" and _inside(candidate, knowledge_dir):
            return candidate
    except Exception as e:  # a broken index must not break reading notes
        logger.info("Knowledge: could not look up note %r: %s", permalink, e)
    return None


def _index_file(file_path: Path, user_id: str, db_path: Optional[Path] = None) -> None:
    """Re-index one note file with its checksum / mtime / size.

    2026-10-23 (KB phase 0): ``sync_entity_from_note`` stores the file data
    only for a new entity; for an existing one the old checksum (or none) was
    kept, so the next sync re-indexed the note again. The entity's path and
    file data are refreshed here too.
    """
    file_meta = get_file_metadata(file_path)
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entity_id = db.sync_entity_from_note(
            parse_note(file_path),
            checksum=file_meta["checksum"],
            mtime=file_meta["mtime"],
            size=file_meta["size"],
        )
        if entity_id:
            db.update_entity_path(entity_id, str(file_path), file_meta["checksum"],
                                  file_meta["mtime"], file_meta["size"])


def validate_permalink(permalink: str) -> str:
    """
    Validate and clean a permalink.
    
    Args:
        permalink: Input permalink
        
    Returns:
        Cleaned permalink
        
    Raises:
        ValueError: If permalink is invalid
    """
    if not permalink:
        raise ValueError("Permalink cannot be empty")
    
    # Convert to lowercase, replace spaces with hyphens
    cleaned = permalink.lower().strip()
    cleaned = re.sub(r'[^\w\-]', '-', cleaned)
    cleaned = re.sub(r'-+', '-', cleaned)  # Collapse multiple hyphens
    cleaned = cleaned.strip('-')
    
    if not cleaned:
        raise ValueError(f"Invalid permalink: {permalink}")
    
    return cleaned


def normalize_text(text: str) -> str:
    """Models sometimes send JSON-escaped text: literal '\\n' instead of new lines."""
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return text.strip()


def _merge_obs(a: List[Observation], b: List[Observation]) -> List[Observation]:
    seen = {(o.category, o.content) for o in a}
    return list(a) + [o for o in b if (o.category, o.content) not in seen]


def _merge_rel(a: List[Relation], b: List[Relation]) -> List[Relation]:
    seen = {(r.relation_type, r.target) for r in a}
    return list(a) + [r for r in b if (r.relation_type, r.target) not in seen]


def write_note(
    name: str,
    title: Optional[str] = None,
    observations: List[Observation] = None,
    relations: List[Relation] = None,
    tags: List[str] = None,
    user_id: str = "default",
    content: Optional[str] = None,  # Optional raw content
    db_path: Optional[Path] = None,
    aliases: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Write a new note to the knowledge base.
    
    Args:
        name: Entity name (used for permalink if not specified)
        title: Display title (defaults to name)
        observations: List of observations
        relations: List of relations
        tags: List of tags
        user_id: User ID for isolation
        content: Optional raw markdown content (overrides generated)
        aliases: Other names of the note (frontmatter ``aliases``); None keeps
            the existing note's aliases
        metadata: Other frontmatter keys (``type``, ``status``…); None keeps
            the existing note's metadata, a dict replaces it

    Returns:
        Permalink of created note
    """
    knowledge_dir = get_knowledge_dir(user_id)
    permalink = validate_permalink(name)

    # 2026-10-23 (KB phase 0): an existing note in a sub-folder is updated in
    # place instead of getting a second file with the same permalink.
    file_path = (_find_note_file(permalink, user_id, db_path, knowledge_dir)
                 or knowledge_dir / f"{permalink}.md")
    
    # Build note
    note = Note(
        name=name,
        permalink=permalink,
        title=title or name,
        content="",  # Will be generated
        tags=tags or [],
        created_at=datetime.now(),
        updated_at=datetime.now(),
        observations=observations or [],
        relations=relations or [],
        file_path=file_path,
        aliases=list(aliases or []),
        metadata=dict(metadata or {}),
    )

    # 2026-10-23: a whole note file (starts with frontmatter) is kept as given;
    # otherwise ``content`` is the free text and the file gets frontmatter,
    # title, text, Observations and Relations. Before, any ``content`` replaced
    # the generated file, so tags, facts and links passed with it were lost
    # (on mindy01: 266 notes, 0 tags, 0 facts, 0 links).
    if content and content.lstrip().startswith("---") and aliases is None and metadata is None:
        markdown_content = content
    elif content and content.lstrip().startswith("---"):
        # 2026-10-23 (KB phase 0): a whole file + aliases/metadata: keep the
        # file's text, tags, facts and links and store the given values too.
        given = _note_from_text(content, file_path)
        given.created_at = given.created_at or note.created_at
        given.updated_at = note.updated_at
        if aliases is not None:
            given.aliases = list(aliases)
        if metadata is not None:
            given.metadata = dict(metadata)
        markdown_content = generate_markdown(given)
    else:
        body = normalize_text(content or "")
        note.content = body
        note.observations = _merge_obs(note.observations, parse_observations(body))
        note.relations = _merge_rel(note.relations, parse_relations(body))
        if file_path.exists():  # keep when the note was first made
            try:
                old = parse_note(file_path)
                note.created_at = old.created_at or note.created_at
                # 2026-10-23 (KB phase 0): also keep aliases and metadata
                if aliases is None:
                    note.aliases = list(old.aliases)
                if metadata is None:
                    note.metadata = dict(old.metadata)
            except Exception:
                pass
        markdown_content = generate_markdown(note)

    # Write file (2026-10-23, KB phase 0: atomically)
    _write_atomic(file_path, markdown_content)
    logger.info(f"Created note: {file_path}")

    # Sync to database, with file metadata for change detection (Phase 1)
    _index_file(file_path, user_id, db_path)

    return permalink


def _note_from_text(text: str, file_path: Path) -> Note:
    """Parse a whole note given as text (as ``parse_note`` does for a file)."""
    fm = parse_frontmatter(text)
    title = fm.get("title") if isinstance(fm.get("title"), str) else None
    return Note(
        name=file_path.stem,
        permalink=file_path.stem,
        title=title or file_path.stem,
        content=text,
        tags=list(fm.get("tags") or []),
        created_at=parse_datetime(fm.get("created") if isinstance(fm.get("created"), str) else ""),
        observations=parse_observations(text),
        relations=parse_relations(text),
        file_path=file_path,
        aliases=list(fm.get("aliases") or []),
        metadata={k: v for k, v in fm.items() if k not in RESERVED_KEYS},
    )


def read_note(permalink: str, user_id: str = "default", db_path: Optional[Path] = None) -> Optional[Note]:
    """
    Read a note from the knowledge base.
    
    Args:
        permalink: Note permalink
        user_id: User ID for isolation
        
    Returns:
        Note object or None if not found
    """
    # 2026-10-23 (KB phase 0): falls back to the indexed file (sub-folder or
    # renamed note); a missing or unreadable note gives None, never an error.
    file_path = _find_note_file(permalink, user_id, db_path)
    if file_path is None:
        return None
    try:
        return parse_note(file_path)  # the file is the source of truth
    except Exception as e:
        logger.warning(f"Failed to parse note {file_path}: {e}")
        return None


def delete_note(permalink: str, user_id: str = "default", db_path: Optional[Path] = None) -> bool:
    """
    Delete a note from the knowledge base.

    Args:
        permalink: Note permalink
        user_id: User ID for isolation

    2026-10-23 (KB phase 0): the file is moved into ``<base>/.trash/`` (as the
    Web UI's Delete does) instead of being deleted for good; move it back to
    undo. The sync does not index dot-folders.

    Returns:
        True if deleted, False if not found
    """
    knowledge_dir = get_knowledge_dir(user_id)
    file_path = _find_note_file(permalink, user_id, db_path, knowledge_dir)

    if file_path is None:
        return False

    dest = _move_to_trash(file_path, knowledge_dir)

    # Remove from database
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db.delete_entity(permalink)

    logger.info(f"Deleted note: {permalink} (moved to {dest})")
    return True


def _move_to_trash(file_path: Path, knowledge_dir: Path) -> Path:
    """Move a note to ``<base>/.trash/<name>-<YYYYmmdd-HHMMSS>.md`` (Web UI scheme)."""
    trash = knowledge_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash / f"{file_path.stem}-{stamp}.md"
    n = 2
    while dest.exists():  # two deletes in the same second
        dest = trash / f"{file_path.stem}-{stamp}-{n}.md"
        n += 1
    shutil.move(str(file_path), str(dest))
    return dest


def list_notes(
    user_id: str = "default",
    tags: Optional[List[str]] = None,
    db_path: Optional[Path] = None
) -> List[Note]:
    """
    List all notes in the knowledge base.
    
    Args:
        user_id: User ID for isolation
        tags: Optional filter by tags
        
    Returns:
        List of Note objects
    """
    knowledge_dir = get_knowledge_dir(user_id)
    notes = []
    # 2026-10-23 (KB phase 0): tags match case-insensitively, with or without '#'
    wanted = {str(t).strip().lstrip("#").lower() for t in (tags or []) if str(t).strip()}

    # All markdown files, sub-folders too (as the sync indexes them), but not
    # dot-folders such as .trash (2026-10-23, KB phase 0)
    files = sorted(knowledge_dir.glob("*.md")) + sorted(
        f for f in knowledge_dir.glob("*/**/*.md") if _inside(f, knowledge_dir))
    for file_path in files:
        if file_path.name.startswith("."):
            continue
        try:
            note = parse_note(file_path)
            # Filter by tags if specified
            if wanted and not wanted.intersection(t.lower() for t in note.tags):
                continue
            notes.append(note)
        except Exception as e:
            logger.warning(f"Failed to parse note {file_path}: {e}")

    return notes


def update_note(permalink: str, user_id: str = "default", db_path: Optional[Path] = None, **kwargs) -> bool:
    """
    Update an existing note.

    Args:
        permalink: Note permalink
        user_id: User ID for isolation
        **kwargs: Fields to update (title, observations, relations, tags,
            content — the new text; facts/links written in it are kept, as
            in ``write_note`` —, aliases, metadata)

    Returns:
        True if updated, False if not found
    """
    note = read_note(permalink, user_id, db_path=db_path)
    if not note or note.file_path is None:
        return False

    # Update fields
    if kwargs.get('title') is not None:
        note.title = kwargs['title']
    if 'observations' in kwargs and kwargs['observations'] is not None:
        note.observations = list(kwargs['observations'])
    if 'relations' in kwargs and kwargs['relations'] is not None:
        note.relations = list(kwargs['relations'])
    if 'tags' in kwargs and kwargs['tags'] is not None:
        note.tags = list(kwargs['tags'])
    # 2026-10-23 (KB phase 0): new text, aliases and metadata
    if kwargs.get('aliases') is not None:
        note.aliases = list(kwargs['aliases'])
    if kwargs.get('metadata') is not None:
        note.metadata = dict(kwargs['metadata'])
    if kwargs.get('content') is not None:
        body = normalize_text(str(kwargs['content']))
        note.content = body
        note.observations = _merge_obs(note.observations, parse_observations(body))
        note.relations = _merge_rel(note.relations, parse_relations(body))

    note.updated_at = datetime.now()

    # Regenerate content (2026-10-23, KB phase 0: written atomically)
    _write_atomic(note.file_path, generate_markdown(note))

    # Resync to database with checksum/mtime/size (2026-10-23, KB phase 0:
    # before, none was stored, so the next sync re-indexed the note)
    _index_file(note.file_path, user_id, db_path)

    logger.info(f"Updated note: {permalink}")
    return True


def _batch_read_notes(
    permalinks: List[str], user_id: str, db_path: Optional[Path] = None
) -> List[Note]:
    """Read multiple notes in parallel using a thread pool.

    ``db_path`` is accepted for signature parity with ``read_note()`` (callers
    such as ``graph.build_context`` pass it); notes are read from disk, which
    is the source of truth. The index (``db_path``) is only asked for the file
    of a note that is not ``<base>/<permalink>.md`` (2026-10-23, KB phase 0).

    Each individual `read_note()` is a blocking stat+open+parse, so for N
    permalinks we'd otherwise pay N * (stat + open + parse) serially.
    Running them on a small thread pool overlaps the syscalls and parses,
    typically a 7-10x improvement at N=10 on warm cache.
    """
    if not permalinks:
        return []
    knowledge_dir = get_knowledge_dir(user_id)

    def _load(permalink: str) -> Optional[Note]:
        # 2026-10-23 (KB phase 0): same fallback to the indexed file as read_note
        file_path = _find_note_file(permalink, user_id, db_path, knowledge_dir)
        if file_path is None:
            return None
        try:
            return parse_note(file_path)
        except Exception as e:
            logger.warning(f"Failed to parse note {file_path}: {e}")
            return None

    # Cap workers so we don't spawn unbounded threads on a huge result set.
    max_workers = min(8, len(permalinks))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_load, permalinks))
    return [n for n in results if n is not None]


async def _batch_read_notes_async(permalinks: List[str], user_id: str) -> List[Note]:
    """Async version: same idea, but yields control to the event loop."""
    if not permalinks:
        return []
    return await asyncio.to_thread(_batch_read_notes, permalinks, user_id)


def search_notes(
    query: str,
    user_id: str = "default",
    limit: int = 10,
    db_path: Optional[Path] = None,
    boolean: bool = False,
) -> List[Note]:
    """
    Search notes using FTS5.

    Args:
        query: Search query
        user_id: User ID for isolation
        limit: Maximum results

    Returns:
        List of matching Note objects
    """
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entities = db.search_fts(query, limit, boolean=boolean)

    # Batch-read all matched notes in parallel. The previous N+1 loop
    # serialized stat()+open()+parse() per result; this overlaps them.
    return _batch_read_notes([e.permalink for e in entities], user_id, db_path)


def get_note_by_tag(tag: str, user_id: str = "default", db_path: Optional[Path] = None) -> List[Note]:
    """
    Get all notes with a specific tag.

    Args:
        tag: Tag to search for
        user_id: User ID for isolation

    Returns:
        List of matching Note objects
    """
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entities = db.search_by_tag(tag)

    return _batch_read_notes([e.permalink for e in entities], user_id, db_path)


def get_all_tags(user_id: str = "default", db_path: Optional[Path] = None) -> List[str]:
    """
    Get all unique tags from all notes.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Sorted list of unique tags
    """
    tags = set()
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db_entities = db.list_all_entities()
    db_notes = _batch_read_notes([e.permalink for e in db_entities], user_id, db_path)
    for note in db_notes:
        tags.update(note.tags)
        for obs in note.observations:
            tags.update(obs.tags)
    return sorted(tags)
