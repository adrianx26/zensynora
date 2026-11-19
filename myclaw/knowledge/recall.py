"""Bounded parallel recall across lexical, semantic, graph, and temporal signals."""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import Future, wait
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

_OPTIONS = {"enabled": True, "timeout_seconds": 1.5}


def set_options(enabled: Any = None, timeout_seconds: Any = None) -> None:
    if enabled is False:
        logger.info("Parallel KB recall stays enabled: full recall requires all four strategies")
    _OPTIONS["enabled"] = True
    if timeout_seconds is not None:
        try:
            _OPTIONS["timeout_seconds"] = max(0.1, min(30.0, float(timeout_seconds)))
        except (TypeError, ValueError):
            logger.warning("Knowledge recall timeout must be a number; keeping the current value")


def _ranked(notes: Iterable[Any]) -> List[Any]:
    unique = {}
    for note in notes or ():
        permalink = str(getattr(note, "permalink", "") or "")
        if permalink:
            unique.setdefault(permalink, note)
    return list(unique.values())


def _graph_search(query: str, user_id: str, limit: int, boolean: bool = False) -> List[Any]:
    from .graph import get_related_entities
    from .storage import _batch_read_notes, search_notes

    seeds = search_notes(query, user_id, limit=max(1, limit),
                         boolean=boolean, mode="fts")
    related = []
    seen = {note.permalink for note in seeds}
    for seed in seeds:
        for item in get_related_entities(seed.permalink, user_id, depth=1):
            permalink = item.get("permalink")
            if permalink and permalink not in seen:
                seen.add(permalink)
                related.append(permalink)
    return _batch_read_notes(related[:limit], user_id)


def _date_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text.casefold() == "unknown":
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
    return None


_TEMPORAL_CUE = re.compile(
    r"\b(?:today|yesterday|tomorrow|recent(?:ly)?|latest|current|"
    r"last\s+(?:week|month|year)|past\s+(?:week|month|year)|"
    r"this\s+(?:week|month|year)|before|after|since|between)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_RX = re.compile(
    r"\b(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b(?:\s+(19\d{2}|20\d{2}))?",
    re.IGNORECASE,
)


def _temporal_window(query: str, now: float) -> Tuple[float, float] | None:
    lower = str(query or "").casefold()
    current = datetime.fromtimestamp(now)
    if "yesterday" in lower:
        day = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return day.timestamp(), (day + timedelta(days=1)).timestamp()
    if "today" in lower:
        day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return day.timestamp(), (day + timedelta(days=1)).timestamp()
    for word, days in (("week", 7), ("month", 30), ("year", 365)):
        if re.search(rf"\b(?:last|past)\s+{word}\b", lower):
            return now - timedelta(days=days).total_seconds(), now
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", lower)
    if date_match:
        start = _date_timestamp(date_match.group(1))
        if start is not None:
            return start, start + 86400
    month_match = _MONTH_RX.search(lower)
    if month_match:
        month_num = _MONTHS[month_match.group(0).split()[0].casefold()]
        if month_match.group(1):
            year = int(month_match.group(1))
        else:
            year = current.year if month_num <= current.month else current.year - 1
        start_dt = datetime(year, month_num, 1)
        end_dt = datetime(year + 1, 1, 1) if month_num == 12 else datetime(year, month_num + 1, 1)
        return start_dt.timestamp(), end_dt.timestamp()
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", lower)
    if year_match:
        year = int(year_match.group(1))
        return datetime(year, 1, 1).timestamp(), datetime(year + 1, 1, 1).timestamp()
    return None


def _temporal_query(query: str) -> str:
    text = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b|\b(?:19|20)\d{2}\b", " ", query)
    text = _MONTH_RX.sub(" ", text)
    text = _TEMPORAL_CUE.sub(" ", text)
    return " ".join(text.split())


