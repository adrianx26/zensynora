"""Backups of settings, API keys and the knowledge base (added in v0.7.1).

One backup = one ``.tar.gz`` file with a ``manifest.json`` inside, so it can
be downloaded, copied to another computer and restored there.

What goes in (``PARTS``)
------------------------
``settings``   config.json, its encryption key file, .env of the install,
               migration ledger — this is where the API keys live
``knowledge``  knowledge base (Markdown notes + search databases, gap log)
``memory``     conversation memory, chat channels and their history
``jobs``       jobs and run history, self-made tools (TOOLBOX), skills,
               agent profiles

Settings are always included; the other parts follow ``config.backup``.
SQLite databases are copied with SQLite's online-backup API, so a backup is
consistent even while ZenSynora is running.

Where backups go
----------------
``config.backup.dir`` or, by default, ``~/.myclaw-backups`` — outside both
the program folder and ``~/.myclaw``, so uninstalling or restoring either one
never deletes your backups. Files are created with permissions 600 (they
contain API keys).

Automatic backups
-----------------
``auto_backup_due()`` / ``run_auto_backup()`` are called by the Web UI's
background runner; they keep the newest ``config.backup.keep`` automatic
backups and never delete manual ones.

Restoring
---------
``restore_backup()`` first makes a safety backup of the current state, then
writes the chosen parts back in place. It never touches files that are not
in the backup.

Note: if the config key is kept in the OS keyring (not in
``~/.myclaw/.config_key``), it is not part of a backup; on another computer
you need that keyring entry or an unencrypted config.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FORMAT = 1
PARTS = ("settings", "knowledge", "memory", "jobs")

# Paths relative to ~/.myclaw (glob patterns allowed). Directories are copied whole.
_PART_PATTERNS: dict[str, list[str]] = {
    "settings": ["config.json", ".config_key", ".migrations.json", "config.json.bak"],
    "knowledge": ["knowledge", "knowledge_*.db", "vectors.db", "knowledge_gaps.jsonl", "knowledge_spaces*"],
    "memory": ["memory*.db", "chat.db", "semantic_memory*"],
    "jobs": ["jobs.db", "TOOLBOX", "skills", "rules", "profiles", "scheduler_jobs.jsonl", "prompts.jsonl", "hub"],
}
# Never copied (caches, sandboxes, logs, SQLite side files).
_SKIP = {"__pycache__", "semantic_cache", "sandbox", "task_logs", "logs", "benchmarks"}
_SKIP_SUFFIXES = ("-wal", "-shm", "-journal")


def data_dir() -> Path:
    return Path.home() / ".myclaw"


def install_root() -> Path:
    """The program folder (where .env lives)."""
    return Path(__file__).resolve().parent.parent


def backup_dir(cfg: Any = None) -> Path:
    if cfg is None:
        try:
            from .config import load_config
            cfg = load_config()
        except Exception:
            cfg = None
    custom = getattr(getattr(cfg, "backup", None), "dir", "") if cfg is not None else ""
    # MYCLAW_BACKUP_DIR wins (Docker keeps backups inside the data volume).
    custom = os.environ.get("MYCLAW_BACKUP_DIR") or custom
    path = Path(custom).expanduser() if custom else Path.home() / ".myclaw-backups"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches(root: Path, patterns: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    if not root.exists():
        return out
    for entry in sorted(root.iterdir()):
        if any(fnmatch.fnmatch(entry.name, p) for p in patterns):
            if entry.name.endswith(_SKIP_SUFFIXES):
                continue
            out.append(entry)
    return out


def _walk(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        for name in filenames:
            if not name.endswith(_SKIP_SUFFIXES) and not name.endswith(".pyc"):
                yield Path(dirpath) / name


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a live SQLite database (online-backup API)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def default_parts(cfg: Any = None) -> list[str]:
    b = getattr(cfg, "backup", None)
    parts = ["settings"]
    if b is None or b.include_knowledge:
        parts.append("knowledge")
    if b is None or b.include_memory:
        parts.append("memory")
    if b is None or b.include_jobs:
        parts.append("jobs")
    return parts


# ── create ───────────────────────────────────────────────────────────────────
def create_backup(parts: list[str] | None = None, label: str = "manual", note: str = "",
                  dest: Path | None = None, cfg: Any = None) -> dict[str, Any]:
    """Write a backup archive; returns its info (as listed by ``list_backups``)."""
    from . import __version__

    if cfg is None:
        try:
            from .config import load_config
            cfg = load_config()
        except Exception:
            cfg = None
    parts = [p for p in (parts or default_parts(cfg)) if p in PARTS]
    if "settings" not in parts:
        parts.insert(0, "settings")
    label = "".join(ch for ch in (label or "manual") if ch.isalnum() or ch in "-_")[:40] or "manual"
    src_root = data_dir()
    dest = dest or backup_dir(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"zensynora-backup-{stamp}-{label}.tar.gz"
    target = dest / name
    n = 1
    while target.exists():  # two backups in the same second
        n += 1
        target = dest / f"zensynora-backup-{stamp}-{label}-{n}.tar.gz"

    files: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="zs-backup-") as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir()
        for part in parts:
            for entry in _matches(src_root, _PART_PATTERNS[part]):
                for f in _walk(entry):
                    rel = f.relative_to(src_root).as_posix()
                    out = stage / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if f.suffix == ".db" and _is_sqlite(f):
                            _snapshot_sqlite(f, out)
                        else:
                            shutil.copy2(f, out)
                    except (OSError, sqlite3.Error) as e:
                        logger.warning("backup: skipped %s (%s)", rel, e)
                        continue
                    files[f"data/{rel}"] = {"part": part, "bytes": out.stat().st_size, "sha256": _sha256(out)}
        env = install_root() / ".env"
        if "settings" in parts and env.is_file():
            (Path(tmp) / "install").mkdir()
            shutil.copy2(env, Path(tmp) / "install" / ".env")
            files["install/.env"] = {"part": "settings", "bytes": env.stat().st_size,
                                     "sha256": _sha256(Path(tmp) / "install" / ".env")}

        manifest = {
            "format": FORMAT, "app": "zensynora", "version": __version__,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": label, "note": note[:500],
            "parts": parts, "host": os.uname().nodename if hasattr(os, "uname") else "",
            "files": files,
        }
        (Path(tmp) / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        partial = target.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(Path(tmp) / "manifest.json", arcname="manifest.json")
            for sub in ("data", "install"):
                if (Path(tmp) / sub).exists():
                    tar.add(Path(tmp) / sub, arcname=sub)
        os.chmod(partial, 0o600)
        os.replace(partial, target)
    logger.info("Backup written: %s (%d files)", target, len(files))
    return _info(target)


# ── list / inspect ───────────────────────────────────────────────────────────
def read_manifest(path: Path) -> dict[str, Any]:
    with tarfile.open(path, "r:gz") as tar:
        member = tar.getmember("manifest.json")
        f = tar.extractfile(member)
        if f is None:
            raise ValueError("manifest.json missing")
        data = json.loads(f.read().decode("utf-8"))
    if data.get("app") != "zensynora":
        raise ValueError("Not a ZenSynora backup")
    return data


def _info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"name": path.name, "path": str(path), "bytes": path.stat().st_size,
                            "mtime": path.stat().st_mtime}
    try:
        m = read_manifest(path)
        counts: dict[str, int] = {}
        for meta in m.get("files", {}).values():
            counts[meta.get("part", "?")] = counts.get(meta.get("part", "?"), 0) + 1
        info.update(version=m.get("version"), created=m.get("created"), label=m.get("label"),
                    note=m.get("note", ""), parts=m.get("parts", []), files=counts, valid=True)
    except Exception as e:
        info.update(valid=False, error=str(e)[:200])
    return info


def list_backups(cfg: Any = None) -> list[dict[str, Any]]:
    root = backup_dir(cfg)
    items = [_info(p) for p in root.glob("zensynora-backup-*.tar.gz")]
    return sorted(items, key=lambda i: i["mtime"], reverse=True)


def resolve(name: str, cfg: Any = None) -> Path:
    """A backup file by name, refusing anything outside the backup folder."""
    root = backup_dir(cfg).resolve()
    path = (root / Path(name).name).resolve()
    if path.parent != root or not path.name.endswith(".tar.gz") or not path.is_file():
        raise FileNotFoundError(name)
    return path


def verify_backup(path: Path) -> tuple[bool, list[str]]:
    """Check every file in the archive against the manifest's SHA-256."""
    problems: list[str] = []
    m = read_manifest(path)
    expected = m.get("files", {})
    seen = set()
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.name == "manifest.json":
                continue
            f = tar.extractfile(member)
            h = hashlib.sha256(f.read()).hexdigest() if f else ""
            seen.add(member.name)
            if member.name in expected and expected[member.name]["sha256"] != h:
                problems.append(f"{member.name}: content changed")
    for missing in set(expected) - seen:
        problems.append(f"{missing}: missing from archive")
    return not problems, problems


