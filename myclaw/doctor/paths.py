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
