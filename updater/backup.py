"""User-data snapshots (``~/.myclaw``) — create, verify, restore."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from .layout import Layout, timestamp

log = logging.getLogger("zensynora.updater")

# Regenerable or bulky folders that do not need to survive an update.
EXCLUDE_DIRS = {"semantic_cache", "task_logs", "logs", "sandbox", "__pycache__", "benchmarks"}

# Files inside a release directory that belong to the user, not to the code.
RELEASE_USER_FILES = [".env", "data"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tar_filter(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    parts = Path(info.name).parts
    if any(p in EXCLUDE_DIRS for p in parts[1:]):
        return None
    return info


def create_backup(
    layout: Layout,
    label: str,
    release_dir: Optional[Path] = None,
    extra_manifest: Optional[Dict] = None,
) -> Path:
    """Snapshot user data (+ user files of the active release). Returns the backup dir."""
    layout.ensure_dirs()
    bdir = layout.backups / f"{timestamp()}-{label}"
    bdir.mkdir(mode=0o700)
    manifest: Dict = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "label": label,
        "data_dir": str(layout.data_dir),
        "files": {},
    }
    if extra_manifest:
        manifest.update(extra_manifest)

    if layout.data_dir.exists():
        archive = bdir / "user-data.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(layout.data_dir, arcname=layout.data_dir.name, filter=_tar_filter)
        os.chmod(archive, 0o600)
        manifest["files"]["user-data.tar.gz"] = {"sha256": _sha256(archive), "bytes": archive.stat().st_size}
        _verify_archive(archive)
    else:
        log.info("  no user data at %s yet — nothing to back up", layout.data_dir)

    if release_dir is not None:
        present = [n for n in RELEASE_USER_FILES if (release_dir / n).exists()]
        if present:
            archive = bdir / "release-files.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name in present:
                    tar.add(release_dir / name, arcname=name)
            os.chmod(archive, 0o600)
            manifest["files"]["release-files.tar.gz"] = {"sha256": _sha256(archive), "items": present}

    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("  backup written: %s", bdir)
    return bdir


def _verify_archive(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for _ in tar:
            pass


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    try:
        tar.extractall(dest, filter="data")  # Python 3.12+ / patched 3.11
    except TypeError:  # pragma: no cover - very old Python
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"Unsafe path in archive: {m.name}")
        tar.extractall(dest)


def restore_backup(layout: Layout, backup_dir: Path, take_safety_copy: bool = True) -> Optional[Path]:
    """Replace ``~/.myclaw`` with the snapshot in ``backup_dir``.

    The current data is first moved aside (and snapshotted) so a restore can
    itself be undone. Returns the safety backup dir, if one was taken.
    """
    archive = backup_dir / "user-data.tar.gz"
    if not archive.exists():
        log.info("  backup %s has no user-data archive; data left as is", backup_dir.name)
        return None

    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest.get("files", {}).get("user-data.tar.gz", {}).get("sha256")
    if expected and _sha256(archive) != expected:
        raise RuntimeError(f"Backup {backup_dir.name} is corrupted (checksum mismatch)")

    safety = None
    if take_safety_copy and layout.data_dir.exists():
        safety = create_backup(layout, "before-restore")

    parent = layout.data_dir.parent
    staging = parent / f".{layout.data_dir.name}.restore-{timestamp()}"
    staging.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, staging)
        restored = staging / layout.data_dir.name
        if not restored.is_dir():
            raise RuntimeError("Backup archive does not contain the data directory")
        old = parent / f".{layout.data_dir.name}.old-{timestamp()}"
        if layout.data_dir.exists():
            os.rename(layout.data_dir, old)
        os.rename(restored, layout.data_dir)
        # Regenerable folders were excluded from the backup; keep the current ones.
        if old.exists():
            for name in EXCLUDE_DIRS:
                if (old / name).exists() and not (layout.data_dir / name).exists():
                    os.rename(old / name, layout.data_dir / name)
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log.info("  user data restored from %s", backup_dir.name)
    return safety


# Files that must survive every update: settings + API keys, the config key,
# the knowledge base (v0.7.1). Patterns are relative to the data directory.
PROTECTED = ("config.json", ".config_key", "knowledge_*.db", "vectors.db", "knowledge_gaps.jsonl")
PROTECTED_DIRS = ("knowledge/",)


def _protected(rel: str) -> bool:
    import fnmatch

    return rel.startswith(PROTECTED_DIRS) or any(fnmatch.fnmatch(rel, p) for p in PROTECTED)


def ensure_user_data(layout: Layout, backup_dir: Path, release_dir: Optional[Path] = None) -> List[str]:
    """Put back any protected file that is missing after an update.

    Compares ``~/.myclaw`` (and the new release's ``.env``) with the pre-update
    backup and restores only what is *missing*; files a migration changed on
    purpose are kept. Returns the restored paths (relative).
    """
    restored: List[str] = []
    archive = backup_dir / "user-data.tar.gz"
    if archive.exists():
        top = layout.data_dir.name
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                parts = Path(member.name).parts
                if len(parts) < 2 or parts[0] != top or ".." in parts:
                    continue
                rel = "/".join(parts[1:])
                dst = layout.data_dir.joinpath(*parts[1:])
                if not _protected(rel) or dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(dst, member.mode & 0o777 or 0o600)
                os.utime(dst, (member.mtime, member.mtime))
                restored.append(rel)
    rel_archive = backup_dir / "release-files.tar.gz"
    if release_dir is not None and rel_archive.exists():
        with tarfile.open(rel_archive, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name == ".env" and not (release_dir / ".env").exists():
                    src = tar.extractfile(member)
                    if src is not None:
                        (release_dir / ".env").write_bytes(src.read())
                        os.chmod(release_dir / ".env", 0o600)
                        restored.append(".env (program folder)")
    return restored


def restore_release_files(backup_dir: Path, release_dir: Path) -> None:
    archive = backup_dir / "release-files.tar.gz"
    if archive.exists():
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, release_dir)
