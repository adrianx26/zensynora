"""
Markdown parsing for knowledge notes.

Supports:
- YAML frontmatter (title, permalink, tags, created, updated)
- Observations: - [category] Content #tag1 #tag2
- Relations: - relation_type [[TargetEntity]]
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


@dataclass
class Observation:
    """A fact or observation about an entity."""
    category: str
    content: str
    tags: List[str] = field(default_factory=list)


@dataclass
class Relation:
    """A directed relationship between entities."""
    relation_type: str
    target: str  # permalink of target entity


@dataclass
class Note:
    """A complete knowledge note."""
    name: str
    permalink: str
    title: str
    content: str  # raw markdown content
    tags: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    observations: List[Observation] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)
    file_path: Optional[Path] = None


# Regex patterns
FRONTMATTER_PATTERN = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
OBSERVATION_PATTERN = re.compile(r'^- \[([^\]]+)\] (.+)$', re.MULTILINE)
RELATION_PATTERN = re.compile(r'^- (\w+) \[\[([^\]]+)\]\]$', re.MULTILINE)
TAG_PATTERN = re.compile(r'#(\w+)')


def parse_frontmatter(content: str) -> Dict:
    """
    Parse YAML frontmatter from markdown content.
    
    Args:
        content: Raw markdown content
        
    Returns:
        Dict with frontmatter fields (title, permalink, tags, etc.)
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}
    
    frontmatter_text = match.group(1)
    result = {}
    
    # Simple YAML parsing (key: value or key: [item1, item2])
    for line in frontmatter_text.strip().split('\n'):
        line = line.strip()
        if ':' not in line:
            continue
            
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        
        # Handle arrays
        if value.startswith('[') and value.endswith(']'):
            # Parse [item1, item2] format
            items = value[1:-1].split(',')
            result[key] = [item.strip().strip('"\'') for item in items if item.strip()]
        elif value.startswith('"') and value.endswith('"'):
            result[key] = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            result[key] = value[1:-1]
        else:
            result[key] = value
    
    return result


def parse_observations(content: str) -> List[Observation]:
    """
    Parse observations from markdown content.
    
    Format: - [category] Content text #tag1 #tag2
    
    Args:
        content: Raw markdown content
        
    Returns:
        List of Observation objects
    """
    observations = []
    
    # Find the Observations section
    obs_section = re.search(
        r'##?\s*(?:Observations?|Notes?|Facts?)\s*\n(.*?)(?:\n##|\Z)',
        content,
        re.IGNORECASE | re.DOTALL
    )
    
    if obs_section:
        section_content = obs_section.group(1)
    else:
        # Look for observations anywhere in the document
        section_content = content
    
    for match in OBSERVATION_PATTERN.finditer(section_content):
        category = match.group(1).strip()
        content_text = match.group(2).strip()
        
        # Extract tags from content
        tags = TAG_PATTERN.findall(content_text)
        # Remove tags from content
        clean_content = TAG_PATTERN.sub('', content_text).strip()
        
        observations.append(Observation(
            category=category,
            content=clean_content,
            tags=tags
        ))
    
    return observations


def parse_relations(content: str) -> List[Relation]:
    """
    Parse relations from markdown content.
    
    Format: - relation_type [[TargetEntity]]
    
    Args:
        content: Raw markdown content
        
    Returns:
        List of Relation objects
    """
    relations = []
    
    # Find the Relations section
    rel_section = re.search(
        r'##?\s*(?:Relations?|Links?|Connections?)\s*\n(.*?)(?:\n##|\Z)',
        content,
        re.IGNORECASE | re.DOTALL
    )
    
    if rel_section:
        section_content = rel_section.group(1)
    else:
        # Look for relations anywhere in the document
        section_content = content
    
    for match in RELATION_PATTERN.finditer(section_content):
        relation_type = match.group(1).strip()
        target = match.group(2).strip()
        
        relations.append(Relation(
            relation_type=relation_type,
            target=target
        ))
    
    return relations


def parse_datetime(value: str) -> Optional[datetime]:
    """Parse ISO format datetime string."""
    if not value:
        return None
    try:
        # Handle various ISO formats
        value = value.replace('Z', '+00:00')
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def parse_note(file_path: Path) -> Note:
    """
    Parse a complete note from a markdown file.
    
    Args:
        file_path: Path to the markdown file
        
    Returns:
        Note object with all parsed data
    """
    content = file_path.read_text(encoding='utf-8')
    
    # Parse frontmatter
    frontmatter = parse_frontmatter(content)
    
    # Get base name for defaults
    base_name = file_path.stem
    
    # Build note
    note = Note(
        name=frontmatter.get('name', base_name),
        permalink=frontmatter.get('permalink', base_name.lower().replace(' ', '-')),
        title=frontmatter.get('title', base_name),
        content=content,
        tags=frontmatter.get('tags', []),
        created_at=parse_datetime(frontmatter.get('created')),
        updated_at=parse_datetime(frontmatter.get('updated')),
        observations=parse_observations(content),
        relations=parse_relations(content),
        file_path=file_path
    )
    
    return note


def generate_markdown(note: Note) -> str:
    """
    Generate markdown content from a Note object.
    
    Args:
        note: Note object to serialize
        
    Returns:
        Markdown string
    """
    lines = []
    
    # Frontmatter
    lines.append('---')
    lines.append(f'title: "{note.title}"')
    lines.append(f'permalink: {note.permalink}')
    if note.tags:
        tags_str = ', '.join(f'"{tag}"' for tag in note.tags)
        lines.append(f'tags: [{tags_str}]')
    if note.created_at:
        lines.append(f'created: {note.created_at.isoformat()}')
    if note.updated_at:
        lines.append(f'updated: {note.updated_at.isoformat()}')
    lines.append('---')
    lines.append('')
    
    # Title
    lines.append(f'# {note.title}')
    lines.append('')
    
    # Observations section
    if note.observations:
        lines.append('## Observations')
        lines.append('')
        for obs in note.observations:
            tags_str = ' '.join(f'#{tag}' for tag in obs.tags) if obs.tags else ''
            lines.append(f'- [{obs.category}] {obs.content} {tags_str}'.strip())
        lines.append('')
    
    # Relations section
    if note.relations:
        lines.append('## Relations')
        lines.append('')
        for rel in note.relations:
            lines.append(f'- {rel.relation_type} [[{rel.target}]]')
        lines.append('')
    
    return '\n'.join(lines)
