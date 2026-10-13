#!/usr/bin/env python3
"""
ZenSynora — deploy this source tree to another Linux machine over SSH.

Rewritten 2026-10-13 (v0.7.0). The previous version had a host, user name and
PASSWORD hard-coded in this file, trusted any SSH host key, uploaded the local
.env (secrets) and copied files over an existing install. Now:

* nothing secret lives in the code — use an SSH key (recommended), or the
  ZENSYNORA_DEPLOY_PASSWORD environment variable, or answer the prompt;
* unknown host keys are refused unless you pass --trust-new-host;
* .env and other local-only files are never uploaded;
* if ZenSynora is already installed on the target, the safe updater is used
  (backup, side-by-side build, automatic rollback) instead of overwriting it.

Usage:
    python deploy.py --host 192.168.1.20 --user me
    python deploy.py --host server --user me --trust-new-host --password-prompt

Requires: paramiko (a core ZenSynora dependency).
"""

from __future__ import annotations

import argparse
import getpass
import os
import shlex
import sys
import warnings
from pathlib import Path

import paramiko

warnings.filterwarnings("ignore", category=DeprecationWarning)  # paramiko TripleDES notice

LOCAL_DIR = Path(__file__).parent.resolve()
# Never uploaded: local environments, caches, VCS data and anything with secrets.
EXCLUDES = {
    "venv", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".git", "node_modules",
    ".kilo", ".opencode", ".claude", "graphify-out", ".env", ".update.lock",
}
EXCLUDE_SUFFIXES = (".pyc", ".egg-info", ".log")

CYAN, GREEN, YELLOW, RED, BOLD, RESET = "\033[0;36m", "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[1m", "\033[0m"


def info(msg):
    print(f"{CYAN}  [INFO]{RESET}  {msg}", flush=True)


def success(msg):
    print(f"{GREEN}  [ OK ]{RESET}  {msg}", flush=True)


def warn(msg):
    print(f"{YELLOW}  [WARN]{RESET}  {msg}", flush=True)


def header(msg):
    print(f"\n{BOLD}-- {msg} --{RESET}", flush=True)


