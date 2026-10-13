"""The individual update steps. Order is defined in ``default_pipeline()``."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import List

from . import UPDATER_PROTOCOL
from .backup import RELEASE_USER_FILES, _safe_extract, create_backup, restore_backup
from .layout import read_version, version_tuple
from .pipeline import Context, NothingToDo, Step, UpdateError, clean_env, sh

log = logging.getLogger("zensynora.updater")

COPY_IGNORE = shutil.ignore_patterns(
    ".git", "venv", ".venv", "node_modules", "__pycache__", "*.pyc", ".pytest_cache",
    ".mypy_cache", "graphify-out", ".update.lock",
)
MIN_FREE_BYTES = 1_500_000_000


class Handoff(Exception):
    """A newer updater was downloaded; re-run it on the staged source."""

    def __init__(self, staged: Path):
        super().__init__(str(staged))
        self.staged = staged


def build_python() -> str:
    """System interpreter used to create the new venv (never a venv python)."""
    for cand in (os.environ.get("ZENSYNORA_PYTHON"), shutil.which("python3")):
        if cand and Path(cand).exists():
            prefix = sh([cand, "-c", "import sys; print(sys.prefix == sys.base_prefix)"], check=False)
            if prefix.returncode == 0 and prefix.stdout.strip() == "True":
                return cand
    return getattr(sys, "_base_executable", sys.executable)


def venv_python(release: Path) -> Path:
    return release / "venv" / "bin" / "python"


# ── 1. Preflight ─────────────────────────────────────────────────────────────
class Preflight(Step):
    name = "preflight"
    description = "Checking the current installation"

    def run(self, ctx: Context) -> None:
        lay = ctx.layout
        lay.ensure_dirs()
        cur = lay.current_release_dir()
        if cur is None:
            raise UpdateError(
                f"No installation found at {lay.source}. Install first with install-online.sh."
            )
        if (cur / ".git").exists() and not ctx.opts.force:
            raise UpdateError(
                f"{cur} is a git checkout. Update it with `git pull` + install.sh, "
                "or pass --force to convert it into a managed install."
            )
        ctx.current_dir = cur
        ctx.current_version = read_version(cur)
        log.info("  installed: %s (%s)", ctx.current_version, cur)

        py = build_python()
        ver = sh([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]).stdout.strip()
        if version_tuple(ver) < version_tuple("3.11"):
            raise UpdateError(f"Python {ver} found; ZenSynora needs 3.11 or newer.")
        ctx.extra["build_python"] = py

        free = shutil.disk_usage(lay.base).free
        if free < MIN_FREE_BYTES:
            raise UpdateError(
                f"Only {free // 1_000_000} MB free in {lay.base}; need about "
                f"{MIN_FREE_BYTES // 1_000_000} MB for a side-by-side update."
            )

        if ctx.opts.manage_service and shutil.which("systemctl"):
            r = sh(["systemctl", "is-active", "--quiet", "myclaw"], check=False)
            ctx.service_was_active = r.returncode == 0
            if ctx.service_was_active:
                log.info("  systemd service 'myclaw' is running (will be restarted)")


# ── 2. Fetch the new release ─────────────────────────────────────────────────
class FetchRelease(Step):
    name = "fetch"
    description = "Getting the new version"

    def run(self, ctx: Context) -> None:
        lay = ctx.layout
        incoming = lay.releases / f".incoming-{ctx.stamp}"
        ctx.extra["incoming"] = incoming
        kind, value = ctx.opts.source_kind, ctx.opts.source_value

        if kind == "dir":
            src = Path(value).expanduser().resolve()
            if not (src / "pyproject.toml").exists():
                raise UpdateError(f"{src} does not look like a ZenSynora source tree")
            shutil.copytree(src, incoming, ignore=COPY_IGNORE, symlinks=True)
        else:
            if kind == "git":
                url = f"https://codeload.github.com/{ctx.opts.repo}/tar.gz/{value}"
                log.info("  downloading %s", url)
                tmp = Path(tempfile.mkstemp(suffix=".tar.gz")[1])
                try:
                    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
                        shutil.copyfileobj(resp, out)
                    self._extract(tmp, incoming)
                finally:
                    tmp.unlink(missing_ok=True)
            elif kind == "archive":
                self._extract(Path(value).expanduser(), incoming)
            else:
                raise UpdateError(f"Unknown source kind: {kind}")

        if not (incoming / "pyproject.toml").exists() or not (incoming / "myclaw").is_dir():
            raise UpdateError("Downloaded source is incomplete (pyproject.toml / myclaw missing)")

        new_version = read_version(incoming)
        cur_v = version_tuple(ctx.current_version or "0")
        new_v = version_tuple(new_version)
        if new_v == cur_v and not ctx.opts.force:
            raise NothingToDo(f"Already on {new_version}. Use --force to reinstall it.")
        if new_v < cur_v and not (ctx.opts.allow_downgrade or ctx.opts.force):
            raise UpdateError(
                f"Source is older ({new_version}) than installed ({ctx.current_version}). "
                "Use --allow-downgrade to install it anyway (or `rollback`)."
            )
        ctx.new_version = new_version
        ctx.new_id = f"{new_version}-{ctx.stamp}"
        ctx.new_dir = lay.releases / ctx.new_id
        os.rename(incoming, ctx.new_dir)
        log.info("  new version: %s -> %s", ctx.current_version, new_version)

    @staticmethod
    def _extract(archive: Path, dest: Path) -> None:
        tmp = dest.parent / (dest.name + ".x")
        tmp.mkdir(parents=True)
        try:
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as z:
                    for name in z.namelist():
                        target = (tmp / name).resolve()
                        if not str(target).startswith(str(tmp.resolve())):
                            raise UpdateError(f"Unsafe path in archive: {name}")
                    z.extractall(tmp)
            else:
                with tarfile.open(archive) as tar:
                    _safe_extract(tar, tmp)
            entries = [p for p in tmp.iterdir()]
            root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
            os.rename(root, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def rollback(self, ctx: Context) -> None:
        if ctx.extra.get("keep_staged"):
            return
        for p in (ctx.extra.get("incoming"), ctx.new_dir):
            if p and Path(p).exists():
                shutil.rmtree(p, ignore_errors=True)


# ── 2b. Let a newer updater take over ────────────────────────────────────────
class HandoffIfNewer(Step):
    name = "handoff"
    description = "Checking for a newer updater"

    def should_run(self, ctx: Context) -> bool:
        return not ctx.extra.get("no_handoff")

    def run(self, ctx: Context) -> None:
        init = ctx.new_dir / "updater" / "__init__.py"
        if not init.exists():
            return
        ns: dict = {}
        try:
            exec(compile(init.read_text(encoding="utf-8"), str(init), "exec"), ns)
        except Exception:
            return
        if int(ns.get("UPDATER_PROTOCOL", 0)) > UPDATER_PROTOCOL:
            log.info("  the new version ships a newer updater — handing over to it")
            ctx.extra["keep_staged"] = True
            raise Handoff(ctx.new_dir)


# ── 3. Back up user data ─────────────────────────────────────────────────────
class BackupUserData(Step):
    name = "backup"
    description = "Backing up your settings and data (~/.myclaw)"

    def run(self, ctx: Context) -> None:
        ctx.backup_dir = create_backup(
            ctx.layout,
            f"{ctx.current_version}-to-{ctx.new_version}",
            release_dir=ctx.current_dir,
            extra_manifest={
                "from_version": ctx.current_version,
                "to_version": ctx.new_version,
                "from_release": str(ctx.current_dir),
            },
        )
    # No rollback: a backup is never deleted automatically on failure.


# ── 4. Carry user files from the old release into the new one ───────────────
class CarryOverUserFiles(Step):
    name = "carry-over"
    description = "Copying your release-local files (.env, data/)"

    def run(self, ctx: Context) -> None:
        for name in RELEASE_USER_FILES:
            src = ctx.current_dir / name
            if not src.exists():
                continue
            dst = ctx.new_dir / name
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst)
            log.info("  kept %s", name)


# ── 5. Build: venv, Python packages, web UI ──────────────────────────────────
class BuildRelease(Step):
    name = "build"
    description = "Installing the new version side by side (old one keeps working)"

    def run(self, ctx: Context) -> None:
        new = ctx.new_dir
        py = ctx.extra.get("build_python") or build_python()
        sh([py, "-m", "venv", new / "venv"])
        vpy = venv_python(new)
        pip = [vpy, "-m", "pip", "--disable-pip-version-check"]
        sh(pip + ["install", "--quiet", "--upgrade", "pip", "setuptools", "wheel"])
        log.info("  installing Python packages (can take a few minutes)…")
        sh(pip + ["install", "--quiet", "-r", new / "requirements.txt"], timeout=3600)
        sh(pip + ["install", "--quiet", "-e", new], timeout=1800)
        self._keep_optional_packages(ctx, pip)
        if not ctx.opts.skip_webui:
            self._build_webui(ctx)

    def _keep_optional_packages(self, ctx: Context, pip: List) -> None:
        """Re-install extras the user added to the old venv (anthropic, playwright…)."""
        old_py = venv_python(ctx.current_dir)
        if not old_py.exists():
            return
        try:
            old = json.loads(sh([old_py, "-m", "pip", "list", "--format=json", "--not-required"],
                                check=False).stdout or "[]")
            new = json.loads(sh(pip + ["list", "--format=json"]).stdout or "[]")
        except ValueError:
            return
        have = {p["name"].lower() for p in new}
        missing = [p["name"] for p in old
                   if p["name"].lower() not in have and p["name"].lower() not in ("zensynora", "pip")]
        if not missing:
            return
        log.info("  re-installing your extra packages: %s", ", ".join(missing))
        r = sh(pip + ["install", "--quiet", *missing], check=False, timeout=1800)
        if r.returncode != 0:
            for name in missing:
                if sh(pip + ["install", "--quiet", name], check=False).returncode != 0:
                    ctx.note(f"Could not re-install optional package '{name}' — install it manually.")

    def _build_webui(self, ctx: Context) -> None:
        webui = ctx.new_dir / "webui"
        if not (webui / "package.json").exists():
            return
        npm = shutil.which("npm")
        if npm:
            log.info("  building the web UI…")
            install = [npm, "ci"] if (webui / "package-lock.json").exists() else [npm, "install"]
            r = sh(install + ["--no-fund", "--no-audit"], cwd=webui, check=False, timeout=1800)
            if r.returncode == 0:
                r = sh([npm, "run", "build"], cwd=webui, check=False, timeout=900)
            if r.returncode == 0 and (webui / "dist" / "index.html").exists():
                shutil.rmtree(webui / "node_modules", ignore_errors=True)  # 100+ MB, not needed at runtime
                return
            ctx.note("Web UI build failed; reusing the previous build.")
        else:
            ctx.note("npm not found; reusing the previous web UI build.")
        old_dist = ctx.current_dir / "webui" / "dist"
        if old_dist.exists() and not (webui / "dist").exists():
            shutil.copytree(old_dist, webui / "dist")


# ── 6. Verify the new release before switching ───────────────────────────────
VERIFY_IMPORTS = [
    "telegram", "httpx", "h2", "fastapi", "uvicorn", "websockets", "openai",
    "yaml", "aiosqlite", "pydantic", "numpy", "mcp",
    "myclaw", "myclaw.cli", "myclaw.web.api",
]


class VerifyRelease(Step):
    name = "verify"
    description = "Checking the new version works with your settings"

    def run(self, ctx: Context) -> None:
        vpy = venv_python(ctx.new_dir)
        code = (
            "import importlib, sys, myclaw\n"
            f"mods = {VERIFY_IMPORTS!r}\n"
            "bad = []\n"
            "for m in mods:\n"
            "    try: importlib.import_module(m)\n"
            "    except Exception as e: bad.append(f'{m}: {e}')\n"
            "from myclaw.config import load_config\n"
            "load_config()  # validates YOUR config file against the new schema\n"
            "print('VERSION', myclaw.__version__)\n"
            "print('FROM', myclaw.__file__)\n"
            "print('BAD', bad)\n"
            "sys.exit(1 if bad else 0)\n"
        )
        r = sh([vpy, "-c", code], cwd=Path("/"), check=False, timeout=300)
        out = r.stdout or ""
        if r.returncode != 0:
            raise UpdateError("New version failed its self-check:\n" + "\n".join(out.splitlines()[-10:]))
        if f"FROM {ctx.new_dir}" not in out:
            raise UpdateError(f"Self-check imported code from the wrong place:\n{out[-300:]}")
        if f"VERSION {ctx.new_version}" not in out:
            raise UpdateError(f"New release reports an unexpected version:\n{out[-300:]}")
        cli = ctx.new_dir / "venv" / "bin" / "zensynora"
        sh([cli, "--help"], cwd=Path("/"), timeout=120)
        log.info("  imports, config and CLI OK")
        if ctx.opts.run_tests:
            if sh([vpy, "-c", "import pytest"], check=False).returncode != 0:
                sh([vpy, "-m", "pip", "install", "--quiet", "pytest", "pytest-asyncio"], check=False)
            log.info("  running the test suite…")
            sh([vpy, "-m", "pytest", "-q", "-x", "-o", "addopts=", "-p", "no:cacheprovider"],
               cwd=ctx.new_dir, timeout=1800)


# ── 7. Data migrations ───────────────────────────────────────────────────────
class MigrateUserData(Step):
    name = "migrate"
    description = "Applying data migrations"

    def run(self, ctx: Context) -> None:
        vpy = venv_python(ctx.new_dir)
        ctx.extra["migrate_attempted"] = True
        r = sh(
            [vpy, "-m", "updater", "migrate", "--data-dir", ctx.layout.data_dir,
             "--app-version", ctx.new_version, "--json"],
            cwd=ctx.new_dir, timeout=1800, env=clean_env(PYTHONPATH=str(ctx.new_dir)),
        )
        try:
            result = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise UpdateError(f"Migration runner returned unexpected output:\n{r.stdout[-500:]}")
        ctx.migrations_applied = result.get("applied", [])
        if ctx.migrations_applied:
            log.info("  applied: %s", ", ".join(ctx.migrations_applied))
        else:
            log.info("  no migrations needed")

    def rollback(self, ctx: Context) -> None:
        if ctx.extra.get("migrate_attempted") and ctx.backup_dir:
            log.info("  restoring your data from the pre-update backup")
            restore_backup(ctx.layout, ctx.backup_dir, take_safety_copy=False)


# ── 8. Stop the service ──────────────────────────────────────────────────────
class StopService(Step):
    name = "stop-service"
    description = "Stopping the background service"

    def should_run(self, ctx: Context) -> bool:
        return ctx.service_was_active

    def run(self, ctx: Context) -> None:
        r = sh(["sudo", "-n", "systemctl", "stop", "myclaw"], check=False)
        if r.returncode != 0:
            ctx.service_was_active = False
            ctx.note("Could not stop the 'myclaw' service without a password; "
                     "restart it yourself: sudo systemctl restart myclaw")
        else:
            ctx.extra["service_stopped"] = True

    def rollback(self, ctx: Context) -> None:
        if ctx.extra.get("service_stopped"):
            sh(["sudo", "-n", "systemctl", "start", "myclaw"], check=False)


# ── 9. Switch to the new release (atomic) ────────────────────────────────────
def point_source_to(layout, release_dir: Path) -> None:
    tmp = layout.base / ".source.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.relpath(release_dir, layout.base), tmp)
    os.replace(tmp, layout.source)


class ActivateRelease(Step):
    name = "activate"
    description = "Switching to the new version"

    def run(self, ctx: Context) -> None:
        lay = ctx.layout
        if lay.is_legacy():
            # First managed update: keep the old in-place install as a release
            # so it stays available for rollback.
            legacy_id = f"{ctx.current_version}-legacy-{ctx.stamp}"
            legacy_dir = lay.releases / legacy_id
            os.rename(lay.source, legacy_dir)
            ctx.adopted_legacy_as = legacy_id
            ctx.current_dir = legacy_dir
            log.info("  previous install kept as releases/%s", legacy_id)
        point_source_to(lay, ctx.new_dir)
        ctx.extra["activated"] = True

    def rollback(self, ctx: Context) -> None:
        lay = ctx.layout
        if ctx.adopted_legacy_as:
            legacy = lay.releases / ctx.adopted_legacy_as
            if lay.source.is_symlink():
                lay.source.unlink()
            if legacy.exists() and not lay.source.exists():
                os.rename(legacy, lay.source)
        elif ctx.extra.get("activated") and ctx.current_dir:
            point_source_to(lay, ctx.current_dir)


# ── 10. Start the service again and confirm it stays up ──────────────────────
class StartService(Step):
    name = "start-service"
    description = "Starting the background service"

    def should_run(self, ctx: Context) -> bool:
        return bool(ctx.extra.get("service_stopped"))

    def run(self, ctx: Context) -> None:
        sh(["sudo", "-n", "systemctl", "start", "myclaw"])
        time.sleep(5)
        if sh(["systemctl", "is-active", "--quiet", "myclaw"], check=False).returncode != 0:
            raise UpdateError("The service did not stay up with the new version "
                              "(see: journalctl -u myclaw -n 50)")


# ── 11. Record the result and clean up old releases/backups ──────────────────
class Finalize(Step):
    name = "finalize"
    description = "Recording the update and cleaning up old versions"

    def run(self, ctx: Context) -> None:
        st = ctx.state
        from_id = ctx.adopted_legacy_as or (ctx.current_dir.name if ctx.current_dir else None)
        st.current = ctx.new_id
        st.record({
            "action": "update",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "id": ctx.new_id,
            "version": ctx.new_version,
            "from_id": from_id,
            "from_version": ctx.current_version,
            "backup": ctx.backup_dir.name if ctx.backup_dir else None,
            "migrations": ctx.migrations_applied,
            "notes": ctx.notes,
        })
        st.save()
        prune(ctx.layout, st, ctx.opts.keep_releases, ctx.opts.keep_backups)


def prune(layout, state, keep_releases: int, keep_backups: int) -> List[str]:
    removed: List[str] = []
    current = layout.current_release_dir()
    protected = {current.name if current else None}
    for entry in reversed(state.history):  # the release we'd roll back to
        if entry.get("id") == state.current and entry.get("from_id"):
            protected.add(entry["from_id"])
            break
    releases = sorted(
        (p for p in layout.releases.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    keep = {p.name for p in releases[:max(keep_releases, 1)]} | protected
    for p in releases:
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(f"releases/{p.name}")
    backups = sorted((p for p in layout.backups.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    for p in backups[max(keep_backups, 1):]:
        shutil.rmtree(p, ignore_errors=True)
        removed.append(f"backups/{p.name}")
    for r in removed:
        log.info("  removed old %s", r)
    return removed


def default_pipeline() -> List[Step]:
    return [
        Preflight(),
        FetchRelease(),
        HandoffIfNewer(),
        BackupUserData(),
        CarryOverUserFiles(),
        BuildRelease(),
        VerifyRelease(),
        StopService(),      # stop before touching data so nothing writes during migration
        MigrateUserData(),
        ActivateRelease(),
        StartService(),
        Finalize(),
    ]
