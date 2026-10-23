"""
Markdown parsing for knowledge notes.

Supports:
- YAML frontmatter (title, permalink, tags, created, updated)
- Observations: - [category] Content #tag1 #tag2
- Relations: - relation_type [[TargetEntity]]
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional


@dataclass
class Observation:
    """A fact or observation about an entity."""
    category: str
    content: str
    tags: List[str] = field(default_factory=list)
    context: Optional[str] = None  # Phase 2 (MemoPad import): provenance note
    provenance_path: Optional[str] = None  # Phase 2 (MemoPad import): source file path


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
    # 2026-10-23 (KB phase 0): frontmatter ``aliases:`` and every other
    # frontmatter key (``type`` included) — stored in entity_aliases /
    # entities.entity_metadata so alias and metadata search work.
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# Regex patterns
# 2026-10-23 (KB phase 0): tolerate a UTF-8 BOM, CRLF line ends and a closing
# '---' at the very end of the file.
FRONTMATTER_PATTERN = re.compile(r'^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.DOTALL)
# 2026-10-23 (KB phase 0): markdown task boxes ``- [ ] todo`` / ``- [x] done``
# are body text, not observations.
OBSERVATION_PATTERN = re.compile(r'^- \[(?![ xX]\])([^\]]+)\] (.+)$', re.MULTILINE)
RELATION_PATTERN = re.compile(r'^- (\w+) \[\[([^\]]+)\]\]$', re.MULTILINE)
TAG_PATTERN = re.compile(r'#(\w+)')

# Frontmatter keys that are always a list of strings.
LIST_KEYS = ("tags", "aliases")
# Frontmatter keys that have their own Note field (everything else is metadata).
RESERVED_KEYS = frozenset({"title", "permalink", "tags", "aliases", "created", "updated", "name"})
_KEY_LINE = re.compile(r'^([A-Za-z_][\w\-. ]*?)\s*:\s*(.*)$')  # 'key:value' too, as before
_PLAIN_VALUE = re.compile(r'^[A-Za-z0-9_][\w .,/+@()\-]*$')


def _unquote(value: str) -> str:
    """Strip one pair of surrounding quotes ("…" is JSON/YAML-escaped)."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            out = json.loads(v)
            if isinstance(out, str):
                return out
        except ValueError:
            pass
        return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def _split_inline(inner: str) -> List[str]:
    """Split ``a, "b, c", 'd'`` on commas outside quotes."""
    items: List[str] = []
    buf: List[str] = []
    quote = ""
    escaped = False
    for ch in inner:
        if quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == '\\' and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'" and not "".join(buf).strip():  # quote opens an item only
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [_unquote(i) for i in items if i.strip()]


def _strip_comment(value: str) -> str:
    """Drop a YAML comment (`` # …``) after an unquoted value."""
    if value[:1] in "\"'[{":
        return value
    m = re.search(r'\s+#\s', value)
    return value[:m.start()] if m else value


def _parse_scalar(value: str) -> Any:
    """One frontmatter value: inline list, JSON object, quoted or plain text."""
    v = value.strip()
    if v.startswith('[') and v.endswith(']'):
        return _split_inline(v[1:-1])
    if v.startswith('{') and v.endswith('}'):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return _unquote(_strip_comment(v))


