"""Semantic search of the knowledge base — status, backfill, install (KB phase 3, 2026-10-24).

Routes
------
GET    /api/knowledge/semantic/status?base=     notes / embedded / stale, model, installed?,
                                                the running backfill job and the install job
POST   /api/knowledge/semantic/backfill?base=&all=   embed the notes without (current) vectors,
                                                in a background thread (progress in status)
DELETE /api/knowledge/semantic/backfill?base=   ask a running backfill to stop (keeps what it did)
POST   /api/knowledge/semantic/install          ``python -m pip install "fastembed…"`` into the
                                                Web UI's virtual environment (refused when it is
                                                installed, running, or not a virtual environment)

Everything runs off the event loop. The routes use the settings guard (this
computer, or the admin API key): the install route runs pip.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from myclaw.web.knowledge_api import _base
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

INSTALL_TIMEOUT = 900          # seconds; pip downloads onnxruntime (~15 MB) and friends
LOG_TAIL_LINES = 40

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}          # base → backfill job
_install: Dict[str, Any] = {"running": False}


def _job_view(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not job:
        return None
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _install_view() -> Dict[str, Any]:
    with _lock:
        view = {k: v for k, v in _install.items() if not k.startswith("_")}
    log = view.pop("log_file", None)
    if log:
        try:
            lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
            view["log"] = "\n".join(lines[-LOG_TAIL_LINES:])
        except OSError:
            view["log"] = ""
        view["log_file"] = log
    return view


@router.get("/api/knowledge/semantic/status", dependencies=guard)
async def semantic_status(base: str | None = None):
    from myclaw.knowledge import semantic

    b = _base(base)
    st = await asyncio.to_thread(semantic.embedding_status, b)
    with _lock:
        st["job"] = _job_view(_jobs.get(b))
    st["install"] = _install_view()
    st["in_venv"] = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return st


def _run_backfill(b: str, job: Dict[str, Any], all_notes: bool) -> None:
    from myclaw.knowledge import semantic

    def progress(done: int, total: int) -> None:
        job["done"], job["total"] = done, total
        job["phase"] = "embedding" if total else "loading the model"

    try:
        r = semantic.backfill_embeddings(b, all_notes=all_notes, progress=progress,
                                         stop=lambda: job["_stop"])
        job["result"] = r
        job["status"] = "done" if r.get("ok") else ("stopped" if r.get("stopped") else "error")
        if not r.get("ok"):
            job["error"] = r.get("message")
    except Exception as e:  # a fault in ZenSynora, not in the user's input
        logger.error("Knowledge: semantic backfill of %s failed: %s", b, e, exc_info=True)
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = time.time()
        job["phase"] = "finished"


@router.post("/api/knowledge/semantic/backfill", dependencies=guard)
async def semantic_backfill(base: str | None = None, all_notes: bool = Query(False, alias="all")):
    from myclaw.knowledge import semantic

    b = _base(base)
    if not semantic.library_installed():
        raise HTTPException(status_code=400, detail=(
            "Semantic search needs the optional package fastembed — click Install, or run "
            f'python -m pip install "{semantic.INSTALL_SPEC}" in ZenSynora\'s virtual environment.'))
    with _lock:
        cur = _jobs.get(b)
        if cur and cur["status"] == "running":
            raise HTTPException(status_code=409, detail="A backfill of this knowledge base is already running.")
        job: Dict[str, Any] = {"base": b, "status": "running", "phase": "starting", "done": 0,
                               "total": 0, "started": time.time(), "finished": None,
                               "error": None, "result": None, "all": bool(all_notes), "_stop": False}
        _jobs[b] = job
    threading.Thread(target=_run_backfill, args=(b, job, bool(all_notes)),
                     name=f"kb-embed-backfill-{b}", daemon=True).start()
    return {"ok": True, "job": _job_view(job)}


@router.delete("/api/knowledge/semantic/backfill", dependencies=guard)
async def semantic_backfill_stop(base: str | None = None):
    b = _base(base)
    with _lock:
        job = _jobs.get(b)
        if not job or job["status"] != "running":
            raise HTTPException(status_code=404, detail="No backfill is running for this knowledge base.")
        job["_stop"] = True
    return {"ok": True, "job": _job_view(job)}


# Indirections (2026-10-24 review): tests replace these, never the global
# subprocess.Popen (other threads of the test process spawn processes too).
_popen = subprocess.Popen
_run = subprocess.run


def _spawn(target: Any, *args: Any) -> None:
    threading.Thread(target=target, args=args, name="kb-semantic-install", daemon=True).start()


_SKIP_PIN = ("zensynora", "myclaw", "pip", "setuptools", "wheel")


def _write_constraints(path: Path) -> Optional[str]:
    """Pin every package already in the venv (``pip freeze``) so installing
    fastembed only adds packages — it never upgrades or downgrades one
    ZenSynora uses. Returns an error text (the install then runs unpinned)."""
    try:
        r = _run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                 capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return f"could not list the installed packages: {e}"
    if r.returncode != 0:
        return f"pip freeze failed: {(r.stderr or '').strip()[:300]}"
    pins = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")) or " @ " in line or "==" not in line:
            continue   # editable installs, direct URLs, comments
        if line.split("==", 1)[0].strip().lower().replace("_", "-") in _SKIP_PIN:
            continue
        pins.append(line)
    path.write_text("\n".join(pins) + "\n", encoding="utf-8")
    return None


def _run_install(cmd: List[str], log_file: Path) -> None:
    from myclaw.knowledge import semantic

    code: Optional[int] = None
    error: Optional[str] = None
    try:
        with open(log_file, "w", encoding="utf-8") as fh:
            constraints = log_file.with_suffix(".constraints.txt")
            problem = _write_constraints(constraints)
            if problem is None:
                cmd = [*cmd, "-c", str(constraints)]
            else:
                fh.write(f"(installed packages not pinned: {problem})\n")
            fh.write("$ " + " ".join(cmd) + "\n")
            fh.flush()
            proc = _popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            try:
                code = proc.wait(timeout=INSTALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
                error = f"pip did not finish within {INSTALL_TIMEOUT // 60} minutes and was stopped."
                fh.write(f"\n{error}\n")
    except OSError as e:
        error = f"Could not run pip: {e}"
    importlib.invalidate_caches()
    ok = code == 0 and error is None and semantic.library_installed()
    if code == 0 and not ok and error is None:
        error = "pip finished, but fastembed cannot be found — restart ZenSynora."
    elif code not in (0, None) and error is None:
        error = (f"pip failed (exit code {code}) — see the log. Packages ZenSynora already uses "
                 "are kept at their versions; a conflict with them is shown there.")
    if ok:
        semantic.get_embedder(wait=0.0, retry=True)  # start loading (and downloading) the model
        semantic.queue_known_bases()
        logger.info("Knowledge: fastembed installed for semantic search")
    else:
        logger.warning("Knowledge: installing fastembed failed: %s", error)
    with _lock:
        _install.update(running=False, ok=ok, exit_code=code, error=error, finished=time.time(),
                        command=" ".join(cmd))


@router.post("/api/knowledge/semantic/install", dependencies=guard)
async def semantic_install():
    from myclaw.knowledge import semantic

    if semantic.library_installed():
        raise HTTPException(status_code=409, detail="fastembed is already installed.")
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        raise HTTPException(status_code=400, detail=(
            "ZenSynora is not running in a virtual environment, so it does not install packages "
            f'itself. Install with: {sys.executable} -m pip install "{semantic.INSTALL_SPEC}"'))
    with _lock:
        if _install.get("running"):
            raise HTTPException(status_code=409, detail="The installation is already running.")
        logs = Path.home() / ".myclaw" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_file = logs / f"semantic-install-{time.strftime('%Y%m%d-%H%M%S')}.log"
        cmd = semantic.install_command()
        _install.clear()
        _install.update(running=True, ok=None, exit_code=None, error=None, started=time.time(),
                        finished=None, command=" ".join(cmd), log_file=str(log_file))
    _spawn(_run_install, cmd, log_file)
    return {"ok": True, "install": _install_view()}
