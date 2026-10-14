"""Check for updates, update, roll back and restart — from the Web UI (v0.7.4).

GET  /api/update/status?check=true   versions (installed / running / latest),
                                     progress of a running update, history
POST /api/update/start {ref}         run the safe updater in the background
POST /api/update/rollback            back to the previous version
POST /api/update/restart             restart this server with the installed version

The update itself is the normal safe updater (`update.sh update`): data and
program-folder backup, side-by-side install, checks, automatic rollback. It
runs as a separate process (its own session), so it keeps going even if the
Web UI is closed, and writes its log to ``<base>/logs/webui-update-*.log``.
The running server keeps the old code until it is restarted — the Restart
button re-executes this process with the newly installed version, so nobody
has to find the right terminal.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.doctor.paths import active_release, install_base, install_root, release_version, stale_release
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

_STEP = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*)$")
_DONE = re.compile(r"✔ (Updated|Rolled back|Already on) (.*?)(?:\. Use --force.*)?$")
_proc: Optional[subprocess.Popen] = None


def _state_file() -> Optional[Path]:
    base = install_base()
    return base / "webui-update.json" if base else None


def _read_state() -> Dict[str, Any]:
    f = _state_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: Dict[str, Any]) -> None:
    f = _state_file()
    if f:
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clean_env() -> Dict[str, str]:
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    return env


def _managed() -> Optional[str]:
    """None if updates can run here, else the reason why not."""
    if install_base() is None:
        return ("This copy of ZenSynora is not a standard install (~/.local/share/zensynora), "
                "so it cannot update itself. Use git pull, or install with install-online.sh.")
    if not (install_root() / "update.sh").exists():
        return "update.sh is missing from this install."
    return None


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if _proc is not None and _proc.pid == pid:
        return _proc.poll() is None
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _progress(log: Optional[str]) -> Dict[str, Any]:
    """Current step, result and tail from an updater log."""
    out: Dict[str, Any] = {"step": None, "steps": None, "step_text": "", "result": None, "tail": ""}
    if not log or not Path(log).exists():
        return out
    try:
        text = Path(log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        m = _STEP.match(line)
        if m:
            out.update(step=int(m.group(1)), steps=int(m.group(2)), step_text=m.group(3))
        d = _DONE.search(line)
        if d:
            out["result"] = f"{d.group(1)} {d.group(2)}"
    out["tail"] = "\n".join(text.splitlines()[-25:])
    return out


def _run_updater_json(args: list, timeout: int = 45) -> Dict[str, Any]:
    base = install_base()
    cmd = [sys.executable, "-m", "updater", *args, "--base", str(base), "--json"]
    r = subprocess.run(cmd, cwd=str(install_root()), env=_clean_env(), capture_output=True,
                       text=True, timeout=timeout)
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise RuntimeError((r.stderr or r.stdout or "updater gave no answer").strip()[-400:])


@router.get("/api/update/status", dependencies=guard)
async def update_status(check: bool = False):
    import asyncio

    from myclaw import __version__

    active = active_release()
    st = _read_state()
    running = _pid_alive(st.get("pid"))
    info: Dict[str, Any] = {
        "running_version": __version__,
        "installed_version": release_version(active) if active else __version__,
        "restart_needed": stale_release() is not None,
        "managed": _managed() is None,
        "not_managed_reason": _managed(),
        "update_running": running,
        "action": st.get("action"),
        "started": st.get("started"),
        "log": st.get("log"),
        **_progress(st.get("log")),
    }
    if st and not running and st.get("pid"):
        info["exit_code"] = st.get("exit_code")
        if _proc is not None and _proc.pid == st.get("pid") and _proc.returncode is not None:
            info["exit_code"] = _proc.returncode
        info["finished_ok"] = bool(info["result"]) and info.get("exit_code") in (0, None)
    if check and info["managed"]:
        try:
            data = await asyncio.to_thread(_run_updater_json, ["status", "--check"])
            info.update(latest_version=data.get("latest_version"),
                        update_available=bool(data.get("update_available")),
                        history=data.get("last_actions", []), releases=data.get("releases", []),
                        active_release=data.get("active_release"))
            if not data.get("latest_version"):
                info["check_error"] = "Could not reach GitHub to look for a new version."
        except Exception as e:
            info["check_error"] = f"Could not check for updates: {str(e)[:300]}"
    return info


class StartIn(BaseModel):
    ref: str = "main"
    force: bool = False  # reinstall even when the version is the same (repair)


def _spawn(action: str, args: list) -> Dict[str, Any]:
    global _proc
    reason = _managed()
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    st = _read_state()
    if _pid_alive(st.get("pid")):
        raise HTTPException(status_code=409, detail="An update is already running.")
    base = install_base()
    (base / "logs").mkdir(parents=True, exist_ok=True)
    log = base / "logs" / f"webui-{action}-{time.strftime('%Y%m%dT%H%M%S')}.log"
    cmd = ["bash", str(install_root() / "update.sh"), action, "--base", str(base), *args]
    fh = open(log, "w", encoding="utf-8")
    fh.write("$ " + " ".join(cmd) + "\n")
    fh.flush()
    # Own session: the update survives the Web UI being stopped or restarted.
    _proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd="/", env=_clean_env(),
                             start_new_session=True)
    fh.close()
    _write_state({"pid": _proc.pid, "action": action, "log": str(log), "started": time.time()})

    def _wait(p=_proc):
        code = p.wait()
        cur = _read_state()
        if cur.get("pid") == p.pid:
            cur["exit_code"] = code
            _write_state(cur)

    threading.Thread(target=_wait, daemon=True).start()
    return {"ok": True, "log": str(log)}


@router.post("/api/update/start", dependencies=guard)
async def start_update(body: StartIn = StartIn()):
    ref = (body.ref or "main").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", ref):
        raise HTTPException(status_code=400, detail="Invalid branch or tag name.")
    args = [] if ref == "main" else ["--ref", ref]
    if body.force:
        args.append("--force")
    return _spawn("update", args)


@router.post("/api/update/rollback", dependencies=guard)
async def rollback():
    return _spawn("rollback", [])


@router.post("/api/update/restart", dependencies=guard)
async def restart():
    """Replace this server process with the installed version (same arguments)."""
    if _pid_alive(_read_state().get("pid")):
        raise HTTPException(status_code=409, detail="Wait until the update has finished.")
    active = active_release()
    py = (active / "venv" / "bin" / "python") if active else Path(sys.executable)
    if not py.exists():
        py = Path(sys.executable)
    argv = [str(py), "-m", "myclaw", *sys.argv[1:]]
    if "webui" not in argv:
        raise HTTPException(status_code=400, detail="Restart works when ZenSynora was started with `zensynora webui`.")
    env = _clean_env()
    env["ZENSYNORA_NO_REEXEC"] = "1"
    if active:
        env["VIRTUAL_ENV"] = str(active / "venv")
        env["PATH"] = os.pathsep.join([str(active / "venv" / "bin"), env.get("PATH", "")])

    def _reexec():
        time.sleep(1.0)  # let this response reach the browser
        logger.info("Restarting the Web UI: %s", " ".join(argv))
        try:
            os.execve(argv[0], argv, env)
        except OSError as e:  # pragma: no cover - logged, server keeps running
            logger.error("Restart failed: %s", e)

    threading.Thread(target=_reexec, daemon=True).start()
    return {"ok": True, "restarting_with": release_version(active) if active else None}
