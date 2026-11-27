"""Minimal step pipeline with automatic rollback.

Each step implements ``run(ctx)`` and (optionally) ``rollback(ctx)``. If a step
raises, the rollbacks of every step that already completed run in reverse
order, and the step that failed gets a chance to clean up its own partial work.

New update behaviour is added by writing a new ``Step`` subclass and inserting
it into ``steps.default_pipeline()`` — nothing else needs to change.
"""

from __future__ import annotations

import logging
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .layout import Layout, State
from .progress import detail, emit, scope

log = logging.getLogger("zensynora.updater")


class UpdateError(Exception):
    """A step failed in a way the user should read (no traceback needed)."""


class NothingToDo(Exception):
    """The installed version is already current — stop without changes."""


class Handoff(Exception):
    """A newer updater was downloaded; re-run it on the staged source."""

    def __init__(self, staged: Path):
        super().__init__(str(staged))
        self.staged = staged


@dataclass
class Options:
    source_kind: str = "git"  # git | dir | archive
    source_value: str = "main"  # ref, directory or archive path
    repo: str = "adrianx26/zensynora"
    force: bool = False
    allow_downgrade: bool = False
    run_tests: bool = False
    keep_releases: int = 3
    keep_backups: int = 5
    manage_service: bool = True
    skip_webui: bool = False
    dry_run: bool = False
    app_backup: str = "full"  # v0.7.1: full | code | none (program-folder backup)
    interactive: bool = False
    control_dir: Optional[str] = None


@dataclass
class Context:
    layout: Layout
    state: State
    opts: Options
    stamp: str
    current_dir: Optional[Path] = None
    current_version: Optional[str] = None
    new_id: Optional[str] = None
    new_dir: Optional[Path] = None
    new_version: Optional[str] = None
    backup_dir: Optional[Path] = None
    migrations_applied: List[str] = field(default_factory=list)
    service_was_active: bool = False
    adopted_legacy_as: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def note(self, msg: str) -> None:
        log.warning(msg)
        self.notes.append(msg)


class Step:
    name = "step"
    description = ""
    skippable = False

    def should_run(self, ctx: Context) -> bool:
        return True

    def run(self, ctx: Context) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def rollback(self, ctx: Context) -> None:
        """Undo this step. Must be safe to call after a partial run()."""


def run_pipeline(steps: Sequence[Step], ctx: Context) -> None:
    done: List[Step] = []
    total = len(steps)
    for i, step in enumerate(steps, 1):
        if not step.should_run(ctx):
            log.info("[%d/%d] %s — skipped", i, total, step.description or step.name)
            emit(step.name, "skipped", step.description or step.name, index=i, total=total)
            continue
        log.info("[%d/%d] %s", i, total, step.description or step.name)
        started = time.monotonic()
        entered = False
        try:
            if ctx.opts.interactive and step.skippable:
                if _decision(step, ctx, i, total) == "skip":
                    emit(step.name, "skipped", "Skipped by user: " + step.description, index=i, total=total)
                    continue
            emit(step.name, "running", step.description or step.name, index=i, total=total)
            entered = True
            with scope(step.name):
                step.run(ctx)
        except NothingToDo:
            emit(step.name, "skipped", "No update needed", index=i, total=total)
            for prev in reversed(done + ([step] if entered else [])):
                _safe_rollback(prev, ctx)
            raise
        except Handoff:
            emit(step.name, "ok", "Passing control to the newer updater", index=i, total=total)
            raise
        except BaseException as exc:
            emit(step.name, "failed", str(exc), index=i, total=total)
            report = log.warning if ctx.extra.get("user_aborted") else log.error
            report("Step '%s' stopped: %s", step.name, exc)
            report("Rolling back completed steps; inspect the recovery outcomes.")
            for prev in reversed(done + ([step] if entered else [])):
                _safe_rollback(prev, ctx)
            raise
        emit(step.name, "ok", step.description or step.name, index=i, total=total,
             seconds=round(time.monotonic() - started, 3))
        done.append(step)


def _decision(step: Step, ctx: Context, index: int, total: int) -> str:
    """Only optional steps reach this gate; mandatory safeguards cannot be skipped."""
    if ctx.opts.control_dir:
        request = uuid.uuid4().hex
        emit(step.name, "waiting", step.description, index=index, total=total, request=request)
        path = Path(ctx.opts.control_dir) / f"{request}.json"
        while True:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                time.sleep(0.2)
                continue
            except ValueError as exc:
                raise UpdateError("Invalid decision file; retry the update using Continue, Skip or Abort.") from exc
            path.unlink()
            action = data.get("action")
            if action not in ("continue", "skip", "abort"):
                raise UpdateError("Invalid user decision; update aborted safely.")
            break
    else:
        if not sys.stdin.isatty():
            raise UpdateError("Interactive update requires a terminal or Web UI control channel.")
        emit(step.name, "waiting", step.description, index=index, total=total)
        while True:
            action = input("Optional step: [c]ontinue, [s]kip, [a]bort: ").strip().lower()
            action = {"c": "continue", "s": "skip", "a": "abort"}.get(action, action)
            if action in ("continue", "skip", "abort"):
                break
    if action == "abort":
        ctx.extra["user_aborted"] = True
        raise UpdateError("User aborted the update; rolling back completed steps.")
    return action


def _safe_rollback(step: Step, ctx: Context) -> None:
    emit(step.name, "rollback-running", f"Rolling back {step.description or step.name}")
    try:
        with scope(step.name):
            step.rollback(ctx)
    except Exception as exc:  # never let a rollback failure hide the original error
        ctx.extra["rollback_failed"] = True
        emit(step.name, "rollback-failed", str(exc))
        log.error("  rollback of '%s' failed: %s", step.name, exc)
    else:
        emit(step.name, "rolled-back", f"Rollback completed: {step.description or step.name}")


def sh(
    cmd: "Sequence[object]",  # str / Path items (converted with str())
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
    quiet: bool = False,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a command, mirror its output into the log, raise UpdateError on failure."""
    log.debug("$ %s", " ".join(str(c) for c in cmd))
    detail("Running: " + " ".join(str(c) for c in cmd))
    if env is None:
        env = clean_env()
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout or ""
    detail(f"Command exited with status {proc.returncode}")
    for line in out.splitlines():
        log.debug("  | %s", line)
    if check and proc.returncode != 0:
        tail = "\n".join(out.splitlines()[-15:])
        raise UpdateError(f"Command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n{tail}")
    if not quiet and proc.returncode == 0:
        pass
    return proc


def clean_env(**extra: str) -> Dict[str, str]:
    """Environment for child processes without the updater's own Python path.

    update.sh puts the *running* updater on PYTHONPATH; leaking that into the
    new release's venv would make it import the OLD code (wrong self-check,
    migrations from the wrong version)."""
    import os

    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSAFEPATH")}
    env.update(extra)
    return env


def python_exe() -> str:
    return sys.executable
