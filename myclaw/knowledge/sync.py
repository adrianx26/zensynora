"""
File-to-database synchronization for knowledge storage.

Ensures the SQLite index stays in sync with Markdown files.

Phase 1 (MemoPad import) additions:
- Circuit breaker: skip files that fail repeatedly
- Move/rename detection via checksum matching
- .bmignore support
- Checksum + mtime + size change detection
"""

import asyncio
import fnmatch
import hashlib  # noqa: F401
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional
from datetime import datetime
from collections import OrderedDict
from dataclasses import dataclass

from .db import KnowledgeDB
from .parser import parse_note
from . import storage as _storage_module

logger = logging.getLogger(__name__)

# Module-level parsed-note cache (shared across all user contexts)
_note_cache: dict[str, tuple] = {}

MAX_CONSECUTIVE_FAILURES = 3
_MAX_TRACKED_FAILURES = 100

# Task handle for start/stop_background_extraction(); must exist before the
# first call or those functions raise NameError.
_background_extraction_task: Optional[asyncio.Task] = None

# Module-level failure tracker (used by backwards-compat shims)
_sync_failures_module: OrderedDict[str, Dict] = OrderedDict()

# 2026-10-23 (KB phase 0): SyncEngine failure tracker, shared by every engine
# (sync_knowledge() makes a new engine per call). Key: "<user>\0<path>";
# bounded to _MAX_TRACKED_FAILURES entries like the one above.
_engine_failures: OrderedDict[str, Dict] = OrderedDict()


def reset_sync_failures() -> None:
    """Forget every recorded sync failure (the circuit breaker starts again)."""
    _sync_failures_module.clear()
    _engine_failures.clear()


# ── Module-level helpers kept for backward-compat and detect_changes ────────
# These short-circuit to SyncEngine internals when called from outside the
# class so external callers (e.g. tests) that import the old names still work.

def _record_sync_failure(path_str: str, error: str, checksum: Optional[str] = None) -> None:
    now = datetime.now()
    if path_str in _sync_failures_module:
        info = _sync_failures_module.pop(path_str)
        info["count"] += 1
        info["last_failure"] = now
        info["last_error"] = error
        if checksum:
            info["last_checksum"] = checksum
        _sync_failures_module[path_str] = info
        logger.warning(
            f"Sync failure {info['count']}/{MAX_CONSECUTIVE_FAILURES} for {path_str}: {error}"
        )
    else:
        _sync_failures_module[path_str] = {
            "count": 1, "first_failure": now, "last_failure": now,
            "last_error": error, "last_checksum": checksum or "",
        }
    while len(_sync_failures_module) > _MAX_TRACKED_FAILURES:
        _sync_failures_module.popitem(last=False)


def _should_skip_file(path_str: str, current_checksum: Optional[str] = None) -> bool:
    if path_str not in _sync_failures_module:
        return False
    info = _sync_failures_module[path_str]
    if info["count"] < MAX_CONSECUTIVE_FAILURES:
        return False
    if current_checksum and current_checksum != info.get("last_checksum"):
        del _sync_failures_module[path_str]
        return False
    return True


def _clear_sync_failure(path_str: str) -> None:
    _sync_failures_module.pop(path_str, None)


def _get_cached_note(file_path: Path):
    path_str = str(file_path)
    mtime = file_path.stat().st_mtime
    if path_str in _note_cache:
        cached_mtime, cached_note = _note_cache[path_str]
        if cached_mtime == mtime:
            return cached_note
    note = parse_note(file_path)
    _note_cache[path_str] = (mtime, note)
    return note


def clear_note_cache() -> None:
    global _note_cache
    _note_cache.clear()


@dataclass
class ChangeSet:
    to_add: List[Path]
    to_update: List[Tuple[str, Path]]
    to_delete: List[str]
    checksums: Dict[str, str]


