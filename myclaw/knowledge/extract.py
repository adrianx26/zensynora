"""Turning model output into structured knowledge notes (2026-10-23).

Both automatic extractors (after a conversation, after a tool result) ask the
model for a JSON array in :data:`FORMAT` and save it with :func:`save_items`:
title + text + tags, plus **facts** (``- [category] text``) and **links**
(``- relation [[Other note]]``), linking to notes that already exist when
possible (:func:`existing_titles`). Before, only title, text and tags were
requested — and the tags were then dropped by ``write_note`` — so the
knowledge graph stayed empty (266 notes, 0 tags/facts/links on mindy01).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, List, Optional

from .quality import normalize_category  # 2026-10-24 (KB phase 2)

logger = logging.getLogger(__name__)

FORMAT = (
    "Return a JSON array of objects: "
    '[{"title": "<short specific title>", "content": "<the detail in 1-5 sentences>", '
    '"tags": "tag1,tag2", '
    '"facts": [{"category": "fact|setting|preference|decision|spec|url|person|how-to", "text": "<one short fact>"}], '
    '"links": [{"type": "relation_in_snake_case", "target": "<title of another note>"}]}]. '
    "tags: 1-4 short lowercase topic words. facts: 1-6 atomic facts from the content. "
    "links: how this note relates to other notes (uses, part_of, runs_on, about, related_to …); "
    "prefer targets from the list of existing notes, otherwise the title of the thing itself. "
)

_REL_TYPE = re.compile(r"[^a-z0-9_]+")
_CATEGORY = re.compile(r"[\[\]\n]+")


def parse_json_array(raw: str) -> List[Any]:
    """The JSON array in a model answer (code fences and leading text are tolerated)."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        data = json.loads(raw)
    except ValueError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return []
    return data if isinstance(data, list) else []


def existing_titles(text: str, user_id: str, limit: int = 12) -> List[str]:
    """Titles of notes related to ``text`` — offered to the model as link targets."""
    try:
        from .storage import search_notes

        words = [w for w in re.findall(r"[\w-]{4,}", text or "")][:12]
        if not words:
            return []
        notes = search_notes(" OR ".join(words), user_id, limit)
        return [n.title for n in notes if n.title][:limit]
    except Exception as e:  # never block extraction
        logger.debug("existing_titles failed: %s", e)
        return []


def known_notes_hint(titles: Iterable[str]) -> str:
    titles = [t for t in titles if t]
    return ("Existing notes you can link to: " + "; ".join(titles[:12]) + ".") if titles else ""


def save_items(items: List[Any], user_id: str, extra_tags: List[str], cap: int, log_prefix: str,
               source: Optional[str] = None) -> int:
    """Write each extracted item as a note with tags, facts and links. Returns how many were saved.

    2026-10-24 (KB phase 2): ``source`` (e.g. ``chat 2026-10-24`` or
    ``tool browse https://…``) is stored with every fact as ``(source: …)``.
    """
    from .parser import Observation, Relation
    from .storage import write_note

    saved = 0
    for item in items[:cap]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()[:120]
        content = str(item.get("content", "")).strip()
        if not title or not content:
            continue
        tags_raw = item.get("tags", "")
        tags = tags_raw if isinstance(tags_raw, list) else str(tags_raw).split(",")
        tag_list = [re.sub(r"\s+", "-", str(t).strip().lower().lstrip("#")) for t in tags if str(t).strip()][:6]
        tag_list += [t for t in extra_tags if t not in tag_list]
        obs = []
        for f in item.get("facts") or []:
            if isinstance(f, dict):
                cat, text = f.get("category", "fact"), f.get("text", "")
            elif isinstance(f, str):
                cat, text = "fact", f
            else:
                continue
            # 2026-10-24 (KB phase 2): one spelling per category (Facts → fact)
            cat = normalize_category(_CATEGORY.sub(" ", str(cat)).strip())[:30] or "fact"
            text = " ".join(str(text).split())[:400]
            if text:
                obs.append(Observation(category=cat, content=text, tags=[],
                                       provenance_path=source or None))
        rels = []
        for link in item.get("links") or []:
            if not isinstance(link, dict):
                continue
            rtype = _REL_TYPE.sub("_", str(link.get("type", "related_to")).strip().lower()).strip("_") or "related_to"
            target = " ".join(str(link.get("target", "")).replace("[", "").replace("]", "").split())[:120]
            if target and target.lower() != title.lower():
                rels.append(Relation(relation_type=rtype, target=target))
        try:
            write_note(name=title, title=title, content=content, tags=tag_list,
                       observations=obs[:8], relations=rels[:8], user_id=user_id,
                       origin="extract")  # 2026-10-24 (KB phase 4 review)
            saved += 1
            logger.info(f"{log_prefix} Saved: '{title}' ({len(obs)} facts, {len(rels)} links)")
        except Exception as e:
            logger.debug(f"{log_prefix} Could not save '{title}': {e}")
    return saved


