"""Doctor report and error viewer API (Web UI → Doctor → Report).

GET /api/doctor/report?hours=24                         full report (JSON)
GET /api/doctor/report/download?format=md|json|zip     the report as a file
GET /api/doctor/errors?hours=24&level=ERROR&q=text      grouped warnings/errors

Secrets never leave the server: the report holds only "key set: yes/no" and
log text is redacted (myclaw/doctor/logscan.py). Still guarded like the other
settings routes (this computer or the admin API key) — logs are private.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

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


@router.get("/api/doctor/errors", dependencies=guard)
async def errors(hours: float = 24, level: str = "", q: str = ""):
    levels = [x.strip().upper() for x in level.split(",") if x.strip()]
    if any(lv not in _LEVELS for lv in levels):
        raise HTTPException(status_code=400, detail="level must be WARNING, ERROR or CRITICAL")
    return await asyncio.to_thread(_report.error_view, _hours(hours), levels or None, q.strip()[:200])
