"""
File storage operations for knowledge notes.

Handles reading/writing Markdown files to the knowledge directory.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..exceptions import KnowledgeBaseError, KnowledgeParseError
from .parser import Note, Observation, Relation, parse_note, generate_markdown
from .db import KnowledgeDB

logger = logging.getLogger(__name__)


def get_knowledge_dir(user_id: str = "default") -> Path:
    """Get the knowledge directory for a user."""
    knowledge_dir = Path.home() / ".myclaw" / "knowledge" / user_id
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    return knowledge_dir


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
        raise KnowledgeBaseError("Permalink cannot be empty")
    
    # Convert to lowercase, replace spaces with hyphens
    cleaned = permalink.lower().strip()
    cleaned = re.sub(r'[^\w\-]', '-', cleaned)
    cleaned = re.sub(r'-+', '-', cleaned)  # Collapse multiple hyphens
    cleaned = cleaned.strip('-')
    
    if not cleaned:
        raise KnowledgeParseError(
            f"Invalid permalink: {permalink}",
            permalink=permalink
        )
    
    return cleaned


def write_note(
    name: str,
    title: Optional[str] = None,
    observations: List[Observation] = None,
    relations: List[Relation] = None,
    tags: List[str] = None,
    user_id: str = "default",
    content: Optional[str] = None,  # Optional raw content
    db_path: Optional[Path] = None
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
        
    Returns:
        Permalink of created note
    """
    knowledge_dir = get_knowledge_dir(user_id)
    permalink = validate_permalink(name)
    
    file_path = knowledge_dir / f"{permalink}.md"
    
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
        file_path=file_path
    )
    
    # Generate or use provided content
    if content:
        markdown_content = content
    else:
        markdown_content = generate_markdown(note)
    
    # Write file
    file_path.write_text(markdown_content, encoding='utf-8')
    logger.info(f"Created note: {file_path}")
    
    # Sync to database
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db.sync_entity_from_note(parse_note(file_path))
    
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
    knowledge_dir = get_knowledge_dir(user_id)
    file_path = knowledge_dir / f"{permalink}.md"
    
    if not file_path.exists():
        return None
    
    return parse_note(file_path)


def delete_note(permalink: str, user_id: str = "default", db_path: Optional[Path] = None) -> bool:
    """
    Delete a note from the knowledge base.
    
    Args:
        permalink: Note permalink
        user_id: User ID for isolation
        
    Returns:
        True if deleted, False if not found
    """
    knowledge_dir = get_knowledge_dir(user_id)
    file_path = knowledge_dir / f"{permalink}.md"
    
    if not file_path.exists():
        return False
    
    # Delete file
    file_path.unlink()
    
    # Remove from database
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db.delete_entity(permalink)
    
    logger.info(f"Deleted note: {permalink}")
    return True


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
    
    # Get all markdown files
    for file_path in sorted(knowledge_dir.glob("*.md")):
        try:
            note = parse_note(file_path)
            # Filter by tags if specified
            if tags:
                if not any(tag in note.tags for tag in tags):
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
        **kwargs: Fields to update (title, observations, relations, tags)
        
    Returns:
        True if updated, False if not found
    """
    note = read_note(permalink, user_id, db_path=db_path)
    if not note:
        return False
    
    # Update fields
    if 'title' in kwargs:
        note.title = kwargs['title']
    if 'observations' in kwargs:
        note.observations = kwargs['observations']
    if 'relations' in kwargs:
        note.relations = kwargs['relations']
    if 'tags' in kwargs:
        note.tags = kwargs['tags']
    
    note.updated_at = datetime.now()
    
    # Regenerate content
    content = generate_markdown(note)
    note.file_path.write_text(content, encoding='utf-8')
    
    # Resync to database
    with KnowledgeDB(user_id, db_path=db_path) as db:
        db.sync_entity_from_note(note)
    
    logger.info(f"Updated note: {permalink}")
    return True


def search_notes(
    query: str,
    user_id: str = "default",
    limit: int = 10,
    db_path: Optional[Path] = None
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
        entities = db.search_fts(query, limit)
    
    notes = []
    for entity in entities:
        note = read_note(entity.permalink, user_id, db_path=db_path)
        if note:
            notes.append(note)
    
    return notes


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
    
    notes = []
    for entity in entities:
        note = read_note(entity.permalink, user_id, db_path=db_path)
        if note:
            notes.append(note)
    
    return notes


def get_all_tags(user_id: str = "default", db_path: Optional[Path] = None) -> List[str]:
    """
    Get all unique tags from all notes.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Sorted list of unique tags
    """
    tags = set()
    for note in list_notes(user_id, db_path=db_path):
        tags.update(note.tags)
        for obs in note.observations:
            tags.update(obs.tags)
    return sorted(tags)
