"""Where things live, resolved the same way the updater does."""

from __future__ import annotations

from pathlib import Path

# Functions (not constants) so a changed HOME — tests, services — is respected.


def data_dir() -> Path:
    return Path.home() / ".myclaw"


def medic_dir() -> Path:
    d = data_dir() / "medic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file() -> Path:
    return data_dir() / "logs" / "zensynora.log"


def install_root() -> Path:
    """Folder that contains the running ``myclaw`` package (a release dir)."""
    return Path(__file__).resolve().parent.parent.parent


def install_base() -> Path | None:
    """``~/.local/share/zensynora``-style base if this is a managed/standard install."""
    root = install_root()
    if root.parent.name == "releases":
        return root.parent.parent
    if root.name == "source":
        return root.parent
    return None


# ── Running vs. installed version (v0.7.2) ───────────────────────────────────
# After `zensynora update` the `source` symlink points at the new release, but
# a process started earlier (or from a terminal whose PATH still holds the old
# release's venv) keeps running the old code. These helpers detect that.


def active_release() -> Path | None:
    """The release folder that ``<base>/source`` points at now, if managed."""
    base = install_base()
    if base is None:
        return None
    src = base / "source"
    return src.resolve() if src.exists() else None


def release_version(folder: Path) -> str | None:
    """Version from a release folder's pyproject.toml (no import needed)."""
    import re

    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def stale_release() -> dict | None:
    """``{"running": …, "installed": …, "folder": …}`` when this process runs
    an older release than the one installed now; otherwise None."""
    active = active_release()
    root = install_root()
    if active is None or active == root.resolve():
        return None
    from myclaw import __version__

    return {"running": __version__, "installed": release_version(active) or "?",
            "folder": str(active)}