class SyncEngine:
    def __init__(self, user_id: str = "default", db_path: Optional[Path] = None,
                 max_failures: int = MAX_CONSECUTIVE_FAILURES) -> None:
        self.user_id = user_id
        self.db_path = db_path
        self.max_failures = max_failures
        # 2026-10-23 (KB phase 0): failures are kept at module level (shared by
        # every engine), keyed by user + path. sync_knowledge() makes a new
        # engine per call, so per-instance counters never reached the limit.
        self._failures: OrderedDict[str, Dict] = _engine_failures
        self._cache: dict[str, tuple] = {}

    def _failure_key(self, path_str: str) -> str:
        return f"{self.user_id}\x00{path_str}"

    def _record_failure(self, path_str: str, error: str,
                        checksum: Optional[str] = None) -> None:
        now = datetime.now()
        key = self._failure_key(path_str)
        if key in self._failures:
            info = self._failures.pop(key)
            info["count"] += 1
            info["last_failure"] = now
            info["last_error"] = error
            if checksum:
                info["last_checksum"] = checksum
            self._failures[key] = info
            if info["count"] <= self.max_failures:  # de-duplicated: quiet once skipped
                logger.warning(
                    f"Sync failure {info['count']}/{self.max_failures} for {path_str}: {error}"
                )
        else:
            self._failures[key] = {
                "count": 1, "first_failure": now, "last_failure": now,
                "last_error": error, "last_checksum": checksum or "",
            }
            logger.warning(f"Could not index {path_str}: {error}")
        while len(self._failures) > _MAX_TRACKED_FAILURES:
            self._failures.popitem(last=False)

    def _should_skip(self, path_str: str,
                     current_checksum: Optional[str] = None) -> bool:
        key = self._failure_key(path_str)
        if key not in self._failures:
            return False
        info = self._failures[key]
        if info["count"] < self.max_failures:
            return False
        if current_checksum and current_checksum != info.get("last_checksum"):
            del self._failures[key]  # the file changed: try again
            return False
        return True

    def _clear_failure(self, path_str: str) -> None:
        self._failures.pop(self._failure_key(path_str), None)

    def _get_cached_note(self, file_path: Path):
        path_str = str(file_path)
        mtime = file_path.stat().st_mtime
        if path_str in self._cache:
            cached_mtime, cached_note = self._cache[path_str]
            if cached_mtime == mtime:
                return cached_note
        note = parse_note(file_path)
        self._cache[path_str] = (mtime, note)
        return note

    def clear_cache(self) -> None:
        self._cache.clear()

    def _load_bmignore_patterns(self) -> List[str]:
        """Load ignore patterns from .bmignore file in knowledge root."""
        knowledge_dir = _storage_module.get_knowledge_dir(self.user_id)
        return _load_bmignore_patterns(knowledge_dir)

    def _scan_markdown_files(self, since_timestamp: Optional[float] = None) -> Set[Path]:
        """Scan the knowledge directory for all Markdown files, respecting .bmignore.

        2026-10-23 (KB phase 0): folders and files whose name starts with "."
        (``.trash``, ``.git``, ``.obsidian``) are skipped — deleted notes in
        ``.trash/`` were indexed again.
        """
        knowledge_dir = _storage_module.get_knowledge_dir(self.user_id)
        patterns = self._load_bmignore_patterns()
        return _filter_since(_list_markdown_files(knowledge_dir, patterns), since_timestamp)

    def _detect_changes(self, db: KnowledgeDB, since: Optional[float] = None) -> ChangeSet:
        """Detects changes between the filesystem and the database.

        2026-10-23 (KB phase 0): deletions come from the full file listing,
        never from the ``since`` subset (an incremental sync deleted every
        note not modified recently); a file that cannot be read or parsed is
        skipped and keeps its index entry (one broken file aborted the sync).
        """
        all_files = self._scan_markdown_files()
        files = _filter_since(all_files, since)
        all_paths = {str(f) for f in all_files}
        db_mapping = db.list_all_entities()
        db_by_permalink = {e.permalink: e for e in db_mapping}

        to_add: List[Path] = []
        to_update: List[Tuple[str, Path]] = []
        checksums: Dict[str, str] = {}
        file_permalinks: Set[str] = set()

        for file_path in files:
            try:
                note = self._get_cached_note(file_path)
                file_meta = _storage_module.get_file_metadata(file_path)
            except Exception as e:
                logger.warning(f"Failed to process {file_path} for change detection: {e}")
                continue
            file_checksum = file_meta["checksum"]
            checksums[str(file_path)] = file_checksum
            file_permalinks.add(note.permalink)
            if note.permalink not in db_by_permalink:
                to_add.append(file_path)
            else:
                db_entity = db_by_permalink[note.permalink]
                db_file_path = Path(db_entity.file_path) if db_entity.file_path else None
                # A file move (permalink exists, path differs) or content change.
                if ((db_file_path and db_file_path != file_path)
                        or db_entity.checksum != file_checksum):
                    to_update.append((note.permalink, file_path))

        to_delete = [p for p, e in db_by_permalink.items()
                     if p not in file_permalinks and e.file_path not in all_paths]
        return ChangeSet(to_add=to_add, to_update=to_update, to_delete=to_delete,
                         checksums=checksums)

    def _detect_moves(self, db: KnowledgeDB, new_files: List[Path],
                      checksums: Dict[str, str]) -> Dict[str, str]:
        """Detects moved/renamed files by matching checksums."""
        moves: Dict[str, str] = {}
        db_entities = db.list_all_entities()
        db_by_checksum: Dict[str, List] = {}
        for e in db_entities:
            if e.checksum:
                db_by_checksum.setdefault(e.checksum, []).append(e)

        for file_path in new_files:
            file_checksum = checksums.get(str(file_path))
            if not file_checksum:
                continue
            candidates = db_by_checksum.get(file_checksum, [])
            for candidate in candidates:
                if (candidate.file_path and candidate.file_path != str(file_path)
                        and candidate.file_path not in moves
                        and not Path(candidate.file_path).exists()):
                    moves[candidate.file_path] = str(file_path)
                    break
        return moves

    def _index_file(self, db: KnowledgeDB, file_path: Path, note, file_meta: Dict,
                    entity_id: Optional[int] = None) -> int:
        return db.sync_entity_from_note(
            note, checksum=file_meta["checksum"], mtime=file_meta["mtime"],
            size=file_meta["size"], entity_id=entity_id,
        )

    def sync(self, force: bool = False, since: Optional[float] = None) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "deleted": 0, "errors": 0}
        with KnowledgeDB(self.user_id, db_path=self.db_path) as db:
            if force:
                self._sync_full(db, stats)
            else:
                self._sync_incremental(db, stats, since)

        total = stats["added"] + stats["updated"] + stats["deleted"]
        logger.info(
            f"Sync complete: {total} changes "
            f"({stats['added']} added, {stats['updated']} updated, "
            f"{stats['deleted']} deleted, {stats['errors']} errors)"
        )
        return stats

    def _sync_full(self, db: KnowledgeDB, stats: Dict[str, int]) -> None:
        """Rebuild the index from the files (``force=True``)."""
        logger.info("Performing full re-sync...")
        self.clear_cache()
        # 2026-10-23 (KB phase 0): read every file BEFORE clearing the index,
        # in a stable order, so the result does not depend on the file system.
        parsed = []
        for file_path in sorted(self._scan_markdown_files()):  # `since` is ignored
            try:
                file_meta = _storage_module.get_file_metadata(file_path)
                note = self._get_cached_note(file_path)
                parsed.append((file_path, note, file_meta))
            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Failed to sync {file_path}: {e}")
        # Use a single, fast transaction to clear the database.
        db.clear_all_data()
        stats["deleted"] = -1  # Indicate a full clear, not individual deletes.
        for file_path, note, file_meta in parsed:
            try:
                self._index_file(db, file_path, note, file_meta)
                stats["added"] += 1
                self._clear_failure(str(file_path))
                logger.debug(f"Synced: {note.permalink}")
            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Failed to sync {file_path}: {e}")

    def _sync_incremental(self, db: KnowledgeDB, stats: Dict[str, int],
                          since: Optional[float]) -> None:
        """Index new/changed/moved files, drop entries whose file is gone.

        2026-10-23 (KB phase 0):
        - deletions are computed from the full listing of existing files, not
          from the ``since`` subset;
        - with ``since``, files at a path the index does not know are read
          too (``mv`` keeps the modification time);
        - a broken file is skipped (counted in ``errors``) and keeps its entry;
        - a moved/renamed file keeps its entity (id, backlinks) and gets the
          new path/permalink instead of being deleted and added again;
        - notes indexed by an older version (no title stored) are re-indexed
          once, so links by title and frontmatter tags/aliases work.
        """
        all_files = self._scan_markdown_files()
        all_paths = {str(f) for f in all_files}
        entities = db.list_all_entities()
        by_permalink = {e.permalink: e for e in entities}
        by_path = {e.file_path: e for e in entities if e.file_path}

        if since is None:
            candidates = sorted(all_files)
        else:
            recent = _filter_since(all_files, since)
            candidates = sorted(f for f in all_files if f in recent or str(f) not in by_path
                                or by_path[str(f)].title is None)

        parsed: List[Tuple[Path, Any, Dict, Any]] = []  # file, note, meta, entity at path
        for file_path in candidates:
            path_str = str(file_path)
            try:
                file_meta = _storage_module.get_file_metadata(file_path)
            except Exception as e:
                stats["errors"] += 1
                self._record_failure(path_str, f"cannot read the file: {e}")
                continue
            checksum = file_meta["checksum"]
            known = by_path.get(path_str)
            if known is not None and known.checksum == checksum and known.title is not None:
                continue  # unchanged
            if self._should_skip(path_str, checksum):
                logger.debug(f"Skipping {file_path} due to repeated failures")
                continue
            try:
                note = self._get_cached_note(file_path)
                if not getattr(note, "permalink", None):
                    raise ValueError("the note has no permalink")
            except Exception as e:
                stats["errors"] += 1
                self._record_failure(path_str, f"cannot parse the note: {e}", checksum)
                continue
            parsed.append((file_path, note, file_meta, known))

        # Which file holds each permalink. Files not (re)read in this run —
        # unchanged, broken or skipped — keep the permalink of their entry.
        parsed_paths = {str(f) for f, _, _, _ in parsed}
        claims: Dict[str, List[str]] = {}
        for e in entities:
            if e.file_path in all_paths and e.file_path not in parsed_paths:
                claims.setdefault(e.permalink, []).append(e.file_path)
        for f, note, _, _ in parsed:
            claims.setdefault(note.permalink, []).append(str(f))

        def winner(permalink: str) -> str:
            paths = claims[permalink]
            owner = by_permalink.get(permalink)
            if owner is not None and owner.file_path in paths:
                return owner.file_path
            return sorted(paths)[0]

        updates: List[Tuple[Path, Any, Dict, int]] = []  # file, note, meta, entity id
        new: List[Tuple[Path, Any, Dict]] = []
        taken_ids: Set[int] = set()
        by_known: List[Tuple[Path, Any, Dict, Any]] = []
        for file_path, note, file_meta, known in parsed:
            first = winner(note.permalink)
            if first != str(file_path):
                stats["errors"] += 1
                self._record_failure(
                    str(file_path),
                    f"same permalink '{note.permalink}' as {first}; give one of them "
                    "another 'permalink:' in its frontmatter", file_meta["checksum"])
                continue
            owner = by_permalink.get(note.permalink)
            if owner is not None:
                updates.append((file_path, note, file_meta, owner.id))
                taken_ids.add(owner.id)
            else:
                by_known.append((file_path, note, file_meta, known))
        for file_path, note, file_meta, known in by_known:
            if known is not None and known.id not in taken_ids:
                # the permalink changed inside the file: same note, keep its id
                updates.append((file_path, note, file_meta, known.id))
                taken_ids.add(known.id)
            else:
                new.append((file_path, note, file_meta))

        # Moves/renames: a new file with the checksum of a note whose file is gone.
        gone_by_checksum: Dict[str, List] = {}
        for e in entities:
            if e.checksum and e.file_path not in all_paths:
                gone_by_checksum.setdefault(e.checksum, []).append(e)
        used: Set[int] = set(taken_ids)
        moved_new: List[Tuple[Path, object, Dict]] = []
        for file_path, note, file_meta in new:
            match = next((e for e in gone_by_checksum.get(file_meta["checksum"], [])
                          if e.id not in used), None)
            if match is None:
                moved_new.append((file_path, note, file_meta))
                continue
            used.add(match.id)
            try:
                self._index_file(db, file_path, note, file_meta, entity_id=match.id)
                stats["updated"] += 1
                self._clear_failure(str(file_path))
                logger.info(f"Detected move: {match.file_path} -> {file_path}")
            except Exception as e:
                stats["errors"] += 1
                self._record_failure(str(file_path), str(e), file_meta["checksum"])

        claimed_ids: Set[int] = set(used)
        path_owner: Dict[str, int] = {}
        for file_path, note, file_meta, eid in updates:
            try:
                got = self._index_file(db, file_path, note, file_meta, entity_id=eid)
                stats["updated"] += 1
                claimed_ids.add(got)
                path_owner[str(file_path)] = got
                self._clear_failure(str(file_path))
                logger.info(f"Updated: {note.permalink}")
            except Exception as e:
                stats["errors"] += 1
                self._record_failure(str(file_path), str(e), file_meta["checksum"])

        for file_path, note, file_meta in moved_new:
            try:
                got = self._index_file(db, file_path, note, file_meta)
                stats["added"] += 1
                claimed_ids.add(got)
                path_owner[str(file_path)] = got
                self._clear_failure(str(file_path))
                logger.info(f"Added: {note.permalink}")
            except Exception as e:
                stats["errors"] += 1
                self._record_failure(str(file_path), str(e), file_meta["checksum"])

        # Deletions: the file is gone (or hidden/ignored now), or another note
        # took over its file. Never from the `since` subset.
        for e in db.list_all_entities():
            if e.id in claimed_ids:
                continue
            gone = e.file_path not in all_paths
            taken = path_owner.get(e.file_path) not in (None, e.id)
            if not (gone or taken):
                continue
            try:
                db.delete_entity(e.permalink)
                stats["deleted"] += 1
                logger.info(f"Deleted: {e.permalink}")
            except Exception as ex:
                stats["errors"] += 1
                logger.error(f"Failed to delete {e.permalink}: {ex}")


