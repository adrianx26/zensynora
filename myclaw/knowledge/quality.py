"""Quality layer of the knowledge base (KB phase 2, 2026-10-24).

Adapted from memopad's "MemGraphRAG" ideas, rewritten for ZenSynora's
sqlite3 code. The Markdown files stay the source of truth: everything this
module stores (normalised categories, the category registry, conflicts) is
derived data in the index database and is rebuilt from the files; it only
changes a note file when the user (or an agent tool that resolves one
conflict) asks for it, through ``storage.update_note`` / ``delete_note`` so
the previous version is kept in ``<base>/.trash/``.

* **Fact sources** — ``- [spec] 32 GB RAM (source: chat 2026-10-24)``;
  helpers for the automatic extractors.
* **Categories** — normalised category per fact, a registry with counts and
  "merge X into Y" suggestions; merging rewrites the notes after a preview.
* **Conflicts** — conservative detection of contradicting facts of the same
  category on the same note (detection only; the user resolves them).
* **Hub penalty** — notes with very many links rank lower in the automatic
  context and are not traversed by multi-hop graph context.
* **Duplicates** — candidate pairs of duplicate notes and a previewed merge
  that keeps links working (the dropped title becomes an alias).
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import re
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── Switches (``knowledge.*`` in the config; applied by config.load_config) ──

_OPTIONS: Dict[str, Any] = {
    "detect_conflicts": True,   # only flags; never changes a note by itself
    "hub_penalty": True,
    "hub_degree": 25,
}


def set_options(detect_conflicts: Optional[bool] = None, hub_penalty: Optional[bool] = None,
                hub_degree: Optional[int] = None) -> None:
    """Apply the ``knowledge.detect_conflicts`` / ``hub_penalty`` / ``hub_degree`` settings."""
    if detect_conflicts is not None:
        _OPTIONS["detect_conflicts"] = bool(detect_conflicts)
    if hub_penalty is not None:
        _OPTIONS["hub_penalty"] = bool(hub_penalty)
    if hub_degree is not None:
        try:
            _OPTIONS["hub_degree"] = max(1, int(hub_degree))
        except (TypeError, ValueError):
            pass


def options() -> Dict[str, Any]:
    return dict(_OPTIONS)


# ── Fact sources (provenance) ────────────────────────────────────────────────

_MAX_SOURCE = 200


def _one_line(text: Any, limit: int = _MAX_SOURCE) -> str:
    out = " ".join(str(text or "").split())
    return out[:limit]


def chat_source(when: Optional[datetime] = None) -> str:
    """Source of facts extracted from a conversation: ``chat YYYY-MM-DD``."""
    return f"chat {(when or datetime.now()).strftime('%Y-%m-%d')}"


# The argument that says where a tool's output came from.
_TOOL_SOURCE_ARGS = ("url", "path", "file_path", "filename", "agent_name", "host", "query", "cmd")


def tool_source(tool_name: str, args: Any = None) -> str:
    """Source of facts extracted from a tool result, e.g. ``tool browse https://…``
    or ``tool read_file /etc/hosts``. ``args`` may be a dict or a JSON string."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    what = ""
    if isinstance(args, dict):
        for key in _TOOL_SOURCE_ARGS:
            value = args.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                what = _one_line(value, 160)
                break
    name = _one_line(tool_name, 40) or "tool"
    return f"tool {name} {what}".strip()


# ── Categories ───────────────────────────────────────────────────────────────

# Synonyms → the category they mean (after lower-casing and singular).
CATEGORY_SYNONYMS: Dict[str, str] = {
    "fact": "fact", "facts": "fact",
    "setting": "setting", "settings": "setting", "config": "setting", "configs": "setting",
    "configuration": "setting", "configurations": "setting", "conf": "setting", "cfg": "setting",
    "pref": "preference", "prefs": "preference", "preference": "preference",
    "preferences": "preference",
    "spec": "spec", "specs": "spec", "specification": "spec", "specifications": "spec",
    "url": "url", "urls": "url", "link": "url", "links": "url", "uri": "url", "website": "url",
    "howto": "how-to", "how-to": "how-to", "how-tos": "how-to", "howtos": "how-to",
    "people": "person", "persons": "person",
    "decisions": "decision", "decided": "decision",
    "todos": "todo", "to-do": "todo",
}

# Words that end in "s" but are not plurals.
_NOT_PLURAL = frozenset({
    "status", "news", "series", "species", "alias", "analysis", "basis", "bus", "gas", "os",
    "dns", "aws", "https", "ssl", "tls", "access", "process", "class", "address", "business",
    "progress", "success", "loss", "less", "bonus", "campus", "corpus", "focus", "virus",
    "census", "thesis", "axis", "chassis", "diagnosis", "canvas", "atlas", "lens", "plus",
    "always", "various", "previous", "kubernetes", "redis", "macos", "ios", "linux",
    "windows", "jenkins", "postgres", "express",
})


def _singular(word: str) -> str:
    if len(word) < 4 or not word.isalpha() or word in _NOT_PLURAL:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes", "zzes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalize_category(category: Any) -> str:
    """The canonical form of a fact category, for grouping and search.

    Lower case, trimmed, spaces/underscores as ``-``, simple plurals made
    singular, and common synonyms mapped (``Facts`` → ``fact``, ``config`` →
    ``setting``, ``prefs`` → ``preference``, ``specs`` → ``spec``, ``link`` →
    ``url``, ``how to`` → ``how-to``). The note files are never rewritten by
    this; it is stored next to the category in the index.
    """
    text = " ".join(str(category or "").split()).strip().lower()
    if not text:
        return ""
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    if text in CATEGORY_SYNONYMS:
        return CATEGORY_SYNONYMS[text]
    parts = text.split("-")
    parts[-1] = _singular(parts[-1])
    text = "-".join(parts)
    return CATEGORY_SYNONYMS.get(text, text)


