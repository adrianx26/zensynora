"""Paths, version helpers and the persistent updater state."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    tomllib = None


def default_base() -> Path:
    env = os.environ.get("ZENSYNORA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".local" / "share" / "zensynora"


def default_data_dir() -> Path:
    return Path.home() / ".myclaw"


def timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def read_version(source_dir: Path) -> str:
    """Version from pyproject.toml (falls back to myclaw/__init__.py)."""
    pyproject = source_dir / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        if tomllib is not None:
            try:
                return str(tomllib.loads(text)["project"]["version"])
            except Exception:
                pass
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        if m:
            return m.group(1)
    init = source_dir / "myclaw" / "__init__.py"
    if init.exists():
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return "0.0.0"


def version_tuple(v: str) -> tuple:
    parts = []
    for p in re.split(r"[.\-+]", v):
        parts.append(int(p) if p.isdigit() else -1)
    return tuple(parts + [0] * (4 - len(parts)))


@dataclass
class Layout:
    base: Path
    data_dir: Path

    @property
    def source(self) -> Path:
        return self.base / "source"

    @property
    def releases(self) -> Path:
        return self.base / "releases"

    @property
    def backups(self) -> Path:
        return self.base / "backups"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    @property
    def state_file(self) -> Path:
        return self.base / "updater-state.json"

    @property
    def lock_file(self) -> Path:
        return self.base / ".update.lock"

    def ensure_dirs(self) -> None:
        for d in (self.base, self.releases, self.backups, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.backups, 0o700)  # backups contain API keys

    def current_release_dir(self) -> Optional[Path]:
        """Real directory of the active release (None if not installed)."""
        if self.source.is_symlink():
            target = self.source.resolve()
            return target if target.is_dir() else None
        if self.source.is_dir():
            return self.source  # legacy in-place install (adopted on first update)
        return None

    def is_legacy(self) -> bool:
        return self.source.is_dir() and not self.source.is_symlink()


@dataclass
class State:
    path: Path
    current: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "State":
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return cls(path=path, current=raw.get("current"), history=raw.get("history", []))
            except (OSError, ValueError):
                pass
        return cls(path=path)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"current": self.current, "history": self.history}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def record(self, entry: Dict[str, Any]) -> None:
        self.history.append(entry)
        self.history = self.history[-50:]