def clear_note_cache() -> None:
    global _note_cache
    _note_cache.clear()


# Phase 1 (MemoPad import): .bmignore support
def _load_bmignore_patterns(knowledge_dir: Path) -> List[str]:
    """Load ignore patterns from .bmignore file in knowledge root."""
    bmignore = knowledge_dir / ".bmignore"
    if not bmignore.exists():
        return []
    patterns = []
    try:
        text = bmignore.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:  # 2026-10-23: never abort the sync
        logger.warning(f"Could not read {bmignore}: {e}")
        return []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


_GLOB_CHARS = ("*", "?", "[")


def _should_ignore_path(path: Path, knowledge_dir: Path, patterns: List[str]) -> bool:
    """Check if path matches any ignore pattern.

    Prefix patterns work as before (``drafts/``, ``notes/old.md``).
    2026-10-23 (KB phase 0): glob patterns too (fnmatch): ``*.tmp``,
    ``archive/**``, ``draft-*/``. A pattern without ``/`` is matched against
    every file/folder name; a pattern with ``/`` against the path from the
    knowledge folder (and each parent folder of it); ``name/`` matches folders
    only; a leading ``/`` is ignored.
    """
    try:
        rel = path.relative_to(knowledge_dir).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    dir_prefixes = ["/".join(parts[:i]) for i in range(1, len(parts))]
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel.startswith(pattern.rstrip("/")):
                return True
        else:
            if rel == pattern or rel.startswith(pattern + "/"):
                return True
        if not any(ch in pattern for ch in _GLOB_CHARS):
            continue
        dir_only = pattern.endswith("/")
        pat = pattern.strip("/")
        if not pat:
            continue
        if pat.endswith("/**"):  # everything below a folder
            base = pat[:-3]
            if any(fnmatch.fnmatchcase(d, base) for d in dir_prefixes):
                return True
            continue
        if "/" in pat:
            targets = dir_prefixes if dir_only else dir_prefixes + [rel]
        else:
            targets = parts[:-1] if dir_only else parts
        if any(fnmatch.fnmatchcase(t, pat) for t in targets):
            return True
    return False


