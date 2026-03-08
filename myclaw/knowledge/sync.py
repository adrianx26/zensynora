"""
File-to-database synchronization for knowledge storage.

Ensures the SQLite index stays in sync with Markdown files.
"""

import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple
from datetime import datetime

from .db import KnowledgeDB
from .parser import parse_note
from .storage import get_knowledge_dir

logger = logging.getLogger(__name__)


def scan_markdown_files(user_id: str = "default") -> Set[Path]:
    """
    Scan the knowledge directory for all Markdown files.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Set of Path objects for all .md files
    """
    knowledge_dir = get_knowledge_dir(user_id)
    return set(knowledge_dir.glob("*.md"))


def get_db_file_mapping(user_id: str = "default") -> Dict[str, str]:
    """
    Get mapping of permalinks to file paths from database.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Dict mapping permalink -> file_path
    """
    with KnowledgeDB(user_id) as db:
        entities = db.list_all_entities()
        return {e.permalink: e.file_path for e in entities}


def detect_changes(
    user_id: str = "default"
) -> Tuple[List[Path], List[str], List[str]]:
    """
    Detect changes between filesystem and database.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Tuple of (to_add, to_update, to_delete)
        - to_add: List of new file paths
        - to_update: List of permalinks with modified files
        - to_delete: List of permalinks to remove
    """
    files = scan_markdown_files(user_id)
    db_mapping = get_db_file_mapping(user_id)
    
    to_add = []
    to_update = []
    
    for file_path in files:
        try:
            note = parse_note(file_path)
            
            if note.permalink not in db_mapping:
                # New file
                to_add.append(file_path)
            elif db_mapping[note.permalink] != str(file_path):
                # File moved or renamed
                to_update.append(note.permalink)
            else:
                # Check if file was modified
                db_entity = None
                with KnowledgeDB(user_id) as db:
                    db_entity = db.get_entity_by_permalink(note.permalink)
                
                if db_entity:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    db_updated = db_entity.updated_at
                    
                    # Allow 1 second tolerance for filesystem differences
                    if file_mtime > db_updated:
                        to_update.append(note.permalink)
                        
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
    
    # Find deleted files
    file_permalinks = set()
    for file_path in files:
        try:
            note = parse_note(file_path)
            file_permalinks.add(note.permalink)
        except:
            pass
    
    to_delete = [p for p in db_mapping if p not in file_permalinks]
    
    return to_add, to_update, to_delete


def sync_knowledge(user_id: str = "default", force: bool = False) -> Dict[str, int]:
    """
    Synchronize filesystem with database.
    
    Args:
        user_id: User ID for isolation
        force: If True, re-sync all files regardless of timestamps
        
    Returns:
        Stats dict with counts of added, updated, deleted, errors
    """
    stats = {"added": 0, "updated": 0, "deleted": 0, "errors": 0}
    
    with KnowledgeDB(user_id) as db:
        if force:
            # Full re-sync: clear and re-add all
            logger.info("Performing full re-sync...")
            
            # Get all existing entities to delete
            existing = db.list_all_entities()
            for entity in existing:
                db.delete_entity(entity.permalink)
                stats["deleted"] += 1
            
            # Re-add all files
            files = scan_markdown_files(user_id)
            for file_path in files:
                try:
                    note = parse_note(file_path)
                    db.sync_entity_from_note(note)
                    stats["added"] += 1
                    logger.info(f"Synced: {note.permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to sync {file_path}: {e}")
        else:
            # Incremental sync
            to_add, to_update, to_delete = detect_changes(user_id)
            
            # Add new files
            for file_path in to_add:
                try:
                    note = parse_note(file_path)
                    db.sync_entity_from_note(note)
                    stats["added"] += 1
                    logger.info(f"Added: {note.permalink}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to add {file_path}: {e}")
            
            # Update modified files
            for permalink in to_update:
                try:
                    # Find the file
                    knowledge_dir = get_knowledge_dir(user_id)
                    # Try to find by permalink
                    file_path = knowledge_dir / f"{permalink}.md"
                    if file_path.exists():
                        note = parse_note(file_path)
                        db.sync_entity_from_note(note)
                        stats["updated"] += 1
                        logger.info(f"Updated: {permalink}")
                except Exception as e:
                    stats["errors"] += 1
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
