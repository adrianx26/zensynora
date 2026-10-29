"""Knowledge base benchmarks (2026-10-27): size, structure and growth in numbers.

Read-only metrics of one knowledge base, from its index database (opened with
``mode=ro``) plus one directory walk for the size on disk: notes, facts,
links, tags, categories, unlinked notes, waiting links, links per note, graph
density, connected components and the largest one, the five most linked notes
(hubs), notes per month (growth), facts per category, open conflicts,
embeddings coverage (when semantic search is on), distillation counts (when
distillation is on) and the index size on disk.

Well under a second on 5000 notes (a few aggregate queries, one union-find
over the links); results are cached for :data:`CACHE_SECONDS` per base.
Never raises: a part that cannot be read reports ``error``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60.0
_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_lock = threading.Lock()


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _empty(base: str) -> Dict[str, Any]:
    return {
        "base": base, "notes": 0, "facts": 0, "links": 0, "tags": 0, "categories": 0,
        "orphans": 0, "waiting_links": 0, "links_per_note": 0.0, "density": 0.0,
        "components": 0, "largest_component": 0, "hubs": [], "notes_by_month": [],
        "facts_by_category": [], "conflicts": 0, "embeddings": None, "distill": None,
        "index_bytes": 0, "files_bytes": 0, "files": 0, "computed_at": time.time(),
        "took_ms": 0.0, "error": None,
    }


def titles(base: str) -> Dict[str, str]:
    """permalink → title of every indexed note (empty when there is no index)."""
    from .health import db_path

    path = db_path(base)
    if not path.is_file():
        return {}
    try:
        con = _ro(path)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(entities)")}
            title = "coalesce(title, name)" if "title" in cols else "name"
            return {r[0]: r[1] for r in con.execute(f"SELECT permalink, {title} FROM entities")}
        finally:
            con.close()
    except sqlite3.Error as e:
        logger.debug("knowledge titles of %s unavailable: %s", base, e)
        return {}


def _components(ids: List[int], pairs: List[Tuple[int, int]]) -> Tuple[int, int]:
    parent = {i: i for i in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    sizes = Counter(find(i) for i in ids)
    return len(sizes), (max(sizes.values()) if sizes else 0)


def _dir_size(folder: Path, limit: int = 50_000) -> Tuple[int, int]:
    total = files = 0
    stack = [str(folder)]
    while stack and files < limit:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False) and e.name.endswith(".md"):
                            total += e.stat().st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files


def compute(base: str = "default") -> Dict[str, Any]:
    """The metrics of ``base`` (no cache)."""
    from .health import base_dir, db_path

    t0 = time.perf_counter()
    out = _empty(base)
    path = db_path(base)
    try:
        out["files_bytes"], out["files"] = _dir_size(base_dir(base))
    except Exception as e:
        logger.debug("knowledge folder size of %s: %s", base, e)
    if path.is_file():
        for suffix in ("", "-wal", "-shm"):
            try:
                out["index_bytes"] += os.path.getsize(str(path) + suffix)
            except OSError:
                pass
        try:
            con = _ro(path)
            try:
                _from_index(con, out)
            finally:
                con.close()
        except sqlite3.Error as e:
            out["error"] = str(e)
    try:
        from . import semantic

        if semantic.enabled():
            from .health import embeddings

            e = embeddings(base)
            out["embeddings"] = {"notes": e.get("notes", 0), "embedded": e.get("embedded", 0),
                                 "waiting": e.get("waiting", 0), "model": e.get("model")}
    except Exception as e:
        logger.debug("embeddings coverage of %s: %s", base, e)
    out["took_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    out["computed_at"] = time.time()
    return out


def _from_index(con: sqlite3.Connection, out: Dict[str, Any]) -> None:
    from .quality import normalize_category

    tables = _tables(con)
    if "entities" not in tables:
        return
    ids = [r[0] for r in con.execute("SELECT id FROM entities")]
    n = len(ids)
    out["notes"] = n
    if "observations" in tables:
        out["facts"] = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        cats: Counter = Counter()
        for r in con.execute("SELECT category, COUNT(*) FROM observations GROUP BY category"):
            cats[normalize_category(r[0] or "") or (r[0] or "fact")] += r[1]
        out["categories"] = len(cats)
        out["facts_by_category"] = [{"name": k, "count": v} for k, v in cats.most_common(15)]
    pairs: List[Tuple[int, int]] = []
    degree: Counter = Counter()
    if "relations" in tables:
        rows = con.execute("SELECT from_entity_id, to_entity_id FROM relations "
                           "WHERE from_entity_id != to_entity_id").fetchall()
        out["links"] = len(rows)
        undirected = set()
        for a, b in rows:
            degree[a] += 1
            degree[b] += 1
            undirected.add((a, b) if a < b else (b, a))
        pairs = list(undirected)
        out["density"] = round(2 * len(undirected) / (n * (n - 1)), 5) if n > 1 else 0.0
        out["links_per_note"] = round(2 * len(rows) / n, 2) if n else 0.0
    waiting_from: set = set()
    if "pending_relations" in tables:
        out["waiting_links"] = con.execute("SELECT COUNT(*) FROM pending_relations").fetchone()[0]
        waiting_from = {r[0] for r in con.execute("SELECT DISTINCT from_entity_id FROM pending_relations")}
    out["orphans"] = sum(1 for i in ids if not degree.get(i) and i not in waiting_from)
    out["components"], out["largest_component"] = _components(ids, pairs)
    if degree:
        cols = {r[1] for r in con.execute("PRAGMA table_info(entities)")}
        title = "coalesce(title, name)" if "title" in cols else "name"
        top = degree.most_common(5)
        names = {r[0]: (r[1], r[2]) for r in con.execute(
            f"SELECT id, permalink, {title} FROM entities WHERE id IN ({','.join('?' * len(top))})",
            [i for i, _ in top])}
        out["hubs"] = [{"permalink": names[i][0], "title": names[i][1], "links": d}
                       for i, d in top if i in names]
    out["notes_by_month"] = [{"month": r[0], "count": r[1]} for r in con.execute(
        "SELECT substr(created_at, 1, 7) AS m, COUNT(*) FROM entities WHERE created_at IS NOT NULL "
        "AND length(created_at) >= 7 GROUP BY m ORDER BY m")]
    if "notes_text_fts" in tables:
        try:
            tags: set = set()
            for r in con.execute("SELECT tags FROM notes_text_fts"):
                tags.update(t for t in (r[0] or "").split() if t)
            out["tags"] = len(tags)
        except sqlite3.Error:
            pass
    if "conflicts" in tables:
        out["conflicts"] = con.execute("SELECT COUNT(*) FROM conflicts WHERE status = 'open'").fetchone()[0]
    try:
        from . import distill

        if distill.enabled() and "distilled_facts" in tables:
            d: Dict[str, Any] = {"facts": con.execute(
                "SELECT COUNT(*) FROM distilled_facts WHERE merged_into IS NULL").fetchone()[0]}
            d["scenarios"] = (con.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0]
                              if "scenarios" in tables else 0)
            d["persona_versions"] = (con.execute("SELECT COUNT(*) FROM persona").fetchone()[0]
                                     if "persona" in tables else 0)
            out["distill"] = d
    except Exception as e:
        logger.debug("distillation counts: %s", e)


def benchmarks(base: str = "default", max_age: float = CACHE_SECONDS) -> Dict[str, Any]:
    """:func:`compute`, cached for ``max_age`` seconds per base (and HOME)."""
    key = (str(Path.home()), base)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < max_age:
            return {**hit[1], "cached": True}
    data = compute(base)
    with _lock:
        _cache[key] = (now, data)
    return {**data, "cached": False}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def counts(b: Dict[str, Any]) -> Dict[str, Optional[int]]:
    return {k: b.get(k) for k in ("notes", "facts", "links", "tags")}