def _list_markdown_files(knowledge_dir: Path, patterns: List[str]) -> Set[Path]:
    """Every indexable ``.md`` file below ``knowledge_dir``.

    2026-10-23 (KB phase 0): folders and files whose name starts with "."
    are never indexed (``.trash`` holds deleted notes; ``.git``,
    ``.obsidian``); unreadable folders are skipped instead of aborting.
    """
    files: Set[Path] = set()
    seen_dirs: Set[str] = set()
    for root, dirs, names in os.walk(knowledge_dir, followlinks=True):
        try:
            real = os.path.realpath(root)
        except OSError:
            real = root
        if real in seen_dirs:  # a symlink loop
            dirs[:] = []
            continue
        seen_dirs.add(real)
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in names:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            file_path = Path(root) / name
            if not _should_ignore_path(file_path, knowledge_dir, patterns):
                files.add(file_path)
    return files


def _filter_since(files: Set[Path], since_timestamp: Optional[float]) -> Set[Path]:
    """The files modified after ``since_timestamp`` (all of them for None)."""
    if since_timestamp is None:
        return set(files)
    out: Set[Path] = set()
    for file_path in files:
        try:
            if file_path.stat().st_mtime <= since_timestamp:
                continue
        except OSError:
            pass  # cannot stat: let the caller try (and count the error)
        out.add(file_path)
    return out


