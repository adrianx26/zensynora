"""
Tools — Knowledge Base
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,
    ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    _rate_limiter, _tool_audit_logger,
    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,
    _runtime_config,
    TOOLS, TOOL_SCHEMAS,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import re
from ..knowledge import (
    write_note, read_note, delete_note, list_notes, search_notes,
    get_related_entities, build_context, sync_knowledge, get_all_tags,
    Observation, Relation
)
# 2026-10-23 (KB phase 0): note text, backlinks and tag search for the tools.
from ..knowledge.graph import get_backlinks, note_snippet, note_text, shorten_text
from ..knowledge.storage import get_note_by_tag

# 2026-10-23 (KB phase 0): how much text the tools show.
READ_TEXT_CHARS = 6000
SEARCH_SNIPPET_CHARS = 160

logger = logging.getLogger(__name__)

# ── Knowledge Tools ───────────────────────────────────────────────────────────

def write_to_knowledge(
    title: str,
    content: str,
    tags: str = "",
    observations: str = "",
    relations: str = "",
    user_id: str = "default"
) -> str:
    """
    Write a note to the knowledge base (it appears in the Knowledge graph).

    title: short specific title (becomes the note's name; writing the same title again replaces the note)
    content: the text of the note (plain text or markdown)
    tags: 1-4 comma-separated topic words, e.g. "homelab, linux" — always add some
    observations: facts, one per line as "category | text", e.g. "spec | 32 GB RAM"
    relations: links to other notes, one per line as "relation | Other note title",
               e.g. "runs_on | Home server" — link to notes that already exist (search_knowledge first)
    user_id: User ID for multi-user isolation
    """
    try:
        # Parse tags
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        # Parse observations
        obs_list = []
        if observations:
            for line in observations.strip().split("\n"):
                if "|" in line:
                    category, obs_content = line.split("|", 1)
                    obs_list.append(Observation(
                        category=category.strip(),
                        content=obs_content.strip(),
                        tags=[]
                    ))

        # Parse relations
        rel_list = []
        if relations:
            for line in relations.strip().split("\n"):
                if "|" in line:
                    rel_type, target = line.split("|", 1)
                    rel_list.append(Relation(
                        relation_type=rel_type.strip(),
                        target=target.strip()
                    ))

        # Create note
        permalink = write_note(
            name=title,
            title=title,
            content=content,
            observations=obs_list,
            relations=rel_list,
            tags=tag_list,
            user_id=user_id
        )

        return f"✅ Knowledge note created: [{title}](memory://{permalink})"
    except Exception as e:
        logger.error(f"Failed to write knowledge: {e}")
        return f"Error writing knowledge: {e}"


def _extract_search_terms(query: str) -> List[str]:
    """Extract potential search terms from a query for suggestions.

    Args:
        query: The original search query

    Returns:
        List of extracted terms (words > 3 chars, bigrams)
    """
    cleaned = re.sub(r'[^\w\s]', ' ', query.lower())
    words = [w for w in cleaned.split() if len(w) > 3]

    terms = words[:5]  # Single words

    # Add bigrams
    if len(words) >= 2:
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(min(len(words) - 1, 3))]
        terms.extend(bigrams)

    return list(dict.fromkeys(terms))  # Remove duplicates, preserve order


# 2026-10-23 (KB phase 0): ``tag:name`` terms in a search_knowledge query.
_TAG_RE = re.compile(r"(?<!\S)tag:([\w][\w\-/.]*)", re.IGNORECASE)
_DANGLING_OP_RE = re.compile(r"^(?:\s*\b(?:AND|OR|NOT)\b\s*)+|(?:\s*\b(?:AND|OR|NOT)\b\s*)+$")


def _split_tag_terms(query: str):
    """``(text query, [tags])`` — the ``tag:name`` terms taken out of the query."""
    tags = [t.lower().rstrip(".") for t in _TAG_RE.findall(query or "")]
    rest = _TAG_RE.sub(" ", query or "")
    rest = re.sub(r"\b(AND|OR)\s+(?=\b(?:AND|OR)\b)", "", rest)  # "a AND  AND b"
    rest = _DANGLING_OP_RE.sub("", " ".join(rest.split())).strip()
    return rest, tags


def _note_tags(note: Any) -> set:
    """Frontmatter tags and the tags of the note's facts, lower case."""
    out = set()
    for t in (getattr(note, "tags", None) or []):
        if isinstance(t, str):
            out.add(t.lower().lstrip("#"))
    for obs in (getattr(note, "observations", None) or []):
        for t in (getattr(obs, "tags", None) or []):
            if isinstance(t, str):
                out.add(t.lower().lstrip("#"))
    return out


def _notes_with_tag(tag: str, user_id: str) -> list:
    """Notes having ``tag`` (frontmatter or fact tags)."""
    try:
        notes = list(get_note_by_tag(tag, user_id) or [])
    except Exception as e:
        logger.debug("get_note_by_tag(%s) failed: %s", tag, e)
        notes = []
    notes = [n for n in notes if tag in _note_tags(n)]
    if not notes:
        # older indexes only know fact tags; frontmatter tags are in the files
        notes = [n for n in list_notes(user_id) if tag in _note_tags(n)]
    return notes


def search_knowledge(query: str, limit: int = 5, user_id: str = "default") -> str:
    """
    Search the knowledge base using full-text search.

    When no results are found, returns an actionable guidance payload that includes:
    - Confirmation that no results were found
    - Suggestion to try broader search terms
    - Explicit recommendation to call write_to_knowledge() to create a new entry
    - Pointer to list_knowledge() to inspect existing entries
    - Tips for improving search (different keywords, checking for typos)

    Args:
        query: Search query (supports AND, OR, NOT, "quoted phrases", prefix*,
               and tag:name to keep only notes with that tag)
        limit: Maximum number of results (default: 5)
        user_id: User ID for multi-user isolation

    Returns:
        Formatted search results, or standardized "no results" payload with guidance.
    """
    try:
        # 2026-10-23 (KB phase 0): AND/OR/NOT really work (boolean=True), and
        # tag:name filters by tag; a query of only tags lists the tagged notes.
        text_query, tags = _split_tag_terms(query)
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 5
        if tags and not text_query:
            notes = _notes_with_tag(tags[0], user_id)
        elif tags:
            notes = search_notes(text_query, user_id, limit * 4, boolean=True)
        else:
            notes = search_notes(query, user_id, limit, boolean=True)
        if tags:
            notes = [n for n in notes if all(t in _note_tags(n) for t in tags)]
        notes = list(notes or [])[:limit]

        if not notes:
            # Generate actionable guidance for empty results
            suggested_terms = _extract_search_terms(text_query if tags else query)

            # Build broader query suggestion
            broader_query = " OR ".join(suggested_terms[:3]) if len(suggested_terms) > 1 else query

            lines = [
                f"🔍 No results found for: '{query}'",
                "",
                "💡 Suggestions:",
            ]

            if suggested_terms:
                lines.append(f"  • Try broader search terms: '{broader_query}'")
            if tags:
                lines.append("  • Check the tag name: list_knowledge_tags() lists every tag")

            lines.extend([
                "  • Check for typos in your query",
                "  • Use different keywords or synonyms",
                "",
                "📝 Actions you can take:",
                "  • Create a new knowledge entry: write_to_knowledge(title='Your Topic', content='Details...')",
                "  • Browse existing entries: list_knowledge()",
                "  • Search with different terms: search_knowledge(query='alternate keywords')",
                "",
                "📚 The knowledge base grows as you add information. Consider saving useful findings!"
            ])

            return "\n".join(lines)

        lines = [f"🔍 Search results for '{query}':", ""]

        for i, note in enumerate(notes, 1):
            lines.append(f"{i}. **{note.title}** ([{note.permalink}](memory://{note.permalink}))")
            snippet = note_snippet(note, SEARCH_SNIPPET_CHARS)
            if snippet:
                lines.append(f"   {snippet}")
            if note.observations:
                for obs in note.observations[:2]:  # Show first 2 observations
                    lines.append(f"   - [{obs.category}] {shorten_text(obs.content, 80)}")
            if note.tags:
                lines.append(f"   Tags: {', '.join(f'#{tag}' for tag in note.tags)}")
            lines.append("")
        lines.append("Read a whole note with read_knowledge(permalink).")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to search knowledge: {e}")
        return f"Error searching knowledge: {e}"


def _unresolved_links(permalink: str, user_id: str) -> list:
    """(relation, target) of links whose target note does not exist yet."""
    from ..knowledge.db import KnowledgeDB

    try:
        with KnowledgeDB(user_id) as db:
            if not hasattr(db, "get_unresolved_relations"):
                return []
            entity = db.get_entity_by_permalink(permalink)
            if not entity:
                return []
            return list(db.get_unresolved_relations(entity.id) or [])
    except Exception as e:
        logger.debug("Unresolved links of %s unavailable: %s", permalink, e)
        return []


def read_knowledge(permalink: str, user_id: str = "default") -> str:
    """
    Read a specific knowledge note by permalink: its text, facts, links,
    tags and the notes that link to it.

    permalink: The note's permalink/identifier
    user_id: User ID for multi-user isolation
    """
    try:
        note = read_note(permalink, user_id)

        if not note:
            return (f"Note not found: {permalink}. Use search_knowledge(query) or "
                    "list_knowledge() to find the right permalink.")

        lines = [
            f"# {note.title}",
            f"Permalink: {note.permalink}",
            ""
        ]

        # 2026-10-23 (KB phase 0): the note's text (agents only saw facts).
        aliases = [a for a in (getattr(note, "aliases", None) or []) if isinstance(a, str) and a]
        if aliases:
            lines.append(f"Aliases: {', '.join(aliases)}")
        metadata = getattr(note, "metadata", None)
        if isinstance(metadata, dict) and metadata:
            meta = ", ".join(f"{k}: {v}" for k, v in metadata.items()
                             if k not in ("aliases", "tags", "title", "permalink"))
            if meta:
                lines.append(f"Metadata: {shorten_text(meta, 400)}")
        if aliases or (isinstance(metadata, dict) and metadata):
            lines.append("")

        text = note_text(note)
        if text:
            if len(text) > READ_TEXT_CHARS:
                text = shorten_text(text, READ_TEXT_CHARS, collapse=False)
                text += f"\n\n(text shortened — the whole note has {len(note_text(note))} characters)"
            lines.append(text)
            lines.append("")

        if note.observations:
            lines.append("## Observations")
            for obs in note.observations:
                lines.append(f"- [{obs.category}] {obs.content}")
            lines.append("")

        if note.relations:
            lines.append("## Relations")
            for rel in note.relations:
                lines.append(f"- {rel.relation_type} → [[{rel.target}]]")
            lines.append("")

        unresolved = _unresolved_links(note.permalink, user_id)
        if unresolved:
            lines.append("Links to notes that do not exist yet: "
                         + ", ".join(f"{r} → [[{t}]]" for r, t in unresolved))
            lines.append("")

        try:
            backlinks = get_backlinks(note.permalink, user_id)
        except Exception as e:
            logger.debug("Backlinks of %s unavailable: %s", note.permalink, e)
            backlinks = []
        if backlinks:
            lines.append("Linked from:")
            for b in backlinks:
                lines.append(f"- {b['name']} ({b['permalink']}) — {b['relation_type']}")
            lines.append("")

        if note.tags:
            lines.append(f"Tags: {', '.join(f'#{tag}' for tag in note.tags)}")

        return "\n".join(lines).rstrip() + "\n"
    except Exception as e:
        logger.error(f"Failed to read knowledge: {e}")
        return f"Error reading knowledge: {e}"


def get_knowledge_context(permalink: str, depth: int = 2, user_id: str = "default") -> str:
    """
    Build context for a knowledge entity including related entities.

    permalink: The starting entity's permalink
    depth: How many relationship hops to include (default: 2)
    user_id: User ID for multi-user isolation
    """
    try:
        context = build_context(permalink, user_id, depth)
        return context
    except Exception as e:
        logger.error(f"Failed to build context: {e}")
        return f"Error building context: {e}"


def list_knowledge(user_id: str = "default", limit: int = 20) -> str:
    """
    List recent knowledge notes.

    user_id: User ID for multi-user isolation
    limit: Maximum number of notes to list
    """
    try:
        notes = list_notes(user_id)
        notes = notes[:limit]

        if not notes:
            return "Knowledge base is empty."

        lines = [f"📚 Knowledge Notes ({len(notes)} shown):", ""]

        for note in notes:
            lines.append(f"- **{note.title}** ([{note.permalink}](memory://{note.permalink}))")
            if note.tags:
                lines.append(f"  Tags: {', '.join(f'#{tag}' for tag in note.tags)}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to list knowledge: {e}")
        return f"Error listing knowledge: {e}"


def sync_knowledge_base(user_id: str = "default") -> str:
    """
    Synchronize the knowledge base (re-index all files).

    user_id: User ID for multi-user isolation
    """
    try:
        result = sync_knowledge(user_id)
        total = result['added'] + result['updated'] + result['deleted']
        return (
            f"✅ Sync complete: {total} changes\n"
            f"  Added: {result['added']}\n"
            f"  Updated: {result['updated']}\n"
            f"  Deleted: {result['deleted']}"
        )
    except Exception as e:
        logger.error(f"Failed to sync knowledge: {e}")
        return f"Error syncing knowledge: {e}"


def get_related_knowledge(permalink: str, user_id: str = "default", depth: int = 1) -> str:
    """
    Get entities related to a knowledge note.

    permalink: The note's permalink
    depth: Relationship depth to traverse (default: 1)
    user_id: User ID for multi-user isolation
    """
    try:
        related = get_related_entities(permalink, user_id, depth)

        if not related:
            return f"No related entities found for: {permalink}"

        lines = [f"🔗 Related to [{permalink}]:", ""]

        for r in related:
            lines.append(f"- {r['relation_type']} → **{r['name']}** (depth: {r['depth']})")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get related knowledge: {e}")
        return f"Error getting related knowledge: {e}"


def list_knowledge_tags(user_id: str = "default") -> str:
    """
    List all tags used in the knowledge base.

    user_id: User ID for multi-user isolation
    """
    try:
        tags = get_all_tags(user_id)

        if not tags:
            return "No tags found in knowledge base."

        return "🏷️ Tags:\n" + " ".join(f"#{tag}" for tag in tags)
    except Exception as e:
        logger.error(f"Failed to list tags: {e}")
        return f"Error listing tags: {e}"




# ── v0.7.5: every knowledge tool uses the knowledge base of the current turn ──
# The tools defaulted to user "default" while the automatic search and saving
# in the Web UI used "webui", so agents could not find their own notes. A
# user_id the model passes no longer picks another (empty) knowledge base.
def _scoped(fn):
    import functools
    import inspect

    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from myclaw.knowledge.scope import for_tool

        bound = sig.bind_partial(*args, **kwargs)
        bound.arguments["user_id"] = for_tool(bound.arguments.get("user_id"))
        return fn(*bound.args, **bound.kwargs)

    return wrapper


write_to_knowledge = _scoped(write_to_knowledge)
search_knowledge = _scoped(search_knowledge)
read_knowledge = _scoped(read_knowledge)
get_knowledge_context = _scoped(get_knowledge_context)
list_knowledge = _scoped(list_knowledge)
sync_knowledge_base = _scoped(sync_knowledge_base)
get_related_knowledge = _scoped(get_related_knowledge)
list_knowledge_tags = _scoped(list_knowledge_tags)
