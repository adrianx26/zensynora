"""Approval-gated self-update: agents propose code changes, you decide (v0.7.1).

Why
---
The medic and other agents can find problems (errors in the log, failing or
badly scored jobs, doctor warnings) and know how to fix them, but an agent
that edits the code it is running from can break the app with no way back.
So agents never write to the program folder. Instead:

1. **Read** — ``read_source_file`` / ``list_source_files`` give agents
   read-only access to the installed code (never ``venv/``, ``.git`` or
   ``.env``).
2. **Propose** — ``propose_code_change`` stores the complete new content of
   one or more files as a *proposal* in ``~/.myclaw/medic/proposals/``. It is
   checked at once: paths stay inside the program folder, Python files must
   compile, sizes are limited, and a unified diff is saved for review.
3. **Approve** — you review the diff in the Web UI (Doctor → Change
   proposals) or with ``zensynora medic proposals`` and approve or reject it.
   Only documentation-only changes can be auto-approved, and only if you turn
   that on (``medic.self_update.auto_approve_low_risk``).
4. **Apply** — an approved proposal is never written over the running code.
   A copy of the current release is made, the change is applied to the copy,
   and the copy is installed by the safe updater exactly like a normal
   update: full backup of the program folder and your data, side-by-side
   install, checks (and the test suite if enabled), switch, automatic
   rollback on any failure. ``zensynora update rollback`` undoes it later.
5. **Learn** — ``get_recent_problems`` collects errors, failed or low-scoring
   job runs and open proposals; ``record_lesson`` saves what was learned in the
   knowledge base (note ``medic-lessons``) so later runs and other agents can
   find it. The "Medic self-review" job template ties this together.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from myclaw.doctor.paths import install_base, install_root, medic_dir

logger = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "applying", "applied", "failed", "rejected")
MAX_FILES = 20
MAX_FILE_BYTES = 400_000
# Never readable or writable by agents: environments, VCS data, secrets, build output.
_BLOCKED_PARTS = {"venv", ".venv", ".git", "node_modules", "__pycache__", "dist", ".update.lock"}
_BLOCKED_NAMES = {".env", ".config_key"}
# Changes that touch only these file types are "low risk" (docs and text).
_LOW_RISK_SUFFIXES = {".md", ".txt", ".rst"}
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "venv", ".venv", "node_modules", "__pycache__", "*.pyc", ".pytest_cache",
    ".mypy_cache", "graphify-out", ".update.lock",
)
_apply_lock = threading.Lock()


# ── Paths ────────────────────────────────────────────────────────────────────
def proposals_dir() -> Path:
    d = medic_dir() / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve(rel: str) -> Path:
    """Map a path given by an agent to a file inside the program folder, or raise."""
    root = install_root().resolve()
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        raise ValueError("Give a path relative to the program folder, e.g. myclaw/agent.py")
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"{rel} is outside the program folder")
    parts = set(p.relative_to(root).parts)
    if parts & _BLOCKED_PARTS or p.name in _BLOCKED_NAMES or p.suffix in (".pyc", ".db"):
        raise ValueError(f"{rel} is not available to agents (environment, VCS, build output or secrets)")
    return p


def _rel(p: Path) -> str:
    return p.relative_to(install_root().resolve()).as_posix()


def _sha(text: str | None) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else ""


# ── 1. Read-only access to the code ──────────────────────────────────────────
def read_source_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
    """Read part of an installed ZenSynora source file (read-only).

    Args:
        path: Path relative to the program folder, e.g. "myclaw/tools/kb.py".
        start_line: First line to show (1-based).
        max_lines: Number of lines to show (max 1000).
    """
    if not _cfg_flag("enabled", True):
        return _OFF
    try:
        p = _resolve(path)
        if not p.is_file():
            return f"Error: {path} does not exist"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), start - 1 + max(1, min(int(max_lines), 1000)))
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n… {len(lines) - end} more line(s); continue with start_line={end + 1}" if end < len(lines) else ""
        return f"{_rel(p)} (lines {start}-{end} of {len(lines)})\n{body}{more}"
    except ValueError as e:
        return f"Error: {e}"


def list_source_files(pattern: str = "myclaw/**/*.py", limit: int = 200) -> str:
    """List installed source files matching a glob, e.g. "myclaw/tools/*.py"."""
    if not _cfg_flag("enabled", True):
        return _OFF
    root = install_root().resolve()
    out = []
    if ".." in Path(pattern or "").parts or Path(pattern or "").is_absolute():
        return "Error: use a pattern inside the program folder, e.g. myclaw/**/*.py"
    for p in sorted(root.glob(pattern or "**/*")):
        try:
            if not p.is_file():
                continue
            _resolve(_rel(p))
        except (ValueError, OSError):
            continue
        out.append(f"{_rel(p)}  ({p.stat().st_size} B)")
        if len(out) >= max(1, min(int(limit), 1000)):
            out.append("… (limit reached)")
            break
    return "\n".join(out) or f"No files match {pattern}"


# ── 2. Proposals ─────────────────────────────────────────────────────────────
def _path_for(pid: str) -> Path:
    if not pid or not all(c.isalnum() or c == "-" for c in pid):
        raise ValueError("invalid proposal id")
    return proposals_dir() / f"{pid}.json"


def _save(prop: dict[str, Any]) -> None:
    prop["updated"] = time.time()
    path = _path_for(prop["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(prop, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_proposal(pid: str) -> dict[str, Any] | None:
    try:
        return json.loads(_path_for(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_proposals(status: str | None = None, include_files: bool = False) -> list[dict[str, Any]]:
    out = []
    for f in proposals_dir().glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if status and p.get("status") != status:
            continue
        if not include_files:
            p = {k: v for k, v in p.items() if k not in ("files", "diff")}
        out.append(p)
    return sorted(out, key=lambda p: p.get("created", 0), reverse=True)


def _validate_files(files: dict[str, str]) -> dict[str, Any]:
    if not isinstance(files, dict) or not files:
        raise ValueError("No files in the change.")
    if len(files) > MAX_FILES:
        raise ValueError(f"Too many files ({len(files)}); at most {MAX_FILES} per proposal.")
    checked, diffs, originals, problems = {}, [], {}, []
    for rel, content in files.items():
        if not isinstance(content, str):
            raise ValueError(f"{rel}: new content must be text")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"{rel}: file too large (max {MAX_FILE_BYTES // 1000} KB)")
        p = _resolve(rel)
        key = _rel(p)
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        if old == content:
            problems.append(f"{key}: no change")
        if p.suffix == ".py":
            try:
                compile(content, key, "exec")
            except SyntaxError as e:
                raise ValueError(f"{key}: Python syntax error on line {e.lineno}: {e.msg}") from None
        checked[key] = content
        originals[key] = _sha(old)
        diffs.extend(difflib.unified_diff(
            (old or "").splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=f"a/{key}" if old is not None else "/dev/null", tofile=f"b/{key}"))
    if len(problems) == len(files):
        raise ValueError("The new content is identical to the current files.")
    risk = "low" if all(Path(k).suffix in _LOW_RISK_SUFFIXES for k in checked) else "high"
    return {"files": checked, "originals": originals, "diff": "".join(diffs), "risk": risk,
            "notes": problems}


def create_proposal(title: str, reason: str, files: dict[str, str], author: str = "agent") -> dict[str, Any]:
    """Validate and store a proposal. Raises ValueError with a readable message."""
    title = (title or "").strip()[:160]
    if not title:
        raise ValueError("Give the change a short title.")
    checked = _validate_files(files)
    now = time.time()
    prop = {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3),
        "title": title,
        "reason": (reason or "").strip()[:4000],
        "author": author or "agent",
        "status": "pending",
        "created": now,
        "history": [{"at": now, "action": "proposed", "by": author or "agent"}],
        **checked,
    }
    _save(prop)
    _emit("proposal_created", prop)
    if prop["risk"] == "low" and _cfg_flag("auto_approve_low_risk"):
        approve(prop["id"], by="auto (low risk)")
        apply_in_background(prop["id"])
    return prop


def _transition(pid: str, allowed_from: tuple, status: str, by: str, note: str = "") -> dict[str, Any]:
    prop = get_proposal(pid)
    if not prop:
        raise ValueError("Proposal not found")
    if prop["status"] not in allowed_from:
        raise ValueError(f"Proposal is {prop['status']}; cannot {status.rstrip('d')} it now.")
    prop["status"] = status
    prop.setdefault("history", []).append({"at": time.time(), "action": status, "by": by, "note": note})
    _save(prop)
    _emit(f"proposal_{status}", prop)
    return prop


def approve(pid: str, by: str = "you") -> dict[str, Any]:
    return _transition(pid, ("pending", "failed"), "approved", by)


def reject(pid: str, by: str = "you", note: str = "") -> dict[str, Any]:
    return _transition(pid, ("pending", "approved", "failed"), "rejected", by, note)


def delete_proposal(pid: str) -> bool:
    prop = get_proposal(pid)
    if not prop or prop["status"] == "applying":
        return False
    _path_for(pid).unlink(missing_ok=True)
    (proposals_dir() / f"{pid}.log").unlink(missing_ok=True)
    return True


# ── 4. Apply through the safe updater ────────────────────────────────────────
def _updater_command(staging: Path, base: Path, run_tests: bool, webui_changed: bool) -> list[str]:
    cmd = ["bash", str(install_root() / "update.sh"), "update", "--from-dir", str(staging),
           "--base", str(base), "--force"]
    if not webui_changed:
        cmd.append("--skip-webui")  # keep the current build; nothing in webui/ changed
    if run_tests:
        cmd.append("--run-tests")
    return cmd


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    return env


def apply_proposal(pid: str, runner=subprocess.run) -> dict[str, Any]:
    """Install an approved proposal as a new release (blocking). Returns the proposal."""
    with _apply_lock:
        prop = get_proposal(pid)
        if not prop:
            raise ValueError("Proposal not found")
        if prop["status"] != "approved":
            raise ValueError(f"Only approved proposals can be applied (this one is {prop['status']}).")
        base = install_base()
        if base is None or not (install_root() / "update.sh").exists():
            raise ValueError("Self-update needs the standard install (~/.local/share/zensynora, "
                             "made by install-online.sh or `zensynora update`).")
        # The code must not have changed since the proposal was made.
        for rel, digest in prop.get("originals", {}).items():
            p = install_root() / rel
            current = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
            if _sha(current) != digest:
                prop = _transition(pid, ("approved",), "failed", "updater",
                                   f"{rel} changed since the proposal was made — ask for a new proposal.")
                return prop

        prop = _transition(pid, ("approved",), "applying", "updater")
        log_path = proposals_dir() / f"{pid}.log"
        staging = base / "staging" / pid
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(install_root(), staging, ignore=_COPY_IGNORE, symlinks=True)
            for rel, content in prop["files"].items():
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            webui_changed = any(rel.startswith("webui/") for rel in prop["files"])
            cmd = _updater_command(staging, base, _cfg_flag("run_tests"), webui_changed)
            with open(log_path, "w", encoding="utf-8") as log:
                log.write("$ " + " ".join(cmd) + "\n")
                log.flush()
                r = runner(cmd, stdout=log, stderr=subprocess.STDOUT, env=_clean_env(),
                           cwd="/", timeout=7200)
            ok = r.returncode == 0
        except Exception as e:  # copying failed, timeout, …
            logger.exception("Applying proposal %s failed", pid)
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\nERROR: {e}\n")
            ok = False
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path.exists() else ""
        note = ("Installed as a new release. Restart ZenSynora to use it; "
                "`zensynora update rollback` undoes it.") if ok else \
            "The update failed and was rolled back — the running version is unchanged."
        prop = get_proposal(pid) or prop
        prop["status"] = "applied" if ok else "failed"
        prop["result"] = {"ok": ok, "log_tail": tail, "log": str(log_path), "note": note}
        prop["history"].append({"at": time.time(), "action": prop["status"], "by": "updater", "note": note})
        _save(prop)
        _emit(f"proposal_{prop['status']}", prop)
        return prop


def apply_in_background(pid: str) -> None:
    def _run():
        try:
            apply_proposal(pid)
        except Exception as e:
            logger.warning("Proposal %s not applied: %s", pid, e)

    threading.Thread(target=_run, name=f"apply-{pid}", daemon=True).start()


# ── 5. Learning ──────────────────────────────────────────────────────────────
def recent_problems(hours: int = 24, limit: int = 20) -> dict[str, Any]:
    """Errors from the log, repeated warnings, restarts, risky custom tools,
    failed/low-score job runs and open proposals."""
    from myclaw.doctor import logscan

    cutoff = time.time() - hours * 3600
    sc = logscan.scan(hours, max_bytes=8_000_000)
    # Messages that differ only in numbers/ids are one group (with a count).
    groups = [{"time": g["last"], "where": g["module"], "message": g["message"][:400], "count": g["count"],
               "level": g["level"], "traceback": g["traceback"]} for g in logscan.group(sc.problems, samples=1)]
    errors = sorted((g for g in groups if g["level"] != "WARNING"), key=lambda g: -g["count"])
    warnings = sorted((g for g in groups if g["level"] == "WARNING" and g["count"] >= 5), key=lambda g: -g["count"])
    restarts = logscan.restart_stats(sc.starts)
    try:
        from myclaw.doctor.checks_more import risky_tools

        tools = [{"name": t["name"], "problems": t["problems"]} for t in risky_tools()]
    except Exception:
        tools = []
    runs: list[dict[str, Any]] = []
    try:
        from myclaw.jobs import store as jobs

        for job in jobs.list_jobs():
            for run in jobs.list_runs(job["id"], limit=10):
                if (run.get("started") or 0) < cutoff:
                    continue
                low = run.get("score") is not None and run["score"] < 6
                if run.get("status") in ("error", "timeout") or low:
                    runs.append({"job": job["name"], "status": run["status"], "score": run.get("score"),
                                 "error": (run.get("error") or "")[:300],
                                 "evaluation": str((run.get("evaluation") or {}).get("reasons", ""))[:300]})
    except Exception as e:  # jobs DB missing or locked — not fatal here
        logger.debug("recent_problems: jobs unavailable: %s", e)
    return {
        "errors": errors[:limit],
        "repeated_warnings": warnings[:limit],
        "restarts": {"starts": restarts["starts"], "max_in_15_min": restarts["max_in_window"],
                     "crash_loop": restarts["max_in_window"] > 10, "typical_gap_s": restarts["typical_gap_s"]},
        "risky_custom_tools": tools,
        "job_problems": runs[:limit],
        "open_proposals": [{"id": p["id"], "title": p["title"], "status": p["status"]}
                           for p in list_proposals() if p["status"] in ("pending", "approved", "failed")][:limit],
    }


LESSONS_NOTE = "medic-lessons"


def record_lesson(topic: str, lesson: str, user_id: str = "default") -> str:
    """Append a lesson to the knowledge-base note 'medic-lessons'."""
    topic, lesson = (topic or "").strip()[:120], (lesson or "").strip()[:2000]
    if not lesson:
        return "Error: lesson is empty"
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Observation, parse_note
    from myclaw.knowledge.storage import get_knowledge_dir

    try:
        path = get_knowledge_dir(user_id) / f"{LESSONS_NOTE}.md"
        # Keep earlier lessons: read them back from the note, add the new one.
        obs = [o for o in parse_note(path).observations if o.category == "lesson"] if path.exists() else []
        stamp = time.strftime("%Y-%m-%d")
        obs.append(Observation(category="lesson", content=f"{stamp} {topic}: {lesson}" if topic else f"{stamp} {lesson}",
                               tags=["medic"]))
        write_note(name=LESSONS_NOTE, title="Medic lessons", observations=obs[-200:],
                   tags=["medic", "lessons"], user_id=user_id)
        return f"Lesson saved to the knowledge base ({LESSONS_NOTE}, {len(obs[-200:])} lessons)."
    except Exception as e:
        logger.warning("record_lesson failed: %s", e)
        return f"Error: could not save the lesson: {e}"


# ── Agent tools (registered in myclaw.tools) ─────────────────────────────────
def propose_code_change(title: str, reason: str, file_path: str = "", new_content: str = "",
                        files_json: str = "") -> str:
    """Propose a change to ZenSynora's own code. You must approve it before anything changes.

    Give the COMPLETE new content of each file (not a diff). Read the file first
    with read_source_file. For one file use file_path + new_content; for several
    use files_json = '{"path/a.py": "...", "docs/x.md": "..."}'.

    Args:
        title: Short summary of the change.
        reason: What problem it fixes and how you checked it.
        file_path: File to change, relative to the program folder.
        new_content: Complete new content of that file.
        files_json: JSON object {path: complete new content} for several files.
    """
    from myclaw.events import CURRENT_AGENT

    if not _cfg_flag("enabled", True):
        return _OFF
    try:
        files: dict[str, str] = json.loads(files_json) if files_json else {}
        if file_path:
            files[file_path] = new_content
        prop = create_proposal(title, reason, files, author=CURRENT_AGENT.get() or "agent")
    except (ValueError, TypeError) as e:
        return f"Proposal rejected: {e}"
    return (f"Proposal {prop['id']} saved ({prop['risk']} risk, {len(prop['files'])} file(s)). "
            f"Status: {prop['status']}. The user reviews it in Doctor → Change proposals; "
            "it is installed only after approval, through the safe updater.")


def list_change_proposals(status: str = "") -> str:
    """List code-change proposals and their status (pending/approved/applied/failed/rejected)."""
    props = list_proposals(status or None)
    if not props:
        return "No proposals."
    return "\n".join(f"{p['id']}  [{p['status']}]  {p['title']}  (by {p['author']}, {p.get('risk', '?')} risk)"
                     for p in props[:50])


def get_recent_problems(hours: int = 24) -> str:
    """Errors from the log, failed or low-scoring job runs and open proposals (JSON)."""
    return json.dumps(recent_problems(hours=int(hours)), indent=1)


# ── helpers ──────────────────────────────────────────────────────────────────
def _cfg_flag(name: str, default: bool = False) -> bool:
    try:
        from myclaw.config import load_config

        return bool(getattr(load_config().medic.self_update, name))
    except Exception:
        return default


_OFF = "Error: self-update is turned off (Settings → Features → medic.self_update.enabled)."


def _emit(kind: str, prop: dict[str, Any]) -> None:
    try:
        from myclaw import events

        events.publish(events.ALL, {"type": kind, "proposal_id": prop["id"], "title": prop["title"],
                                    "status": prop["status"], "author": prop.get("author"),
                                    "ts": time.time()})
    except Exception:
        pass


SELF_REVIEW_PROMPT = """You are the Medic doing a self-review of ZenSynora.
1. Call get_recent_problems to see recent errors, repeated warnings, restarts (crash_loop),
   risky custom tools, failed or low-scoring jobs and open proposals.
2. Pick at most ONE problem that clearly comes from ZenSynora's own code (skip network,
   model-server or API-key problems — report those instead). A crash loop comes first:
   find the error logged just before the restarts. A risky custom tool (placeholder or
   hard-coded credentials) is not a code fix: tell the user to disable it with disable_skill.
3. Use list_source_files and read_source_file to find and read the code involved.
4. If you are confident of a small, safe fix, call propose_code_change with the COMPLETE
   new file content and a clear reason. Do not repeat an open proposal.
5. Call record_lesson with what you learned (cause, fix or workaround).
6. Reply with a short report: problem, cause, what you proposed or why you did not."""