def scan_markdown_files(user_id: str = "default", since_timestamp: Optional[float] = None) -> Set[Path]:
    """
    Scan the knowledge directory for all Markdown files.

    Phase 1 (MemoPad import): respects .bmignore patterns.
    Phase 4 (MemoPad import): supports incremental scanning with since_timestamp.
    2026-10-23 (KB phase 0): skips folders/files whose name starts with ".".

    Args:
        user_id: User ID for isolation
        since_timestamp: Optional Unix timestamp; only return files modified after this

    Returns:
        Set of Path objects for all .md files
    """
    knowledge_dir = _storage_module.get_knowledge_dir(user_id)
    patterns = _load_bmignore_patterns(knowledge_dir)
    return _filter_since(_list_markdown_files(knowledge_dir, patterns), since_timestamp)


def resolve_forward_references(user_id: str = "default", db_path: Optional[Path] = None) -> Dict[str, int]:
    """
    Resolve forward references in relations.

    2026-10-23 (KB phase 0): links to a note that does not exist yet are kept
    as unresolved links by ``KnowledgeDB.sync_entity_from_note`` and resolved
    automatically when that note is indexed. This resolves every unresolved
    link whose target exists now (by permalink, slug, name, title or alias).
    It no longer creates placeholder entities: they pointed at files that do
    not exist, so the next sync deleted them (and their links) again.

    Args:
        user_id: User ID for isolation
        db_path: Optional custom DB path

    Returns:
        Stats dict: "resolved" (links resolved), "created" (always 0),
        "errors"
    """
    stats = {"resolved": 0, "created": 0, "errors": 0}
    try:
        with KnowledgeDB(user_id, db_path=db_path) as db:
            stats["resolved"] = db.resolve_pending_relations()["resolved"]
    except Exception as e:
        stats["errors"] += 1
        logger.warning(f"Could not resolve links: {e}")
    return stats