def _temporal_search(query: str, user_id: str, limit: int) -> List[Any]:
    """Search dated evidence independently, then rank matches by relevance and time."""
    from .db import KnowledgeDB
    from .decision import _tokens, assess_memory
    from .decay import note_factor
    from .storage import _batch_read_notes

    now = time.time()
    window = _temporal_window(query, now)
    fetch = max(1, limit)
    capture_epoch = (
        "CAST(strftime('%s', CASE "
        "WHEN length(captured_at) = 4 THEN captured_at || '-01-01' "
        "WHEN length(captured_at) = 7 THEN captured_at || '-01' "
        "ELSE captured_at END) AS REAL)"
    )
    effective_epoch = f"COALESCE({capture_epoch}, CAST(strftime('%s', updated_at) AS REAL))"
    window_filter = ""
    params: Tuple[Any, ...] = (fetch,)
    if window is not None:
        window_filter = f"WHERE {effective_epoch} >= ? AND {effective_epoch} < ? "
        params = (window[0] - 86400, window[1] + 86400, fetch)
    with KnowledgeDB(user_id) as db:
        rows = db._get_connection().execute(
            "WITH dated_entities AS ("
            "SELECT e.id, e.permalink, e.updated_at, "
            "MAX(CASE WHEN o.captured_at IS NOT NULL "
            "AND lower(trim(o.captured_at)) != 'unknown' THEN o.captured_at END) AS captured_at "
            "FROM entities e LEFT JOIN observations o ON o.entity_id = e.id "
            "GROUP BY e.id) "
            "SELECT permalink, updated_at, captured_at FROM dated_entities "
            + window_filter
            + f"ORDER BY {effective_epoch} DESC LIMIT ?",
            params,
        ).fetchall()
    notes = _batch_read_notes([row["permalink"] for row in rows], user_id)
    by_permalink = {str(note.permalink): note for note in notes}
    topic_query = _temporal_query(query)
    scored = []
    for index, row in enumerate(rows):
        note = by_permalink.get(str(row["permalink"]))
        if note is None:
            continue
        raw_metadata = getattr(note, "metadata", {})
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        observations = getattr(note, "observations", None) or []
        captures = [
            _date_timestamp(getattr(obs, "captured_at", None))
            for obs in observations
        ]
        valid_captures = [capture for capture in captures if capture is not None]
        metadata_capture = _date_timestamp(metadata.get("captured_at"))
        captured_at = max(
            valid_captures + ([metadata_capture] if metadata_capture is not None else []),
            default=_date_timestamp(row["captured_at"]),
        )
        updated_at = _date_timestamp(row["updated_at"])
        dated_at = captured_at if captured_at is not None else updated_at
        if dated_at is None:
            continue
        if window is not None and not window[0] <= dated_at < window[1]:
            continue
        states = [str(getattr(obs, "status", "active")).casefold()
                  for obs in observations]
        if states and all(state == "superseded" for state in states):
            continue
        claims = [str(getattr(obs, "content", "") or "") for obs in observations]
        claims.extend([str(getattr(note, "title", "") or ""),
                       str(getattr(note, "content", "") or "")])
        relevance = max(
            (assess_memory(topic_query, claim).relevance_score for claim in claims if claim),
            default=0.0,
        )
        if _tokens(topic_query) and relevance <= 0:
            continue
        capture_for_decay = captured_at if captured_at is not None else dated_at
        factor, age, _half_life = note_factor(
            getattr(note, "tags", []), capture_for_decay, now=now)
        archived = str(metadata.get("status") or "active").casefold() == "archived"
        scored.append((-relevance, archived, captured_at is None, -factor, -dated_at, age, index, note))
    scored.sort(key=lambda item: item[:-1])
    return [item[-1] for item in scored[:limit]]


def _streams(query: str, user_id: str, limit: int, boolean: bool = False,
             details: Dict[str, Any] | None = None) -> Dict[str, Callable[[], List[Any]]]:
    from .semantic import AGENT_TIMEOUT
    from .storage import search_notes

    semantic_details: Dict[str, Any] = {}
    if details is not None:
        details["semantic"] = semantic_details
    has_explicit_syntax = bool(re.search(
        r'\b(?:AND|OR|NOT)\b|tag:[^\s]+|"[^"]+"|\b[\w-]+\*',
        query, flags=re.IGNORECASE))

    def constrain(search: Callable[[], List[Any]]) -> Callable[[], List[Any]]:
        def run() -> List[Any]:
            notes = search()
            if not boolean or not has_explicit_syntax:
                return notes
            allowed = {note.permalink for note in search_notes(
                query, user_id, limit=max(limit * 4, limit), boolean=True, mode="fts")}
            return [note for note in notes if note.permalink in allowed]
        return run

    def semantic_search() -> List[Any]:
        semantic_query = re.sub(r"\b(?:AND|OR|NOT)\b|tag:[^\s]+", " ", query,
                                flags=re.IGNORECASE)
        semantic_query = " ".join(semantic_query.replace('"', " ").replace("*", " ").split())
        if not semantic_query:
            semantic_details["fallback"] = "the query has no semantic search terms"
            return []
        notes = search_notes(
            semantic_query, user_id, limit=limit, mode="semantic",
            semantic_timeout=min(AGENT_TIMEOUT, _OPTIONS["timeout_seconds"]),
            semantic_wait=0.0, info=semantic_details)
        if semantic_details.get("fallback") or semantic_details.get("mode") != "semantic":
            return []
        return notes

    return {
        "fts": lambda: search_notes(query, user_id, limit=limit,
                                    boolean=boolean, mode="fts"),
        "semantic": constrain(semantic_search),
        "graph": constrain(lambda: _graph_search(query, user_id, limit, boolean)),
        "temporal": constrain(lambda: _temporal_search(query, user_id, limit)),
    }


