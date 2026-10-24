"""
Markdown parsing for knowledge notes.

Supports:
- YAML frontmatter (title, permalink, tags, aliases, created, updated and any
  other key, kept as ``Note.metadata``)
- Observations: - [category] Content #tag1 #tag2
- Relations: - relation_type [[TargetEntity]]

2026-10-23 (KB phase 0): facts and links are read from every line of the note
body (outside fenced code blocks), and ``extract_body`` removes exactly those
lines, so nothing is lost when a note is rewritten. Frontmatter keys keep
their original lines when their value did not change.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


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
    # 2026-10-23 (KB phase 0): the original frontmatter lines per key
    # (key -> (lines, parsed value)); generate_markdown writes them back
    # unchanged when the value did not change. "" holds trailing comments.
    raw_frontmatter: Dict[str, Tuple[str, Any]] = field(
        default_factory=dict, repr=False, compare=False)


# Regex patterns
# 2026-10-23 (KB phase 0): tolerate a UTF-8 BOM, CRLF line ends and a closing
# '---' at the very end of the file.
FRONTMATTER_PATTERN = re.compile(
    r'^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.DOTALL)
# 2026-10-23 (KB phase 0): markdown task boxes ``- [ ] todo`` / ``- [x] done``
# are body text, not observations.
OBSERVATION_PATTERN = re.compile(r'^- \[(?![ xX]\])([^\]]+)\] (.+)$', re.MULTILINE)
RELATION_PATTERN = re.compile(r'^- (\w+) \[\[([^\]]+)\]\]$', re.MULTILINE)
TAG_PATTERN = re.compile(r'#(\w+)')
# The section headings generate_markdown writes (removed by extract_body).
_SECTION_HEADING = re.compile(r'^##\s+(?:Observations|Relations)\s*$', re.IGNORECASE)
_FENCE = re.compile(r'^\s*(`{3,}|~{3,})')

# Frontmatter keys that are always a list of strings.
LIST_KEYS = ("tags", "aliases")
# Frontmatter keys that have their own Note field (everything else is metadata).
RESERVED_KEYS = frozenset({"title", "permalink", "tags", "aliases", "created", "updated", "name"})
_BLOCK_SCALAR = ('|', '|-', '|+', '>', '>-', '>+')

# ``key: value`` / ``key:`` — the key may contain spaces, digits, non-ASCII
# letters; ``"quoted key": v`` and the old ``key:value`` are accepted too.
_QUOTED_KEY = re.compile(r'''^(?:"((?:[^"\\]|\\.)*)"|'((?:[^']|'')*)')\s*:(?:\s+(.*)|\s*)$''')
_PLAIN_KEY = re.compile(r'''^([^\s#\-\[\]{}"'|>!&*%@`,?:][^:]*?)\s*:(?:\s+(.*)|\s*)$''')
_NOSPACE_KEY = re.compile(r'^([A-Za-z_][\w\-]*):(\S.*)$')
_PLAIN_VALUE = re.compile(r'^[\w][\w .,/+@()\-]*$')
_SAFE_KEY = re.compile(r'^[\w][\w .\-/]*$')


# ── frontmatter: reading ─────────────────────────────────────────────────────

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
    if (v.startswith('[') and v.endswith(']')) or (v.startswith('{') and v.endswith('}')):
        try:
            return json.loads(v)
        except ValueError:
            if v.startswith('['):
                return _split_inline(v[1:-1])
            return v
    return _unquote(_strip_comment(v))


def _clean_list(value: Any, key: str = "tags") -> List[str]:
    """``tags`` / ``aliases`` as a list of clean strings.

    Accepts a list, ``a, b`` and (for tags) ``#a #b`` / ``a b``. Quotes and a
    leading ``#`` are removed, empty and repeated items dropped.
    """
    if value is None:
        return []
    raw: List[str]
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


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def _match_key(line: str) -> Optional[Tuple[str, str]]:
    """``(key, inline value)`` of a ``key: value`` line (stripped), else None."""
    m = _QUOTED_KEY.match(line)
    if m:
        key = _unquote(f'"{m.group(1)}"') if m.group(1) is not None else (m.group(2) or '').replace("''", "'")
        return key, (m.group(3) or '').strip()
    if line.startswith('- ') or line == '-' or line.startswith('#'):
        return None
    m = _PLAIN_KEY.match(line)
    if m:
        return m.group(1).strip(), (m.group(2) or '').strip()
    m = _NOSPACE_KEY.match(line)  # old notes: 'key:value'
    if m and not m.group(2).startswith('//'):
        return m.group(1), m.group(2).strip()
    return None


def _block_text(lines: List[str], style: str) -> str:
    """A ``|`` / ``>`` block scalar."""
    body = [ln for ln in lines]
    while body and not body[-1].strip():
        body.pop()
    ind = min((_indent(ln) for ln in body if ln.strip()), default=0)
    rows = [ln[ind:] if ln.strip() else '' for ln in body]
    if style.startswith('|'):
        return '\n'.join(rows)
    return ' '.join(r for r in rows if r)


def _parse_list(lines: List[str]) -> List[Any]:
    """``- item`` lines (the first line sets the indent)."""
    items: List[Any] = []
    base = _indent(lines[0])
    for ln in lines:
        s = ln.strip()
        if _indent(ln) <= base and (s.startswith('- ') or s == '-'):
            items.append(_parse_scalar(s[1:].strip()) if s != '-' else '')
        elif items and isinstance(items[-1], str):  # a wrapped item
            items[-1] = f"{items[-1]} {s}".strip()
    return items


def _parse_block(inline: str, children: List[str]) -> Any:
    """The value of a key from its inline text and its indented lines."""
    content = [c for c in children if c.strip() and not c.strip().startswith('#')]
    if inline in _BLOCK_SCALAR:
        return _block_text(children, inline)
    if inline:
        value = _parse_scalar(inline)
        if content and isinstance(value, str) and inline[:1] not in '"\'[{':
            value = ' '.join([value] + [c.strip() for c in content])  # wrapped plain text
        return value
    if not content:
        return ''
    first = content[0].strip()
    if first.startswith('- ') or first == '-':
        return _parse_list(content)
    return _parse_map(content)


def _parse_map(lines: List[str]) -> Dict[str, Any]:
    """An indented ``key: value`` block (nested map, any depth)."""
    out: Dict[str, Any] = {}
    base = _indent(lines[0])
    key: Optional[str] = None
    inline = ''
    children: List[str] = []

    def _flush() -> None:
        if key is not None:
            out[key] = _parse_block(inline, children)

    for ln in lines:
        if _indent(ln) <= base:
            kv = _match_key(ln.strip())
            if kv:
                _flush()
                key, inline = kv
                children = []
                continue
        children.append(ln)
    _flush()
    return out


def _frontmatter_entries(text: str) -> List[Tuple[str, str, Any]]:
    """``(key, original lines, value)`` per top-level key, in file order.

    Comment lines belong to the key that follows them; trailing comments are
    returned under the key ``""``.
    """
    entries: List[Tuple[str, str, Any]] = []
    pending: List[str] = []
    key: Optional[str] = None
    inline = ''
    raw: List[str] = []
    children: List[str] = []

    def _flush() -> None:
        if key is None:
            return
        while raw and not raw[-1].strip():
            raw.pop()
        while children and not children[-1].strip():
            children.pop()
        try:
            value = _parse_block(inline, children)
        except Exception:  # never let one odd value break the note
            value = '\n'.join(c.strip() for c in children) or inline
        entries.append((key, '\n'.join(raw), value))

    for line in text.replace('\r\n', '\n').split('\n'):
        stripped = line.strip()
        indented = line[:1] in (' ', '\t')
        if key is not None and (indented or not stripped or stripped.startswith('- ') or stripped == '-'):
            raw.append(line)
            children.append(line)
            continue
        if not stripped:
            continue
        kv = None if indented else _match_key(stripped)
        if kv is None:
            pending.append(line)  # a comment or a line we do not understand
            continue
        _flush()
        key, inline = kv
        raw = pending + [line]
        children = []
        pending = []
    _flush()
    if pending:
        entries.append(("", '\n'.join(pending), None))
    return entries


def _frontmatter_with_raw(content: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, Any]]]:
    """Frontmatter values and their original lines. Never raises."""
    try:
        match = FRONTMATTER_PATTERN.match(content or '')
        if not match:
            return {}, {}
        values: Dict[str, Any] = {}
        raws: Dict[str, Tuple[str, Any]] = {}
        for key, raw, value in _frontmatter_entries(match.group(1)):
            if key == "":
                raws[""] = (raw, None)
                continue
            if key in LIST_KEYS:
                value = _clean_list(value, key)
            values[key] = value
            raws[key] = (raw, value)
        return values, raws
    except Exception:  # pragma: no cover - a bad header must not break reading notes
        return {}, {}


def parse_frontmatter(content: str) -> Dict:
    """
    Parse YAML frontmatter from markdown content.

    2026-10-23 (KB phase 0): a small tolerant parser (no dependency): inline
    lists ``[a, b]`` and JSON values, block lists (``key:`` + ``- item``
    lines), block scalars (``|`` / ``>``), nested maps, quoted values and
    keys, keys with spaces / digits / non-ASCII letters, and comments.
    ``tags`` and ``aliases`` are always a list of clean strings — before,
    ``tags: foo`` gave the string "foo" (iterated as 'f', 'o', 'o') and
    ``tags: a, b`` or a block list were not understood. Other values stay
    strings (``true``, ``5``…). A broken frontmatter never raises.

    Args:
        content: Raw markdown content

    Returns:
        Dict with frontmatter fields (title, permalink, tags, etc.)
    """
    return _frontmatter_with_raw(content)[0]


# ── body: facts, links, text ────────────────────────────────────────────────

def _body_lines(content: str) -> Iterator[Tuple[str, bool]]:
    """``(line, inside a fenced code block)`` for the note without frontmatter."""
    text = FRONTMATTER_PATTERN.sub('', content or '', count=1).replace('\r\n', '\n')
    fence = ''
    for line in text.split('\n'):
        m = _FENCE.match(line)
        if m:
            mark = m.group(1)[0]
            if not fence:
                fence = mark
                yield line, True
                continue
            if mark == fence:
                fence = ''
                yield line, True
                continue
        yield line, bool(fence)


def parse_observations(content: str) -> List[Observation]:
    """
    Parse observations from markdown content.

    Format: - [category] Content text #tag1 #tag2

    2026-10-23 (KB phase 0): read from every line of the note (not only the
    first "Observations/Notes/Facts" section, which lost facts after a
    ``## Notes`` heading), outside code blocks; repeated facts once.

    Args:
        content: Raw markdown content

    Returns:
        List of Observation objects
    """
    observations: List[Observation] = []
    seen = set()
    for line, in_code in _body_lines(content):
        if in_code:
            continue
        match = OBSERVATION_PATTERN.match(line.rstrip())
        if not match:
            continue
        category = match.group(1).strip()
        content_text = match.group(2).strip()
        tags = TAG_PATTERN.findall(content_text)  # Extract tags from content
        clean_content = TAG_PATTERN.sub('', content_text).strip()
        if not category or (category, clean_content) in seen:
            continue
        seen.add((category, clean_content))
        observations.append(Observation(category=category, content=clean_content, tags=tags))
    return observations


def parse_relations(content: str) -> List[Relation]:
    """
    Parse relations from markdown content.

    Format: - relation_type [[TargetEntity]]

    2026-10-23 (KB phase 0): read from every line of the note (links under
    "Further reading", or in a note titled "Links", were lost), outside code
    blocks; repeated links once.

    Args:
        content: Raw markdown content

    Returns:
        List of Relation objects
    """
    relations: List[Relation] = []
    seen = set()
    for line, in_code in _body_lines(content):
        if in_code:
            continue
        match = RELATION_PATTERN.match(line.rstrip())
        if not match:
            continue
        key = (match.group(1).strip(), match.group(2).strip())
        if key in seen:
            continue
        seen.add(key)
        relations.append(Relation(relation_type=key[0], target=key[1]))
    return relations


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO format datetime string."""
    if not value:
        return None
    try:
        # Handle various ISO formats
        value = value.replace('Z', '+00:00')
        return datetime.fromisoformat(value)
    except (ValueError, TypeError, AttributeError):
        return None


def parse_note_text(content: str, file_path: Path) -> Note:
    """Parse a note given as text (``file_path`` gives the default name)."""
    frontmatter, raws = _frontmatter_with_raw(content)
    base_name = file_path.stem

    def _text(key: str, default: str) -> str:
        # 2026-10-23 (KB phase 0): a list/empty value never becomes the name
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    return Note(
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
        raw_frontmatter=raws,
    )


def parse_note(file_path: Path) -> Note:
    """
    Parse a complete note from a markdown file.

    Args:
        file_path: Path to the markdown file

    Returns:
        Note object with all parsed data
    """
    return parse_note_text(file_path.read_text(encoding='utf-8'), file_path)


def _title_key(text: Any) -> str:
    return str(text).replace('"', "'").replace('\n', ' ').strip().lower()


def extract_body(content: str, title: Optional[str] = None) -> str:
    """The free text of a note: without frontmatter, the top '# Title' line,
    the ``## Observations`` / ``## Relations`` headings and exactly the
    fact/link lines that ``parse_observations`` / ``parse_relations`` read.

    2026-10-23 (KB phase 0): the first ``# heading`` is removed only when it
    is the note's title (``title`` or the frontmatter title/name) — or, for
    plain text without a known title, as before. Task boxes, other headings
    and anything in code blocks stay.

    Works on a whole note file and on plain text alike (2026-10-23).
    """
    fm = parse_frontmatter(content or '')
    titles = {_title_key(t) for t in (title, fm.get('title'), fm.get('name'))
              if isinstance(t, str) and t.strip()}
    lines = list(_body_lines(content or ''))
    # the title heading: first non-empty line
    for i, (line, in_code) in enumerate(lines):
        if not line.strip():
            continue
        m = re.match(r'^# (.*)$', line.rstrip())
        if m and not in_code and (not titles or _title_key(m.group(1)) in titles):
            lines = lines[:i] + lines[i + 1:]
        break
    kept = []
    for line, in_code in lines:
        s = line.rstrip()
        if not in_code and (OBSERVATION_PATTERN.match(s) or RELATION_PATTERN.match(s)
                            or _SECTION_HEADING.match(s)):
            continue
        kept.append(line)
    text = '\n'.join(kept)
    text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text)
    return text.strip()