def get_db_file_mapping(user_id: str = "default", db_path: Optional[Path] = None) -> Dict[str, str]:
    """
    Get mapping of permalinks to file paths from database.

    Args:
        user_id: User ID for isolation
        db_path: Optional custom DB path

    Returns:
        Dict mapping permalink -> file_path
    """
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entities = db.list_all_entities()
        return {e.permalink: e.file_path for e in entities}


def detect_moves(user_id: str, new_files: List[Path],
                 checksums: Dict[str, str], db_path: Optional[Path] = None) -> Dict[str, str]:
    """
    Detect moved/renamed files by matching checksums.

    Phase 1 (MemoPad import): if a new file has the same checksum as an
    existing DB entry with a different file_path, it's a move.

    Args:
        user_id: User ID for isolation
        new_files: List of new file paths
        checksums: Dict of file_path -> checksum

    Returns:
        Dict mapping old_file_path -> new_file_path for detected moves
    """
    moves: Dict[str, str] = {}
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db_entities = db.list_all_entities()

    db_by_checksum: Dict[str, List] = {}
    for e in db_entities:
        if e.checksum:
            db_by_checksum.setdefault(e.checksum, []).append(e)

    for file_path in new_files:
        file_checksum = checksums.get(str(file_path))
        if not file_checksum:
            continue
        candidates = db_by_checksum.get(file_checksum, [])
        for candidate in candidates:
            if candidate.file_path and candidate.file_path != str(file_path):
                old_path = Path(candidate.file_path)
                if not old_path.exists():
                    moves[candidate.file_path] = str(file_path)
                    break

    return moves


