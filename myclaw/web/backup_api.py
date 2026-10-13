"""Backups API (added in v0.7.1) — see ``myclaw.backup``.

GET    /api/backups                    list + settings + folder
POST   /api/backups                    create {parts?, note?}
GET    /api/backups/{name}/download    download the .tar.gz
POST   /api/backups/{name}/restore     restore {parts?} (safety backup first)
POST   /api/backups/{name}/verify      check the archive against its manifest
DELETE /api/backups/{name}             delete
POST   /api/backups/import             upload a backup file (raw body, application/gzip)

Backups contain API keys, so every route uses the settings guard (this
computer or the admin API key).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from myclaw import backup
from myclaw.config import load_config
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_IMPORT_BYTES = 2 * 1024 ** 3  # 2 GB

# Called after a restore so agents pick up restored settings (set by web.api).
_on_restored = None


def set_restore_hook(fn) -> None:
    global _on_restored
    _on_restored = fn


class CreateIn(BaseModel):
    parts: list[str] | None = None
    note: str = ""


class RestoreIn(BaseModel):
    parts: list[str] | None = None


def _not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No backup called '{name}'")


@router.get("/api/backups", dependencies=guard)
async def list_backups():
    cfg = load_config()
    items = await asyncio.to_thread(backup.list_backups, cfg)
    return {
        "backups": items,
        "folder": str(backup.backup_dir(cfg)),
        "parts": list(backup.PARTS),
        "default_parts": backup.default_parts(cfg),
        "settings": cfg.backup.model_dump(),
    }


@router.post("/api/backups", dependencies=guard)
async def create_backup(body: CreateIn = CreateIn()):
    bad = [p for p in (body.parts or []) if p not in backup.PARTS]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown part(s): {', '.join(bad)}")
    try:
        return await asyncio.to_thread(backup.create_backup, body.parts, "manual", body.note)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@router.get("/api/backups/{name}/download", dependencies=guard)
async def download_backup(name: str):
    try:
        path = backup.resolve(name)
    except FileNotFoundError:
        raise _not_found(name)
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.post("/api/backups/{name}/verify", dependencies=guard)
async def verify_backup(name: str):
    try:
        path = backup.resolve(name)
    except FileNotFoundError:
        raise _not_found(name)
    try:
        ok, problems = await asyncio.to_thread(backup.verify_backup, path)
    except Exception as e:
        return {"ok": False, "problems": [str(e)]}
    return {"ok": ok, "problems": problems}


@router.post("/api/backups/{name}/restore", dependencies=guard)
async def restore_backup(name: str, body: RestoreIn = RestoreIn()):
    try:
        result = await asyncio.to_thread(backup.restore_backup, name, body.parts)
    except FileNotFoundError:
        raise _not_found(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if _on_restored:
        try:
            res = _on_restored()
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.warning("post-restore hook failed: %s", e)
    return result


@router.delete("/api/backups/{name}", dependencies=guard)
async def delete_backup(name: str):
    try:
        await asyncio.to_thread(backup.delete_backup, name)
    except FileNotFoundError:
        raise _not_found(name)
    return {"status": "deleted"}


@router.post("/api/backups/import", dependencies=guard)
async def import_backup(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Backup file is too large")
    try:
        return await asyncio.to_thread(backup.import_backup, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not import: {str(e)[:300]}")


async def auto_backup_tick() -> None:
    """Called every few seconds by the job runner; cheap unless a backup is due."""
    import time

    global _last_check
    now = time.monotonic()
    if now - _last_check < 60:  # look at the folder at most once a minute
        return
    _last_check = now
    cfg = load_config()
    if not cfg.backup.auto_enabled:
        return
    info = await asyncio.to_thread(backup.run_auto_backup, cfg)
    if info:
        logger.info("Automatic backup created: %s", info["name"])


_last_check = 0.0