# ── writing ──────────────────────────────────────────────────────────────────

def _yaml_item(value: Any) -> str:
    """A list item / value: JSON-quoted (valid YAML) unless a number/bool."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return json.dumps(text, ensure_ascii=False)


def _yaml_key(key: str) -> str:
    k = str(key)
    if _SAFE_KEY.match(k) and k == k.strip():
        return k
    return json.dumps(k, ensure_ascii=False)


def _yaml_lines(key: str, value: Any, indent: int = 0) -> List[str]:
    """``key: value`` lines for one frontmatter entry (maps as indented blocks)."""
    pad = ' ' * indent
    k = _yaml_key(key)
    if value is None or value == '':
        return [f'{pad}{k}: ""']
    if isinstance(value, (bool, int, float)):
        return [f'{pad}{k}: {_yaml_item(value)}']
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if all(v is None or isinstance(v, (str, bool, int, float)) for v in items):
            return [f'{pad}{k}: [{", ".join(_yaml_item("" if v is None else v) for v in items)}]']
        return [f'{pad}{k}: {json.dumps(items, ensure_ascii=False, default=str)}']
    if isinstance(value, dict):
        if not value:
            return [f'{pad}{k}: {{}}']
        out = [f'{pad}{k}:']
        for sub_key, sub_value in value.items():
            out.extend(_yaml_lines(str(sub_key), sub_value, indent + 2))
        return out
    text = str(value)
    if _PLAIN_VALUE.match(text) and text == text.strip() and ' #' not in text:
        return [f'{pad}{k}: {text}']
    return [f'{pad}{k}: {_yaml_item(text)}']


def _metadata_line(key: Any, value: Any) -> Optional[str]:
    """``key: value`` line(s) for a metadata entry (None = skipped)."""
    k = str(key).strip()
    if not k or k in RESERVED_KEYS or '\n' in k:
        return None
    return '\n'.join(_yaml_lines(k, value))


def _norm(value: Any) -> Any:
    """Compare values the way they read back from a file."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple, set)):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if value is None:
        return ''
    return str(value)


