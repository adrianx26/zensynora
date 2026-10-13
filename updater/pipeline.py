"""Minimal step pipeline with automatic rollback.

Each step implements ``run(ctx)`` and (optionally) ``rollback(ctx)``. If a step
raises, the rollbacks of every step that already completed run in reverse
order, and the step that failed gets a chance to clean up its own partial work.

New update behaviour is added by writing a new ``Step`` subclass and inserting
it into ``steps.default_pipeline()`` — nothing else needs to change.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .layout import Layout, State

log = logging.getLogger("zensynora.updater")


class UpdateError(Exception):
    """A step failed in a way the user should read (no traceback needed)."""


class NothingToDo(Exception):
    """The installed version is already current — stop without changes."""


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
            continue
        log.info("[%d/%d] %s", i, total, step.description or step.name)
        try:
            step.run(ctx)
        except NothingToDo:
            for prev in reversed(done + [step]):
                _safe_rollback(prev, ctx)
            raise
        except BaseException as exc:
            log.error("Step '%s' failed: %s", step.name, exc)
            log.error("Rolling back — the previous installation stays active.")
            for prev in reversed(done + [step]):
                _safe_rollback(prev, ctx)
            raise
        done.append(step)


def _safe_rollback(step: Step, ctx: Context) -> None:
    try:
        step.rollback(ctx)
    except Exception as exc:  # never let a rollback failure hide the original error
        log.error("  rollback of '%s' failed: %s", step.name, exc)


def sh(
    cmd: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
    quiet: bool = False,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a command, mirror its output into the log, raise UpdateError on failure."""
    log.debug("$ %s", " ".join(str(c) for c in cmd))
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
