"""Versioned, idempotent migrations of user data in ``~/.myclaw``.

How to add one (future releases)
--------------------------------
Create ``updater/migrations/m_NNNN_short_name.py`` (NNNN = next number) with::

    ID = "0002_rename_x"            # unique, never reused
    DESCRIPTION = "What it changes"
    REVERSIBLE = False              # True only if rollback needs no data restore

    def apply(ctx):                 # ctx.data_dir, ctx.log, ctx.app_version
        ...                         # must be safe to run twice

Migrations run in ID order, once each, with the NEW release's Python
environment (so they may import ``myclaw``), after a full backup and before the
new version is activated. Applied IDs are recorded in
``~/.myclaw/.migrations.json``. If one fails, the updater restores the backup
and keeps the old version active.
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

log = logging.getLogger("zensynora.updater")

LEDGER_NAME = ".migrations.json"


@dataclass
class MigrationContext:
    data_dir: Path
    app_version: str
    log: logging.Logger


def discover() -> list:
    mods = []
    for info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        if info.name.startswith("m_"):
            mod = importlib.import_module(f"{__name__}.{info.name}")
            mods.append(mod)
    mods.sort(key=lambda m: m.ID)
    ids = [m.ID for m in mods]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate migration IDs: {ids}")
    return mods


def _ledger_path(data_dir: Path) -> Path:
    return data_dir / LEDGER_NAME


def read_ledger(data_dir: Path) -> dict:
    p = _ledger_path(data_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"applied": []}


def _write_ledger(data_dir: Path, ledger: dict) -> None:
    p = _ledger_path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    tmp.replace(p)


def pending(data_dir: Path) -> list:
    done = {e["id"] for e in read_ledger(data_dir).get("applied", [])}
    return [m for m in discover() if m.ID not in done]


def run_pending(data_dir: Path, app_version: str) -> List[str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = MigrationContext(data_dir=data_dir, app_version=app_version, log=log)
    applied: List[str] = []
    ledger = read_ledger(data_dir)
    for mod in pending(data_dir):
        log.info("migration %s: %s", mod.ID, getattr(mod, "DESCRIPTION", ""))
        mod.apply(ctx)
        ledger.setdefault("applied", []).append(
            {"id": mod.ID, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "app_version": app_version}
        )
        _write_ledger(data_dir, ledger)  # record each one immediately
        applied.append(mod.ID)
    return applied
