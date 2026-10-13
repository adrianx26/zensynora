"""Command line: ``python -m updater <command>`` (normally via ``update.sh``)."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import DEFAULT_REF, DEFAULT_REPO, UPDATER_PROTOCOL
from .backup import create_backup, restore_backup, restore_release_files
from .layout import Layout, State, default_base, default_data_dir, read_version, timestamp, version_tuple
from .pipeline import Context, NothingToDo, Options, UpdateError, run_pipeline, sh
from .steps import Handoff, default_pipeline, point_source_to, prune

log = logging.getLogger("zensynora.updater")


def _setup_logging(layout: Layout, verbose: bool, to_file: bool) -> Path | None:
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    if not to_file:
        return None
    layout.logs.mkdir(parents=True, exist_ok=True)
    path = layout.logs / f"update-{timestamp()}.log"
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    return path


@contextmanager
def _lock(layout: Layout):
    layout.ensure_dirs()
    fd = os.open(layout.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError("Another update/rollback is already running.")
        yield
    finally:
        os.close(fd)


def _layout(args) -> Layout:
    base = Path(args.base).expanduser() if args.base else default_base()
    data = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    return Layout(base=base.resolve() if base.exists() else base, data_dir=data)


# ── update ───────────────────────────────────────────────────────────────────
def cmd_update(args) -> int:
    layout = _layout(args)
    logfile = _setup_logging(layout, args.verbose, to_file=True)
    if args.from_dir:
        kind, value = "dir", args.from_dir
    elif args.from_archive:
        kind, value = "archive", args.from_archive
    else:
        kind, value = "git", args.ref
    opts = Options(
        source_kind=kind, source_value=value, repo=args.repo, force=args.force,
        allow_downgrade=args.allow_downgrade, run_tests=args.run_tests,
        keep_releases=args.keep_releases, keep_backups=args.keep_backups,
        manage_service=not args.no_service, skip_webui=args.skip_webui,
    )
    with _lock(layout):
        ctx = Context(layout=layout, state=State.load(layout.state_file), opts=opts, stamp=timestamp())
        ctx.extra["no_handoff"] = args.no_handoff
        try:
            run_pipeline(default_pipeline(), ctx)
        except NothingToDo as e:
            log.info("✔ %s", e)
            return 0
        except Handoff as h:
            cmd = [sys.executable, "-m", "updater", *_passthrough(args), "--from-dir", str(h.staged),
                   "--no-handoff", "--cleanup-source"]
            os.chdir(h.staged)
            env = dict(os.environ, PYTHONPATH=str(h.staged))
            os.execve(sys.executable, cmd, env)
        except UpdateError as e:
            log.error("✘ Update failed: %s", e)
            log.error("Your previous version is still active. Log: %s", logfile)
            return 1
        except KeyboardInterrupt:
            log.error("✘ Update interrupted — previous version restored. Log: %s", logfile)
            return 130
        finally:
            if args.cleanup_source and args.from_dir:
                shutil.rmtree(args.from_dir, ignore_errors=True)

    log.info("")
    log.info("✔ Updated %s → %s", ctx.current_version, ctx.new_version)
    log.info("  active release : %s", ctx.new_dir)
    log.info("  data backup    : %s", ctx.backup_dir)
    for n in ctx.notes:
        log.info("  note: %s", n)
    if not ctx.service_was_active:
        log.info("  If ZenSynora is running (webui/agent/gateway), restart it to use the new version.")
    log.info("  Undo with      : %s rollback", _self_cmd())
    log.info("  Log            : %s", logfile)
    return 0


def _passthrough(args) -> list:
    out = ["update", "--repo", args.repo, "--keep-releases", str(args.keep_releases),
           "--keep-backups", str(args.keep_backups)]
    for flag in ("force", "allow_downgrade", "run_tests", "no_service", "skip_webui", "verbose"):
        if getattr(args, flag):
            out.append("--" + flag.replace("_", "-"))
    if args.base:
        out += ["--base", args.base]
    if args.data_dir:
        out += ["--data-dir", args.data_dir]
    return out


def _self_cmd() -> str:
    return "zensynora update" if shutil.which("zensynora") else "bash update.sh"


# ── rollback ─────────────────────────────────────────────────────────────────
def cmd_rollback(args) -> int:
    layout = _layout(args)
    logfile = _setup_logging(layout, args.verbose, to_file=True)
    with _lock(layout):
        state = State.load(layout.state_file)
        cur = layout.current_release_dir()
        if cur is None:
            raise UpdateError("No active installation found.")
        entry = next((e for e in reversed(state.history)
                      if e.get("action") == "update" and e.get("id") == cur.name), None)
        target_id = args.to or (entry or {}).get("from_id")
        if not target_id:
            raise UpdateError("No previous version recorded. Use `status` to list releases and --to ID.")
        target = layout.releases / target_id
        if not target.is_dir():
            raise UpdateError(f"Release {target_id} no longer exists (pruned?).")

        restore = args.restore_data
        if restore is None:  # default: restore only if the update changed data
            restore = bool(entry and entry.get("migrations"))
        backup_dir = None
        if restore:
            if not (entry and entry.get("backup")):
                raise UpdateError("No backup recorded for this update; use `restore --backup NAME`.")
            backup_dir = layout.backups / entry["backup"]

        service = shutil.which("systemctl") and \
            sh(["systemctl", "is-active", "--quiet", "myclaw"], check=False).returncode == 0
        if service:
            sh(["sudo", "-n", "systemctl", "stop", "myclaw"], check=False)
        if backup_dir:
            safety = restore_backup(layout, backup_dir)
            restore_release_files(backup_dir, target)
            log.info("  your current data was saved first: %s", safety)
        point_source_to(layout, target)
        if service:
            sh(["sudo", "-n", "systemctl", "start", "myclaw"], check=False)

        state.current = target_id
        state.record({"action": "rollback", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "id": target_id, "version": read_version(target), "from_id": cur.name,
                      "from_version": read_version(cur), "data_restored": bool(backup_dir)})
        state.save()
    log.info("✔ Rolled back to %s (%s)%s", read_version(target), target_id,
             " and restored your data" if backup_dir else "")
    log.info("  Restart ZenSynora if it is running. Log: %s", logfile)
    return 0


# ── status / check ───────────────────────────────────────────────────────────
def _latest_remote_version(repo: str, ref: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/pyproject.toml"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            text = r.read().decode("utf-8")
    except Exception:
        return None
    import re
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def cmd_status(args) -> int:
    layout = _layout(args)
    _setup_logging(layout, args.verbose, to_file=False)
    cur = layout.current_release_dir()
    state = State.load(layout.state_file)
    info = {
        "base": str(layout.base),
        "data_dir": str(layout.data_dir),
        "installed_version": read_version(cur) if cur else None,
        "active_release": cur.name if cur else None,
        "layout": "legacy (adopted on first update)" if layout.is_legacy() else "managed",
        "releases": sorted(p.name for p in layout.releases.iterdir() if p.is_dir()) if layout.releases.exists() else [],
        "backups": sorted(p.name for p in layout.backups.iterdir() if p.is_dir()) if layout.backups.exists() else [],
        "last_actions": state.history[-5:],
        "updater_protocol": UPDATER_PROTOCOL,
    }
    if args.check:
        latest = _latest_remote_version(args.repo, args.ref)
        info["latest_version"] = latest
        info["update_available"] = bool(
            latest and cur and version_tuple(latest) > version_tuple(read_version(cur)))
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"Installed : {info['installed_version']}  ({info['active_release']}, {info['layout']})")
    print(f"Base      : {info['base']}")
    print(f"User data : {info['data_dir']}")
    if args.check:
        latest = info["latest_version"] or "unknown (offline?)"
        flag = "  ← update available: run update" if info["update_available"] else ""
        print(f"Latest    : {latest} on {args.repo}@{args.ref}{flag}")
    print("Releases  : " + (", ".join(info["releases"]) or "-"))
    print("Backups   : " + (", ".join(info["backups"]) or "-"))
    for e in info["last_actions"]:
        print(f"  {e.get('at')}  {e.get('action'):8} {e.get('from_version')} → {e.get('version')}")
    return 0


# ── backup / restore / prune / migrate ───────────────────────────────────────
def cmd_backup(args) -> int:
    layout = _layout(args)
    _setup_logging(layout, args.verbose, to_file=False)
    with _lock(layout):
        b = create_backup(layout, args.label or "manual", release_dir=layout.current_release_dir())
    print(f"✔ Backup saved: {b}")
    return 0


def cmd_restore(args) -> int:
    layout = _layout(args)
    _setup_logging(layout, args.verbose, to_file=True)
    bdir = layout.backups / args.backup
    if not bdir.is_dir():
        raise UpdateError(f"No backup named {args.backup}. See `status`.")
    with _lock(layout):
        safety = restore_backup(layout, bdir)
        cur = layout.current_release_dir()
        if cur and args.with_release_files:
            restore_release_files(bdir, cur)
    print(f"✔ Data restored from {bdir.name}. Previous data saved as {safety.name if safety else '-'}.")
    print("  Restart ZenSynora if it is running.")
    return 0


def cmd_prune(args) -> int:
    layout = _layout(args)
    _setup_logging(layout, args.verbose, to_file=False)
    with _lock(layout):
        removed = prune(layout, State.load(layout.state_file), args.keep_releases, args.keep_backups)
    print("✔ Removed: " + (", ".join(removed) if removed else "nothing"))
    return 0


def cmd_migrate(args) -> int:
    from .migrations import pending, run_pending

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    if not args.json:
        _setup_logging(Layout(default_base(), data_dir), args.verbose, to_file=False)
    if args.list:
        print(json.dumps({"pending": [m.ID for m in pending(data_dir)]}))
        return 0
    app_version = args.app_version or read_version(Path(__file__).resolve().parent.parent)
    applied = run_pending(data_dir, app_version)
    if args.json:
        print(json.dumps({"applied": applied}))
    else:
        print("✔ Applied: " + (", ".join(applied) if applied else "nothing pending"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="update.sh", description="Safely update ZenSynora.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", help="install base (default ~/.local/share/zensynora)")
    common.add_argument("--data-dir", help="user data dir (default ~/.myclaw)")
    common.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    u = sub.add_parser("update", parents=[common], help="update to a newer version (default)")
    src = u.add_mutually_exclusive_group()
    src.add_argument("--ref", default=DEFAULT_REF, help="GitHub branch or tag (default: main)")
    src.add_argument("--from-dir", help="install from a local source folder")
    src.add_argument("--from-archive", help="install from a .tar.gz/.zip")
    u.add_argument("--repo", default=DEFAULT_REPO)
    u.add_argument("--force", action="store_true", help="reinstall even if versions match")
    u.add_argument("--allow-downgrade", action="store_true")
    u.add_argument("--run-tests", action="store_true", help="run the test suite before switching")
    u.add_argument("--keep-releases", type=int, default=3)
    u.add_argument("--keep-backups", type=int, default=5)
    u.add_argument("--no-service", action="store_true", help="don't stop/start the systemd service")
    u.add_argument("--skip-webui", action="store_true", help="don't rebuild the web UI")
    u.add_argument("--no-handoff", action="store_true", help=argparse.SUPPRESS)
    u.add_argument("--cleanup-source", action="store_true", help=argparse.SUPPRESS)
    u.set_defaults(func=cmd_update)

    r = sub.add_parser("rollback", parents=[common], help="go back to the previous version")
    r.add_argument("--to", help="release ID to activate (see status)")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--restore-data", dest="restore_data", action="store_true", default=None,
                   help="also restore ~/.myclaw from the pre-update backup")
    g.add_argument("--keep-data", dest="restore_data", action="store_false",
                   help="keep current ~/.myclaw even if the update migrated it")
    r.set_defaults(func=cmd_rollback)

    s = sub.add_parser("status", parents=[common], help="show versions, releases and backups")
    s.add_argument("--check", action="store_true", help="also check GitHub for a newer version")
    s.add_argument("--repo", default=DEFAULT_REPO)
    s.add_argument("--ref", default=DEFAULT_REF)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    b = sub.add_parser("backup", parents=[common], help="back up ~/.myclaw now")
    b.add_argument("--label")
    b.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore", parents=[common], help="restore ~/.myclaw from a backup")
    rs.add_argument("--backup", required=True)
    rs.add_argument("--with-release-files", action="store_true", help="also restore .env and data/")
    rs.set_defaults(func=cmd_restore)

    pr = sub.add_parser("prune", parents=[common], help="delete old releases/backups")
    pr.add_argument("--keep-releases", type=int, default=3)
    pr.add_argument("--keep-backups", type=int, default=5)
    pr.set_defaults(func=cmd_prune)

    m = sub.add_parser("migrate", parents=[common], help="apply pending data migrations")
    m.add_argument("--app-version")
    m.add_argument("--json", action="store_true")
    m.add_argument("--list", action="store_true")
    m.set_defaults(func=cmd_migrate)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["update", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UpdateError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
