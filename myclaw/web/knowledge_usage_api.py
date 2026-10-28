"""Knowledge base usage log and benchmarks for the Web UI (2026-10-27).

Routes
------
GET /api/knowledge/usage?base=&agent=&kind=&q=&since=&limit=&offset=
        log rows, newest first (kind: context | tool | write, or an action such as
        search_knowledge / auto-context / extraction); also the agent names for the filter
GET /api/knowledge/usage/stats?base=&days=
        accesses per day per kind, per agent, top notes, top queries, hit rate,
        empty-result queries, latency (avg / p95, per day), notes never used, snapshots
GET /api/knowledge/usage/note/{permalink}?base=
        how often agents used one note, and the last time and agent
GET /api/knowledge/benchmarks?base=
        size and structure of the knowledge base (cached ~60 s)

The stats and benchmark routes — and the Web UI itself, at start and then
hourly — update today's snapshot of the main counts for the growth chart. The log is private like the notes, so every
route uses the settings guard (this computer, or the admin API key).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from myclaw.web.knowledge_api import _base
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()


def _unavailable(e: Exception) -> HTTPException:
    logger.warning("Knowledge usage log unreadable: %s", e)
    return HTTPException(status_code=503, detail=f"The usage log cannot be read ({e}). "
                         "It is rebuilt when ~/.myclaw/knowledge-usage.db is deleted.")


def _log(base: str, agent: str, kind: str, q: str, since: float | None, limit: int, offset: int,
         note: str = "") -> dict[str, Any]:
    from myclaw.knowledge import usage

    usage.flush(0.5)
    data = usage.query_log(base, agent=agent, kind=kind, q=q, since=since, limit=limit, offset=offset,
                           note=note)
    data.update({"base": base, "limit": limit, "offset": offset, "enabled": usage.enabled(),
                 "agents": usage.agents(base)})
    return data


def _snapshot(base: str, b: dict[str, Any]) -> None:
    from myclaw.knowledge import usage
    from myclaw.knowledge.benchmarks import counts

    if b.get("notes") or b.get("files"):
        usage.snapshot(base, counts(b))


def _stats(base: str, days: int) -> dict[str, Any]:
    from myclaw.knowledge import usage
    from myclaw.knowledge.benchmarks import benchmarks, titles

    usage.flush(0.5)
    t0 = time.perf_counter()
    try:
        _snapshot(base, benchmarks(base))
    except Exception as e:  # the stats work without a snapshot
        logger.debug("knowledge snapshot skipped: %s", e)
    data = usage.stats(base, days, titles=titles(base))
    data["took_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return data


def _benchmarks(base: str) -> dict[str, Any]:
    from myclaw.knowledge.benchmarks import benchmarks

    b = benchmarks(base)
    try:
        _snapshot(base, b)
    except Exception as e:
        logger.debug("knowledge snapshot skipped: %s", e)
    return b


@router.get("/api/knowledge/usage", dependencies=guard)
async def usage_log(base: str | None = None, agent: str = "", kind: str = "", q: str = "",
                    since: float | None = None, limit: int = Query(50, ge=1, le=500),
                    offset: int = Query(0, ge=0), note: str = ""):
    b = _base(base)
    try:
        return await asyncio.to_thread(_log, b, agent.strip(), kind.strip(), q.strip()[:200], since, limit,
                                       offset, note.strip()[:300])
    except sqlite3.Error as e:
        raise _unavailable(e)


@router.get("/api/knowledge/usage/stats", dependencies=guard)
async def usage_stats(base: str | None = None, days: int = Query(30, ge=1, le=365)):
    b = _base(base)
    try:
        return await asyncio.to_thread(_stats, b, days)
    except sqlite3.Error as e:
        raise _unavailable(e)


@router.get("/api/knowledge/usage/note/{permalink}", dependencies=guard)
async def usage_of_note(permalink: str, base: str | None = None):
    from myclaw.knowledge import usage

    b = _base(base)
    if not permalink or len(permalink) > 300:
        raise HTTPException(status_code=400, detail="Invalid note name.")
    try:
        return await asyncio.to_thread(usage.note_usage, b, permalink)
    except sqlite3.Error as e:
        raise _unavailable(e)


@router.get("/api/knowledge/benchmarks", dependencies=guard)
async def knowledge_benchmarks(base: str | None = None):
    return await asyncio.to_thread(_benchmarks, _base(base))


# ── growth snapshots from the Web UI (2026-10-27, review: no gaps in the chart) ──

SNAPSHOT_SECONDS = 3600.0
_snapshot_task: asyncio.Task | None = None


def snapshot_all() -> int:
    """Today's snapshot of every knowledge base (only while the usage log is on)."""
    from myclaw.knowledge import usage
    from myclaw.knowledge.benchmarks import benchmarks
    from myclaw.knowledge.health import bases

    if not usage.enabled():
        return 0
    n = 0
    for base in bases():
        try:
            b = benchmarks(base)
            if (b.get("notes") or b.get("files")) and usage.snapshot(base, counts_of(b)):
                n += 1
        except Exception as e:  # one base must not stop the others
            logger.debug("knowledge snapshot of %s skipped: %s", base, e)
    return n


def counts_of(b: dict[str, Any]) -> dict[str, Any]:
    from myclaw.knowledge.benchmarks import counts

    return counts(b)


async def _snapshot_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(snapshot_all)
        except Exception as e:  # pragma: no cover - never stop the loop
            logger.debug("knowledge snapshots failed: %s", e)
        await asyncio.sleep(SNAPSHOT_SECONDS)


async def start_snapshots() -> None:
    global _snapshot_task
    if _snapshot_task is None or _snapshot_task.done():
        _snapshot_task = asyncio.create_task(_snapshot_loop())


async def stop_snapshots() -> None:
    global _snapshot_task
    if _snapshot_task is not None:
        _snapshot_task.cancel()
        _snapshot_task = None
