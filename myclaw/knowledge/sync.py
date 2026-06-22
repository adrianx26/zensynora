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
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime
from collections import OrderedDict

from .db import KnowledgeDB
from .parser import parse_note
from . import storage as _storage_module

logger = logging.getLogger(__name__)

# Background task reference for auto-extraction
_background_extraction_task: asyncio.Task | None = None

# Cache for parsed notes
_parsed_note_cache: dict[str, tuple] = {}  # path -> (note, mtime)

# Phase 1 (MemoPad import): circuit breaker for sync failures
MAX_CONSECUTIVE_FAILURES = 3
_sync_failures: OrderedDict[str, Dict] = OrderedDict()
_MAX_TRACKED_FAILURES = 100


def _record_sync_failure(path_str: str, error: str, checksum: Optional[str] = None):
    """Record a sync failure for circuit breaker tracking."""
    global _sync_failures
    now = datetime.now()
    if path_str in _sync_failures:
        info = _sync_failures.pop(path_str)
        info["count"] += 1
        info["last_failure"] = now
        info["last_error"] = error
        if checksum:
            info["last_checksum"] = checksum
        _sync_failures[path_str] = info
        logger.warning(f"Sync failure {info['count']}/{MAX_CONSECUTIVE_FAILURES} for {path_str}: {error}")
    else:
        _sync_failures[path_str] = {
            "count": 1,
            "first_failure": now,
            "last_failure": now,
            "last_error": error,
            "last_checksum": checksum or "",
        }
    while len(_sync_failures) > _MAX_TRACKED_FAILURES:
        _sync_failures.popitem(last=False)


def _should_skip_file(path_str: str, current_checksum: Optional[str] = None) -> bool:
    """Check if file should be skipped due to repeated failures."""
    if path_str not in _sync_failures:
        return False
    info = _sync_failures[path_str]
    if info["count"] < MAX_CONSECUTIVE_FAILURES:
        return False
    if current_checksum and current_checksum != info.get("last_checksum"):
        del _sync_failures[path_str]
        return False
    return True


def _clear_sync_failure(path_str: str):
    """Clear failure tracking after successful sync."""
    global _sync_failures
    if path_str in _sync_failures:
        del _sync_failures[path_str]


def _get_cached_note(file_path: Path):
    """Get note from cache or parse and cache it."""
    path_str = str(file_path)
    mtime = file_path.stat().st_mtime
    
    if path_str in _parsed_note_cache:
        cached_mtime, cached_note = _parsed_note_cache[path_str]
        if cached_mtime == mtime:
            return cached_note
    
    # Parse and cache
    note = parse_note(file_path)
    _parsed_note_cache[path_str] = (mtime, note)
    return note


def clear_note_cache():
    """Clear the parsed note cache."""
    global _parsed_note_cache
    _parsed_note_cache = {}


# Phase 1 (MemoPad import): .bmignore support
def _load_bmignore_patterns(knowledge_dir: Path) -> List[str]:
    """Load ignore patterns from .bmignore file in knowledge root."""
    bmignore = knowledge_dir / ".bmignore"
    if not bmignore.exists():
        return []
    patterns = []
    for line in bmignore.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _should_ignore_path(path: Path, knowledge_dir: Path, patterns: List[str]) -> bool:
    """Check if path matches any ignore pattern."""
    rel = path.relative_to(knowledge_dir).as_posix()
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel.startswith(pattern.rstrip("/")):
                return True
        else:
            if rel == pattern or rel.startswith(pattern + "/"):
                return True
    return False


def scan_markdown_files(user_id: str = "default", since_timestamp: Optional[float] = None) -> Set[Path]:
    """
    Scan the knowledge directory for all Markdown files.

    Phase 1 (MemoPad import): respects .bmignore patterns.
    Phase 4 (MemoPad import): supports incremental scanning with since_timestamp.

    Args:
        user_id: User ID for isolation
        since_timestamp: Optional Unix timestamp; only return files modified after this

    Returns:
        Set of Path objects for all .md files
    """
    knowledge_dir = _storage_module.get_knowledge_dir(user_id)
    patterns = _load_bmignore_patterns(knowledge_dir)
    files = set()
    for file_path in knowledge_dir.rglob("*.md"):
        if not _should_ignore_path(file_path, knowledge_dir, patterns):
            if since_timestamp is not None:
                if file_path.stat().st_mtime <= since_timestamp:
                    continue
            files.add(file_path)
    return files


