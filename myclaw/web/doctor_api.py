"""Doctor report and error viewer API (Web UI → Doctor → Report).

GET /api/doctor/report?hours=24                         full report (JSON)
GET /api/doctor/report/download?format=md|json|zip     the report as a file
GET /api/doctor/timeline?hours=24                       health over time (infographic)
GET /api/doctor/errors?hours=24&level=ERROR&q=text&kind= grouped warnings/errors; kind=failures|jobs|restarts
POST /api/doctor/ai/summary      {hours, offline}             the model's health summary (2026-11-04)
POST /api/doctor/ai/explain      {kind, key, level, hours}    explain one error group / check (cached)
GET  /api/doctor/ai/explanations                              cached explanations (also automatic ones)
POST /api/doctor/ai/investigate  {kind, key, level, hours, agent}  an agent job (may file a proposal)

Secrets never leave the server: the report holds only "key set: yes/no" and
log text is redacted (myclaw/doctor/logscan.py). Still guarded like the other
settings routes (this computer or the admin API key) — logs are private.
"""

from __future__ import annotations

import asyncio

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from myclaw.doctor import report as _report
from myclaw.web.settings_api import guard

router = APIRouter()

_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


def _hours(hours: float) -> float:
    return max(0.25, min(float(hours), 24 * 30))


@router.get("/api/doctor/report", dependencies=guard)
async def get_report(hours: float = 24, offline: bool = False, log_tail: bool = True):
    return await asyncio.to_thread(_report.build_report, _hours(hours), log_tail, offline)


@router.get("/api/doctor/report/download", dependencies=guard)
async def download_report(format: str = Query("md", pattern="^(md|json|zip)$"), hours: float = 24,
                          offline: bool = False):
    rep = await asyncio.to_thread(_report.build_report, _hours(hours), True, offline)
    name = _report.filename(format)
    if format == "json":
        body, media = _report.to_json(rep).encode("utf-8"), "application/json"
    elif format == "zip":
        body, media = await asyncio.to_thread(_report.to_zip, rep), "application/zip"
    else:
        body, media = _report.to_markdown(rep).encode("utf-8"), "text/markdown; charset=utf-8"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/doctor/timeline", dependencies=guard)
async def health_timeline(hours: float = 24):
    """2026-11-01: errors, failures, restarts and job runs per time bucket (infographic)."""
    return await asyncio.to_thread(_report.timeline, _hours(hours))


@router.get("/api/doctor/errors", dependencies=guard)
async def errors(hours: float = 24, level: str = "", q: str = "", kind: str = ""):
    levels = [x.strip().upper() for x in level.split(",") if x.strip()]
    if any(lv not in _LEVELS for lv in levels):
        raise HTTPException(status_code=400, detail="level must be WARNING, ERROR or CRITICAL")
    if kind and kind not in _report.KINDS:
        raise HTTPException(status_code=400, detail="kind must be failures, jobs or restarts")
    return await asyncio.to_thread(_report.error_view, _hours(hours), levels or None, q.strip()[:200], 5, kind)


# ── 2026-11-04: AI ───────────────────────────────────────────────────────────

class SummaryIn(BaseModel):
    hours: float = 24
    offline: bool = False


class ProblemIn(BaseModel):
    kind: Literal["error", "check"]
    key: str = Field(..., max_length=2000)
    level: str = ""
    hours: float = 24
    refresh: bool = False
    agent: str = "default"


@router.post("/api/doctor/ai/summary", dependencies=guard)
async def ai_summary(body: SummaryIn):
    from myclaw.doctor import ai

    try:
        return await ai.summary(_hours(body.hours), body.offline)
    except ai.AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/doctor/ai/explain", dependencies=guard)
async def ai_explain(body: ProblemIn):
    from myclaw.doctor import ai

    try:
        return await ai.explain(body.kind, body.key, body.level, _hours(body.hours), body.refresh)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ai.AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/doctor/ai/explanations", dependencies=guard)
async def ai_explanations():
    from myclaw.doctor import ai

    return ai.explanations()


@router.post("/api/doctor/ai/investigate", dependencies=guard)
async def ai_investigate(body: ProblemIn):
    """Runs in the event loop on purpose: starting the job schedules it on this loop."""
    from myclaw.doctor import ai

    try:
        return ai.investigate(body.kind, body.key, body.level, _hours(body.hours), body.agent)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
