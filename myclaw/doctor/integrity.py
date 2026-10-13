"""File-integrity baseline, local file backups and recovery for the medic.

* ``scan``    — record sha256 of the installed code (baseline per version)
* ``verify``  — report files that changed or disappeared since the baseline
* ``backup_file`` / ``list_file_backups`` — copies under ~/.myclaw/medic/backups
* ``recover`` — restore a file from the newest local backup, or from GitHub
  at the tag matching the installed version
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Iterable
from pathlib import Path

from .paths import install_root, medic_dir

TRACKED_GLOBS = ["myclaw/**/*.py", "updater/**/*.py", "myclaw/agents/data/*.yaml",
                 "pyproject.toml", "requirements.txt", "install.sh", "update.sh"]
SKIP_PARTS = {"__pycache__", "venv", "node_modules", ".git"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _baseline_file() -> Path:
    return medic_dir() / "integrity.json"


def _state_file() -> Path:
    return medic_dir() / "state.json"


def load_state() -> dict:
    p = _state_file()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def save_state(state: dict) -> None:
    _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


def hash_check_enabled() -> bool:
    state = load_state()
    if "hash_check" in state:
        return bool(state["hash_check"])
    try:
        from ..config import load_config

        return bool(load_config().medic.enable_hash_check)
    except Exception:
        return True


def resolve_in_root(file_path: str) -> Path:
    """Resolve a user/LLM supplied path and refuse anything outside the install."""
    root = install_root()
    p = Path(file_path)
    p = (p if p.is_absolute() else root / p).resolve()
    if root not in p.parents and p != root:
        raise ValueError(f"{file_path} is outside the ZenSynora installation ({root})")
    return p


def _tracked_files(paths: Iterable[str] | None = None) -> list[Path]:
    root = install_root()
    if paths:
        return [resolve_in_root(p) for p in paths]
    files = set()
    for pattern in TRACKED_GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_PARTS):
                files.add(p)
    return sorted(files)


def scan(paths: Iterable[str] | None = None) -> dict:
    from .. import __version__

    root = install_root()
    files = _tracked_files(paths)
    baseline = load_baseline() if paths else None
    entries = dict(baseline["files"]) if baseline and baseline.get("version") == __version__ else {}
    for f in files:
        entries[str(f.relative_to(root))] = _sha256(f)
    data = {"version": __version__, "root": str(root), "scanned": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": entries}
    _baseline_file().write_text(json.dumps(data, indent=1), encoding="utf-8")
    return {"files": len(files), "version": __version__, "baseline": str(_baseline_file())}


def load_baseline() -> dict | None:
    p = _baseline_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def verify(file_path: str | None = None) -> dict:
    """Compare files with the baseline. Returns {status, modified, missing, checked, note}."""
    from .. import __version__

    base = load_baseline()
    if base is None:
        return {"status": "no-baseline", "modified": [], "missing": [], "checked": 0,
                "note": "No baseline yet — run scan_files first."}
    if base.get("version") != __version__:
        return {"status": "stale-baseline", "modified": [], "missing": [], "checked": 0,
                "note": f"Baseline is for {base.get('version')}, installed is {__version__} — run scan_files."}
    root = install_root()
    items = base["files"].items()
    if file_path:
        rel = str(resolve_in_root(file_path).relative_to(root))
        items = [(rel, base["files"].get(rel))]
    modified, missing = [], []
    for rel, digest in items:
        p = root / rel
        if digest is None:
            continue
        if not p.exists():
            missing.append(rel)
        elif _sha256(p) != digest:
            modified.append(rel)
    status = "ok" if not modified and not missing else "changed"
    return {"status": status, "modified": modified, "missing": missing, "checked": len(list(items)),
            "note": ""}


# ── backups of individual files ──────────────────────────────────────────────
def _backup_root() -> Path:
    custom = ""
    try:
        from ..config import load_config

        custom = load_config().medic.backup_dir
    except Exception:
        pass
    d = Path(custom).expanduser() if custom else medic_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_file(file_path: str) -> Path:
    src = resolve_in_root(file_path)
    if not src.is_file():
        raise FileNotFoundError(f"{file_path} does not exist")
    rel = src.relative_to(install_root())
    # Microsecond stamp: two backups in the same second must never overwrite
    # each other (recover() backs up the damaged file right before restoring).
    stamp = time.strftime("%Y%m%dT%H%M%S") + f".{int(time.time() * 1e6) % 1_000_000:06d}"
    dest = _backup_root() / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def list_file_backups() -> list[dict]:
    root = _backup_root()
    out = []
    for stamp in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        for f in stamp.rglob("*"):
            if f.is_file():
                out.append({"stamp": stamp.name, "file": str(f.relative_to(stamp)), "path": str(f),
                            "bytes": f.stat().st_size})
    return out


def recover(file_path: str, allow_main: bool = False) -> str:
    """Restore a file: newest local backup first, then GitHub at the version tag."""
    from .. import __version__

    target = resolve_in_root(file_path)
    rel = str(target.relative_to(install_root()))
    candidates = [b for b in list_file_backups() if b["file"] == rel]
    # Prefer a backup that matches the integrity baseline (the known-good
    # file); the newest backup may be a copy of the damaged file.
    base = load_baseline()
    want = (base or {}).get("files", {}).get(rel) if (base or {}).get("version") == __version__ else None
    if want:
        matching = [b for b in candidates if _sha256(Path(b["path"])) == want]
        candidates = matching + [b for b in candidates if b not in matching]
    good = Path(candidates[0]["path"]).read_bytes() if candidates else None
    if target.exists():
        backup_file(rel)  # keep whatever is there now, even if it is damaged

    if good is not None:
        target.write_bytes(good)
        return f"Restored {rel} from local backup {candidates[0]['stamp']}."

    repo = "adrianx26/zensynora"
    try:
        from ..config import load_config

        url = load_config().medic.repo_url.rstrip("/")
        if "github.com/" in url:
            repo = url.split("github.com/", 1)[1].removesuffix(".git")
    except Exception:
        pass
    import httpx

    refs = [f"v{__version__}"] + (["main"] if allow_main else [])
    for ref in refs:
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{rel}"
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True)
        except httpx.HTTPError as e:
            return f"Could not reach GitHub to recover {rel}: {e}"
        if r.status_code == 200:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(r.content)
            return f"Restored {rel} from GitHub ({repo}@{ref})."
    hint = "" if allow_main else " Pass allow_main=True to use the latest main branch instead."
    return f"No local backup of {rel} and no GitHub tag v{__version__} found.{hint}"