def resolve_forward_references(user_id: str = "default", db_path: Optional[Path] = None) -> Dict[str, int]:
    """
    Resolve forward references in relations.

    Phase 2 (MemoPad import): when a relation targets an entity that doesn't exist,
    try to find it by alias or create a placeholder entity.

    Args:
        user_id: User ID for isolation
        db_path: Optional custom DB path

    Returns:
        Stats dict with counts of resolved and created
    """
    stats = {"resolved": 0, "created": 0, "errors": 0}
    with KnowledgeDB(user_id, db_path=db_path) as db:
        entities = db.list_all_entities()
        for entity in entities:
            relations = db.get_relations_from(entity.id)
            for rel_type, target_permalink, target_name in relations:
                target = db.get_entity_by_permalink(target_permalink)
                if target is None:
                    alias_match = db.get_entity_by_alias(target_permalink)
                    if alias_match:
                        db.add_relation(entity.id, rel_type, alias_match.id)
                        stats["resolved"] += 1
                    else:
                        placeholder_id = db.create_entity(
                            name=target_name or target_permalink,
                            permalink=target_permalink,
                            file_path=f"{target_permalink}.md",
                        )
                        db.add_relation(entity.id, rel_type, placeholder_id)
                        stats["created"] += 1
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
) -> Tuple[List[Path], List[str], List[str], Dict[str, str]]:
    """
    Detect changes between filesystem and database.

    Phase 1 (MemoPad import): uses checksum + mtime + size for robust detection.
    Also detects moves/renames via checksum matching.

    Args:
        user_id: User ID for isolation

    Returns:
        Tuple of (to_add, to_update, to_delete, checksums)
        - to_add: List of new file paths
        - to_update: List of permalinks with modified files
        - to_delete: List of permalinks to remove
        - checksums: Dict mapping file_path -> checksum for new/modified files
    """
    files = scan_markdown_files(user_id)
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db_mapping = db.list_all_entities()
    db_by_permalink = {e.permalink: e for e in db_mapping}
    db_by_file = {e.file_path: e for e in db_mapping}
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
            file_mtime = file_meta["mtime"]
            file_size = file_meta["size"]

            if note.permalink not in db_by_permalink:
                to_add.append(file_path)
                checksums[str(file_path)] = file_checksum
            else:
                db_entity = db_by_permalink[note.permalink]
                db_file_path = Path(db_entity.file_path) if db_entity.file_path else None

                # Check for move/rename: permalink exists but file_path changed
                if db_file_path and db_file_path != file_path:
                    # File was moved/renamed
                    to_update.append(note.permalink)
                    checksums[str(file_path)] = file_checksum
                    continue

                # Check if content changed via checksum
                if db_entity.checksum != file_checksum:
                    to_update.append(note.permalink)
                    checksums[str(file_path)] = file_checksum
        except Exception as e:
            logger.warning(f"Failed to process {file_path}: {e}")

    # Detect deleted files
    file_permalinks = set()
    for file_path in files:
        try:
            note = _get_cached_note(file_path)
            file_permalinks.add(note.permalink)
        except Exception:
            pass
    to_delete = [p for p in db_by_permalink if p not in file_permalinks]

    return to_add, to_update, to_delete, checksums


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
    stats = {"added": 0, "updated": 0, "deleted": 0, "errors": 0}

    with KnowledgeDB(user_id, db_path=db_path) as db:
        if force:
            logger.info("Performing full re-sync...")
            existing = db.list_all_entities()
            for entity in existing:
                db.delete_entity(entity.permalink)
                stats["deleted"] += 1

            files = scan_markdown_files(user_id, since_timestamp=since)
            for file_path in files:
                try:
                    file_meta = _storage_module.get_file_metadata(file_path)
                    note = parse_note(file_path)
                    db.sync_entity_from_note(
                        note,
                        checksum=file_meta["checksum"],
                        mtime=file_meta["mtime"],
                        size=file_meta["size"],
                    )
                    stats["added"] += 1
                    logger.info(f"Synced: {note.permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to sync {file_path}: {e}")
        else:
            # Phase 1 import: incremental sync with checksum detection
            to_add, to_update, to_delete, checksums = detect_changes(user_id, db_path=db_path)

            # Phase 1 import: detect moves before adding
            moves = detect_moves(user_id, to_add, checksums, db_path=db_path)
            for old_path, new_path in moves.items():
                try:
                    old_permalink = Path(old_path).stem
                    db_entity = None
                    with KnowledgeDB(user_id, db_path=db_path) as db:
                        db_entity = db.get_entity_by_file_path(old_path)
                    if db_entity:
                        with KnowledgeDB(user_id, db_path=db_path) as db:
                            db.update_entity_metadata(
                                db_entity.id,
                                checksum=checksums.get(new_path),
                                mtime=_storage_module.get_file_metadata(Path(new_path))["mtime"],
                                size=_storage_module.get_file_metadata(Path(new_path))["size"],
                            )
                            db.update_entity_timestamp(db_entity.id)
                        logger.info(f"Moved: {old_path} -> {new_path}")
                        stats["updated"] += 1
                        to_add = [f for f in to_add if str(f) != new_path]
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to move {old_path}: {e}")

            # Add new files
            for file_path in to_add:
                try:
                    file_checksum = checksums.get(str(file_path))
                    if file_checksum and _should_skip_file(str(file_path), file_checksum):
                        logger.warning(f"Skipping {file_path} due to repeated failures")
                        continue
                    note = parse_note(file_path)
                    file_meta = _storage_module.get_file_metadata(file_path)
                    db.sync_entity_from_note(
                        note,
                        checksum=file_meta["checksum"],
                        mtime=file_meta["mtime"],
                        size=file_meta["size"],
                    )
                    stats["added"] += 1
                    _clear_sync_failure(str(file_path))
                    logger.info(f"Added: {note.permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    _record_sync_failure(str(file_path), str(e), checksums.get(str(file_path)))
                    logger.error(f"Failed to add {file_path}: {e}")

            # Update modified files
            for permalink in to_update:
                try:
                    knowledge_dir = _storage_module.get_knowledge_dir(user_id)
                    file_path = knowledge_dir / f"{permalink}.md"
                    if file_path.exists():
                        file_checksum = checksums.get(str(file_path))
                        if file_checksum and _should_skip_file(str(file_path), file_checksum):
                            logger.warning(f"Skipping {permalink} due to repeated failures")
                            continue
                        note = parse_note(file_path)
                        file_meta = _storage_module.get_file_metadata(file_path)
                        db.sync_entity_from_note(
                            note,
                            checksum=file_meta["checksum"],
                            mtime=file_meta["mtime"],
                            size=file_meta["size"],
                        )
                        stats["updated"] += 1
                        _clear_sync_failure(str(file_path))
                        logger.info(f"Updated: {permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    file_path = knowledge_dir / f"{permalink}.md"
                    _record_sync_failure(str(file_path), str(e), checksums.get(str(file_path)))
                    logger.error(f"Failed to update {permalink}: {e}")

            # Delete removed files
            for permalink in to_delete:
                try:
                    db.delete_entity(permalink)
                    stats["deleted"] += 1
                    logger.info(f"Deleted: {permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to delete {permalink}: {e}")

    total = stats["added"] + stats["updated"] + stats["deleted"]
    logger.info(f"Sync complete: {total} changes ({stats['added']} added, {stats['updated']} updated, {stats['deleted']} deleted, {stats['errors']} errors)")

    return stats


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
    
    # Check all files are in DB
    for file_path in files:
        try:
            note = parse_note(file_path)
            if note.permalink not in db_mapping:
                logger.warning(f"File not in DB: {file_path}")
                return False
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
            return False
    
    # Check all DB entries have files
    for permalink in db_mapping:
        file_path = Path(db_mapping[permalink])
        if not file_path.exists():
            logger.warning(f"DB entry missing file: {permalink}")
            return False
    
    logger.info("Sync verification passed")
    return True


# Note: File watching is disabled to avoid watchdog dependency
# Users should manually run sync when needed or rely on the sync
# that happens automatically during write_note operations

def sync_and_report(user_id: str = "default") -> str:
    """
    Sync knowledge and return a user-friendly report.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Human-readable sync report
    """
    stats = sync_knowledge(user_id)
    
    lines = ["📚 Knowledge Sync Report"]
    lines.append("")
    lines.append(f"  ✅ Added: {stats['added']}")
    lines.append(f"  🔄 Updated: {stats['updated']}")
    lines.append(f"  🗑️ Deleted: {stats['deleted']}")
    
    if stats['errors'] > 0:
        lines.append(f"  ❌ Errors: {stats['errors']}")
    
    total = stats['added'] + stats['updated'] + stats['deleted']
    lines.append("")
    lines.append(f"Total changes: {total}")
    
    return "\n".join(lines)


# ── Background Knowledge Extraction ─────────────────────────────────────────────

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
            # No running loop, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _background_extraction_task = loop.create_task(
                _background_extraction_loop(user_id, interval_seconds)
            )
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
