"""Doctor report and error viewer API (Web UI → Doctor → Report).

GET /api/doctor/report?hours=24                         full report (JSON)
GET /api/doctor/report/download?format=md|json|zip     the report as a file
GET /api/doctor/timeline?hours=24                       health over time (infographic)
GET /api/doctor/errors?hours=24&level=ERROR&q=text&kind= grouped warnings/errors; kind=failures|jobs|restarts
POST /api/doctor/ai/summary      {hours, offline}             the model's health summary (2026-11-04)
POST /api/doctor/ai/explain      {kind, key, level, hours}    explain one error group / check (cached)
GET  /api/doctor/ai/explanations                              cached explanations (also automatic ones)
POST /api/doctor/ai/investigate  {kind, key, level, hours, agent}  an agent job (may file a proposal)
GET  /api/doctor/ai/investigations/{run_id}/report   privacy-filtered GitHub report draft
GET  /api/doctor/logs?source=activity|app&hours=&kind=&status=&level=&q=&limit=&offset=   (2026-11-05)
GET  /api/doctor/logs/export?source=&hours=&fmt=txt|jsonl&…   the period as a file
POST /api/doctor/logs/analyze    {hours}           the model's summary and options
POST /api/doctor/logs/act        {option, agent}   start what an option suggests (a job)
POST /api/doctor/ui-crash        {message, stack, component_stack, view}  a Web UI crash (2026-11-28)

Secrets never leave the server: the report holds only "key set: yes/no" and
log text is redacted (myclaw/doctor/logscan.py). Still guarded like the other
settings routes (this computer or the admin API key) — logs are private. The
one exception is ui-crash, which only writes a bounded, de-duplicated line.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from myclaw.doctor import report as _report
from myclaw.web.settings_api import guard
from myclaw.web.settings_api import ui_guard

router = APIRouter()
logger = logging.getLogger(__name__)


class ReinstallIn(BaseModel):
    ref: str = Field("main", max_length=100)
    confirmed: bool = False


@router.post("/api/doctor/reinstall", dependencies=ui_guard)
async def reinstall(body: ReinstallIn):
    """User-approved replacement of application code, never of the user's data."""
    from myclaw.web.update_api import StartIn, start_update

    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Confirm reinstalling application code; user data is preserved.")
    return await start_update(StartIn(ref=body.ref, force=True))

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


@router.get("/api/doctor/ai/investigations/{run_id}/report", dependencies=guard)
async def investigation_report(run_id: int):
    from myclaw.doctor import ai

    try:
        text = await asyncio.to_thread(ai.investigation_report, run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"text": text}


# ── 2026-11-05: logs ─────────────────────────────────────────────────────────

def _log_hours(hours: float) -> float:
    return max(0.25, min(float(hours), 24 * 31))


@router.get("/api/doctor/logs", dependencies=guard)
async def logs(source: str = "activity", hours: float = 24, kind: str = "", status: str = "", level: str = "",
               q: str = "", limit: int = Query(300, ge=1, le=2000), offset: int = Query(0, ge=0)):
    from myclaw.doctor import logs as L

    try:
        return await asyncio.to_thread(L.read, source, _log_hours(hours), kind, status, level, q, limit, offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/doctor/logs/export", dependencies=guard)
async def logs_export(source: str = "activity", hours: float = 24, fmt: str = "txt", kind: str = "",
                      status: str = "", level: str = "", q: str = ""):
    from myclaw.doctor import logs as L

    try:
        name, text = await asyncio.to_thread(L.export, source, _log_hours(hours), fmt, kind, status, level, q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class AnalyzeIn(BaseModel):
    hours: float = 24


class ActIn(BaseModel):
    option: dict
    agent: str = "default"


@router.post("/api/doctor/logs/analyze", dependencies=guard)
async def logs_analyze(body: AnalyzeIn):
    from myclaw.doctor import logs as L

    try:
        return await L.analyze(_log_hours(body.hours))
    except L.AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/doctor/logs/act", dependencies=guard)
async def logs_act(body: ActIn):
    """Runs in the event loop on purpose: starting the job schedules it on this loop."""
    from myclaw.doctor import logs as L

    try:
        return L.act(body.option, body.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 2026-11-28: Web UI crash reports ─────────────────────────────────────────
# A render error unmounts the whole React app (black screen). The browser
# reports it here so it shows in Doctor → Errors. Not behind the admin guard:
# the crash must be reported from other computers too, where the admin key is
# often missing. Same-site + Web UI header, bounded fields, de-duplicated and
# rate-limited so a crash loop or a LAN client cannot flood the log.
CRASH_REPEAT_SECONDS = 600
CRASH_WINDOW_SECONDS = 600
CRASH_MAX_PER_WINDOW = 20
_crash_seen: dict[str, list[float]] = {}  # signature -> [last logged, repeats since then]
_crash_times: list[float] = []
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class UiCrashIn(BaseModel):
    message: str = Field("", max_length=1000)
    stack: str = Field("", max_length=8000)
    component_stack: str = Field("", max_length=8000)
    view: str = Field("", max_length=40)


async def _same_site_ui(request: Request) -> None:
    from myclaw.web.settings_api import _foreign_origin, require_ui

    if _foreign_origin(request):
        raise HTTPException(status_code=403, detail="Crash reports are only accepted from the ZenSynora Web UI.")
    await require_ui(request)


@router.post("/api/doctor/ui-crash", dependencies=[Depends(_same_site_ui)])
async def ui_crash(body: UiCrashIn):
    from myclaw.doctor.logscan import redact

    message = redact(_CONTROL.sub(" ", body.message).strip()) or "unknown error"
    view = re.sub(r"[^a-z0-9_-]", "", body.view.lower()) or "unknown"
    signature = f"{view}|{message}"
    now = time.time()
    seen = _crash_seen.get(signature)
    if seen and now - seen[0] < CRASH_REPEAT_SECONDS:
        seen[1] += 1
        return {"logged": False, "reason": "Already reported recently; repeats are counted."}
    _crash_times[:] = [t for t in _crash_times if now - t < CRASH_WINDOW_SECONDS]
    if len(_crash_times) >= CRASH_MAX_PER_WINDOW:
        return {"logged": False, "reason": "Too many crash reports right now; try again in a few minutes."}
    repeats = int(seen[1]) if seen else 0
    if len(_crash_seen) >= 200:
        _crash_seen.pop(next(iter(_crash_seen)))
    _crash_seen[signature] = [now, 0]
    _crash_times.append(now)
    details = "\n".join(part for part in (
        redact(body.stack.strip()),
        "Component stack:" + redact(body.component_stack.rstrip()) if body.component_stack.strip() else "",
    ) if part)
    logger.error(
        "Web UI crashed on the %s page: %s%s", view, message,
        f" (repeated {repeats} more time(s) since the last report)" if repeats else "",
        extra={"extra_fields": {"exception": details}} if details else None,
    )
    return {"logged": True}
