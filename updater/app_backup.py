"""Full backup of the installed program folder (added in v0.7.1, protocol 2).

Before an update, the folder of the version that is running now is archived
completely — code, ``venv/``, the built web UI, ``.env`` and anything you put
there — together with an inventory of every file (path, size, mode, mtime,
symlink target). If the update fails, the rollback compares the folder with
that inventory and, if anything differs, puts the archived copy back, so the
install is exactly as it was before the update.

Modes (``update.sh update --app-backup …``):
  ``full``  everything (default; about the size of the folder, compressed)
  ``code``  everything except ``venv/`` (much smaller; the venv can be rebuilt)
  ``none``  no program-folder archive (user data is still backed up)

Stdlib only, like the rest of the updater.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path

log = logging.getLogger("zensynora.updater")

ARCHIVE = "app-folder.tar.gz"
INVENTORY = "app-folder.inventory.json"
MODES = ("full", "code", "none")

# Never part of the snapshot or the comparison: caches rebuilt automatically.
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}


def _skip(rel_parts: tuple, mode: str) -> bool:
    if any(p in _SKIP_DIRS for p in rel_parts):
        return True
    if rel_parts and rel_parts[-1].endswith((".pyc", ".pyo")):
        return True
    return mode == "code" and bool(rel_parts) and rel_parts[0] == "venv"


def inventory(folder: Path, mode: str = "full") -> dict[str, dict]:
    """Every file / symlink under *folder* (relative path → metadata)."""
    out: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        rel_dir = Path(dirpath).relative_to(folder)
        keep = []
        for d in dirnames:
            full = Path(dirpath) / d
            rel = (rel_dir / d).parts
            if _skip(rel, mode):
                continue
            if full.is_symlink():  # symlinked directory: record the link, don't descend
                out[(rel_dir / d).as_posix()] = {"link": os.readlink(full)}
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            rel = rel_dir / name
            if _skip(rel.parts, mode):
                continue
            full = Path(dirpath) / name
            try:
                if full.is_symlink():
                    out[rel.as_posix()] = {"link": os.readlink(full)}
                else:
                    st = full.stat()
                    out[rel.as_posix()] = {"size": st.st_size, "mtime": int(st.st_mtime),
                                           "mode": st.st_mode & 0o7777}
            except OSError:
                continue
    return out


def folder_size(folder: Path, mode: str = "full") -> int:
    return sum(m.get("size", 0) for m in inventory(folder, mode).values())


def snapshot(folder: Path, backup_dir: Path, mode: str = "full") -> Path | None:
    """Archive *folder* into ``backup_dir/app-folder.tar.gz`` + inventory."""
    if mode == "none":
        return None
    if mode not in MODES:
        raise ValueError(f"app backup mode must be one of {', '.join(MODES)}")
    started = time.monotonic()
    inv = inventory(folder, mode)
    archive = backup_dir / ARCHIVE
    partial = backup_dir / (ARCHIVE + ".partial")

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts[1:]  # strip the top-level "app/"
        return None if parts and _skip(parts, mode) else info

    with tarfile.open(partial, "w:gz", compresslevel=3) as tar:  # level 3: much faster, ~same size
        tar.add(folder, arcname="app", filter=_filter)
    os.chmod(partial, 0o600)
    os.replace(partial, archive)
    (backup_dir / INVENTORY).write_text(json.dumps({
        "folder": str(folder), "mode": mode, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "files": inv,
    }), encoding="utf-8")
    log.info("  program folder archived: %d files, %d MB → %s (%ds)", len(inv),
             archive.stat().st_size // 1_000_000, archive.name, time.monotonic() - started)
    return archive


def differences(folder: Path, backup_dir: Path, limit: int = 50) -> list[str]:
    """What changed in *folder* since the snapshot (empty list = identical)."""
    data = json.loads((backup_dir / INVENTORY).read_text(encoding="utf-8"))
    before: dict[str, dict] = data["files"]
    if not folder.exists():
        return [f"{folder} is missing"]
    now = inventory(folder, data.get("mode", "full"))
    diffs: list[str] = []
    for rel, meta in before.items():
        cur = now.get(rel)
        if cur is None:
            diffs.append(f"missing: {rel}")
        elif "link" in meta or "link" in cur:
            if meta.get("link") != cur.get("link"):
                diffs.append(f"changed link: {rel}")
        elif cur["size"] != meta["size"] or cur["mtime"] != meta["mtime"]:
            diffs.append(f"changed: {rel}")
        if len(diffs) >= limit:
            return diffs
    for rel in now.keys() - before.keys():
        diffs.append(f"added: {rel}")
        if len(diffs) >= limit:
            break
    return diffs


def restore(folder: Path, backup_dir: Path) -> None:
    """Replace *folder* with the archived copy (atomic swap of the folder)."""
    archive = backup_dir / ARCHIVE
    if not archive.exists():
        raise RuntimeError(f"No program-folder archive in {backup_dir}")
    data = json.loads((backup_dir / INVENTORY).read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    staging = folder.parent / f".{folder.name}.restore-{stamp}"
    staging.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(staging, filter="tar")  # keeps modes and in-tree symlinks
            except TypeError:  # pragma: no cover - Python without extraction filters
                tar.extractall(staging)
        restored = staging / "app"
        broken = folder.parent / f".{folder.name}.failed-{stamp}"
        if data.get("mode") == "code" and (folder / "venv").is_dir() and not (restored / "venv").exists():
            os.rename(folder / "venv", restored / "venv")  # venv was not archived: keep the current one
        if folder.exists() or folder.is_symlink():
            os.rename(folder, broken)
        os.rename(restored, folder)
        shutil.rmtree(broken, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log.info("  program folder restored from %s", archive)
