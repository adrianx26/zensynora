"""Knowledge base overview for the infographic (2026-11-01).

One read-only pass over the index: growth per day by *note date* (the earlier
of the file's modification time and the index date — the index date alone is
when a note was last re-indexed, so a rebuild made every note look new), the
change over 7 and 30 days, new and growing groups of linked notes, and what
needs attention, each with a few examples. Totals and structure come from
``benchmarks`` (cached); hit/miss ratios from the usage log and the response
cache. Facts and links count on the day of their note.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .benchmarks import _ro, _tables, benchmarks
from .health import db_path

MAX_DAYS = 730      # growth history returned (the browser groups it by week / month)
EXAMPLES = 5
STALE_DAYS = 90


def _ts(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def overview(base: str = "default") -> Dict[str, Any]:
    now = time.time()
    b = benchmarks(base)
    out: Dict[str, Any] = {
        "base": base, "totals": {k: b.get(k, 0) for k in ("notes", "facts", "links", "tags", "categories")},
        "change": {"7d": {"notes": 0, "facts": 0, "links": 0}, "30d": {"notes": 0, "facts": 0, "links": 0}},
        "growth": [], "groups": {"count": b.get("components", 0), "largest": b.get("largest_component", 0),
                                 "new": [], "growing": [], "sizes": []},
        "attention": [], "usage": _usage(base), "cache": _cache(), "embeddings": b.get("embeddings"),
        "computed_at": now, "error": b.get("error"),
    }
    path = db_path(base)
    if not path.is_file():
        return out
    con = _ro(path)
    try:
        if "entities" not in _tables(con):
            return out
        _fill(con, out, b, now)
    except sqlite3.Error as e:
        out["error"] = str(e)
    finally:
        con.close()
    return out


def _fill(con: sqlite3.Connection, out: Dict[str, Any], b: Dict[str, Any], now: float) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(entities)")}
    mtime = "mtime" if "mtime" in cols else "NULL"
    meta = "entity_metadata" if "entity_metadata" in cols else "NULL"
    title = "COALESCE(title, name)" if "title" in cols else "name"
    notes: Dict[int, Dict[str, Any]] = {}
    for i, permalink, t, created, updated, mt, md in con.execute(
            f"SELECT id, permalink, {title}, created_at, updated_at, {mtime}, {meta} FROM entities"):
        dates = [d for d in (_ts(created), _ts(mt)) if d]
        try:
            m = json.loads(md or "{}")
        except ValueError:
            m = {}
        own = _ts(m.get("created") or m.get("date")) if isinstance(m, dict) else None
        when = own or (min(dates) if dates else now)
        changed = max([d for d in (_ts(updated), _ts(mt)) if d] or [when])
        tags = m.get("tags") if isinstance(m, dict) else None
        notes[i] = {"permalink": permalink, "title": t or permalink, "date": when, "changed": changed,
                    "tags": bool(tags), "facts": 0, "links": 0}
    tables = _tables(con)
    facts_per: Counter = Counter()
    if "observations" in tables:
        facts_per.update({e: n for e, n in con.execute("SELECT entity_id, COUNT(*) FROM observations GROUP BY 1")})
    pairs: List[tuple] = []
    if "relations" in tables:
        pairs = [(f, t) for f, t in con.execute(
            "SELECT from_entity_id, to_entity_id FROM relations WHERE to_entity_id IS NOT NULL")
                 if f in notes and t in notes and f != t]
    degree: Counter = Counter()
    links_per: Counter = Counter()
    for f, t in pairs:
        degree[f] += 1
        degree[t] += 1
        links_per[f] += 1
    waiting_from = set()
    missing_names: Counter = Counter()
    if "pending_relations" in tables:
        for f, name in con.execute("SELECT from_entity_id, to_name FROM pending_relations"):
            waiting_from.add(f)
            missing_names[name] += 1

    # growth per day (by note date), oldest first; the browser groups it by week / month
    per_day: Dict[str, Dict[str, int]] = defaultdict(lambda: {"notes": 0, "facts": 0, "links": 0})
    for i, n in notes.items():
        n["facts"], n["links"] = facts_per.get(i, 0), links_per.get(i, 0)
        d = per_day[_day(n["date"])]
        d["notes"] += 1
        d["facts"] += n["facts"]
        d["links"] += n["links"]
        for span, days in (("7d", 7), ("30d", 30)):
            if n["date"] >= now - days * 86_400:
                c = out["change"][span]
                c["notes"] += 1
                c["facts"] += n["facts"]
                c["links"] += n["links"]
    if per_day:
        first = max(min(n["date"] for n in notes.values()), now - MAX_DAYS * 86_400)
        day, total = time.localtime(first), {"notes": 0, "facts": 0, "links": 0}
        t = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 12, 0, 0, 0, 0, -1))
        older = [n for n in notes.values() if n["date"] < first]  # beyond the window: start of the totals
        for k in total:
            total[k] = sum(n[k] if k != "notes" else 1 for n in older)
        while t <= now + 86_400:
            key = _day(t)
            row = per_day.get(key, {"notes": 0, "facts": 0, "links": 0})
            for k in total:
                total[k] += row[k]
            out["growth"].append({"day": key, **row, **{f"total_{k}": v for k, v in total.items()}})
            t += 86_400
        if out["growth"] and out["growth"][-1]["day"] > _day(now):
            out["growth"].pop()

    # groups of linked notes: new (first note this week) and growing (a new note this week)
    parent = {i: i for i in notes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f, t in pairs:
        a, c = find(f), find(t)
        if a != c:
            parent[a] = c
    members: Dict[int, List[int]] = defaultdict(list)
    for i in notes:
        members[find(i)].append(i)
    groups = [m for m in members.values() if len(m) > 1]
    week = now - 7 * 86_400
    summary = []
    for m in groups:
        hub = max(m, key=lambda i: degree[i])
        first_date = min(notes[i]["date"] for i in m)
        recent = sum(1 for i in m if notes[i]["date"] >= week)
        summary.append({"size": len(m), "hub": notes[hub]["title"], "hub_permalink": notes[hub]["permalink"],
                        "first": _day(first_date), "new_notes": recent, "is_new": first_date >= week})
    summary.sort(key=lambda g: -g["size"])
    g = out["groups"]
    g["count"], g["largest"] = len(groups), (summary[0]["size"] if summary else 0)
    g["sizes"] = [s["size"] for s in summary]
    g["top"] = summary[:10]
    g["new"] = [s for s in summary if s["is_new"]][:EXAMPLES]
    g["growing"] = sorted([s for s in summary if s["new_notes"] and not s["is_new"]],
                          key=lambda s: -s["new_notes"])[:EXAMPLES]
    g["new_count"] = sum(1 for s in summary if s["is_new"])
    g["growing_count"] = sum(1 for s in summary if s["new_notes"] and not s["is_new"])

    # what needs attention, with examples
    def ex(ids: List[int]) -> List[Dict[str, str]]:
        return [{"permalink": notes[i]["permalink"], "title": notes[i]["title"]}
                for i in sorted(ids, key=lambda i: -notes[i]["date"])[:EXAMPLES]]

    unlinked = [i for i in notes if not degree[i] and i not in waiting_from]
    no_facts = [i for i in notes if not notes[i]["facts"]]
    no_tags = [i for i in notes if not notes[i]["tags"]]
    stale = [i for i in notes if notes[i]["changed"] < now - STALE_DAYS * 86_400]
    pairs_only = [m for m in groups if len(m) == 2]
    usage = out["usage"]
    items = [
        ("unlinked", "Notes without links", len(unlinked), ex(unlinked),
         "Run ✨ Add tags & links, or link them by hand — they never show up as context for related notes."),
        ("missing", "Linked notes that do not exist", sum(missing_names.values()),
         [{"permalink": "", "title": n} for n, _ in missing_names.most_common(EXAMPLES)],
         "Create them (click the dashed dot in the graph) or remove the links."),
        ("conflicts", "Open conflicts (contradicting facts)", int(b.get("conflicts") or 0), [],
         "Resolve them in the note's side panel."),
        ("no_facts", "Notes without facts", len(no_facts), ex(no_facts),
         "Facts ('- [category] text') are what agents and distillation use most."),
        ("no_tags", "Notes without tags", len(no_tags), ex(no_tags),
         "Tags group notes and colour the graph; ✨ Add tags & links fills them."),
        ("small_groups", "Groups of only two notes", len(pairs_only),
         ex([m[0] for m in pairs_only]), "Small islands: link them to a bigger group if they belong there."),
        ("never_used", "Notes no agent used in 30 days", int(usage.get("never_used") or 0),
         usage.get("never_used_sample", [])[:EXAMPLES], "Candidates to improve, merge or move to the trash."),
        ("stale", f"Not changed in {STALE_DAYS} days", len(stale), ex(stale),
         "Check whether they are still true."),
    ]
    emb = b.get("embeddings") or {}
    if emb.get("waiting"):
        items.append(("embeddings", "Notes waiting for semantic search", int(emb["waiting"]), [],
                      "Backfill on the Semantic search card."))
    total = max(1, len(notes))
    out["attention"] = [{"id": i, "label": label, "count": n, "share": round(n / total, 3), "examples": e,
                         "hint": hint} for i, label, n, e, hint in items]


def _usage(base: str) -> Dict[str, Any]:
    """Knowledge search hits / misses (usage log, 30 days) and notes never used."""
    try:
        from . import usage
        from .benchmarks import titles

        s = usage.stats(base, days=30, titles=titles(base))
    except Exception as e:  # the log is optional
        return {"enabled": False, "error": str(e)[:200]}
    searches, hits = int(s.get("searches") or 0), int(s.get("hits") or 0)
    return {"enabled": bool(s.get("enabled")), "searches": searches, "hits": hits, "misses": searches - hits,
            "hit_rate": s.get("hit_rate"), "accesses_7d": s.get("accesses_7d", 0),
            "accesses_30d": s.get("accesses_30d", 0), "writes_30d": s.get("writes_30d", 0),
            "notes_used": s.get("notes_used", 0), "never_used": s.get("never_used"),
            "never_used_sample": s.get("never_used_sample", [])}


def _cache() -> Dict[str, Any]:
    """Semantic response cache hits / misses since start (only if it was created)."""
    from .. import semantic_cache

    c = semantic_cache._semantic_cache
    if c is None:
        return {"active": False, "hits": 0, "misses": 0, "entries": 0}
    return {"active": True, "hits": c.hits, "misses": c.misses, "entries": len(c._cache)}
