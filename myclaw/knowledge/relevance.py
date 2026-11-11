"""Search relevance: your 👍 / 👎, the re-rank step, the test bench, measured tuning (2026-11-11).

* **Feedback** — on the test bench (Knowledge → Tune) you mark a result of a query as
  right (👍) or wrong (👎). It is kept in ``~/.myclaw/knowledge-feedback.db`` (your data: its own file, never
  pruned, not part of the rebuildable index or of the usage log). The same
  query later shows 👍 notes first and 👎 notes last.
* **Re-rank** — ``semantic.search_entities`` passes its ordered results through
  :func:`rerank`: 👍 notes, then position score × temporal decay (``decay.py``), then 👎 notes. With decay
  off and no feedback for the query the order is not touched.
* **Auto-tune** — :func:`autotune` replays the 👍 queries with other fusion settings and
  similarity floors and reports which give the best mean reciprocal rank. It only
  measures; applying goes through ``tuning.py``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import decay

logger = logging.getLogger(__name__)

POSITION_C = 5.0          # position score = 1 / (C + rank): ×0.5 moves the first result to about 6th
MIN_LABELLED = 5          # 👍 queries needed before auto-tune says anything
SEM_WEIGHTS = (0.2, 0.4, 0.6, 0.8, 1.0, 1.2)
KEEP_FTS = (0, 1, 2, 3)

_SCHEMA = """CREATE TABLE IF NOT EXISTS kb_feedback (
    base TEXT NOT NULL, query_key TEXT NOT NULL, query TEXT NOT NULL,
    permalink TEXT NOT NULL, vote INTEGER NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY (base, query_key, permalink))"""
_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Dict[str, int]]]] = {}


def query_key(query: str) -> str:
    return " ".join(re.findall(r"\w+", str(query or "").casefold()))


def db_path() -> Any:
    """``~/.myclaw/knowledge-feedback.db`` — its own file: the usage log next to it may be
    deleted ("loses only the log"), your marks must not go with it (2026-11-12, review)."""
    from . import usage

    return usage.db_path().parent / "knowledge-feedback.db"


def _connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=2.0)
    con.execute(_SCHEMA)
    return con


def _stamp() -> float:
    try:
        return db_path().stat().st_mtime
    except OSError:
        return 0.0


def _all(base: str) -> Dict[str, Dict[str, int]]:
    """{query key: {permalink: vote}} of one base; re-read when the file changed
    (a mark made in another process is seen)."""
    key = (str(db_path()), base)
    stamp = _stamp()
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    out: Dict[str, Dict[str, int]] = {}
    if stamp:
        try:
            con = _connect()
            try:
                for q, p, v in con.execute("SELECT query_key, permalink, vote FROM kb_feedback "
                                           "WHERE base = ?", (base,)):
                    out.setdefault(q, {})[p] = int(v)
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            logger.info("Knowledge: search feedback could not be read: %s", e)
    _CACHE[key] = (stamp, out)
    return out


def votes_for(base: str, query: str) -> Dict[str, int]:
    return _all(base).get(query_key(query), {})


def vote(base: str, query: str, permalink: str, value: int) -> Dict[str, Any]:
    """👍 (1), 👎 (-1) or clear (0) one result of one query."""
    key = query_key(query)
    if not key or not str(permalink or "").strip():
        raise ValueError("a query and a note are needed")
    value = 1 if value > 0 else -1 if value < 0 else 0
    try:
        con = _connect()
        try:
            if value:
                con.execute("INSERT OR REPLACE INTO kb_feedback VALUES (?, ?, ?, ?, ?, ?)",
                            (base, key, " ".join(str(query).split())[:300], permalink, value, time.time()))
            else:
                con.execute("DELETE FROM kb_feedback WHERE base = ? AND query_key = ? AND permalink = ?",
                            (base, key, permalink))
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError) as e:
        raise ValueError(f"The mark could not be saved ({db_path().name}: {e}).") from e
    _CACHE.pop((str(db_path()), base), None)
    return {"query": query, "permalink": permalink, "vote": value}


def feedback(base: str = "default") -> Dict[str, Any]:
    """The labelled set: every query with its 👍 / 👎 notes."""
    rows: List[Dict[str, Any]] = []
    error = None
    if _stamp():
        try:
            con = _connect()
            try:
                for r in con.execute("SELECT query, query_key, permalink, vote, ts FROM kb_feedback WHERE base = ? "
                                     "ORDER BY ts DESC", (base,)):
                    rows.append({"query": r[0], "key": r[1], "permalink": r[2], "vote": r[3], "ts": r[4]})
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            error = f"{db_path().name} could not be read: {e}"
    queries = {r["key"] for r in rows}
    labelled = {r["key"] for r in rows if r["vote"] > 0}
    return {"items": rows, "queries": len(queries), "labelled": len(labelled), "needed": MIN_LABELLED,
            "error": error}


def active(base: str, query: str) -> bool:
    """Does the re-rank step have anything to do for this query?"""
    return decay.enabled() or bool(votes_for(base, query))


def rerank(base: str, query: str, entities: List[Any], info: Optional[Dict[str, Any]] = None,
           fts_ids: Sequence[int] = (), sem: Sequence[Tuple[int, float]] = (),
           apply: bool = True) -> List[Any]:
    """Order: 👍 notes, then by position score × decay, then 👎 notes. ``info`` gets ``explain``: one row per
    result with every part of its score."""
    votes = votes_for(base, query)
    used = decay.last_used(base) if decay.enabled() else {}
    now = time.time()
    fts_rank = {eid: i + 1 for i, eid in enumerate(dict.fromkeys(fts_ids))}
    sem_rank = {eid: (i + 1, s) for i, (eid, s) in enumerate(sem)}
    rows = []
    for rank, e in enumerate(entities):
        d, age, hl = decay.note_factor(decay.entity_tags(e), e.updated_at, used.get(e.permalink), now)
        v = votes.get(e.permalink, 0)
        rows.append((rank, e, d, age, hl, v, (1.0 / (POSITION_C + rank)) * d))
    if apply:   # your mark for this query decides first (also against decay), then the score
        rows.sort(key=lambda r: (-r[5], -r[6], r[0]))
    if info is not None:
        info["explain"] = [{
            "permalink": e.permalink, "title": getattr(e, "title", None) or e.name,
            "position": i + 1, "before": rank + 1,
            "word_rank": fts_rank.get(e.id), "meaning_rank": (sem_rank.get(e.id) or (None, None))[0],
            "similarity": round((sem_rank.get(e.id) or (None, 0.0))[1], 3) if e.id in sem_rank else None,
            "decay": round(d, 3), "age_days": round(age), "half_life_days": hl,
            "vote": v, "score": round(score, 4),
        } for i, (rank, e, d, age, hl, v, score) in enumerate(rows)]
    return [r[1] for r in rows]


def _mrr(ranks: List[Optional[int]]) -> float:
    return round(sum(1.0 / r for r in ranks if r) / len(ranks), 4) if ranks else 0.0


def autotune(base: str = "default", top: int = 10) -> Dict[str, Any]:
    """Replay the 👍 queries with other settings (blocking; call from a thread).

    Tries the meaning weight, the number of word matches kept on top and the
    similarity floor; the word weight stays 1.0 (only the ratio of the two weights
    changes the order). Feedback and decay are left out, so the settings are judged
    on their own."""
    from . import semantic
    from .db import KnowledgeDB

    fb = feedback(base)
    wanted: Dict[str, Tuple[str, set]] = {}
    for r in fb["items"]:
        if r["vote"] > 0:
            wanted.setdefault(r["key"], (r["query"], set()))[1].add(r["permalink"])
    out: Dict[str, Any] = {"labelled": len(wanted), "needed": MIN_LABELLED, "changes": {}}
    if len(wanted) < MIN_LABELLED:
        out["message"] = (f"Mark results of at least {MIN_LABELLED} different searches with 👍 first "
                          f"({len(wanted)} so far) — fewer say too little.")
        return out
    if not semantic.enabled():
        out["message"] = "Semantic search is off: with word search alone there is nothing to tune here."
        return out
    w_fts, w_sem, keep = semantic.fusion()
    floor_now = semantic.min_score()
    floors = sorted({round(floor_now, 2), *(round(floor_now + d, 2) for d in (-0.1, -0.05, 0.05, 0.1)
                                           if 0.3 <= floor_now + d <= 0.9)})
    runs = []
    with KnowledgeDB(base) as db:
        conn = db._get_connection()
        for _key, (query, good) in wanted.items():
            fts = db.search_fts(query, 30)
            vec, _why = semantic.vector_rank(conn, db.db_path, query, 30, floor=0.0)
            if vec is None:
                out["message"] = f"Semantic search is not ready ({_why}); try again when the notes are embedded."
                return out
            ids = {e.id: e.permalink for e in fts}
            missing = [i for i, _ in vec if i not in ids]
            if missing:
                for r in conn.execute(f"SELECT id, permalink FROM entities WHERE id IN "
                                      f"({','.join('?' * len(missing))})", missing):
                    ids[r["id"]] = r["permalink"]
            runs.append(([e.id for e in fts], vec, ids, good))

    def score(ws: float, k: int, floor: float) -> float:
        ranks: List[Optional[int]] = []
        for fts_ids, vec, ids, good in runs:
            order = semantic.fuse(fts_ids, [i for i, s in vec if s >= floor], w_fts=1.0, w_sem=ws, keep_fts=k)[:top]
            ranks.append(next((n + 1 for n, i in enumerate(order) if ids.get(i) in good), None))
        return _mrr(ranks)

    current = score(w_sem / w_fts if w_fts else w_sem, keep, floor_now)
    best = (current, w_sem, keep, floor_now)
    for ws in SEM_WEIGHTS:
        for k in KEEP_FTS:
            for fl in floors:
                s = score(ws, k, fl)
                if s > best[0] + 0.01:
                    best = (s, ws, k, fl)
    out.update(current={"mrr": current, "fusion_sem_weight": w_sem, "fusion_keep_fts": keep,
                        "semantic_min_score": round(floor_now, 2)},
               best={"mrr": best[0], "fusion_sem_weight": best[1], "fusion_keep_fts": best[2],
                     "semantic_min_score": round(best[3], 2)})
    if best[0] > current:
        changes: Dict[str, Any] = {}
        if best[1] != w_sem or w_fts != 1.0:
            changes.update(fusion_fts_weight=1.0, fusion_sem_weight=best[1])
        if best[2] != keep:
            changes["fusion_keep_fts"] = best[2]
        if round(best[3], 2) != round(floor_now, 2):
            changes["semantic_min_score"] = round(best[3], 2)
        out["changes"] = changes
        out["message"] = (f"On your {len(wanted)} marked searches the right note is higher with these "
                          f"settings (score {current:.2f} → {best[0]:.2f}; 1.00 = always first).")
    else:
        out["message"] = f"The current settings are already the best of those tried (score {current:.2f})."
    return out
