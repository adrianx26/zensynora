"""Node.js for building the Web UI (stdlib only).

The Web UI is built with Vite, which needs Node.js 20.19+ (Node 22 LTS is
used). ``ensure_node()`` returns a usable Node:

1. ZenSynora's own copy in ``BASE/node`` (``~/.local/share/zensynora/node``), or
2. a ``node``/``npm`` on PATH that is new enough, or
3. downloads the latest Node.js 22 LTS build for this OS/CPU from nodejs.org
   into ``BASE/node-v22.x.y-<os>-<arch>`` (checked against the published
   SHA-256 sums) and points ``BASE/node`` at it.

No sudo and no system packages are needed, so it works when the update runs
from the Web UI too. ``python -m updater.nodejs`` does the same from shell
scripts (install.sh) and prints the folder that holds ``node`` and ``npm``.

2026-10-21: removing another app (Hermes) removed the only node/npm on the
computer; every update then reused the old Web UI build.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("zensynora.updater")

NODE_MAJOR = 22
MIN_VERSION = (20, 19, 0)  # Vite 8
DIST = "https://nodejs.org/dist"
TIMEOUT = 60


def _version(node: Path) -> Optional[Tuple[int, int, int]]:
    try:
        out = subprocess.run([str(node), "--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", out.strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def _usable(bin_dir: Path) -> Optional[Tuple[int, int, int]]:
    node, npm = bin_dir / "node", bin_dir / "npm"
    if not (node.exists() and npm.exists()):
        return None
    v = _version(node)
    return v if v and v >= MIN_VERSION else None


def platform_tag() -> Optional[str]:
    """'linux-x64', 'darwin-arm64' … as used in nodejs.org file names; None if unsupported."""
    system = platform.system().lower()
    if system not in ("linux", "darwin"):
        return None
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64",
            "armv7l": "armv7l", "ppc64le": "ppc64le", "s390x": "s390x"}.get(machine)
    return f"{system}-{arch}" if arch else None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "zensynora-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def latest_release(tag: str, fetch: Callable[[str], bytes] = _fetch) -> Tuple[str, str, str]:
    """(version, file name, sha256) of the newest Node 22 build for this platform."""
    sums = fetch(f"{DIST}/latest-v{NODE_MAJOR}.x/SHASUMS256.txt").decode("utf-8", "replace")
    found: Dict[str, Tuple[str, str]] = {}
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts
        m = re.fullmatch(rf"node-(v{NODE_MAJOR}\.\d+\.\d+)-{re.escape(tag)}\.tar\.(xz|gz)", name)
        if m:
            found[m.group(2)] = (m.group(1), name + " " + digest)
    pick = found.get("xz") if _has_lzma() else None
    pick = pick or found.get("gz")
    if not pick:
        raise RuntimeError(f"nodejs.org has no Node.js {NODE_MAJOR} build for {tag}")
    version, rest = pick
    name, digest = rest.split()
    return version, name, digest


def _has_lzma() -> bool:
    try:
        import lzma  # noqa: F401
        return True
    except ImportError:
        return False


def _safe_members(tar: tarfile.TarFile, dest: Path):
    root = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError(f"unsafe path in the Node.js archive: {m.name}")
        yield m


def download(base: Path, fetch: Callable[[str], bytes] = _fetch) -> Path:
    """Download + verify + unpack Node 22 into BASE; point BASE/node at it. Returns BASE/node/bin."""
    tag = platform_tag()
    if tag is None:
        raise RuntimeError(f"no Node.js download for {platform.system()} {platform.machine()}")
    version, name, digest = latest_release(tag, fetch)
    base.mkdir(parents=True, exist_ok=True)
    folder = base / name.split(".tar.")[0]
    if not _usable(folder / "bin"):
        log.info("  downloading Node.js %s for %s (about 30 MB)…", version, tag)
        data = fetch(f"{DIST}/latest-v{NODE_MAJOR}.x/{name}")
        got = hashlib.sha256(data).hexdigest()
        if got != digest:
            raise RuntimeError(f"Node.js download is damaged (sha256 {got[:12]}… ≠ {digest[:12]}…)")
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            archive = Path(tmp) / name
            archive.write_bytes(data)
            unpack = Path(tmp) / "x"
            unpack.mkdir()
            with tarfile.open(archive) as tar:
                extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tar.extractall(unpack, members=list(_safe_members(tar, unpack)), **extra)
            inner = unpack / folder.name
            if not (inner / "bin" / "node").exists():
                raise RuntimeError("the Node.js archive has an unexpected layout")
            if folder.exists():
                shutil.rmtree(folder)
            inner.rename(folder)
    link = base / "node"
    tmp_link = base / ".node.tmp"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    tmp_link.symlink_to(folder.name)
    os.replace(tmp_link, link)  # atomic switch
    # keep only the active copy
    for old in base.glob(f"node-v{NODE_MAJOR}.*-{tag}"):
        if old != folder and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    v = _usable(link / "bin")
    if not v:
        raise RuntimeError("the downloaded Node.js does not run on this computer")
    log.info("  Node.js %s ready in %s", ".".join(map(str, v)), folder)
    return link / "bin"


def find_node(base: Path) -> Optional[Path]:
    """Folder with a new-enough node + npm: ZenSynora's own copy first, then PATH."""
    own = base / "node" / "bin"
    if _usable(own):
        return own
    npm = shutil.which("npm")
    if npm:
        bin_dir = Path(npm).resolve().parent
        if _usable(bin_dir):
            return bin_dir
        node = shutil.which("node")
        if node and _usable(Path(node).resolve().parent):
            return Path(node).resolve().parent
    return None


def ensure_node(base: Path, allow_download: bool = True,
                fetch: Callable[[str], bytes] = _fetch) -> Tuple[Optional[Path], str]:
    """(folder with node + npm, note). The note says what happened when there is no usable Node."""
    found = find_node(base)
    if found:
        return found, ""
    on_path = shutil.which("node")
    old = _version(Path(on_path)) if on_path else None
    why = (f"Node.js {'.'.join(map(str, old))} is too old (the Web UI needs {'.'.join(map(str, MIN_VERSION))}+)"
           if old else "Node.js/npm not found")
    if not allow_download:
        return None, why
    try:
        return download(base, fetch), ""
    except Exception as e:  # network down, unsupported CPU …
        return None, f"{why}, and Node.js could not be downloaded: {e}"


def node_env(bin_dir: Path, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment with this Node first on PATH (npm scripts start `node` by name)."""
    env = dict(env if env is not None else os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def main(argv: Optional[list] = None) -> int:
    """`python -m updater.nodejs [--base DIR] [--no-download]` → prints the bin folder."""
    import argparse

    from .layout import default_base

    p = argparse.ArgumentParser(description="Find or install Node.js 22 for building the ZenSynora Web UI.")
    p.add_argument("--base", help="ZenSynora program folder (default ~/.local/share/zensynora)")
    p.add_argument("--no-download", action="store_true", help="only look, never download")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base = Path(args.base).expanduser() if args.base else default_base()
    bin_dir, note = ensure_node(base, allow_download=not args.no_download)
    if bin_dir is None:
        print(note, file=__import__("sys").stderr)
        return 1
    print(bin_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