def _clean_list(value: Any, key: str = "tags") -> List[str]:
    """``tags`` / ``aliases`` as a list of clean strings.

    Accepts a list, ``a, b`` and (for tags) ``#a #b`` / ``a b``. Quotes and a
    leading ``#`` are removed, empty and repeated items dropped.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value if v is not None and not isinstance(v, (list, dict))]
    else:
        text = str(value).strip()
        if text.startswith('[') and text.endswith(']'):
            raw = _split_inline(text[1:-1])
        elif ',' in text:
            raw = _split_inline(text)
        elif key == "tags":
            raw = text.split()
        else:
            raw = [text]
    out: List[str] = []
    for item in raw:
        clean = _unquote(item).strip()
        if key == "tags":
            clean = clean.lstrip('#').strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def _parse_frontmatter_text(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    lines = text.replace('\r\n', '\n').split('\n')
    key: Optional[str] = None      # last top-level key with an empty value
    block: Optional[str] = None    # '|' or '>' while reading a block scalar
    block_lines: List[str] = []

    def _end_block() -> None:
        nonlocal block, block_lines
        if block and key is not None:
            sep = '\n' if block == '|' else ' '
            result[key] = sep.join(block_lines).strip()
        block, block_lines = None, []

    for raw in lines:
        indented = raw[:1] in (' ', '\t')
        line = raw.strip()
        if block:
            if indented or not line:
                block_lines.append(line)
                continue
            _end_block()
        if not line or line.startswith('#'):
            continue
        if key is not None and (line.startswith('- ') or line == '-'):
            item = _parse_scalar(line[1:].strip())
            current = result.get(key)
            if not isinstance(current, list):
                current = []
            current.append(item if isinstance(item, str) else json.dumps(item))
            result[key] = current
            continue
        m = _KEY_LINE.match(line)
        if not m:
            continue
        k, value = m.group(1).strip(), (m.group(2) or '').strip()
        if indented and key is not None and not isinstance(result.get(key), list):
            # nested mapping under an empty key: ``source:\n  url: x``
            nested = result.get(key)
            if not isinstance(nested, dict):
                nested = {}
            nested[k] = _parse_scalar(value) if value else ''
            result[key] = nested
            continue
        key = None
        if not value:
            result[k] = ''
            key = k
        elif value in ('|', '|-', '|+', '>', '>-', '>+'):
            result[k] = ''
            key = k
            block = value[0]
        else:
            result[k] = _parse_scalar(value)
    _end_block()
    return result


def parse_frontmatter(content: str) -> Dict:
    """
    Parse YAML frontmatter from markdown content.

    2026-10-23 (KB phase 0): a small tolerant parser (no dependency): inline
    lists ``[a, b]``, block lists (``key:`` + ``- item`` lines), block
    scalars (``|`` / ``>``), one level of nested mapping, quoted values and
    comments. ``tags`` and ``aliases`` are always a list of clean strings —
    before, ``tags: foo`` gave the string "foo" (iterated as 'f', 'o', 'o')
    and ``tags: a, b`` or a block list were not understood. Other values stay
    strings (``true``, ``5``…). A broken frontmatter never raises.

    Args:
        content: Raw markdown content

    Returns:
        Dict with frontmatter fields (title, permalink, tags, etc.)
    """
    try:
        match = FRONTMATTER_PATTERN.match(content or '')
        if not match:
            return {}
        result = _parse_frontmatter_text(match.group(1))
        for k in LIST_KEYS:
            if k in result:
                result[k] = _clean_list(result[k], k)
        return result
    except Exception:  # pragma: no cover - never let a bad header break reading notes
        return {}


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
    
    def _text(key: str, default: str) -> str:
        # 2026-10-23 (KB phase 0): a list/empty value never becomes the name
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    # Build note
    note = Note(
        name=_text('name', base_name),
        permalink=_text('permalink', base_name.lower().replace(' ', '-')),
        title=_text('title', base_name),
        content=content,
        tags=_clean_list(frontmatter.get('tags'), 'tags'),
        created_at=parse_datetime(_text('created', '')),
        updated_at=parse_datetime(_text('updated', '')),
        observations=parse_observations(content),
        relations=parse_relations(content),
        file_path=file_path,
        # 2026-10-23 (KB phase 0): aliases + every other frontmatter key
        aliases=_clean_list(frontmatter.get('aliases'), 'aliases'),
        metadata={k: v for k, v in frontmatter.items() if k not in RESERVED_KEYS},
    )
    
    return note


_SECTION_HEADING = re.compile(r'^##?\s*(?:Observations?|Facts?|Relations?|Links?|Connections?)\s*$',
                              re.IGNORECASE | re.MULTILINE)


def extract_body(content: str) -> str:
    """The free text of a note: without frontmatter, the top '# Title' line,
    the Observations/Relations headings and the fact/link lines.

    Works on a whole note file and on plain text alike (2026-10-23).
    """
    text = FRONTMATTER_PATTERN.sub('', content or '', count=1)
    text = re.sub(r'^\s*# [^\n]*\n', '', text, count=1)  # the generated title heading
    text = OBSERVATION_PATTERN.sub('', text)
    text = RELATION_PATTERN.sub('', text)
    text = _SECTION_HEADING.sub('', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _yaml_item(value: Any) -> str:
    """A list item / value: JSON-quoted (valid YAML) unless plain text."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False)


def _metadata_line(key: Any, value: Any) -> Optional[str]:
    """``key: value`` / ``key: [..]`` for a metadata entry (None = skipped)."""
    k = str(key).strip()
    if not k or k in RESERVED_KEYS or not re.match(r'^[A-Za-z_][\w\-.]*$', k):
        return None
    if value is None:
        return f'{k}: ""'
    if isinstance(value, (list, tuple, set)):
        return f'{k}: [{", ".join(_yaml_item(v) for v in value)}]'
    if isinstance(value, dict):
        return f'{k}: {json.dumps(value, ensure_ascii=False, default=str)}'
    if isinstance(value, (bool, int, float)):
        return f'{k}: {_yaml_item(value)}'
    text = str(value)
    if _PLAIN_VALUE.match(text) and text == text.strip() and ' #' not in text:
        return f'{k}: {text}'
    return f'{k}: {_yaml_item(text)}'


def generate_markdown(note: Note) -> str:
    """
    Generate markdown content from a Note object.

    2026-10-23: the note's free text (``note.content`` — plain text or a whole
    note file) is written too. Before, the body was dropped, so callers passed
    raw content instead and every tag, fact and link was lost.

    Args:
        note: Note object to serialize
        
    Returns:
        Markdown string
    """
    lines = []
    
    # Frontmatter
    lines.append('---')
    title = (note.title or note.name).replace('"', "'").replace("\n", " ")
    # 2026-10-23 (KB phase 0): a backslash is escaped (the value is read back
    # as a JSON/YAML double-quoted string); tags quoted the same way.
    lines.append(f'title: "{title.replace(chr(92), chr(92) * 2)}"')
    lines.append(f'permalink: {note.permalink}')
    tags = _clean_list(note.tags, 'tags')
    if tags:
        tags_str = ', '.join(_yaml_item(tag) for tag in tags)
        lines.append(f'tags: [{tags_str}]')
    # 2026-10-23 (KB phase 0): aliases and the other frontmatter keys
    # (``type``, ``status``, ``source``…) survive a read → write round trip.
    aliases = _clean_list(note.aliases, 'aliases')
    if aliases:
        lines.append(f'aliases: [{", ".join(_yaml_item(a) for a in aliases)}]')
    for key, value in (note.metadata or {}).items():
        line = _metadata_line(key, value)
        if line:
            lines.append(line)
    if note.created_at:
        lines.append(f'created: {note.created_at.isoformat()}')
    if note.updated_at:
        lines.append(f'updated: {note.updated_at.isoformat()}')
    lines.append('---')
    lines.append('')
    
    # Title
    lines.append(f'# {title}')
    lines.append('')

    body = extract_body(note.content)
    if body:
        lines.append(body)
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
