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
import ipaddress
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
    "*.tfstate", "*.tfstate.*", "*.tfvars", "*.tfvars.json", "token.json", "*.token",
    "service-account*.json", "known_hosts", "authorized_keys", "*.sqlite", "*.db", "shadow",
    "passwd", "kubeconfig", "*.kubeconfig", "kube.config", "wp-config.php", "appsettings*.json",
    ".htpasswd", "htpasswd", ".dockercfg", ".docker-config.json", "keychain*", "*.keychain*",
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
# 2026-10-24 (KB phase 5, review): any "<name containing secret/pass/token/key…> = value"
# or ': value' — quoted values may contain spaces and symbols. Values that are
# code (a call, an index, a variable reference) and plain booleans are left alone.
_SECRET_NAME = (r"(?:[\w.-]*[_.-])?(?:secret|passw(?:or)?d|pass|pwd|token|api_?key|apikey|"
                r"access_?key|access_?token|auth_?token|refresh_?token|bot_?token|private_?key|"
                r"client_?secret|credentials?)(?:[_.-]?(?:key|value|id|hash|b64|base64|string))?")
_GENERIC_KV = re.compile(
    r"(?i)(?<![\w-])(" + _SECRET_NAME + r"[\"']?\s*(?:=|:|=>)\s*)"
    r"(?:\"([^\"\n]{3,})\"|'([^'\n]{3,})'|([^\s\"',;}{()\[\]<>]{4,})(?![\w(\[]))")
_NOT_SECRET = {"true", "false", "null", "none", "nil", "yes", "no", "required", "optional",
               "string", "str", "int", "bool", "[redacted]", "redacted", "changeme", "xxx", "****"}
# ?token=… / &api_key=… in URLs
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_?token|refresh_?token|id_?token|token|api_?key|apikey|key|secret|"
    r"client_?secret|password|passwd|pwd|sig|signature|x-amz-signature|x-amz-credential|"
    r"x-amz-security-token|auth|session|sessionid|code)=)[^&#\s\"'<>]+")


def _kv(m: "re.Match[str]") -> str:
    value = m.group(2) or m.group(3) or m.group(4) or ""
    if value.lower() in _NOT_SECRET or value.startswith(("$", "%", "{{", "<", "os.", "env.",
                                                         "process.env", "[redacted")):
        return m.group(0)
    quote = '"' if m.group(2) is not None else ("'" if m.group(3) is not None else "")
    return f"{m.group(1)}{quote}[redacted]{quote}"


def redact(text: str) -> str:
    """Hide anything that looks like a secret (``[redacted]``)."""
    if not text:
        return text
    from myclaw.doctor.logscan import redact as _log_redact  # the doctor's patterns

    text = _PRIVATE_KEY.sub("[redacted private key]", text)
    for rx in _EXTRA:
        text = rx.sub("[redacted]", text)
    text = _QUERY_SECRET.sub(lambda m: m.group(1) + "[redacted]", text)
    text = _log_redact(text)
    return _GENERIC_KV.sub(_kv, text)


# ── URLs ─────────────────────────────────────────────────────────────────────
_SENSITIVE_PARAMS = {
    "token", "access_token", "accesstoken", "refresh_token", "id_token", "key", "api_key",
    "apikey", "api-key", "sig", "signature", "secret", "client_secret", "password", "passwd",
    "pwd", "auth", "authorization", "session", "sessionid", "session_id", "code", "jwt",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "x-goog-signature",
    "x-goog-credential", "sv", "se", "sp",  # Azure SAS
}


def clean_url(url: str) -> str:
    """``url`` without user:password and without sensitive query parameters.

    2026-10-24 (KB phase 5, review): every source URL that is stored in a note,
    shown or logged goes through this.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return redact(url)
    if parts.scheme not in ("http", "https"):
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "") if parts.netloc else ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _SENSITIVE_PARAMS and not k.lower().endswith(
                 ("token", "secret", "password", "_key", "-key", "signature"))]
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))


def clean_source(source: str) -> str:
    """A source as it may be shown: URLs cleaned, other text redacted."""
    s = (source or "").strip()
    if re.match(r"^https?://", s, re.I):
        return clean_url(s)
    return redact(s)


def ip_is_public(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Only globally routable addresses (also the IPv4 inside mapped/NAT64/6to4 IPv6)."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in ipaddress.IPv6Network("64:ff9b::/96"):
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None and not ip_is_public(inner):
            return False
    return bool(ip.is_global) and not ip.is_multicast


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


def _hidden_part(target: Path, roots: List[Path]) -> Optional[str]:
    """The first hidden path part below the nearest root (or anywhere when outside them)."""
    base: Optional[Path] = None
    for r in roots:
        if _within(target, r) and (base is None or len(r.parts) > len(base.parts)):
            base = r
    rel = target.relative_to(base).parts if base is not None else target.parts[1:]
    return next((p for p in rel if p.startswith(".")), None)


def check_policy(target: Path, raw: str) -> None:
    """No hidden files/folders, no secret files — for every caller (agents,
    Web UI and the command line alike).

    2026-10-24 (KB phase 5, review): the workspace and CLI paths skipped
    these checks before.
    """
    hidden = _hidden_part(target, allowed_roots())
    if hidden:
        raise IngestError(
            f"'{raw}' is in a hidden file or folder ({hidden}); hidden folders such as ~/.ssh or "
            "ZenSynora's settings are never read. Copy the files to a normal folder first.")
    if is_secret_file(target.name):
        raise IngestError(f"'{raw}' looks like a file with keys or passwords; it is never "
                          "imported into the knowledge base.")


def check_local_path(raw: str, trusted: bool = False) -> Path:
    """The resolved path if ingestion may read it, else :class:`IngestError`.

    Allowed: inside the agent workspace or the home folder (``trusted`` — the
    command line, Web UI uploads — also elsewhere), never a hidden file or
    folder or a secret file. Symlinks are resolved first, so a link cannot
    point around the rules. The rules are checked before existence, so nothing
    is revealed about files that may not be read.
    """
    if not raw or "\x00" in raw:
        raise IngestError("Give a file or folder path (for example ~/Documents/report.pdf).")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        from myclaw.tools import core as _core

        p = (Path.cwd() if trusted else Path(_core.WORKSPACE)) / p
    try:
        target = p.resolve()
    except (OSError, RuntimeError) as e:
        raise IngestError(f"Cannot read '{raw}': {e}.") from e
    if not trusted and not any(_within(target, r) for r in allowed_roots()):
        raise IngestError(
            f"'{raw}' is outside the folders ZenSynora may read (your home folder and the agent "
            "workspace). Copy it into your home folder first.")
    check_policy(target, raw)
    return _existing(target, raw)


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