def delete_backup(name: str, cfg: Any = None) -> None:
    resolve(name, cfg).unlink()


def import_backup(data: bytes, cfg: Any = None) -> dict[str, Any]:
    """Save an uploaded backup file into the backup folder (after checking it)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        f = tar.extractfile(tar.getmember("manifest.json"))
        m = json.loads(f.read().decode("utf-8")) if f else {}
    if m.get("app") != "zensynora":
        raise ValueError("This file is not a ZenSynora backup.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir(cfg) / f"zensynora-backup-{stamp}-imported.tar.gz"
    target.write_bytes(data)
    os.chmod(target, 0o600)
    ok, problems = verify_backup(target)
    if not ok:
        target.unlink()
        raise ValueError("The backup is damaged: " + "; ".join(problems[:3]))
    return _info(target)


# ── restore ──────────────────────────────────────────────────────────────────
def restore_backup(name: str, parts: list[str] | None = None, safety_copy: bool = True,
                   cfg: Any = None) -> dict[str, Any]:
    """Put the chosen parts of a backup back in place.

    A safety backup of the current state is taken first (label
    ``before-restore``), so a restore can itself be undone.
    """
    path = resolve(name, cfg)
    ok, problems = verify_backup(path)
    if not ok:
        raise ValueError("The backup is damaged: " + "; ".join(problems[:3]))
    m = read_manifest(path)
    chosen = [p for p in (parts or m.get("parts", [])) if p in m.get("parts", [])]
    if not chosen:
        raise ValueError("Nothing to restore — pick at least one part that the backup contains.")
    safety = None
    if safety_copy:
        safety = create_backup(parts=list(PARTS), label="before-restore", cfg=cfg)["name"]

    root = data_dir()
    restored: list[str] = []
    wanted = {k for k, v in m["files"].items() if v.get("part") in chosen}
    with tempfile.TemporaryDirectory(prefix="zs-restore-") as tmp:
        with tarfile.open(path, "r:gz") as tar:
            members = [mb for mb in tar.getmembers() if mb.name in wanted and mb.isfile()]
            try:
                tar.extractall(tmp, members=members, filter="data")
            except TypeError:  # Python without extraction filters
                for mb in members:
                    if mb.name.startswith("/") or ".." in Path(mb.name).parts:
                        raise ValueError(f"Unsafe path in backup: {mb.name}")
                tar.extractall(tmp, members=members)
        for rel in sorted(wanted):
            src = Path(tmp) / rel
            if not src.is_file():
                continue
            if rel.startswith("data/"):
                dst = root / rel[len("data/"):]
            elif rel == "install/.env":
                dst = install_root() / ".env"
            else:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            for side in ("-wal", "-shm"):  # stale SQLite side files would corrupt the restored DB
                Path(str(dst) + side).unlink(missing_ok=True)
            tmp_dst = dst.with_name(dst.name + ".restoring")
            shutil.copy2(src, tmp_dst)
            os.replace(tmp_dst, dst)
            restored.append(rel)
    for secret in (root / "config.json", root / ".config_key", install_root() / ".env"):
        if secret.exists():
            try:
                os.chmod(secret, 0o600)
            except OSError:
                pass
    try:
        from .config import load_config
        load_config(force_reload=True)
    except Exception as e:
        logger.warning("Config reload after restore failed: %s", e)
    logger.info("Restored %d files from %s (parts: %s)", len(restored), path.name, ", ".join(chosen))
    return {"restored": len(restored), "parts": chosen, "safety_backup": safety, "from": path.name}


# ── automatic backups ────────────────────────────────────────────────────────
def _auto_backups(cfg: Any = None) -> list[dict[str, Any]]:
    return [b for b in list_backups(cfg) if b.get("label") == "auto"]


def auto_backup_due(cfg: Any = None, now: float | None = None) -> bool:
    b = getattr(cfg, "backup", None)
    if b is None or not b.auto_enabled:
        return False
    autos = _auto_backups(cfg)
    if not autos:
        return True
    return (now or time.time()) - autos[0]["mtime"] >= max(1, int(b.interval_hours)) * 3600


def prune_auto_backups(cfg: Any = None) -> list[str]:
    keep = max(1, int(getattr(getattr(cfg, "backup", None), "keep", 10) or 10))
    removed = []
    for b in _auto_backups(cfg)[keep:]:
        Path(b["path"]).unlink(missing_ok=True)
        removed.append(b["name"])
    return removed


def run_auto_backup(cfg: Any = None) -> dict[str, Any] | None:
    """Take an automatic backup if one is due; prune old automatic ones."""
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    if not auto_backup_due(cfg):
        return None
    info = create_backup(label="auto", cfg=cfg)
    prune_auto_backups(cfg)
    return info