def detect_changes(
    user_id: str = "default",
    db_path: Optional[Path] = None
) -> ChangeSet:
    files = scan_markdown_files(user_id)
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db_mapping = db.list_all_entities()
    db_by_permalink = {e.permalink: e for e in db_mapping}
    db_by_checksum: Dict[str, List] = {}
    for e in db_mapping:
        if e.checksum:
            db_by_checksum.setdefault(e.checksum, []).append(e)

    to_add: List[Path] = []
    to_update: List[str] = []
    checksums: Dict[str, str] = {}

    for file_path in files:
        try:
            note = _get_cached_note(file_path)
            file_meta = _storage_module.get_file_metadata(file_path)
            file_checksum = file_meta["checksum"]

            if note.permalink not in db_by_permalink:
                to_add.append(file_path)
                checksums[str(file_path)] = file_checksum
            else:
                db_entity = db_by_permalink[note.permalink]
                db_file_path = Path(db_entity.file_path) if db_entity.file_path else None

                if db_file_path and db_file_path != file_path:
                    to_update.append(note.permalink)
                    checksums[str(file_path)] = file_checksum
                    continue

                if db_entity.checksum != file_checksum:
                    to_update.append(note.permalink)
                    checksums[str(file_path)] = file_checksum
        except Exception as e:
            logger.warning(f"Failed to process {file_path}: {e}")

    file_permalinks = set()
    for file_path in files:
        try:
            note = _get_cached_note(file_path)
            file_permalinks.add(note.permalink)
        except Exception:
            pass
    # 2026-10-23 (KB phase 0): a file that cannot be parsed keeps its entry
    file_paths = {str(f) for f in files}
    to_delete = [p for p, e in db_by_permalink.items()
                 if p not in file_permalinks and e.file_path not in file_paths]

    return ChangeSet(to_add=to_add, to_update=to_update, to_delete=to_delete, checksums=checksums)


def sync_knowledge(user_id: str = "default", force: bool = False, db_path: Optional[Path] = None,
                   since: Optional[float] = None) -> Dict[str, int]:
    """
    Synchronize filesystem with database.

    Phase 1 (MemoPad import): uses checksum-based change detection,
    circuit breaker for failures, and move detection.
    Phase 4 (MemoPad import): supports incremental scanning with watermarks.

    Args:
        user_id: User ID for isolation
        force: If True, re-sync all files regardless of timestamps
        db_path: Optional custom DB path
        since: Optional Unix timestamp; only scan files modified after this

    Returns:
        Stats dict with counts of added, updated, deleted, errors
    """
    engine = SyncEngine(user_id=user_id, db_path=db_path)
    return engine.sync(force=force, since=since)


def verify_sync(user_id: str = "default") -> bool:
    """
    Verify that filesystem and database are in sync.

    Args:
        user_id: User ID for isolation

    Returns:
        True if in sync, False otherwise
    """
    files = scan_markdown_files(user_id)
    db_mapping = get_db_file_mapping(user_id)
    for file_path in files:
        try:
            note = parse_note(file_path)
            if note.permalink not in db_mapping:
                logger.warning(f"File not in DB: {file_path}")
                return False
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
            return False
    for permalink in db_mapping:
        file_path = Path(db_mapping[permalink])
        if not file_path.exists():
            logger.warning(f"DB entry missing file: {permalink}")
            return False
    logger.info("Sync verification passed")
    return True