def fail(msg) -> None:
    print(f"{RED}  [ERR ]{RESET}  {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def excluded(name: str) -> bool:
    return name in EXCLUDES or name.endswith(EXCLUDE_SUFFIXES)


def upload_tree(sftp: paramiko.SFTPClient, local: Path, remote: str) -> int:
    try:
        sftp.stat(remote)
    except FileNotFoundError:
        sftp.mkdir(remote)
    count = 0
    for item in sorted(local.iterdir()):
        if excluded(item.name) or item.is_symlink():
            continue
        target = f"{remote}/{item.name}"
        if item.is_dir():
            count += upload_tree(sftp, item, target)
        elif item.is_file():
            sftp.put(str(item), target)
            sftp.chmod(target, item.stat().st_mode & 0o777)
            count += 1
    return count


def run_remote(ssh: paramiko.SSHClient, cmd: str) -> int:
    print(f"{CYAN}  $ {cmd}{RESET}")
    _, stdout, _ = ssh.exec_command(cmd, get_pty=True)
    for line in iter(stdout.readline, ""):
        print("  " + line.encode("ascii", errors="replace").decode("ascii"), end="")
    return stdout.channel.recv_exit_status()


def connect(args) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    known = Path.home() / ".ssh" / "known_hosts"
    if known.exists():
        ssh.load_host_keys(str(known))
    if args.trust_new_host:
        warn("Accepting an unknown host key (--trust-new-host). Verify it with the server owner.")
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

    password = os.environ.get("ZENSYNORA_DEPLOY_PASSWORD")
    if args.password_prompt and not password:
        password = getpass.getpass(f"Password for {args.user}@{args.host}: ")
    try:
        ssh.connect(
            hostname=args.host, port=args.port, username=args.user,
            password=password or None, key_filename=args.key or None,
            allow_agent=True, look_for_keys=True, timeout=20,
        )
    except paramiko.SSHException as e:
        if "not found in known_hosts" in str(e):
            fail(f"{args.host} is not a known host. Connect once with `ssh {args.user}@{args.host}` "
                 "to verify its key, or pass --trust-new-host.")
        fail(f"SSH connection failed: {e}")
    except Exception as e:
        fail(f"SSH connection failed: {e}")
    return ssh


def main() -> None:
    p = argparse.ArgumentParser(description="Deploy ZenSynora to a Linux machine over SSH.")
    p.add_argument("--host", default=os.environ.get("ZENSYNORA_DEPLOY_HOST"), help="target host")
    p.add_argument("--user", default=os.environ.get("ZENSYNORA_DEPLOY_USER", getpass.getuser()))
    p.add_argument("--port", type=int, default=22)
    p.add_argument("--key", help="private key file (default: ssh-agent / ~/.ssh keys)")
    p.add_argument("--password-prompt", action="store_true", help="ask for a password")
    p.add_argument("--trust-new-host", action="store_true", help="accept an unknown host key")
    p.add_argument("--dir", default="", help="fresh-install folder on the target "
                   "(default ~/.local/share/zensynora/source)")
    args = p.parse_args()
    if not args.host:
        fail("No target. Use --host HOST (or set ZENSYNORA_DEPLOY_HOST).")

    print(f"\n{BOLD}  ZenSynora — SSH deployment\n  {'=' * 40}{RESET}")
    print(f"  Target: {args.user}@{args.host}\n  Source: {LOCAL_DIR}\n")

    header("1. Connecting")
    ssh = connect(args)
    success(f"Connected to {args.host}")

    _, out, _ = ssh.exec_command("printf %s \"$HOME\"")
    home = out.read().decode().strip() or f"/home/{args.user}"
    base = f"{home}/.local/share/zensynora"
    install_dir = args.dir or f"{base}/source"
    staging = f"{home}/.cache/zensynora-deploy"
    existing = run_remote(ssh, f"test -x {shlex.quote(base)}/source/venv/bin/python") == 0

    header("2. Uploading (without venv, .git, .env, caches)")
    run_remote(ssh, f"rm -rf {shlex.quote(staging)} && mkdir -p {shlex.quote(staging)}")
    sftp = ssh.open_sftp()
    try:
        n = upload_tree(sftp, LOCAL_DIR, staging)
    finally:
        sftp.close()
    success(f"Uploaded {n} files to {staging}")
    run_remote(ssh, f"cd {shlex.quote(staging)} && find . -name '*.sh' -exec sed -i 's/\\r$//' {{}} + "
                    f"&& chmod +x *.sh")

    if existing:
        header("3. Existing install found — running the safe updater")
        rc = run_remote(ssh, f"bash {shlex.quote(staging)}/update.sh update --from-dir {shlex.quote(staging)} "
                             f"--base {shlex.quote(base)} --force")
        verify_dir = f"{base}/source"
    else:
        header("3. Fresh install")
        rc = run_remote(ssh, f"mkdir -p {shlex.quote(install_dir)} && cp -a {shlex.quote(staging)}/. "
                             f"{shlex.quote(install_dir)}/ && cd {shlex.quote(install_dir)} "
                             f"&& bash install.sh --no-docker --yes --no-optional")
        verify_dir = install_dir
    if rc != 0:
        fail(f"Remote step failed (exit {rc}); see the output above. An existing install stays active.")
    success("Remote install/update finished")

    header("4. Verifying")
    run_remote(ssh, f"cd / && {shlex.quote(verify_dir)}/venv/bin/zensynora doctor --offline --problems")
    run_remote(ssh, f"rm -rf {shlex.quote(staging)}")
    ssh.close()

    print(f"\n{GREEN}{BOLD}  Deployed to {args.host}.{RESET}\n")
    print(f"  ssh {args.user}@{args.host}")
    print(f"  source {verify_dir}/venv/bin/activate")
    print("  zensynora webui        # or: zensynora agent / zensynora gateway")


if __name__ == "__main__":
    main()
