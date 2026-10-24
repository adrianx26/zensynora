"""What ingestion may read and what it must never store (KB phase 5, 2026-10-24).

* :func:`check_local_path` — local files/folders only inside the agent
  workspace or the user's home, never in hidden folders (``~/.ssh``,
  ``~/.myclaw`` settings, ``~/.aws``…). The allow-list is read at call time.
* :func:`is_secret_file` / :func:`skip_dir` — key files, ``.env``,
  credentials and tool folders (``.git``, ``node_modules``…) are never read.
* :func:`redact` — tokens, API keys, passwords and private keys in the text
  become ``[redacted]`` (the doctor's log redaction plus private-key blocks
  and cloud keys).
* :class:`GitIgnore` — the ``.gitignore`` of a folder (a small subset of the
  syntax: ``*``, ``**``, ``dir/``, ``/anchored``; ``!`` re-includes are
  ignored, so at worst a file is left out).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List, Optional

from .model import IngestError

# Folders never walked into (besides every dot-folder).
SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", "env", "site-packages", "dist", "build",
    "target", "vendor", "third_party", "bower_components", "coverage", "htmlcov",
    "__MACOSX",
})

_SECRET_NAMES = (
    ".env", ".env.*", "*.env", ".envrc", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
    "*.keystore", "*.kdbx", "*.gpg", "*.asc", "id_rsa*", "id_dsa*", "id_ecdsa*",
    "id_ed25519*", "*.ppk", "credentials*", "*credentials.json", "secrets.*", "*secret*.json",
    "*secrets*.y*ml", ".netrc", "_netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials",
    "*.tfstate", "*.tfstate.*", "token.json", "*.token", "service-account*.json",
    "known_hosts", "authorized_keys", "*.sqlite", "*.db", "shadow", "passwd",
)


def is_secret_file(name: str) -> bool:
    """True for files that typically hold keys, passwords or tokens."""
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pat) for pat in _SECRET_NAMES)


def skip_dir(name: str) -> bool:
    """Folders that are never read: hidden ones and tool/build folders."""
    return name.startswith(".") or name in SKIP_DIRS or name.endswith(".egg-info")


# ── redaction ────────────────────────────────────────────────────────────────
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.DOTALL)
_EXTRA = [
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),                   # GitHub fine-grained token
    re.compile(r"\bxapp-[A-Za-z0-9-]{20,}"),                          # Slack app token
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),                             # Hugging Face
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{20,}"),               # Stripe
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),                            # npm
]


def redact(text: str) -> str:
    """Hide anything that looks like a secret (``[redacted]``)."""
    if not text:
        return text
    from myclaw.doctor.logscan import redact as _log_redact  # the doctor's patterns

    text = _PRIVATE_KEY.sub("[redacted private key]", text)
    for rx in _EXTRA:
        text = rx.sub("[redacted]", text)
    return _log_redact(text)


# ── local paths ──────────────────────────────────────────────────────────────
def allowed_roots() -> List[Path]:
    """Where agents may read local files from: the workspace and the home folder."""
    from myclaw.tools import core as _core  # read at call time (tests change it)

    roots = [Path(_core.WORKSPACE).expanduser(), Path.home()]
    out: List[Path] = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def check_local_path(raw: str) -> Path:
    """The resolved path if ingestion may read it, else :class:`IngestError`.

    Allowed: anything inside the agent workspace, and anything inside the
    home folder that is not in (or itself) a hidden file/folder. Symlinks are
    resolved first, so a link cannot point outside.
    """
    if not raw or "\x00" in raw:
        raise IngestError("Give a file or folder path (for example ~/Documents/report.pdf).")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        from myclaw.tools import core as _core

        p = Path(_core.WORKSPACE) / p  # relative paths are relative to the workspace
    try:
        target = p.resolve()
    except (OSError, RuntimeError) as e:
        raise IngestError(f"Cannot read '{raw}': {e}.") from e
    # the rules first, then existence: nothing is said about files outside
    roots = allowed_roots()
    workspace, home = (roots + [None, None])[:2]
    if workspace is not None and _within(target, workspace):
        return _existing(target, raw)
    if home is not None and _within(target, home):
        rel = target.relative_to(home)
        if not any(part.startswith(".") for part in rel.parts):
            if is_secret_file(target.name):
                raise IngestError(f"'{raw}' looks like a file with keys or passwords; it is never "
                                  "imported into the knowledge base.")
            return _existing(target, raw)
        raise IngestError(
            f"'{raw}' is in a hidden folder ({'/'.join(rel.parts[:1])}); hidden folders such as "
            "~/.ssh or ZenSynora's settings are never read. Copy the files to a normal folder "
            "first.")
    raise IngestError(
        f"'{raw}' is outside the folders ZenSynora may read (your home folder and the agent "
        "workspace). Copy it into your home folder first.")


def _existing(target: Path, raw: str) -> Path:
    if not target.exists():
        raise IngestError(f"'{raw}' does not exist. Check the path (relative paths start in the "
                          "agent workspace; use ~/… for your home folder).")
    return target


def _within(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


# ── .gitignore ───────────────────────────────────────────────────────────────
class GitIgnore:
    """Patterns of a folder's top-level ``.gitignore``."""

    def __init__(self, root: Path):
        self.patterns: List[tuple] = []  # (pattern, dir_only, anchored)
        try:
            text = (root / ".gitignore").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines()[:2000]:
            line = line.strip()
            if not line or line.startswith(("#", "!")):
                continue
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/") or "/" in line
            self.patterns.append((line.lstrip("/"), dir_only, anchored))

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        """``rel`` is a path relative to the folder, with '/' separators."""
        if not self.patterns:
            return False
        name = rel.rsplit("/", 1)[-1]
        for pat, dir_only, anchored in self.patterns:
            if dir_only and not is_dir:
                continue
            if anchored:
                if _glob(rel, pat) or _glob(rel, pat + "/**"):
                    return True
            elif fnmatch.fnmatchcase(name, pat):
                return True
        return False


def _glob(path: str, pat: str) -> bool:
    rx = _glob_cache.get(pat)
    if rx is None:
        parts: List[str] = []
        i = 0
        while i < len(pat):
            if pat.startswith("**/", i):
                parts.append(r"(?:.*/)?")
                i += 3
            elif pat.startswith("**", i):
                parts.append(r".*")
                i += 2
            elif pat[i] == "*":
                parts.append(r"[^/]*")
                i += 1
            elif pat[i] == "?":
                parts.append(r"[^/]")
                i += 1
            else:
                parts.append(re.escape(pat[i]))
                i += 1
        rx = re.compile("^" + "".join(parts) + "$")
        _glob_cache[pat] = rx
    return bool(rx.match(path))


_glob_cache: dict = {}


def safe_relpath(name: str) -> Optional[str]:
    """A member name of an archive as a safe relative path, or None.

    No absolute paths, no '..', no hidden parts — zip entries cannot write
    outside the target folder.
    """
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return None
    if re.match(r"^[A-Za-z]:$", parts[0]):
        return None
    return "/".join(parts)
