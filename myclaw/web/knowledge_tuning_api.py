"""Knowledge → Tune: thresholds, decay, models, relevance, relations, duplicates (2026-11-11).

Routes
------
GET    /api/knowledge/tuning?base=                  values with limits, proposals, history, models
PUT    /api/knowledge/tuning                        you set values ({changes, tuning_ai?, decay_ai?})
POST   /api/knowledge/tuning/undo                   put back the values before a change ({id?})
POST   /api/knowledge/tuning/proposals/{id}         approve or reject a proposal ({approve})
POST   /api/knowledge/tuning/ai?base=&scope=        the AI suggests (all | decay | search)
POST   /api/knowledge/tuning/autotune?base=         replay your 👍 searches with other settings (measures only)
GET    /api/knowledge/tuning/decay?base=            ages / use / tags the decay policy is decided from
POST   /api/knowledge/tuning/test?base=             test bench: a search with every result's score parts
GET    /api/knowledge/tuning/feedback?base=         your 👍 / 👎
POST   /api/knowledge/tuning/feedback?base=         👍 (1) / 👎 (-1) / clear (0) ({query, permalink, vote})
GET    /api/knowledge/relations?base=&q=&only=      links with strength, use together, vague types
POST   /api/knowledge/relations/strength?base=      set or raise one link's strength
POST   /api/knowledge/relations/from-usage?base=    strengthen links of notes used together ({dry_run})
POST   /api/knowledge/relations/ai?base=            AI proposals: precise types | new links ({kind, permalink?})
POST   /api/knowledge/relations/apply?base=         write the proposals you chose
POST   /api/knowledge/duplicates/dismiss?base=      "these are different notes" ({a, b})
POST   /api/knowledge/duplicates/merge-identical?base=   merge every pair with identical text ({dry_run})

All routes use the settings guard (this computer, or the admin API key).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from myclaw.web.knowledge_api import _base
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()


def _bad(e: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(e))


def _models() -> List[Dict[str, Any]]:
    """The offered models first, then every other model fastembed supports (if installed)."""
    from myclaw.knowledge import semantic

    out = [{"name": k, "dim": v.dim, "size_mb": v.size_mb, "licence": v.licence, "languages": v.languages,
            "min_score": v.min_score, "offered": True} for k, v in semantic.MODELS.items()]
    if semantic.library_installed():
        try:
            from fastembed import TextEmbedding

            for m in TextEmbedding.list_supported_models():
                if m.get("model") and m["model"] not in semantic.MODELS:
                    out.append({"name": m["model"], "dim": m.get("dim"),
                                "size_mb": round(float(m.get("size_in_GB") or 0) * 1000),
                                "licence": m.get("license") or "", "languages": "see the model card",
                                "min_score": None, "offered": False})
        except Exception as e:
            logger.info("Knowledge: fastembed's model list could not be read: %s", e)
    return out


@router.get("/api/knowledge/tuning", dependencies=guard)
async def tuning_get(base: str | None = None):
    from myclaw.knowledge import relevance, semantic, tuning

    def run() -> Dict[str, Any]:
        d = tuning.describe()
        d["models"] = _models()
        d["semantic"] = {"enabled": semantic.enabled(), "installed": semantic.library_installed()}
        fb = relevance.feedback(_base(base))
        d["feedback"] = {k: fb[k] for k in ("queries", "labelled", "needed")}
        return d

    return await asyncio.to_thread(run)


class TuningPut(BaseModel):
    changes: Dict[str, Any] = {}
    tuning_ai: Optional[str] = None
    decay_ai: Optional[bool] = None
    reason: str = ""


@router.put("/api/knowledge/tuning", dependencies=guard)
async def tuning_put(req: TuningPut):
    from myclaw.knowledge import tuning

    def run() -> Dict[str, Any]:
        if req.tuning_ai is not None or req.decay_ai is not None:
            if req.tuning_ai not in (None, "suggest", "safe"):
                raise ValueError("tuning_ai is suggest or safe")
            switches = {k: v for k, v in (("tuning_ai", req.tuning_ai), ("decay_ai", req.decay_ai)) if v is not None}
            tuning._write_config(switches)
        out = tuning.set_values(req.changes, actor="user", reason=req.reason or "set on the Tune page")
        return {**out, "tuning": tuning.describe()}

    try:
        return await asyncio.to_thread(run)
    except ValueError as e:
        raise _bad(e) from e


class UndoReq(BaseModel):
    id: Optional[int] = None


@router.post("/api/knowledge/tuning/undo", dependencies=guard)
async def tuning_undo(req: UndoReq):
    from myclaw.knowledge import tuning

    try:
        return await asyncio.to_thread(tuning.undo, req.id)
    except ValueError as e:
        raise _bad(e, 404) from e


class DecideReq(BaseModel):
    approve: bool


@router.post("/api/knowledge/tuning/proposals/{proposal_id}", dependencies=guard)
async def tuning_decide(proposal_id: int, req: DecideReq):
    from myclaw.knowledge import tuning

    try:
        return await asyncio.to_thread(tuning.decide, proposal_id, req.approve)
    except ValueError as e:
        raise _bad(e, 404) from e


@router.post("/api/knowledge/tuning/ai", dependencies=guard)
async def tuning_ai(base: str | None = None, scope: str = Query("all", pattern="^(all|decay|search)$")):
    from myclaw.config import load_config
    from myclaw.knowledge import tuning

    try:
        return await tuning.ai_suggest(load_config(), _base(base), scope=scope)
    except tuning.AIUnavailable as e:
        raise _bad(e, 503) from e


@router.post("/api/knowledge/tuning/autotune", dependencies=guard)
async def tuning_autotune(base: str | None = None):
    from myclaw.knowledge import relevance

    return await asyncio.to_thread(relevance.autotune, _base(base))


@router.get("/api/knowledge/tuning/decay", dependencies=guard)
async def tuning_decay(base: str | None = None):
    from myclaw.knowledge import decay

    return await asyncio.to_thread(decay.digest, _base(base))


class TestReq(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: str = "auto"
    limit: int = Field(10, ge=1, le=50)


@router.post("/api/knowledge/tuning/test", dependencies=guard)
async def tuning_test(req: TestReq, base: str | None = None):
    from myclaw.knowledge import decay, semantic
    from myclaw.knowledge.db import KnowledgeDB

    b = _base(base)

    def run() -> Dict[str, Any]:
        info: Dict[str, Any] = {"explain": True}
        with KnowledgeDB(b) as db:
            semantic.search_entities(db, req.query, req.limit, mode=req.mode, info=info)
        rows = info.pop("explain", [])
        return {"query": req.query, "results": rows if isinstance(rows, list) else [], "info": info,
                "decay": decay.enabled(), "min_score": semantic.min_score(), "fusion": list(semantic.fusion())}

    return await asyncio.to_thread(run)


class VoteReq(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    permalink: str = Field(min_length=1, max_length=300)
    vote: int = Field(ge=-1, le=1)


@router.get("/api/knowledge/tuning/feedback", dependencies=guard)
async def feedback_get(base: str | None = None):
    from myclaw.knowledge import relevance

    return await asyncio.to_thread(relevance.feedback, _base(base))


@router.post("/api/knowledge/tuning/feedback", dependencies=guard)
async def feedback_post(req: VoteReq, base: str | None = None):
    from myclaw.knowledge import relevance

    try:
        return await asyncio.to_thread(relevance.vote, _base(base), req.query, req.permalink, req.vote)
    except ValueError as e:
        raise _bad(e) from e


# ── relations ───────────────────────────────────────────────────────────────
@router.get("/api/knowledge/relations", dependencies=guard)
async def relations_list(base: str | None = None, q: str = "", only: str = Query("", pattern="^(|vague|strong|used)$"),
                         limit: int = Query(300, ge=1, le=2000)):
    from myclaw.knowledge import relations

    return await asyncio.to_thread(relations.list_links, _base(base), q, only, limit)


class StrengthReq(BaseModel):
    permalink: str
    type: str
    target: str
    strength: Optional[int] = Field(None, ge=1, le=5)
    delta: int = Field(0, ge=-4, le=4)


@router.post("/api/knowledge/relations/strength", dependencies=guard)
async def relations_strength(req: StrengthReq, base: str | None = None):
    from myclaw.knowledge import relations

    try:
        return await asyncio.to_thread(relations.set_strength, _base(base), req.permalink, req.type, req.target,
                                       req.strength, req.delta)
    except (ValueError, LookupError) as e:
        raise _bad(e, 404 if isinstance(e, LookupError) else 400) from e


class DryRun(BaseModel):
    dry_run: bool = True


@router.post("/api/knowledge/relations/from-usage", dependencies=guard)
async def relations_from_usage(req: DryRun, base: str | None = None):
    from myclaw.knowledge import relations

    return await asyncio.to_thread(relations.strengthen_from_usage, _base(base), req.dry_run)


class RelAI(BaseModel):
    kind: str = Field("types", pattern="^(types|links)$")
    permalink: str = ""


@router.post("/api/knowledge/relations/ai", dependencies=guard)
async def relations_ai(req: RelAI, base: str | None = None):
    from myclaw.config import load_config
    from myclaw.knowledge import relations

    try:
        if req.kind == "types":
            return await relations.propose_types(load_config(), _base(base))
        return await relations.propose_links(load_config(), _base(base), permalink=req.permalink)
    except relations.AIUnavailable as e:
        raise _bad(e, 503) from e


class RelApply(BaseModel):
    proposals: List[Dict[str, Any]] = Field(max_length=50)


@router.post("/api/knowledge/relations/apply", dependencies=guard)
async def relations_apply(req: RelApply, base: str | None = None):
    from myclaw.knowledge import relations

    return await asyncio.to_thread(relations.apply, _base(base), req.proposals)


# ── duplicates: curate ──────────────────────────────────────────────────────
class PairReq(BaseModel):
    a: str
    b: str


@router.post("/api/knowledge/duplicates/dismiss", dependencies=guard)
async def duplicates_dismiss(req: PairReq, base: str | None = None):
    from myclaw.knowledge.quality import dismiss_duplicate

    try:
        await asyncio.to_thread(dismiss_duplicate, _base(base), req.a, req.b)
    except ValueError as e:
        raise _bad(e) from e
    return {"ok": True}


@router.post("/api/knowledge/duplicates/merge-identical", dependencies=guard)
async def duplicates_merge_identical(req: DryRun, base: str | None = None):
    from myclaw.knowledge.quality import merge_identical

    return await asyncio.to_thread(merge_identical, _base(base), req.dry_run)
