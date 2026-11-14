"""Read-only independent review for proposed source-note merges."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def review_candidate(user_id: str, candidate: Dict[str, Any], db_path: Any = None) -> Dict[str, Any]:
    """Return pass/hold/reject without changing note files or the index."""
    from .parser import extract_body
    from .quality import _norm_text, conflict_kind, normalize_category
    from .storage import read_note

    keep_ref = candidate.get("keep") or {}
    drop_ref = candidate.get("drop") or {}
    keep_id, drop_id = str(keep_ref.get("permalink") or ""), str(drop_ref.get("permalink") or "")
    if not keep_id or not drop_id or keep_id == drop_id:
        return {"status": "reject", "reasons": ["two distinct source notes are required"]}

    evidence = candidate.get("evidence") or {}
    expected = {
        str(source.get("permalink") or ""): str(source.get("sha256") or "")
        for source in evidence.get("sources", [])
    }
    if not expected.get(keep_id) or not expected.get(drop_id):
        return {"status": "reject", "reasons": ["candidate is missing source checksums"]}

    keep = read_note(keep_id, user_id, db_path=db_path)
    drop = read_note(drop_id, user_id, db_path=db_path)
    if keep is None or drop is None:
        return {"status": "reject", "reasons": ["a source note no longer exists"]}

    current = {}
    for note in (keep, drop):
        try:
            current[note.permalink] = hashlib.sha256(note.file_path.read_bytes()).hexdigest()
        except (AttributeError, OSError):
            return {"status": "reject", "reasons": [f"cannot verify source checksum: {note.permalink}"]}
    if any(current.get(permalink) != expected.get(permalink) for permalink in (keep_id, drop_id)):
        return {
            "status": "reject",
            "reasons": ["source changed after candidate generation; regenerate the proposal"],
            "source_checksums": current,
        }

    conflicts = []
    for left in keep.observations or []:
        if getattr(left, "status", "active") != "active":
            continue
        for right in drop.observations or []:
            if getattr(right, "status", "active") != "active":
                continue
            category = normalize_category(left.category)
            if category != normalize_category(right.category):
                continue
            kind = conflict_kind(left.content, right.content, category)
            if kind:
                conflicts.append({"category": category, "kind": kind,
                                  "keep": left.content, "drop": right.content})
    if conflicts:
        return {
            "status": "hold",
            "reasons": ["contradictory facts must be resolved before merging"],
            "conflicts": conflicts,
            "source_checksums": current,
        }

    def fact_signature(note: Any) -> list:
        return sorted((
            (
                normalize_category(item.category), _norm_text(item.content),
                tuple(item.tags or []), item.context, item.provenance_path,
                item.captured_at, item.confidence, item.verification_status,
                item.status, item.superseded_by,
            )
            for item in note.observations or []
        ), key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))

    def relation_signature(note: Any) -> list:
        return sorted((
            (item.relation_type, _norm_text(item.target), getattr(item, "strength", 1))
            for item in note.relations or []
        ), key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))

    identical = (
        _norm_text(extract_body(keep.content, keep.title))
        == _norm_text(extract_body(drop.content, drop.title))
        and fact_signature(keep) == fact_signature(drop)
        and relation_signature(keep) == relation_signature(drop)
    )
    return {
        "status": "pass" if identical else "hold",
        "reasons": (["source content, facts, and relations are equivalent"]
                    if identical else ["notes differ; a person must review the full merge preview"]),
        "source_checksums": current,
    }