def _fuse(results: Dict[str, List[Any]], limit: int, query: str = "",
          info: Dict[str, Any] | None = None) -> List[Any]:
    from .decision import MemoryDecision, assess_memory

    def assess(*args: Any, **kwargs: Any) -> MemoryDecision:
        try:
            return assess_memory(*args, **kwargs)
        except (TypeError, ValueError, AttributeError) as exc:
            logger.warning("Knowledge decision rules failed; preserving candidate for local review: %s", exc)
            return MemoryDecision(
                is_new=True, relevant=True, relevance_score=0.0, stale=False,
                evidence_sufficient=False, include=True,
                reasons=("deterministic decision failed; preserved for review",),
            )

    scores: Dict[str, float] = {}
    notes: Dict[str, Any] = {}
    first_seen: Dict[str, int] = {}
    contributors: Dict[str, set] = {}
    stream_rankings: Dict[str, List[str]] = {}
    decisions: Dict[str, Any] = {}
    claims_by_note: Dict[str, List[Tuple[str, Any]]] = {}
    for stream in ("fts", "semantic", "graph", "temporal"):
        for note in _ranked(results.get(stream, [])):
            permalink = str(note.permalink)
            if permalink in claims_by_note:
                continue
            observations = getattr(note, "observations", None) or []
            claims = [(str(getattr(obs, "content", "")).strip(), obs)
                      for obs in observations if str(getattr(obs, "content", "")).strip()]
            if not claims:
                title = str(getattr(note, "title", "")).strip()
                claims = [(title, None)] if title else []
            claims_by_note[permalink] = claims
    known_claims = {
        permalink: [claim for other, claims in claims_by_note.items() if other != permalink
                    for claim, _obs in claims]
        for permalink in claims_by_note
    }
    serial = 0
    for stream in ("fts", "semantic", "graph", "temporal"):
        stream_rankings[stream] = []
        for rank, note in enumerate(_ranked(results.get(stream, []))):
            permalink = str(note.permalink)
            notes.setdefault(permalink, note)
            contributors.setdefault(permalink, set()).add(stream)
            stream_rankings[stream].append(permalink)
            scores[permalink] = scores.get(permalink, 0.0) + 1.0 / (60 + rank + 1)
            if permalink not in first_seen:
                first_seen[permalink] = serial
                serial += 1
                raw_metadata = getattr(note, "metadata", {})
                metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                observations = getattr(note, "observations", None) or []
                base_state = str(metadata.get("status") or "active")
                if observations and all(
                        str(getattr(obs, "status", "active")).casefold() == "superseded"
                        for obs in observations):
                    base_state = "superseded"
                elif observations and all(
                        str(getattr(obs, "status", "active")).casefold() == "archived"
                        for obs in observations):
                    base_state = "archived"
                assessments = []
                for claim, observation in claims_by_note.get(permalink, []):
                    state = base_state
                    confidence = metadata.get("confidence")
                    verification = metadata.get("verification_status", "unknown")
                    source = permalink
                    if observation is not None:
                        obs_state = str(getattr(observation, "status", "active"))
                        if base_state.casefold() == "active":
                            state = obs_state
                        confidence = getattr(observation, "confidence", confidence)
                        verification = getattr(observation, "verification_status", verification)
                        source = getattr(observation, "provenance_path", None) or permalink
                    assessments.append(assess(
                        query, claim, known_claims.get(permalink, []), status=state,
                        confidence=confidence, verification_status=verification, source=source))
                if assessments:
                    decisions[permalink] = max(
                        assessments, key=lambda decision: (
                            decision.relevance_score, not decision.stale,
                            decision.evidence_sufficient))
                else:
                    decisions[permalink] = assess(
                        query, "", known_claims.get(permalink, []), status=base_state,
                        confidence=metadata.get("confidence"),
                        verification_status=metadata.get("verification_status", "unknown"),
                        source=permalink)
    ranked = sorted(scores, key=lambda p: (
        -scores[p], -decisions[p].relevance_score, decisions[p].stale,
        not decisions[p].evidence_sufficient, first_seen[p]))
    model_candidates = [{
        "id": permalink,
        "title": str(getattr(notes[permalink], "title", "") or permalink),
        "evidence": " | ".join(claim for claim, _observation in claims_by_note.get(permalink, [])[:3]),
    } for permalink in ranked]
    from .decision import rank_candidates

    model_candidates, model_decisions = rank_candidates(
        query, model_candidates, decisions)
    ranked = [str(candidate["id"]) for candidate in model_candidates]
    coverage: List[str] = []
    for stream in ("fts", "semantic", "graph", "temporal"):
        candidate = next((p for p in stream_rankings[stream]
                          if p not in coverage and decisions[p].include
                          and decisions[p].relevant), None)
        if candidate is not None:
            coverage.append(candidate)
    covered = set(coverage)
    ordered = ([permalink for permalink in ranked if permalink in covered]
               + [permalink for permalink in ranked if permalink not in covered])
    if info is not None:
        info["strategies_by_note"] = {
            permalink: [stream for stream in ("fts", "semantic", "graph", "temporal")
                        if stream in streams]
            for permalink, streams in contributors.items()
        }
        info["candidate_titles"] = {
            permalink: str(getattr(note, "title", "") or permalink)
            for permalink, note in notes.items()
        }
        info["decisions"] = {
            permalink: {
                "is_new": decision.is_new,
                "relevant": decision.relevant,
                "relevance_score": round(decision.relevance_score, 4),
                "stale": decision.stale,
                "evidence_sufficient": decision.evidence_sufficient,
                "include": decision.include,
                "reasons": list(decision.reasons),
            }
            for permalink, decision in decisions.items()
        }
        info["decision_model"] = model_decisions
    return [notes[p] for p in ordered[:max(0, limit)]]


