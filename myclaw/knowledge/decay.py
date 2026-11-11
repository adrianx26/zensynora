"""Temporal decay: old, unused notes and facts rank lower (2026-11-11).

Off by default (``knowledge.decay_half_life_days`` = 0 and no category entry): then
nothing here changes any result.

* A note's weight halves every *half-life* days since it was last changed **or last
  used by an agent** (a note that is still used stays fresh), never below
  ``decay_floor``. Search results (and so the automatic agent context) are re-ordered
  with it; nothing is hidden or deleted, and the notes are not changed.
* ``knowledge.decay_categories`` gives a tag (for notes) or a fact category its own
  half-life in days; 0 = never decays. A note tagged ``evergreen`` never decays.
* Distilled facts use the same half-lives for their confidence (from the next
  distillation on).
* The AI can choose the policy from a digest of the base (how old the notes are, how
  much the old ones are still used, which tags and categories there are): Knowledge →
  Tune → "Let the AI decide". It says why; the values go through the tuning rules
  (``tuning.py``: applied inside the safe limits or proposed, logged, undoable).
  ``knowledge.decay_ai`` (off by default) repeats that review once a week while
  ZenSynora runs.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

EVERGREEN = "evergreen"
REVIEW_EVERY = 7 * 86400.0
LAST_USED_TTL = 300.0     # seconds the "last used" map is kept in memory
AGE_BUCKETS = ((30, "up to 30 days"), (90, "31-90 days"), (180, "91-180 days"),
               (365, "181-365 days"), (10 ** 9, "over a year"))

_OPTIONS: Dict[str, Any] = {"half_life_days": 0.0, "floor": 0.3, "categories": {}, "ai": False}
_LAST_USED: Dict[Tuple[str, str], Tuple[float, Dict[str, float]]] = {}


def set_options(half_life_days: Optional[float] = None, floor: Optional[float] = None,
                categories: Optional[Dict[str, Any]] = None, ai: Optional[bool] = None) -> None:
    try:
        if half_life_days is not None:
            _OPTIONS["half_life_days"] = max(0.0, float(half_life_days))
        if floor is not None:
            _OPTIONS["floor"] = max(0.0, min(1.0, float(floor)))
        if categories is not None:
            _OPTIONS["categories"] = {str(k).strip().lower(): max(0.0, float(v))
                                      for k, v in dict(categories).items() if str(k).strip()}
    except (TypeError, ValueError):
        pass
    if ai is not None:
        _OPTIONS["ai"] = bool(ai)


def options() -> Dict[str, Any]:
    return {**_OPTIONS, "categories": dict(_OPTIONS["categories"])}


def enabled() -> bool:
    return _OPTIONS["half_life_days"] > 0 or any(v > 0 for v in _OPTIONS["categories"].values())


def half_life_for(labels: Iterable[str]) -> float:
    """Half-life in days for a note's tags / a fact's category; 0 = never decays."""
    labels = [str(x).strip().lower() for x in labels if str(x).strip()]
    if EVERGREEN in labels:
        return 0.0
    own = [_OPTIONS["categories"][x] for x in labels if x in _OPTIONS["categories"]]
    if own:
        return 0.0 if min(own) <= 0 else max(own)
    return float(_OPTIONS["half_life_days"])


def factor(age_days: float, half_life: float) -> float:
    """1.0 (fresh, or no decay) down to the floor."""
    if half_life <= 0 or age_days <= 0:
        return 1.0
    return max(float(_OPTIONS["floor"]), 0.5 ** (age_days / half_life))


def _ts(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)[:19]).timestamp()
    except ValueError:
        return 0.0


def note_factor(tags: Iterable[str], updated_at: Any, last_used: Optional[float] = None,
                now: Optional[float] = None) -> Tuple[float, float, float]:
    """(factor, age in days since the last change or use, half-life used)."""
    now = time.time() if now is None else now
    fresh = max(_ts(updated_at), float(last_used or 0.0))
    age = max(0.0, (now - fresh) / 86400.0) if fresh else 0.0
    hl = half_life_for(tags)
    return factor(age, hl), age, hl


def fact_factor(category: str, age_days: float) -> Optional[float]:
    """The factor for a distilled fact's confidence, or None when decay is off
    (the caller keeps its built-in rule)."""
    if not enabled():
        return None
    return factor(age_days, half_life_for([category]))


def last_used(base: str) -> Dict[str, float]:
    """{permalink: time an agent last read it} from the usage log (cached a few minutes)."""
    from . import usage

    key = (str(usage.db_path()), base)
    hit = _LAST_USED.get(key)
    if hit and time.monotonic() - hit[0] < LAST_USED_TTL:
        return hit[1]
    out: Dict[str, float] = {}
    con = None
    try:
        con = usage._open_ro()
        if con is not None:
            for p, ts in con.execute("SELECT permalink, MAX(ts) FROM kb_usage_notes WHERE base = ? "
                                     "AND kind != 'write' GROUP BY permalink", (base,)):
                out[p] = float(ts or 0)
    except (sqlite3.Error, OSError) as e:
        logger.info("Knowledge: the usage log could not be read for decay: %s", e)
    finally:
        if con is not None:
            con.close()
    _LAST_USED[key] = (time.monotonic(), out)
    return out


def digest(base: str = "default") -> Dict[str, Any]:
    """What the AI (and the Tune page) sees: ages, use of old notes, tags, fact categories.
    Counts and names only — no note text."""
    from .db import KnowledgeDB

    now = time.time()
    used = last_used(base)
    buckets = [{"age": label, "notes": 0, "used_last_90_days": 0} for _, label in AGE_BUCKETS]
    tags: Dict[str, List[float]] = {}
    cats: Dict[str, List[float]] = {}
    total = 0
    with KnowledgeDB(base) as db:
        conn = db._get_connection()
        for r in conn.execute("SELECT permalink, updated_at, entity_metadata FROM entities"):
            total += 1
            age = max(0.0, (now - _ts(r["updated_at"])) / 86400.0)
            i = next(i for i, (days, _) in enumerate(AGE_BUCKETS) if age <= days)
            buckets[i]["notes"] += 1
            if now - used.get(r["permalink"], 0.0) <= 90 * 86400:
                buckets[i]["used_last_90_days"] += 1
            for t in _tags(r["entity_metadata"]):
                tags.setdefault(t, []).append(age)
        for r in conn.execute("SELECT category, created_at FROM observations"):
            c = str(r["category"] or "").strip().lower()
            if c:
                cats.setdefault(c, []).append(max(0.0, (now - _ts(r["created_at"])) / 86400.0))

    def top(d: Dict[str, List[float]], n: int = 25) -> List[Dict[str, Any]]:
        rows = sorted(d.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:n]
        return [{"name": k, "count": len(v), "median_age_days": round(statistics.median(v))} for k, v in rows]

    return {"base": base, "notes": total, "ages": buckets, "tags": top(tags), "fact_categories": top(cats),
            "usage_log": bool(used), "current": options(), "enabled": enabled()}


def _tags(meta_json: Any) -> List[str]:
    import json

    try:
        meta = json.loads(meta_json) if meta_json else {}
    except (TypeError, ValueError):
        return []
    tags = meta.get("tags") if isinstance(meta, dict) else None
    if isinstance(tags, str):
        tags = [t for t in tags.replace(",", " ").split()]
    return [str(t).strip().lower() for t in tags or [] if str(t).strip()]


def entity_tags(entity: Any) -> List[str]:
    meta = getattr(entity, "entity_metadata", None) or {}
    tags = meta.get("tags") if isinstance(meta, dict) else None
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    return [str(t).strip().lower() for t in tags or [] if str(t).strip()]


async def weekly_review(config: Any, base: str = "default") -> Optional[Dict[str, Any]]:
    """The AI review of the decay policy, at most once a week, only when
    ``knowledge.decay_ai`` is on. Returns what ``tuning.ai_suggest`` did, or None."""
    if not _OPTIONS["ai"]:
        return None
    from . import tuning

    if time.time() - float(tuning.state().get("decay_review_ts") or 0) < REVIEW_EVERY:
        return None
    tuning.mark("decay_review_ts", time.time())   # first: a failing model is not retried every tick
    try:
        return await tuning.ai_suggest(config, base, scope="decay", actor="ai (weekly decay review)")
    except Exception as e:
        logger.info("Knowledge: the weekly decay review did not run: %s", e)
        return None