def generate_markdown(note: Note) -> str:
    """
    Generate markdown content from a Note object.

    2026-10-23: the note's free text (``note.content`` — plain text or a whole
    note file) is written too. Before, the body was dropped, so callers passed
    raw content instead and every tag, fact and link was lost.

    2026-10-23 (KB phase 0): every frontmatter key is written back; a key
    whose value did not change keeps its original line(s) and position.

    Args:
        note: Note object to serialize

    Returns:
        Markdown string
    """
    raws = note.raw_frontmatter or {}
    title = (note.title or note.name).replace('"', "'").replace("\n", " ")
    entries: Dict[str, str] = {}

    def put(key: str, value: Any, generated: str) -> None:
        old = raws.get(key)
        if old is not None and _norm(old[1]) == _norm(value):
            entries[key] = old[0]
        else:
            entries[key] = generated

    # 2026-10-23 (KB phase 0): a backslash is escaped (the value is read back
    # as a JSON/YAML double-quoted string); tags quoted the same way.
    put('title', note.title or note.name, f'title: "{title.replace(chr(92), chr(92) * 2)}"')
    put('permalink', note.permalink, f'permalink: {note.permalink}')
    stem = note.file_path.stem if note.file_path else ''
    if note.name and (note.name not in (note.title, note.permalink, stem)
                      or ('name' in raws and _norm(raws['name'][1]) == note.name)):
        put('name', note.name, '\n'.join(_yaml_lines('name', note.name)))
    tags = _clean_list(note.tags, 'tags')
    if tags:
        put('tags', tags, f'tags: [{", ".join(_yaml_item(t) for t in tags)}]')
    aliases = _clean_list(note.aliases, 'aliases')
    if aliases:
        put('aliases', aliases, f'aliases: [{", ".join(_yaml_item(a) for a in aliases)}]')
    for key, value in (note.metadata or {}).items():
        line = _metadata_line(key, value)
        if line:
            put(str(key).strip(), value, line)
    for key, when in (('created', note.created_at), ('updated', note.updated_at)):
        old = raws.get(key)
        if when is not None:
            if old is not None and parse_datetime(old[1] if isinstance(old[1], str) else '') == when:
                entries[key] = old[0]
            else:
                entries[key] = f'{key}: {when.isoformat()}'
        elif old is not None:  # e.g. 'created: Jan 1 2024' — kept as written
            entries[key] = old[0]

    head = [k for k in ('title', 'permalink') if k in entries and k not in raws]
    order = head + [k for k in raws if k in entries and k not in head]
    order += [k for k in entries if k not in order]
    lines = ['---'] + [entries[k] for k in order]
    if "" in raws:
        lines.append(raws[""][0])
    lines += ['---', '']

    # Title
    lines.append(f'# {title}')
    lines.append('')

    body = extract_body(note.content, note.title or note.name)
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
