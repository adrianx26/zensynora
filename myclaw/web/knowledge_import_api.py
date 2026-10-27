"""Knowledge base import for the Web UI (KB phase 5, 2026-10-24).

Routes (guarded like the other knowledge routes: this computer or the admin key)
------
POST   /api/knowledge/assimilate           {base, source, max_notes} → {job_id}
POST   /api/knowledge/import?base=&kind=&filename=
                                           upload → {job_id}. The body is the file itself
                                           (application/octet-stream — what the Web UI
                                           sends), or multipart/form-data with the
                                           fields base, kind, file (needs python-multipart)
GET    /api/knowledge/import/{job_id}      status, progress, result
DELETE /api/knowledge/import/{job_id}      ask a running job to stop (keeps what it wrote)

Jobs run in a background thread (never on the event loop), at most
``MAX_RUNNING`` at a time, each with a time limit. Uploads are limited to
``MAX_UPLOAD_BYTES``, stored in a temporary folder and deleted when the job
ends.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_RUNNING = 2
JOB_SECONDS = 1800.0
KEEP_JOBS = 50
BASE_RE = re.compile(r"^[\w.@-]{1,64}$")

_jobs: Dict[str, Dict[str, Any]] = {}
_budgets: Dict[str, Any] = {}
_lock = threading.Lock()
_uploads = 0           # uploads being received (they count against MAX_RUNNING)
GRACE_SECONDS = 60     # a job past JOB_SECONDS + this is marked failed and frees its slot


def _base(base: Optional[str]) -> str:
    b = (base or "default").strip() or "default"
    if not BASE_RE.match(b) or b.startswith("."):
        raise HTTPException(status_code=400, detail="Unknown knowledge base.")
    return b


def _reap() -> None:
    """Watchdog: a job past its time limit is cancelled, marked failed and frees its slot.

    2026-10-24 (KB phase 5, review): a stuck job (e.g. a hanging download)
    blocked the two job slots for good.
    """
    now = time.time()
    for job in list(_jobs.values()):
        if job["status"] == "running" and now - job["started"] > JOB_SECONDS + GRACE_SECONDS:
            budget = _budgets.pop(job["id"], None)
            if budget is not None:
                budget.cancel.set()
            job["status"] = "error"
            job["error"] = (f"Stopped: the import took longer than {int(JOB_SECONDS // 60)} minutes. "
                            "Notes saved so far are kept; run it again to continue.")
            job["finished"] = now
            logger.warning("Knowledge import %s timed out and was stopped", job["id"])


def _running() -> int:
    _reap()
    return sum(1 for j in _jobs.values() if j["status"] == "running") + _uploads


def _new_job(kind: str, source: str, base: str, reserved: bool = False) -> str:
    """Register a job; ``reserved``: the caller already holds an upload slot."""
    global _uploads
    with _lock:
        if _running() - (1 if reserved else 0) >= MAX_RUNNING:
            raise HTTPException(status_code=429, detail=(
                f"{MAX_RUNNING} imports are already running; wait until one has finished."))
        if reserved:  # the upload's slot becomes the job's
            _uploads = max(0, _uploads - 1)
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "source": source, "base": base,
                         "status": "running", "progress": "starting", "result": None,
                         "error": "", "started": time.time(), "finished": None}
        old = sorted((j for j in _jobs.values() if j["status"] != "running"),
                     key=lambda j: j["started"])
        for j in old[: max(0, len(_jobs) - KEEP_JOBS)]:
            _jobs.pop(j["id"], None)
        return job_id


def _start(job_id: str, work, cleanup: Optional[Path] = None, max_notes: int = 10_000) -> None:
    from myclaw.knowledge.ingest import Budget, IngestError

    def progress(text: str) -> None:
        _jobs[job_id]["progress"] = text

    budget = Budget(max_notes=max_notes, max_bytes=2_000_000_000, seconds=JOB_SECONDS,
                    progress=progress)
    _budgets[job_id] = budget

    def run() -> None:
        job = _jobs[job_id]
        try:
            result = work(budget)
            if job["status"] != "running":  # the watchdog gave up on it meanwhile
                return
            job["result"] = result.to_dict()
            job["status"] = "done"
            job["progress"] = "finished"
        except IngestError as e:
            if job["status"] == "running":
                job["status"], job["error"] = "error", str(e)
            logger.info("Knowledge import %s: %s", job_id, e)
        except Exception as e:  # already logged with traceback by the entry point
            if job["status"] == "running":
                job["status"] = "error"
                job["error"] = (f"The import failed unexpectedly ({type(e).__name__}). "
                                "Details are in the log (Doctor → Errors).")
        finally:
            job["finished"] = job["finished"] or time.time()
            _budgets.pop(job_id, None)
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)

    threading.Thread(target=run, name=f"kb-import-{job_id}", daemon=True).start()


class AssimilateIn(BaseModel):
    base: str = "default"
    source: str
    max_notes: int = 40


@router.post("/api/knowledge/assimilate", dependencies=guard)
async def assimilate(body: AssimilateIn) -> Dict[str, Any]:
    from myclaw.knowledge.ingest import assimilate as run_assimilate
    from myclaw.knowledge.ingest.safety import clean_source

    base = _base(body.base)
    source = (body.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="Enter a web address, a GitHub repository URL "
                                                    "or a file/folder path.")
    n = max(1, min(int(body.max_notes or 40), 500))
    job_id = _new_job("assimilate", clean_source(source), base)
    _start(job_id, lambda b: run_assimilate(source, base=base, max_notes=n, budget=b), max_notes=n)
    return {"job_id": job_id}


def _safe_name(name: str) -> str:
    name = Path((name or "").replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    return name[:120] or "upload"


async def _save_stream(stream, dest: Path) -> int:
    size = 0
    with open(dest, "wb") as f:
        async for chunk in stream:
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=(
                    f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Import it "
                    "with: zensynora knowledge import FILE"))
            f.write(chunk)
    return size


@router.post("/api/knowledge/import", dependencies=guard)
async def import_upload(request: Request, base: str = "default", kind: str = "auto",
                        filename: str = "") -> Dict[str, Any]:
    from myclaw.knowledge.ingest import import_file
    from myclaw.knowledge.ingest.importers import IngestError, normalize_kind

    global _uploads
    too_big = HTTPException(status_code=413, detail=(
        f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Import it with: "
        "zensynora knowledge import FILE"))
    length = request.headers.get("content-length", "")
    ctype = request.headers.get("content-type", "")
    if length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 1_000_000:
        raise too_big
    if ctype.startswith("multipart/form-data") and not length.isdigit():
        # 2026-10-24 (KB phase 5, review): the form parser stores the whole
        # file before we see it — only uploads of a known, allowed size
        raise HTTPException(status_code=411, detail=(
            "Multipart uploads need a Content-Length; send the file as the request body instead "
            "(?base=&kind=&filename=)."))
    with _lock:  # an upload in progress takes a job slot (disk + work)
        if _running() >= MAX_RUNNING:
            raise HTTPException(status_code=429, detail=(
                f"{MAX_RUNNING} imports are already running; wait until one has finished."))
        _uploads += 1
    reserved = True
    tmp = Path(tempfile.mkdtemp(prefix="zensynora-import-"))
    try:
        if ctype.startswith("multipart/form-data"):
            try:
                form = await request.form()
            except AssertionError as e:  # python-multipart is missing
                raise HTTPException(status_code=400, detail=(
                    "Multipart uploads need the python-multipart package; send the file as the "
                    "request body instead (?base=&kind=&filename=).")) from e
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="No file in the upload (field 'file').")
            base = str(form.get("base") or base)
            kind = str(form.get("kind") or kind)
            filename = getattr(upload, "filename", "") or filename

            async def chunks():
                while True:
                    data = await upload.read(1024 * 1024)
                    if not data:
                        break
                    yield data

            name = _safe_name(filename)
            size = await _save_stream(chunks(), tmp / name)
        else:
            name = _safe_name(filename or request.headers.get("x-filename", ""))
            size = await _save_stream(request.stream(), tmp / name)
        b = _base(base)
        try:
            k = normalize_kind(kind)
        except IngestError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if size == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        path = tmp / name
        if path.suffix == "" and k in ("auto", "chatgpt", "claude", "mcp_memory"):
            # no extension (some browsers): guess zip vs json from the first bytes
            head = path.read_bytes()[:4]
            path = path.rename(path.with_suffix(".zip" if head.startswith(b"PK") else ".json"))
        job_id = _new_job(f"import:{k}", name, b, reserved=True)
        reserved = False
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        if reserved:
            with _lock:
                _uploads = max(0, _uploads - 1)
    _start(job_id, lambda bud: import_file(str(path), base=b, kind=k, budget=bud, trusted=True,
                                           display_name=name), cleanup=tmp)
    return {"job_id": job_id}


@router.get("/api/knowledge/import/{job_id}", dependencies=guard)
async def import_status(job_id: str) -> Dict[str, Any]:
    _reap()
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown import job (jobs are kept until the "
                                                    "Web UI restarts).")
    out = dict(job)
    out["elapsed"] = round((job["finished"] or time.time()) - job["started"], 1)
    return out


@router.delete("/api/knowledge/import/{job_id}", dependencies=guard)
async def import_cancel(job_id: str) -> Dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown import job.")
    budget = _budgets.get(job_id)
    if budget is not None:
        budget.cancel.set()
    return {"ok": True, "status": job["status"]}