# Short descriptions of the usual categories (shown in the registry).
_DESCRIPTIONS: Dict[str, str] = {
    "fact": "A plain fact",
    "setting": "A configuration value (ports, paths, options)",
    "preference": "Something the user prefers",
    "decision": "A decision that was taken",
    "spec": "A specification (hardware, versions, sizes)",
    "url": "A web address",
    "person": "A person and their role",
    "how-to": "Steps to do something",
    "todo": "Something still to do",
}


def ensure_schema(conn: Any) -> None:
    """Tables of the quality layer (additive; safe on any older index database)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observation_schema (
            category TEXT PRIMARY KEY,
            description TEXT,
            count INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT,
            variants TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conflicts (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER,
            permalink TEXT NOT NULL,
            category TEXT,
            obs_a TEXT NOT NULL,
            obs_b TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            pair_hash TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_permalink ON conflicts(permalink)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)")


def refresh_registry(conn: Any) -> int:
    """Recount the category registry from the indexed facts. Returns the number of categories.

    Counts are numbers of facts; ``variants`` the spellings used in the files.
    A category no longer used keeps its row with count 0 (and its first_seen).
    """
    now = datetime.now().isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT COALESCE(category_norm, '') AS norm, category, COUNT(*) AS n, "
        "MIN(created_at) AS first FROM observations GROUP BY norm, category").fetchall()
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        norm = r["norm"] or normalize_category(r["category"])
        if not norm:
            continue
        a = agg.setdefault(norm, {"count": 0, "variants": {}, "first": r["first"] or now})
        a["count"] += r["n"]
        a["variants"][r["category"] or norm] = a["variants"].get(r["category"] or norm, 0) + r["n"]
        if r["first"] and r["first"] < a["first"]:
            a["first"] = r["first"]
    conn.execute("UPDATE observation_schema SET count = 0, variants = '{}'")
    for norm, a in agg.items():
        conn.execute(
            "INSERT INTO observation_schema (category, description, count, first_seen, variants) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(category) DO UPDATE SET count = excluded.count, "
            "variants = excluded.variants, "
            "first_seen = MIN(COALESCE(observation_schema.first_seen, excluded.first_seen), "
            "excluded.first_seen), "
            "description = COALESCE(observation_schema.description, excluded.description)",
            (norm, _DESCRIPTIONS.get(norm), a["count"], a["first"],
             json.dumps(a["variants"], ensure_ascii=False)))
    return len(agg)


def _edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Levenshtein distance (stops early above ``limit``)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


RARE_USES = 2      # a spelling used this often or less may be merged
COMMON_USES = 3    # into one used at least this often


def category_suggestions(spellings: Dict[str, int]) -> list:
    """"Merge X into Y" suggestions from ``{spelling as written: number of facts}``.

    A rare spelling (≤ 2 facts) is suggested for merging into a common one
    (≥ 3 facts, and more than the rare one) when both normalise to the same
    category (``Specs`` / ``spec``, ``config`` / ``setting``), or their
    normalised forms differ by at most 2 letters (only for words of 5+
    letters: ``preferance`` / ``preference``; short words like ``port`` /
    ``post`` differ too easily).
    """
    out = []
    common = sorted(((c, n) for c, n in spellings.items() if n >= COMMON_USES),
                    key=lambda x: (-x[1], x[0]))
    for rare, n in sorted(spellings.items()):
        if n > RARE_USES:
            continue
        rn = normalize_category(rare)
        best = None
        for cand, m in common:
            if cand == rare or m <= n:
                continue
            cn = normalize_category(cand)
            if rn == cn:
                reason = "same category"
                if rare.strip().lower() == cand.strip().lower():
                    reason = "same word, other spelling"
                elif CATEGORY_SYNONYMS.get(re.sub(r"[\s_]+", "-", rare.strip().lower())) == cn:
                    reason = "synonym"
                best = (cand, m, reason)
                break
            if min(len(rn), len(cn)) >= 5 and _edit_distance(rn, cn, 2) <= 2:
                best = best or (cand, m, "similar spelling")
        if best:
            out.append({"from": rare, "to": best[0], "from_count": n, "to_count": best[1],
                        "reason": best[2]})
    return out


def list_categories(user_id: str = "default", db_path: Any = None) -> Dict[str, Any]:
    """The category registry (refreshed now) and merge suggestions."""
    from .db import KnowledgeDB

    with KnowledgeDB(user_id, db_path=db_path) as db:
        with db._write_txn() as conn:
            refresh_registry(conn)
        conn = db._get_connection()
        rows = conn.execute(
            "SELECT category, description, count, first_seen, variants FROM observation_schema "
            "WHERE count > 0 ORDER BY count DESC, category").fetchall()
        spellings: Dict[str, int] = {}
        for r in conn.execute("SELECT category, COUNT(*) AS n FROM observations "
                              "WHERE category IS NOT NULL AND category != '' GROUP BY category"):
            spellings[r["category"]] = r["n"]
    cats = []
    for r in rows:
        try:
            variants = json.loads(r["variants"] or "{}")
        except ValueError:
            variants = {}
        cats.append({"name": r["category"], "count": r["count"], "description": r["description"],
                     "first_seen": r["first_seen"], "variants": variants})
    return {"categories": cats, "suggestions": category_suggestions(spellings)}


def _same_spelling(a: str, b: str) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def merge_category(user_id: str, from_category: str, to_category: str, dry_run: bool = True,
                   db_path: Any = None) -> Dict[str, Any]:
    """Rename the fact category ``from_category`` to ``to_category`` in every note.

    ``dry_run=True`` (default) only returns what would change: per note the
    fact lines before → after. With ``dry_run=False`` each note is rewritten
    with ``storage.update_note`` (its previous version is kept in
    ``<base>/.trash/``). The spelling is matched case-insensitively.
    """
    from .db import KnowledgeDB
    from .parser import Observation
    from .storage import read_note, update_note

    src = " ".join(str(from_category or "").split())
    dst = " ".join(str(to_category or "").split()).strip("[]").strip()
    if not src or not dst:
        raise ValueError("Give both categories: the one to merge and the one to keep.")
    if "]" in dst or "\n" in dst:
        raise ValueError("A category cannot contain ']' or a line break.")
    if _same_spelling(src, dst) and src == dst:
        raise ValueError("The two categories are the same.")
    with KnowledgeDB(user_id, db_path=db_path) as db:
        rows = db._get_connection().execute(
            "SELECT DISTINCT e.permalink FROM observations o JOIN entities e ON e.id = o.entity_id "
            "WHERE lower(trim(o.category)) = lower(?) ORDER BY e.permalink", (src,)).fetchall()
    permalinks = [r["permalink"] for r in rows]
    notes_out = []
    changed_facts = 0
    for permalink in permalinks:
        note = read_note(permalink, user_id, db_path=db_path)
        if note is None:
            continue
        changes = []
        new_obs = []
        seen = set()
        for o in note.observations:
            cat = dst if _same_spelling(o.category, src) else o.category
            if cat != o.category:
                changes.append({"before": f"[{o.category}] {o.content}", "after": f"[{cat}] {o.content}"})
            key = (cat, o.content)
            if key in seen:
                continue
            seen.add(key)
            new_obs.append(Observation(category=cat, content=o.content, tags=list(o.tags or []),
                                       context=o.context, provenance_path=o.provenance_path))
        if not changes:
            continue
        changed_facts += len(changes)
        notes_out.append({"permalink": note.permalink, "title": note.title, "changes": changes})
        if not dry_run:
            update_note(note.permalink, user_id, db_path=db_path, observations=new_obs)
    return {"from": src, "to": dst, "dry_run": bool(dry_run), "notes": notes_out,
            "facts": changed_facts}


# ── Conflicts (detection only) ───────────────────────────────────────────────
#
# memopad flagged every pair of differently-worded facts in a category, which
# its own review called far too aggressive. Here a pair is flagged only when
# it clearly contradicts:
#   * "number": the same words with different numbers ("32 GB RAM" / "64 GB
#     RAM", "listens on port 11434" / "… port 8080"); dates and times are
#     history, not contradictions, and "Server 1 …" / "Server 2 …" are two
#     subjects;
#   * "value": the same "key: value" / "key = value" key with another value,
#     or "X is Y" with two value-like Ys ("default model is llama3" /
#     "… is qwen2.5").
# Identical facts, one fact containing the other, and facts about different
# things are never flagged.

_NUM = re.compile(r"\d+(?:[.,:/\-]\d+)*")
_DATE_TIME = re.compile(
    r"^(?:\d{4}-\d{1,2}(?:-\d{1,2})?|\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?)$")
_YEAR = re.compile(r"^(?:19|20)\d\d$")  # a year in a sentence; "port: 2000" is a value
# Words before a number that make it an ordinal/identifier, not a value.
_ORDINAL_WORDS = frozenset({
    "step", "phase", "part", "item", "no", "nr", "number", "chapter", "section", "line", "page",
    "day", "week", "round", "attempt", "level", "stage", "option", "case", "example", "rule",
    "point", "task", "issue", "pr", "ticket", "#",
})
_GENERIC_KEYS = frozenset({
    "note", "notes", "tip", "tips", "todo", "warning", "info", "example", "see", "update", "idea",
    "question", "important", "fyi", "ps", "reminder", "also", "nb", "source", "why", "how",
    "what", "when", "result", "summary", "status update",
})
_KV = re.compile(r"^\s*([^:=]{1,60}?)\s*(?::\s+|\s*=\s*)(.+?)\s*$")
_IS = re.compile(r"^\s*(.{2,80}?)\s+(?:is|are|was set to|is set to|=)\s+(.+?)\s*\.?\s*$",
                 re.IGNORECASE)
_ARTICLES = re.compile(r"^(?:the|a|an|my|our|its|his|her|their)\s+", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|n't)\b", re.IGNORECASE)


@functools.lru_cache(maxsize=8192)
def _norm_text(text: str) -> str:
    return " ".join(str(text or "").split()).casefold().rstrip(".")


@functools.lru_cache(maxsize=8192)
def _split_units(text: str) -> str:
    """``32GB`` → ``32 gb`` so "32 GB RAM" and "32GB RAM" read the same."""
    return re.sub(r"(?<=\d)(?=[^\W\d_])", " ", _norm_text(text))


def _contains(a: str, b: str) -> bool:
    """Is ``b`` a whole-word part of ``a``? (no regex: patterns per pair thrash re's cache)"""
    if not b:
        return False
    start = a.find(b)
    while start != -1:
        end = start + len(b)
        before_ok = start == 0 or not (a[start - 1].isalnum() or a[start - 1] == "_")
        after_ok = end == len(a) or not (a[end].isalnum() or a[end] == "_")
        if before_ok and after_ok:
            return True
        start = a.find(b, start + 1)
    return False


@functools.lru_cache(maxsize=8192)
def _number_shape(text: str) -> tuple:
    """``(spelled text, numbers, template, words before each number)`` of a fact."""
    t = _split_units(text)
    nums = tuple(_NUM.findall(t))
    tmpl = _NUM.sub("\x01", t)
    words = re.findall(r"\x01|[^\W\d_]+|[^\w\s\x01]", tmpl)
    positions = [i for i, w in enumerate(words) if w == "\x01"]
    before = tuple((p, words[p - 1] if p > 0 else "") for p in positions)
    return t, nums, tmpl, before


_SMALL_INT = re.compile(r"\d{1,2}")
_HAS_WORD = re.compile(r"[^\W\d_]{2,}")


def _number_conflict(a: str, b: str) -> bool:
    ta, na, tmpl_a, before_a = _number_shape(a)
    tb, nb, tmpl_b, _ = _number_shape(b)
    if ta == tb or not na or len(na) != len(nb):
        return False
    if tmpl_a != tmpl_b or not _HAS_WORD.search(tmpl_a):
        return False
    diffs = [k for k, (x, y) in enumerate(zip(na, nb)) if x != y]
    if not diffs:
        return False
    for k in diffs:
        x, y = na[k], nb[k]
        pos, before = before_a[k] if k < len(before_a) else (-1, "")
        if _DATE_TIME.match(x) and _DATE_TIME.match(y):
            return False  # history ("backup ran on 2026-10-01" / "… 10-02")
        if _YEAR.match(x) and _YEAR.match(y) and before not in (":", "="):
            return False  # "released in 2019" / "… 2021"
        if before in _ORDINAL_WORDS:
            return False  # "step 1" / "step 2"
        if pos == 1 and before and _SMALL_INT.fullmatch(x) and _SMALL_INT.fullmatch(y):
            # "Server 1 has …" / "Server 2 has …": the number names the subject
            return False
    return True


def _value_like(v: str) -> bool:
    v = v.strip().strip("\"'`")
    if not v or len(v.split()) > 3 or _NEGATION.search(v):
        return False
    return bool(re.search(r"[\d./:@_\\-]", v)) or v[:1].isupper()


@functools.lru_cache(maxsize=8192)
def _key_value(text: str):
    """``(kind, key, value)`` of a "key: value" / "X is Y" fact, or None."""
    t = " ".join(str(text or "").split()).rstrip(".")
    m = _KV.match(t)
    if m and re.search(r"[^\W\d_]", m.group(1)):
        key = _ARTICLES.sub("", m.group(1).strip()).casefold()
        if key and key not in _GENERIC_KEYS and len(key.split()) <= 6:
            return ("kv", key, m.group(2).strip())
    m = _IS.match(t)
    if m:
        key = _ARTICLES.sub("", m.group(1).strip()).casefold()
        value = m.group(2).strip()
        if key and key not in _GENERIC_KEYS and len(key.split()) <= 6 and not _NEGATION.search(t):
            return ("is", key, value)
    return None


def _value_conflict(a: str, b: str) -> bool:
    ka, kb = _key_value(a), _key_value(b)
    if not ka or not kb or ka[1] != kb[1]:
        return False
    va, vb = _norm_text(ka[2]), _norm_text(kb[2])
    if not va or not vb or va == vb or _contains(va, vb) or _contains(vb, va):
        return False
    if _NUM.sub("#", _split_units(va)) == _NUM.sub("#", _split_units(vb)):
        return _number_conflict(a, b)  # only numbers differ: dates/ids are not conflicts
    if "is" in (ka[0], kb[0]):
        return _value_like(ka[2]) and _value_like(kb[2])
    return len(va) <= 120 and len(vb) <= 120


def conflict_kind(a: str, b: str) -> Optional[str]:
    """"number" / "value" when the two facts clearly contradict each other, else None."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb or na == nb or _contains(na, nb) or _contains(nb, na):
        return None
    if _value_conflict(a, b):
        return "value"
    if _number_conflict(a, b):
        return "number"
    return None


def find_conflicts(observations: Any) -> list:
    """``[(index a, index b, kind)]`` for contradicting facts of the same category."""
    by_cat: Dict[str, list] = {}
    for i, o in enumerate(observations or []):
        content = getattr(o, "content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        by_cat.setdefault(normalize_category(getattr(o, "category", "")), []).append(i)
    out = []
    for idx in by_cat.values():
        if len(idx) < 2 or len(idx) > 200:  # 200+ facts in one category: a list, not settings
            continue
        # Only facts with the same words around their numbers, or the same
        # "key", can contradict: compare within those groups (fast for big notes).
        groups: Dict[tuple, list] = {}
        for i in idx:
            text = observations[i].content
            su = _split_units(text)
            if _NUM.search(su):
                groups.setdefault(("n", _NUM.sub("#", su)), []).append(i)
            kv = _key_value(text)
            if kv:
                groups.setdefault(("k", kv[1]), []).append(i)
        pairs = set()
        for members in groups.values():
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    pairs.add((min(members[x], members[y]), max(members[x], members[y])))
        for i, j in sorted(pairs):
            kind = conflict_kind(observations[i].content, observations[j].content)
            if kind:
                out.append((i, j, kind))
    return out


def pair_hash(permalink: str, category: str, a: str, b: str) -> str:
    x, y = sorted((_norm_text(a), _norm_text(b)))
    raw = "\x00".join((str(permalink), normalize_category(category), x, y))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def on_note_indexed(conn: Any, entity_id: int, permalink: str, observations: Any) -> None:
    """Called by ``KnowledgeDB.sync_entity_from_note`` inside its transaction.

    Records new conflicts of this note, re-opens one whose facts came back,
    keeps dismissed pairs dismissed, and closes (``resolved``) open conflicts
    whose two facts are no longer both in the note.
    """
    if not _OPTIONS.get("detect_conflicts", True):
        return
    now = datetime.now().isoformat(timespec="seconds")
    current: Dict[str, tuple] = {}
    obs = list(observations or [])
    for i, j, kind in find_conflicts(obs):
        h = pair_hash(permalink, obs[i].category, obs[i].content, obs[j].content)
        current[h] = (normalize_category(obs[i].category), obs[i].content, obs[j].content, kind)
    rows = conn.execute("SELECT id, pair_hash, status FROM conflicts WHERE permalink = ?",
                        (permalink,)).fetchall()
    known = {r["pair_hash"]: r for r in rows}
    for h, r in known.items():
        if h not in current and r["status"] == "open":
            conn.execute("UPDATE conflicts SET status = 'resolved', updated_at = ? WHERE id = ?",
                         (now, r["id"]))
    for h, (cat, a, b, kind) in current.items():
        r = known.get(h)
        if r is None:
            # the hash is unique across notes too (it includes the permalink)
            conn.execute(
                "INSERT OR IGNORE INTO conflicts (entity_id, permalink, category, obs_a, obs_b, kind, "
                "status, created_at, updated_at, pair_hash) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (entity_id, permalink, cat, a, b, kind, now, now, h))
        elif r["status"] == "dismissed":
            conn.execute("UPDATE conflicts SET entity_id = ? WHERE id = ?", (entity_id, r["id"]))
        else:
            conn.execute(
                "UPDATE conflicts SET entity_id = ?, obs_a = ?, obs_b = ?, kind = ?, status = 'open', "
                "updated_at = CASE WHEN status = 'open' THEN updated_at ELSE ? END WHERE id = ?",
                (entity_id, a, b, kind, now, r["id"]))


def on_note_deleted(conn: Any, permalink: str) -> None:
    """Close the open conflicts of a deleted note (called by ``delete_entity``)."""
    conn.execute("UPDATE conflicts SET status = 'resolved', updated_at = ? "
                 "WHERE permalink = ? AND status = 'open'",
                 (datetime.now().isoformat(timespec="seconds"), permalink))


def _conflict_dict(r: Any) -> Dict[str, Any]:
    return {"id": r["id"], "permalink": r["permalink"], "category": r["category"],
            "a": r["obs_a"], "b": r["obs_b"], "kind": r["kind"], "status": r["status"],
            "created_at": r["created_at"], "title": r["title"] if "title" in r.keys() else None}


def list_conflicts(user_id: str = "default", permalink: str = "", status: str = "open",
                   db_path: Any = None) -> list:
    """Conflicts (default: the open ones) of existing notes, newest first."""
    from .db import KnowledgeDB

    sql = ("SELECT c.*, COALESCE(e.title, e.name) AS title FROM conflicts c "
           "JOIN entities e ON e.permalink = c.permalink WHERE 1 = 1")
    params: list = []
    if status:
        sql += " AND c.status = ?"
        params.append(status)
    if permalink:
        sql += " AND c.permalink = ?"
        params.append(permalink)
    sql += " ORDER BY c.id DESC"
    with KnowledgeDB(user_id, db_path=db_path) as db:
        rows = db._get_connection().execute(sql, params).fetchall()
    return [_conflict_dict(r) for r in rows]


def conflict_counts(user_id: str = "default", db_path: Any = None) -> Dict[str, int]:
    """``{permalink: open conflicts}`` (for the graph marker)."""
    from .db import KnowledgeDB

    try:
        with KnowledgeDB(user_id, db_path=db_path) as db:
            rows = db._get_connection().execute(
                "SELECT permalink, COUNT(*) AS n FROM conflicts WHERE status = 'open' "
                "GROUP BY permalink").fetchall()
    except Exception as e:  # derived data: never break the graph
        logger.debug("conflict counts unavailable: %s", e)
        return {}
    return {r["permalink"]: r["n"] for r in rows}


def resolve_conflict(user_id: str, conflict_id: Any, keep: str, db_path: Any = None) -> Dict[str, Any]:
    """Resolve one open conflict.

    ``keep="a"`` / ``"b"``: the other fact is removed from the note FILE
    (``storage.update_note``; the previous version goes to ``.trash/``).
    ``keep="both"``: the pair is dismissed and never flagged again.
    Raises ``ValueError`` for a bad id / keep, ``LookupError`` when unknown.
    """
    from .db import KnowledgeDB
    from .storage import read_note, update_note

    keep = str(keep or "").strip().lower()
    if keep not in ("a", "b", "both"):
        raise ValueError('keep must be "a", "b" or "both".')
    try:
        cid = int(conflict_id)
    except (TypeError, ValueError):
        raise ValueError(f"Conflict id must be a number, not {conflict_id!r}.") from None
    with KnowledgeDB(user_id, db_path=db_path) as db:
        row = db._get_connection().execute("SELECT * FROM conflicts WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise LookupError(f"No conflict with id {cid}.")
    c = _conflict_dict(row)
    if c["status"] != "open":
        return {"ok": True, "conflict": c, "changed": False,
                "message": f"Conflict {cid} is already {c['status']}."}
    now = datetime.now().isoformat(timespec="seconds")

    def _set(status: str) -> None:
        with KnowledgeDB(user_id, db_path=db_path) as db2:
            with db2._write_txn() as conn:
                conn.execute("UPDATE conflicts SET status = ?, updated_at = ? WHERE id = ?",
                             (status, now, cid))

    if keep == "both":
        _set("dismissed")
        c["status"] = "dismissed"
        return {"ok": True, "conflict": c, "changed": False,
                "message": "Both facts kept; this pair will not be flagged again."}
    drop_text = c["b"] if keep == "a" else c["a"]
    note = read_note(c["permalink"], user_id, db_path=db_path)
    if note is None:
        _set("resolved")
        return {"ok": True, "conflict": c, "changed": False,
                "message": f"The note {c['permalink']} no longer exists; conflict closed."}
    target = _norm_text(drop_text)
    kept = [o for o in note.observations
            if not (_norm_text(o.content) == target
                    and normalize_category(o.category) == (c["category"] or normalize_category(o.category)))]
    if len(kept) == len(note.observations):
        _set("resolved")
        return {"ok": True, "conflict": c, "changed": False,
                "message": "That fact is no longer in the note; conflict closed."}
    update_note(note.permalink, user_id, db_path=db_path, observations=kept)
    _set("resolved")
    c["status"] = "resolved"
    return {"ok": True, "conflict": c, "changed": True,
            "message": f"Kept \"{c[keep]}\"; removed \"{drop_text}\" from {note.title} "
                       "(the previous version is in .trash/)."}


# ── Hub penalty ──────────────────────────────────────────────────────────────
#
# A note with very many links ("Homelab", "Linux") matches many searches and
# says little about any one of them. In the automatic context such hubs rank
# lower (never removed), and multi-hop graph context lists a hub without
# walking through it (its neighbours would flood the context).


def hub_threshold() -> Optional[int]:
    """Links (in + out) above which a note is a hub; None when the penalty is off."""
    if not _OPTIONS.get("hub_penalty", True):
        return None
    try:
        return max(1, int(_OPTIONS.get("hub_degree", 25)))
    except (TypeError, ValueError):
        return 25


def hub_factor(degree: int, threshold: Optional[int] = None) -> float:
    """Score multiplier: 1 up to the threshold, then 1 / (1 + ln(degree / threshold))."""
    thr = hub_threshold() if threshold is None else threshold
    if not thr or degree <= thr:
        return 1.0
    return 1.0 / (1.0 + math.log(degree / thr))


def degrees_by_permalink(conn: Any, permalinks: Any) -> Dict[str, int]:
    """``{permalink: links in + out}`` for the given notes (one query)."""
    plinks = [p for p in dict.fromkeys(permalinks or []) if isinstance(p, str) and p]
    if not plinks:
        return {}
    ph = ",".join("?" * len(plinks))
    rows = conn.execute(
        f"SELECT e.permalink, "
        f"(SELECT COUNT(*) FROM relations r WHERE r.from_entity_id = e.id) + "
        f"(SELECT COUNT(*) FROM relations r WHERE r.to_entity_id = e.id) AS degree "
        f"FROM entities e WHERE e.permalink IN ({ph})", plinks).fetchall()
    return {r["permalink"]: int(r["degree"] or 0) for r in rows}


def degrees_by_id(conn: Any, ids: Any) -> Dict[int, int]:
    """``{entity id: links in + out}``."""
    eids = [int(i) for i in dict.fromkeys(ids or [])]
    if not eids:
        return {}
    ph = ",".join("?" * len(eids))
    out = {i: 0 for i in eids}
    for r in conn.execute(f"SELECT from_entity_id AS i, COUNT(*) AS n FROM relations "
                          f"WHERE from_entity_id IN ({ph}) GROUP BY from_entity_id", eids):
        out[r["i"]] += r["n"]
    for r in conn.execute(f"SELECT to_entity_id AS i, COUNT(*) AS n FROM relations "
                          f"WHERE to_entity_id IN ({ph}) GROUP BY to_entity_id", eids):
        out[r["i"]] += r["n"]
    return out


def rank_with_hub_penalty(notes: Any, user_id: str = "default", db_path: Any = None,
                          pinned: Any = ()) -> list:
    """``notes`` (best match first) re-ordered so hubs rank lower; nothing is removed.

    Score = 1 / (1 + rank) × :func:`hub_factor`. Notes in ``pinned`` (asked
    for explicitly, e.g. ``memory://x``) keep their place. Any problem (no
    index, mocks in tests) returns the list unchanged.
    """
    notes = list(notes or [])
    thr = hub_threshold()
    if thr is None or len(notes) < 2:
        return notes
    try:
        from .db import KnowledgeDB

        plinks = [getattr(n, "permalink", None) for n in notes]
        with KnowledgeDB(user_id, db_path=db_path) as db:
            degrees = degrees_by_permalink(db._get_connection(), plinks)
    except Exception as e:
        logger.debug("hub penalty skipped: %s", e)
        return notes
    if not any(d > thr for d in degrees.values()):
        return notes
    pinned = set(pinned or ())
    scored = []
    for i, n in enumerate(notes):
        p = getattr(n, "permalink", None)
        f = 1.0 if p in pinned else hub_factor(degrees.get(p, 0) if isinstance(p, str) else 0, thr)
        scored.append((-(1.0 / (1 + i)) * f, i, n))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [n for _, _, n in scored]


# ── Duplicate notes (optimize_storage) ───────────────────────────────────────
#
# Never automatic: find_duplicates() only lists candidate pairs, and
# merge_notes() returns a preview unless dry_run=False is passed (a user
# action in the Web UI, not an agent tool).

_SHORT_NOTE = 1500          # "very similar text" is only checked for short notes
_SIMILAR = 0.92
_MAX_COMPARISONS = 200_000


def _title_key(text: Any) -> str:
    t = str(text or "").casefold()
    t = re.sub(r"[^\w]+", " ", t)
    return " ".join(t.split())


def _body_of(note: Any) -> str:
    from .parser import extract_body

    try:
        return extract_body(getattr(note, "content", "") or "", getattr(note, "title", None))
    except Exception:
        return ""


def _squash(text: str) -> str:
    return " ".join(str(text or "").split())


def _note_summary(note: Any, degree: int) -> Dict[str, Any]:
    created = getattr(note, "created_at", None)
    return {"permalink": note.permalink, "title": note.title or note.name or note.permalink,
            "facts": len(note.observations or []), "links": degree,
            "created": created.isoformat() if created else None,
            "file": str(note.file_path) if note.file_path else None}


def _older_key(note: Any) -> tuple:
    created = getattr(note, "created_at", None)
    try:
        ts = created.timestamp() if created else None
    except (OverflowError, OSError, ValueError):
        ts = None
    if ts is None:
        try:
            ts = note.file_path.stat().st_mtime if note.file_path else float("inf")
        except OSError:
            ts = float("inf")
    return (ts, note.permalink)


def find_duplicates(user_id: str = "default", db_path: Any = None, limit: int = 100) -> list:
    """Candidate pairs of duplicate notes, best evidence first. Changes nothing.

    A pair is listed when the titles are the same after normalising (case,
    punctuation, spaces), one note's title is an alias of the other, the text
    is identical, or two short notes (≤ 1500 characters) are ≥ 92 % similar.
    Each pair says which note would be kept: more links, then more facts,
    then the older one.
    """
    import difflib

    from .db import KnowledgeDB
    from .storage import list_notes

    notes = [n for n in list_notes(user_id) if getattr(n, "permalink", None)]
    if len(notes) < 2:
        return []
    try:
        with KnowledgeDB(user_id, db_path=db_path) as db:
            degrees = degrees_by_permalink(db._get_connection(), [n.permalink for n in notes])
    except Exception as e:
        logger.debug("duplicates: link counts unavailable: %s", e)
        degrees = {}
    by_perm = {n.permalink: n for n in notes}
    reasons: Dict[tuple, list] = {}

    def add(a: str, b: str, why: str) -> None:
        if a == b:
            return
        key = tuple(sorted((a, b)))
        lst = reasons.setdefault(key, [])
        if why not in lst:
            lst.append(why)

    by_title: Dict[str, list] = {}
    for n in notes:
        k = _title_key(n.title or n.name)
        if k:
            by_title.setdefault(k, []).append(n.permalink)
    for perms in by_title.values():
        for i in range(len(perms)):
            for j in range(i + 1, len(perms)):
                add(perms[i], perms[j], "same title")
    for n in notes:
        for alias in n.aliases or []:
            for other in by_title.get(_title_key(alias), []):
                add(n.permalink, other, "title is an alias of the other note")
    bodies: Dict[str, str] = {}
    by_hash: Dict[str, list] = {}
    for n in notes:
        body = _squash(_body_of(n))
        bodies[n.permalink] = body
        if len(body) >= 20:
            h = hashlib.md5(body.casefold().encode("utf-8")).hexdigest()
            by_hash.setdefault(h, []).append(n.permalink)
    for perms in by_hash.values():
        for i in range(len(perms)):
            for j in range(i + 1, len(perms)):
                add(perms[i], perms[j], "identical text")
    short = sorted(((len(b), p) for p, b in bodies.items() if 40 <= len(b) <= _SHORT_NOTE))
    comparisons = 0
    for i, (la, pa) in enumerate(short):
        for lb, pb in short[i + 1:]:
            if lb > la / _SIMILAR:  # sorted by length: the rest are too long
                break
            comparisons += 1
            if comparisons > _MAX_COMPARISONS:
                break
            if tuple(sorted((pa, pb))) in reasons:
                continue
            sm = difflib.SequenceMatcher(None, bodies[pa].casefold(), bodies[pb].casefold(),
                                         autojunk=False)
            if sm.real_quick_ratio() >= _SIMILAR and sm.quick_ratio() >= _SIMILAR \
                    and sm.ratio() >= _SIMILAR:
                add(pa, pb, f"very similar text ({int(sm.ratio() * 100)} %)")
        if comparisons > _MAX_COMPARISONS:
            break

    out = []
    for (a, b), why in reasons.items():
        na, nb = by_perm[a], by_perm[b]

        def rank(n: Any) -> tuple:
            return (-degrees.get(n.permalink, 0), -len(n.observations or []), _older_key(n))

        keep, drop = (na, nb) if rank(na) <= rank(nb) else (nb, na)
        out.append({"keep": _note_summary(keep, degrees.get(keep.permalink, 0)),
                    "drop": _note_summary(drop, degrees.get(drop.permalink, 0)),
                    "reasons": why})
    strength = {"same title": 0, "identical text": 0, "title is an alias of the other note": 1}
    out.sort(key=lambda d: (min(strength.get(r, 2) for r in d["reasons"]), -len(d["reasons"]),
                            d["keep"]["permalink"], d["drop"]["permalink"]))
    return out[:max(1, int(limit))]


def _fold_names(note: Any) -> set:
    names = {note.permalink, note.title, note.name, *(note.aliases or [])}
    return {_squash(x).casefold() for x in names if isinstance(x, str) and x.strip()}


def merge_notes(user_id: str, keep: str, drop: str, dry_run: bool = True,
                db_path: Any = None) -> Dict[str, Any]:
    """Merge note ``drop`` into note ``keep``.

    Result: the union of tags, aliases, facts and links (links between the
    two notes are dropped), the dropped note's text appended under
    ``## Merged from <title>`` unless the kept text already contains it, and
    the dropped note's title, aliases and permalink added as aliases of the
    kept note — so existing ``[[links]]`` to the dropped note now resolve to
    the kept one. ``dry_run=True`` (default) returns this preview only; with
    ``dry_run=False`` the kept note is rewritten (``update_note``) and the
    dropped file is moved to ``<base>/.trash/`` (``delete_note``).
    Raises ``ValueError`` / ``LookupError`` for bad input.
    """
    from .parser import Observation, Relation
    from .storage import delete_note, read_note, update_note

    keep = str(keep or "").strip()
    drop = str(drop or "").strip()
    if not keep or not drop:
        raise ValueError("Give the note to keep and the note to merge into it.")
    if keep == drop:
        raise ValueError("A note cannot be merged into itself.")
    k = read_note(keep, user_id, db_path=db_path)
    if k is None:
        raise LookupError(f"Note not found: {keep}")
    d = read_note(drop, user_id, db_path=db_path)
    if d is None:
        raise LookupError(f"Note not found: {drop}")

    def _uniq(items: Any) -> list:
        out: list = []
        seen = set()
        for x in items:
            key = _squash(x).casefold()
            if key and key not in seen:
                seen.add(key)
                out.append(_squash(x))
        return out

    tags = _uniq(list(k.tags or []) + list(d.tags or []))
    k_names = {_squash(x).casefold() for x in (k.title, k.permalink) if x}
    aliases = [a for a in _uniq(list(k.aliases or []) + [d.title or "", *(d.aliases or []), d.permalink])
               if a.casefold() not in k_names]
    facts: list = []
    seen_f = set()
    for o in list(k.observations or []) + list(d.observations or []):
        key = (normalize_category(o.category), _norm_text(o.content))
        if key in seen_f:
            continue
        seen_f.add(key)
        facts.append(Observation(category=o.category, content=o.content, tags=list(o.tags or []),
                                 context=o.context, provenance_path=o.provenance_path))
    both = _fold_names(k) | _fold_names(d)
    links: list = []
    seen_l = set()
    for r in list(k.relations or []) + list(d.relations or []):
        target = _squash(r.target)
        base = target.split("|", 1)[0].split("#", 1)[0].strip().casefold()
        if base in both:
            continue  # a link between the two notes would become a self-link
        key = (r.relation_type, target.casefold())
        if key in seen_l:
            continue
        seen_l.add(key)
        links.append(Relation(relation_type=r.relation_type, target=target))
    k_body = _body_of(k).strip()
    d_body = _body_of(d).strip()
    title_d = d.title or d.name or d.permalink
    if d_body and _squash(d_body).casefold() not in _squash(k_body).casefold():
        body = (k_body + "\n\n" if k_body else "") + f"## Merged from {title_d}\n\n{d_body}"
    else:
        body = k_body
    metadata = {**(d.metadata or {}), **(k.metadata or {})}
    preview = {
        "keep": k.permalink, "drop": d.permalink, "title": k.title, "dry_run": bool(dry_run),
        "tags": tags, "aliases": aliases,
        "facts": [f"[{o.category}] {o.content}" for o in facts],
        "links": [f"{r.relation_type} [[{r.target}]]" for r in links],
        "body": body,
        "added": {
            "tags": [t for t in tags if t.casefold() not in {x.casefold() for x in (k.tags or [])}],
            "aliases": [a for a in aliases if a.casefold() not in {x.casefold() for x in (k.aliases or [])}],
            "facts": len(facts) - len(k.observations or []),
            "links": len(links) - len([r for r in (k.relations or [])
                                        if r.target.split("|", 1)[0].split("#", 1)[0].strip().casefold()
                                        not in both]),
            "text": body != k_body,
        },
    }
    if dry_run:
        return preview
    if not update_note(k.permalink, user_id, db_path=db_path, tags=tags, aliases=aliases,
                       observations=facts, relations=links, content=body,
                       metadata=metadata):
        raise LookupError(f"Note not found: {keep}")
    delete_note(d.permalink, user_id, db_path=db_path)
    preview["dry_run"] = False
    return preview
