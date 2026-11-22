"""Shared limits and normalization for knowledge-note writes."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, List, Sequence


MAX_TITLE_CHARS = 120
MAX_CONTENT_BYTES = 200_000
MAX_TAGS = 6
MAX_TAG_CHARS = 50
MAX_FACTS = 10
MAX_IMPORT_FACTS = 64
MAX_FACT_CATEGORY_CHARS = 30
MAX_FACT_TEXT_CHARS = 400
MAX_RELATIONS = 8
MAX_RELATION_TYPE_CHARS = 50
MAX_RELATION_TARGET_CHARS = 120
MAX_PROVENANCE_CHARS = 1_000
MAX_CAPTURED_AT_CHARS = 128


class NoteValidationError(ValueError):
    """A user-correctable note validation failure."""


@dataclass
class NormalizedNote:
    title: str
    content: str
    tags: List[str]
    observations: List[Any]
    relations: List[Any]


def _utf8_truncate(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _bounded(field: str, value: Any, maximum: int, *, strict: bool,
             unit: str = "characters") -> str:
    text = str(value or "").strip()
    size = len(text.encode("utf-8")) if unit == "bytes" else len(text)
    if size <= maximum:
        return text
    if strict:
        return_error = (
            f"{field} is too long ({size} {unit}); the maximum is {maximum}. "
            f"Shorten {field} and try again."
        )
        raise NoteValidationError(return_error)
    return _utf8_truncate(text, maximum) if unit == "bytes" else text[:maximum]


def _unique(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def normalize_note_input(
    *,
    title: Any,
    content: Any,
    tags: Sequence[Any] = (),
    observations: Sequence[Any] = (),
    relations: Sequence[Any] = (),
    strict: bool,
    max_facts: int = MAX_FACTS,
) -> NormalizedNote:
    """Validate or safely normalize a note before it reaches storage.

    Strict mode is for user/tool input and rejects anything that would be
    discarded. Non-strict mode preserves the established extraction behavior:
    model output is normalized and capped, rather than failing the extraction.
    """
    clean_title = _bounded("title", title, MAX_TITLE_CHARS, strict=strict)
    clean_content = _bounded("content", content, MAX_CONTENT_BYTES, strict=strict, unit="bytes")
    if not clean_title:
        raise NoteValidationError("title is empty. Provide a short, specific title.")
    if not clean_content:
        raise NoteValidationError("content is empty. Add the note's text and try again.")

    raw_tags = list(tags or [])
    if strict and len(raw_tags) > MAX_TAGS:
        raise NoteValidationError(
            f"tags contains {len(raw_tags)} entries; the maximum is {MAX_TAGS}. "
            "Reduce the number of tags and try again."
        )
    clean_tags = []
    for value in raw_tags[:MAX_TAGS]:
        tag = re.sub(r"\s+", "-", str(value or "").strip().lower().lstrip("#"))
        tag = _bounded("each tag", tag, MAX_TAG_CHARS, strict=strict)
        if tag:
            clean_tags.append(tag)
    clean_tags = _unique(clean_tags)

    raw_observations = list(observations or [])
    if strict and len(raw_observations) > max_facts:
        raise NoteValidationError(
            f"observations contains {len(raw_observations)} facts; the maximum is "
            f"{max_facts}. Split the note or shorten the fact list and try again."
        )
    clean_observations = []
    for index, observation in enumerate(raw_observations[:max_facts], 1):
        if not hasattr(observation, "category") or not hasattr(observation, "content"):
            if strict:
                raise NoteValidationError(
                    f"fact {index} is invalid. Use 'category | text' for every fact."
                )
            continue
        category = _bounded(
            f"fact {index} category", getattr(observation, "category"),
            MAX_FACT_CATEGORY_CHARS, strict=strict,
        )
        fact_text = _bounded(
            f"fact {index} text", getattr(observation, "content"),
            MAX_FACT_TEXT_CHARS, strict=strict,
        )
        if strict and (not category or not fact_text):
            raise NoteValidationError(
                f"fact {index} is incomplete. Give both a category and text, "
                "separated by ' | '."
            )
        if not category or not fact_text:
            continue
        clean_observations.append(replace(
            observation,
            category=category,
            content=fact_text,
            provenance_path=_bounded(
                f"fact {index} source", getattr(observation, "provenance_path", None),
                MAX_PROVENANCE_CHARS, strict=strict,
            ) or None,
            captured_at=_bounded(
                f"fact {index} captured_at", getattr(observation, "captured_at", None),
                MAX_CAPTURED_AT_CHARS, strict=strict,
            ) or None,
        ))

    raw_relations = list(relations or [])
    if strict and len(raw_relations) > MAX_RELATIONS:
        raise NoteValidationError(
            f"relations contains {len(raw_relations)} links; the maximum is "
            f"{MAX_RELATIONS}. Split the note or reduce the relation list."
        )
    clean_relations = []
    for index, relation in enumerate(raw_relations[:MAX_RELATIONS], 1):
        if not hasattr(relation, "relation_type") or not hasattr(relation, "target"):
            if strict:
                raise NoteValidationError(
                    f"relation {index} is invalid. Use 'relation | Other note title'."
                )
            continue
        relation_type = _bounded(
            f"relation {index} type", getattr(relation, "relation_type"),
            MAX_RELATION_TYPE_CHARS, strict=strict,
        )
        relation_type = re.sub(r"[^a-z0-9_]+", "_", relation_type.lower()).strip("_")
        target = _bounded(
            f"relation {index} target", getattr(relation, "target"),
            MAX_RELATION_TARGET_CHARS, strict=strict,
        )
        if strict and (not relation_type or not target):
            raise NoteValidationError(
                f"relation {index} is incomplete. Give both a relation type and "
                "target note title, separated by ' | '."
            )
        if relation_type and target and target.casefold() == clean_title.casefold():
            if strict:
                raise NoteValidationError(
                    f"relation {index} points to this note itself. Choose another "
                    "target note title or remove the relation."
                )
            continue
        if relation_type and target:
            clean_relations.append(replace(relation, relation_type=relation_type, target=target))

    return NormalizedNote(
        title=clean_title,
        content=clean_content,
        tags=clean_tags,
        observations=clean_observations,
        relations=clean_relations,
    )