def parallel_recall(query: str, user_id: str = "default", limit: int = 10,
                    info: Dict[str, Any] | None = None,
                    boolean: bool = False) -> List[Any]:
    """Run all four strategies concurrently and fuse results within the time budget."""
    if not str(query or "").strip() or limit <= 0:
        return []
    info = info if info is not None else {}
    candidates = max(limit, limit * 3)
    stream_details: Dict[str, Any] = {}
    streams = _streams(query, user_id, candidates, boolean, stream_details)
    futures: Dict[Future, str] = {}

    def run_search(future: Future, search: Callable[[], List[Any]]) -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = search()
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    for name, search in streams.items():
        future: Future = Future()
        futures[future] = name
        threading.Thread(
            target=run_search,
            args=(future, search),
            name=f"kb-recall-{name}",
            daemon=True,
        ).start()
    done, pending = wait(futures, timeout=_OPTIONS["timeout_seconds"])
    results: Dict[str, List[Any]] = {}
    statuses: Dict[str, str] = {name: "pending" for name in streams}
    for future in done:
        name = futures[future]
        try:
            results[name] = future.result()
            statuses[name] = "ok"
        except Exception as exc:
            logger.info("Knowledge %s recall stream failed: %s", name, exc)
            results[name] = []
            statuses[name] = "error"
    for future in pending:
        name = futures[future]
        future.cancel()
        results[name] = []
        statuses[name] = "timeout"
    semantic_details = stream_details.get("semantic", {})
    if statuses.get("semantic") == "ok" and semantic_details.get("fallback"):
        statuses["semantic"] = "unavailable"
    info.update(
        strategies=statuses,
        timed_out=[name for name, status in statuses.items() if status == "timeout"],
        strategy_details=stream_details,
        result_counts={name: len(results.get(name, [])) for name in streams},
        partial=any(status != "ok" for status in statuses.values()),
        search_mode="parallel-hybrid",
    )
    fused = _fuse(results, limit, query=query, info=info)
    if fused:
        from .db import touch_access

        touch_access(user_id, [getattr(note, "permalink", None) for note in fused])
    return fused
