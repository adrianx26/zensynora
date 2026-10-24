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
from typing import Any, Dict, List, Optional, Tuple

from .parser import (
    FRONTMATTER_PATTERN, Note, Observation, Relation, extract_body, generate_markdown,
    parse_note, parse_note_text, parse_observations, parse_relations,
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


def _direct_path(knowledge_dir: Path, permalink: str) -> Optional[Path]:
    """``<base>/<permalink>.md`` for a safe permalink (no path separators)."""
    if not permalink or "/" in permalink or "\\" in permalink or permalink.startswith("."):
        return None
    return knowledge_dir / f"{permalink}.md"


def _try_parse(file_path: Path) -> Optional[Note]:
    try:
        return parse_note(file_path)
    except Exception as e:  # unreadable file: logged, treated as "not this note"
        logger.warning(f"Failed to parse note {file_path}: {e}")
        return None


def _indexed_path(file_path: Optional[str], knowledge_dir: Path) -> Optional[Path]:
    """The index's file path if it is a note file inside the knowledge folder."""
    if not file_path:
        return None
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = knowledge_dir / candidate
    if candidate.suffix == ".md" and candidate.is_file() and _inside(candidate, knowledge_dir):
        return candidate
    return None


def _resolve_notes(permalinks: List[str], user_id: str, db_path: Optional[Path],
                   knowledge_dir: Path, parallel: bool = False) -> Dict[str, Tuple[Path, Note]]:
    """``permalink -> (file, parsed note)`` for the notes that exist.

    2026-10-23 (KB phase 0): a file counts only if its own permalink (the
    frontmatter ``permalink:``, else the file name) is the one asked for —
    ``x.md`` holding ``permalink: y`` is note y, not x. ``<base>/<permalink>.md``
    is tried first; the index (one query for the whole batch) is asked only
    for the misses, and its file is used only when it exists inside the
    knowledge folder (not in a dot-folder). Never raises.
    """
    found: Dict[str, Tuple[Path, Note]] = {}

    def _direct(permalink: str) -> Optional[Tuple[str, Path, Note]]:
        path = _direct_path(knowledge_dir, permalink)
        if path is None or not path.is_file():
            return None
        note = _try_parse(path)
        if note is not None and note.permalink == permalink:
            return permalink, path, note
        return None

    unique = list(dict.fromkeys(p for p in permalinks if p))
    if parallel and len(unique) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(unique))) as pool:
            hits = list(pool.map(_direct, unique))
    else:
        hits = [_direct(p) for p in unique]
    for hit in hits:
        if hit is not None:
            found[hit[0]] = (hit[1], hit[2])

    misses = [p for p in unique if p not in found]
    if not misses:
        return found
    try:
        with KnowledgeDB(user_id, db_path=db_path) as db:
            entities = db.get_entities_by_permalinks(misses)
    except Exception as e:  # a broken index must not break reading notes
        logger.info("Knowledge: could not look up notes in the index: %s", e)
        return found
    for permalink in misses:
        entity = entities.get(permalink)
        path = _indexed_path(getattr(entity, "file_path", None), knowledge_dir)
        if path is None:
            continue
        note = _try_parse(path)
        if note is not None and note.permalink == permalink:
            found[permalink] = (path, note)
    return found


def _resolve_note(permalink: str, user_id: str, db_path: Optional[Path] = None,
                  knowledge_dir: Optional[Path] = None) -> Optional[Tuple[Path, Note]]:
    """``(file, note)`` of one note, or None (see :func:`_resolve_notes`)."""
    try:
        knowledge_dir = knowledge_dir or get_knowledge_dir(user_id)
        return _resolve_notes([permalink], user_id, db_path, knowledge_dir).get(permalink)
    except Exception as e:
        logger.info("Knowledge: could not look up note %r: %s", permalink, e)
        return None


def _find_note_file(permalink: str, user_id: str, db_path: Optional[Path] = None,
                    knowledge_dir: Optional[Path] = None) -> Optional[Path]:
    """The file of a note (see :func:`_resolve_notes`), or None. Never raises."""
    hit = _resolve_note(permalink, user_id, db_path, knowledge_dir)
    return hit[0] if hit else None


def _free_path(knowledge_dir: Path, permalink: str) -> Path:
    """``<base>/<permalink>.md``, or ``<permalink>-2.md``… if another note has it."""
    path = knowledge_dir / f"{permalink}.md"
    n = 2
    while path.exists():
        path = knowledge_dir / f"{permalink}-{n}.md"
        n += 1
    return path


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