# ── Adding structure to existing notes (Knowledge page → ✨ Add tags & links) ──
ENRICH_PROMPT = (
    "You add structure to one note of a personal knowledge base. Read the note and return ONE JSON "
    'object: {"tags": "tag1,tag2", "facts": [{"category": "...", "text": "..."}], '
    '"links": [{"type": "relation_in_snake_case", "target": "<title of another note>"}]}. '
    "tags: 1-4 short lowercase topic words. facts: up to 6 atomic facts that are really in the note. "
    "links: up to 5, to notes from the list of existing notes when they are related. "
    "Do not invent anything that is not in the note. Return ONLY the JSON object."
)


def needs_structure(note: Any) -> bool:
    return not note.tags and not note.observations and not note.relations


async def enrich_note(config: Any, base: str, permalink: str) -> dict:
    """Ask the model for tags, facts and links of one note and add them (text unchanged)."""
    import asyncio

    from ..provider import complete_with_fallback
    from .parser import Observation, Relation, extract_body
    from .storage import read_note, write_note

    note = await asyncio.to_thread(read_note, permalink, base)
    if note is None:
        return {"permalink": permalink, "ok": False, "error": "not found"}
    body = extract_body(note.content)
    known = [t for t in await asyncio.to_thread(existing_titles, f"{note.title} {body[:400]}", base)
             if t != note.title]
    messages = [
        {"role": "system", "content": ENRICH_PROMPT},
        {"role": "user", "content": f"Title: {note.title}\n\n{body[:2500]}\n\n{known_notes_hint(known)}".strip()},
    ]
    text, _, _ = await complete_with_fallback(config, messages)
    raw = (text or "").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    item = {"title": note.title, "content": body or note.title, "tags": data.get("tags", ""),
            "facts": data.get("facts") or [], "links": data.get("links") or []}
    # reuse the cleaning of save_items, but keep the note's own name and dates
    tags_raw = item["tags"]
    tags = tags_raw if isinstance(tags_raw, list) else str(tags_raw).split(",")
    tag_list = [re.sub(r"\s+", "-", str(t).strip().lower().lstrip("#")) for t in tags if str(t).strip()][:6]
    obs = list(note.observations)
    for f in item["facts"]:
        if isinstance(f, dict) and str(f.get("text", "")).strip():
            obs.append(Observation(category=normalize_category(_CATEGORY.sub(" ", str(f.get("category", "fact"))).strip())[:30] or "fact",
                                   content=" ".join(str(f["text"]).split())[:400], tags=[]))
    rels = list(note.relations)
    for link in item["links"]:
        if isinstance(link, dict) and str(link.get("target", "")).strip():
            rtype = _REL_TYPE.sub("_", str(link.get("type", "related_to")).lower()).strip("_") or "related_to"
            target = " ".join(str(link["target"]).replace("[", "").replace("]", "").split())[:120]
            if target.lower() != note.title.lower():
                rels.append(Relation(relation_type=rtype, target=target))
    if not (tag_list or len(obs) > len(note.observations) or len(rels) > len(note.relations)):
        return {"permalink": permalink, "ok": False, "error": "the model returned nothing usable"}
    await asyncio.to_thread(write_note, permalink, note.title, obs[:10], rels[:8],
                            list(dict.fromkeys(list(note.tags) + tag_list)), base, body or None)
    return {"permalink": permalink, "ok": True, "tags": len(tag_list),
            "facts": len(obs) - len(note.observations), "links": len(rels) - len(note.relations)}