def sync_and_report(user_id: str = "default") -> str:
    """
    Sync knowledge and return a user-friendly report.

    Args:
        user_id: User ID for isolation

    Returns:
        Human-readable sync report
    """
    stats = sync_knowledge(user_id)

    lines = ["Knowledge Sync Report", "",
             f"  Added: {stats['added']}",
             f"  Updated: {stats['updated']}",
             f"  Deleted: {stats['deleted']}"]
    if stats['errors'] > 0:
        lines.append(f"  Errors: {stats['errors']}")
    total = stats['added'] + stats['updated'] + stats['deleted']
    lines += ["", f"Total changes: {total}"]
    return "\n".join(lines)


# ── Background Knowledge Extraction ─────────────────────────────────────────────
_background_extraction_task: "asyncio.Task | None" = None  # was never defined → NameError at start

async def _background_extraction_loop(user_id: str, interval_seconds: int = 60):
    """
    Background loop that periodically extracts knowledge from markdown files.
    
    Args:
        user_id: User ID for isolation
        interval_seconds: How often to run the sync (default: 60 seconds)
    """
    logger.info(f"Starting background knowledge extraction for user: {user_id}")
    
    while True:
        try:
            # Run sync in thread to avoid blocking the event loop
            stats = await asyncio.to_thread(sync_knowledge, user_id)
            
            total_changes = stats['added'] + stats['updated'] + stats['deleted']
            if total_changes > 0:
                logger.info(f"Background sync completed: {total_changes} changes ({stats['added']} added, {stats['updated']} updated, {stats['deleted']} deleted)")
            
        except asyncio.CancelledError:
            logger.info("Background knowledge extraction cancelled")
            raise
        except Exception as e:
            logger.error(f"Background knowledge extraction error: {e}")
        
        # Wait for next interval
        await asyncio.sleep(interval_seconds)


def start_background_extraction(
    user_id: str = "default",
    interval_seconds: int = 60,
    loop: asyncio.AbstractEventLoop | None = None
) -> asyncio.Task:
    """
    Start background knowledge extraction as an asyncio task.
    
    Args:
        user_id: User ID for isolation
        interval_seconds: How often to run the sync (default: 60 seconds)
        loop: Optional event loop to use. If None, uses asyncio.get_event_loop()
        
    Returns:
        The created asyncio Task
    """
    global _background_extraction_task
    
    # Cancel existing task if running
    if _background_extraction_task is not None and not _background_extraction_task.done():
        _background_extraction_task.cancel()
        logger.info("Cancelled existing background extraction task")
    
    # Get or create event loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
            # If we're in an existing loop, we need to create the task in that loop
            _background_extraction_task = loop.create_task(
                _background_extraction_loop(user_id, interval_seconds)
            )
        except RuntimeError:
            # No running loop: a task on a new, never-run loop would never run
            # (and replaced the caller's current loop). Start it from a running
            # loop instead (the gateway does so when the bot starts).
            raise RuntimeError("start_background_extraction() needs a running event loop")
    else:
        _background_extraction_task = loop.create_task(
            _background_extraction_loop(user_id, interval_seconds)
        )
    
    logger.info(f"Background knowledge extraction started (interval: {interval_seconds}s)")
    return _background_extraction_task


def stop_background_extraction() -> bool:
    """
    Stop the background knowledge extraction task.
    
    Returns:
        True if a task was stopped, False if no task was running
    """
    global _background_extraction_task
    
    if _background_extraction_task is not None and not _background_extraction_task.done():
        _background_extraction_task.cancel()
        _background_extraction_task = None
        logger.info("Background knowledge extraction stopped")
        return True
    
    return False


def is_background_extraction_running() -> bool:
    """
    Check if background knowledge extraction is currently running.
    
    Returns:
        True if running, False otherwise
    """
    return _background_extraction_task is not None and not _background_extraction_task.done()