def _union(a: Optional[List[str]], b: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for item in list(a or []) + list(b or []):
        if item not in out:
            out.append(item)
    return out


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _what_is_lost(old: Note, new: Note) -> List[str]:
    """What of ``old`` the ``new`` version no longer has (empty = nothing)."""
    lost: List[str] = []
    old_body = _squash(extract_body(old.content, old.title))
    if old_body and old_body not in _squash(extract_body(new.content, new.title)):
        lost.append("text")
    new_obs = {(o.category, o.content) for o in new.observations}
    if any((o.category, o.content) not in new_obs for o in old.observations):
        lost.append("facts")
    new_rel = {(r.relation_type, r.target) for r in new.relations}
    if any((r.relation_type, r.target) not in new_rel for r in old.relations):
        lost.append("links")
    if {t.lower() for t in old.tags} - {t.lower() for t in new.tags}:
        lost.append("tags")
    if set(old.aliases) - set(new.aliases):
        lost.append("aliases")
    if set(old.metadata) - set(new.metadata):
        lost.append("metadata")
    return lost


def _trash_path(knowledge_dir: Path, stem: str) -> Path:
    """``<base>/.trash/<stem>-<YYYYmmdd-HHMMSS>.md`` (the Web UI's scheme)."""
    trash = knowledge_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash / f"{stem}-{stamp}.md"
    n = 2
    while dest.exists():  # two in the same second
        dest = trash / f"{stem}-{stamp}-{n}.md"
        n += 1
    return dest


def _replace_note_file(file_path: Path, old: Optional[Note], new_text: str,
                       knowledge_dir: Path) -> None:
    """Write ``new_text`` over a note; keep the old file in .trash if it loses anything.

    2026-10-23 (KB phase 0): ``write_note`` replaced a hand-written note found
    in a sub-folder (text, facts, links, tags gone, no copy anywhere). Now the
    previous version is copied to ``<base>/.trash/`` first whenever the new
    file drops text, a fact, link, tag, alias or metadata key. Appending text
    or adding tags makes no copy.
    """
    if old is not None and file_path.exists():
        lost = _what_is_lost(old, parse_note_text(new_text, file_path))
        if lost:
            dest = _trash_path(knowledge_dir, file_path.stem)
            shutil.copy2(file_path, dest)
            logger.info("Knowledge: note %s rewritten without its old %s; previous version kept in "
                        ".trash/%s", file_path.name, ", ".join(lost), dest.name)
    _write_atomic(file_path, new_text)


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
        content: The note's text, or a whole note file (frontmatter first);
            the other arguments are merged into it
        aliases: Other names of the note (frontmatter ``aliases``); None keeps
            the existing note's aliases
        metadata: Other frontmatter keys (``type``, ``status``…); None keeps
            the existing note's metadata, a dict replaces it

    A note with the same permalink is replaced; if that loses anything, the
    previous file is kept in ``<base>/.trash/`` (2026-10-23, KB phase 0).

    Returns:
        Permalink of created note
    """
    knowledge_dir = get_knowledge_dir(user_id)
    permalink = validate_permalink(name)
    now = datetime.now()

    # 2026-10-23 (KB phase 0): the existing note with this permalink (also in a
    # sub-folder) is updated in place; a file of the same name that belongs to
    # another note is left alone.
    hit = _resolve_note(permalink, user_id, db_path, knowledge_dir)
    old: Optional[Note] = hit[1] if hit else None
    file_path = hit[0] if hit else _free_path(knowledge_dir, permalink)

    # 2026-10-23 (KB phase 0): ``content`` is a note file only when it really
    # starts with frontmatter (a body may start with a '---' rule).
    text = (content or "").lstrip()
    if content and FRONTMATTER_PATTERN.match(text):
        given = parse_note_text(text, file_path)
        fm_title = given.raw_frontmatter.get("title")
        merged = Note(
            name=name,
            # 2026-10-23 (KB phase 0): the file name decides the permalink — a
            # pasted 'permalink:' must not take over another note's entry
            permalink=permalink,
            title=title or (given.title if fm_title else name),
            content=text,
            # 2026-10-23 (KB phase 0): the other arguments are merged in
            # (before, a whole file made write_note ignore them)
            tags=_union(given.tags, tags),
            created_at=given.created_at,
            updated_at=given.updated_at,
            observations=_merge_obs(given.observations, observations or []),
            relations=_merge_rel(given.relations, relations or []),
            file_path=file_path,
            aliases=_union(given.aliases, aliases),
            metadata={**given.metadata, **(metadata or {})},
            raw_frontmatter=given.raw_frontmatter,
        )
        if "name" in given.raw_frontmatter:
            merged.name = given.name
        unchanged = (
            given.permalink == permalink
            and (title is None or title == given.title)
            and merged.tags == given.tags and merged.aliases == given.aliases
            and merged.metadata == given.metadata
            and len(merged.observations) == len(given.observations)
            and len(merged.relations) == len(given.relations)
        )
        if unchanged:
            markdown_content = text  # kept exactly as given
        else:
            if merged.created_at is None and "created" not in given.raw_frontmatter:
                merged.created_at = (old.created_at if old else None) or now
            markdown_content = generate_markdown(merged)
    else:
        # ``content`` is the free text: the file gets frontmatter, title, text,
        # Observations and Relations. Before 2026-10-23 any ``content``
        # replaced the generated file, so tags, facts and links were lost (on
        # mindy01: 266 notes, 0 tags, 0 facts, 0 links).
        body = normalize_text(content or "")
        note = Note(
            name=name,
            permalink=permalink,
            title=title or name,
            content=body,
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
            observations=_merge_obs(list(observations or []), parse_observations(body)),
            relations=_merge_rel(list(relations or []), parse_relations(body)),
            file_path=file_path,
            aliases=list(aliases or []),
            metadata=dict(metadata or {}),
        )
        if old is not None:
            # keep when the note was first made (also a 'created' we cannot
            # parse), its aliases/metadata unless new ones are given, and the
            # original frontmatter lines of unchanged keys
            if old.created_at or "created" in old.raw_frontmatter:
                note.created_at = old.created_at
            if aliases is None:
                note.aliases = list(old.aliases)
            if metadata is None:
                note.metadata = dict(old.metadata)
            note.raw_frontmatter = old.raw_frontmatter
        markdown_content = generate_markdown(note)

    # Write file (2026-10-23, KB phase 0: atomically; a lossy replace keeps
    # the previous version in .trash)
    _replace_note_file(file_path, old, markdown_content, knowledge_dir)
    logger.info(f"Created note: {file_path}")

    # Sync to database, with file metadata for change detection (Phase 1)
    _index_file(file_path, user_id, db_path)

    return permalink


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
    hit = _resolve_note(permalink, user_id, db_path)
    return hit[1] if hit else None  # the file is the source of truth


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
    dest = _trash_path(knowledge_dir, file_path.stem)
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

    If the update loses anything (text, facts, links, tags, aliases, metadata
    keys) the previous file is kept in ``<base>/.trash/`` (2026-10-23).

    Returns:
        True if updated, False if not found
    """
    knowledge_dir = get_knowledge_dir(user_id)
    hit = _resolve_note(permalink, user_id, db_path, knowledge_dir)
    if not hit:
        return False
    file_path, note = hit
    old = parse_note_text(note.content, file_path)  # an untouched copy

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

    # Regenerate content (2026-10-23, KB phase 0: written atomically; a lossy
    # update keeps the previous version in .trash)
    _replace_note_file(file_path, old, generate_markdown(note), knowledge_dir)

    # Resync to database with checksum/mtime/size (2026-10-23, KB phase 0:
    # before, none was stored, so the next sync re-indexed the note)
    _index_file(file_path, user_id, db_path)

    logger.info(f"Updated note: {permalink}")
    return True


def _batch_read_notes(
    permalinks: List[str], user_id: str, db_path: Optional[Path] = None
) -> List[Note]:
    """Read multiple notes in parallel using a thread pool.

    ``db_path`` is accepted for signature parity with ``read_note()`` (callers
    such as ``graph.build_context`` pass it); notes are read from disk, which
    is the source of truth. The index (``db_path``) is asked once per batch,
    only for the notes that are not ``<base>/<permalink>.md`` (2026-10-23, KB
    phase 0 — before, one database connection per note).

    Each individual `read_note()` is a blocking stat+open+parse, so for N
    permalinks we'd otherwise pay N * (stat + open + parse) serially.
    Running them on a small thread pool overlaps the syscalls and parses,
    typically a 7-10x improvement at N=10 on warm cache.
    """
    if not permalinks:
        return []
    knowledge_dir = get_knowledge_dir(user_id)
    found = _resolve_notes(list(permalinks), user_id, db_path, knowledge_dir, parallel=True)
    return [found[p][1] for p in permalinks if p in found]


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
