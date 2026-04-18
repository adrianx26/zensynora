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
    Write a new note to the knowledge base.

    title: The title/name of the note (becomes permalink)
    content: Main content/description
    tags: Comma-separated list of tags (optional)
    observations: One observation per line, format: "category | content" (optional)
    relations: One relation per line, format: "relation_type | target_entity" (optional)
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
        query: Search query (supports FTS5 syntax: AND, OR, NOT, *)
        limit: Maximum number of results (default: 5)
        user_id: User ID for multi-user isolation

    Returns:
        Formatted search results, or standardized "no results" payload with guidance.
    """
    try:
        notes = search_notes(query, user_id, limit)

        if not notes:
            # Generate actionable guidance for empty results
            suggested_terms = _extract_search_terms(query)

            # Build broader query suggestion
            broader_query = " OR ".join(suggested_terms[:3]) if len(suggested_terms) > 1 else query

            lines = [
                f"🔍 No results found for: '{query}'",
                "",
                "💡 Suggestions:",
            ]

            if suggested_terms:
                lines.append(f"  • Try broader search terms: '{broader_query}'")

            lines.extend([
                "  • Check for typos in your query",
                "  • Use different keywords or synonyms",
                "",
                "📝 Actions you can take:",
                f"  • Create a new knowledge entry: write_to_knowledge(title='Your Topic', content='Details...')",
                f"  • Browse existing entries: list_knowledge()",
                f"  • Search with different terms: search_knowledge(query='alternate keywords')",
                "",
                "📚 The knowledge base grows as you add information. Consider saving useful findings!"
            ])

            return "\n".join(lines)

        lines = [f"🔍 Search results for '{query}':", ""]

        for i, note in enumerate(notes, 1):
            lines.append(f"{i}. **{note.title}** ([{note.permalink}](memory://{note.permalink}))")
            if note.observations:
                for obs in note.observations[:2]:  # Show first 2 observations
                    lines.append(f"   - [{obs.category}] {obs.content[:80]}...")
            if note.tags:
                lines.append(f"   Tags: {', '.join(f'#{tag}' for tag in note.tags)}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to search knowledge: {e}")
        return f"Error searching knowledge: {e}"


def read_knowledge(permalink: str, user_id: str = "default") -> str:
    """
    Read a specific knowledge note by permalink.

    permalink: The note's permalink/identifier
    user_id: User ID for multi-user isolation
    """
    try:
        note = read_note(permalink, user_id)

        if not note:
            return f"Note not found: {permalink}"

        lines = [
            f"# {note.title}",
            f"Permalink: {note.permalink}",
            ""
        ]

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

        if note.tags:
            lines.append(f"Tags: {', '.join(f'#{tag}' for tag in note.tags)}")

        return "\n".join(lines)
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


