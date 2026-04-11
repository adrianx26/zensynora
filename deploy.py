#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZenSynora / MyClaw — Automated SSH Deployment Script
Transfers the project to a Linux machine and runs the installer.

Requirements: pip install paramiko
Usage: python deploy.py
"""

import os
import sys
import time
import warnings
import paramiko
import stat
from pathlib import Path

# Suppress paramiko TripleDES deprecation warning (harmless, cosmetic)
warnings.filterwarnings('ignore', category=DeprecationWarning)

# ── Config ────────────────────────────────────────────────────────────────────
REMOTE_HOST = "192.168.8.110"
REMOTE_USER = "adi"
REMOTE_PASS = "Alpin2003@"
REMOTE_DIR  = "/home/adi/zensynora"

# Directories / files to exclude from transfer
EXCLUDES = {
    "venv", "__pycache__", ".pytest_cache", ".git",
    "node_modules", ".egg-info", "*.pyc", ".kilo",
    "out.txt", "out_storage.txt", "test_output.txt",
}

# Script directory (project root)
LOCAL_DIR = Path(__file__).parent.resolve()

# ── Colors (ANSI) ─────────────────────────────────────────────────────────────
CYAN   = "\033[0;36m"
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
RED    = "\033[0;31m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def info(msg):    print(f"{CYAN}  [INFO]{RESET}  {msg}", flush=True)
def success(msg): print(f"{GREEN}  [ OK ]{RESET}  {msg}", flush=True)
def warn(msg):    print(f"{YELLOW}  [WARN]{RESET}  {msg}", flush=True)
def header(msg):  print(f"\n{BOLD}-- {msg} --{RESET}", flush=True)
def err(msg):
    print(f"{RED}  [ERR ]{RESET}  {msg}", flush=True)
    sys.exit(1)


def should_exclude(name: str) -> bool:
    """Check if a file/directory name matches exclusion patterns."""
    for exc in EXCLUDES:
        if exc.startswith("*"):
            if name.endswith(exc[1:]):
                return True
        elif exc.endswith("*"):
            if name.startswith(exc[:-1]):
                return True
        elif name == exc or name.endswith(exc):
            return True
    return False


def upload_directory(sftp: paramiko.SFTPClient, local_path: Path, remote_path: str):
    """Recursively upload a local directory to a remote path via SFTP."""
    try:
        sftp.stat(remote_path)
    except FileNotFoundError:
        sftp.mkdir(remote_path)

    items = list(local_path.iterdir())
    for item in items:
        if should_exclude(item.name):
            continue

        remote_item = f"{remote_path}/{item.name}"

        if item.is_dir():
            try:
                sftp.stat(remote_item)
            except FileNotFoundError:
                sftp.mkdir(remote_item)
            upload_directory(sftp, item, remote_item)
        elif item.is_file():
            try:
                sftp.put(str(item), remote_item)
            except Exception as e:
                warn(f"Skipped {item.name}: {e}")


def run_remote(ssh: paramiko.SSHClient, cmd: str, stream_output: bool = True) -> int:
    """Run a command on the remote machine, streaming output. Returns exit code."""
    print(f"{CYAN}  $ {cmd}{RESET}")
    stdin, stdout, stderr = ssh.exec_command(cmd, get_pty=True)

    if stream_output:
        for line in iter(stdout.readline, ""):
            # Replace characters that Windows CP1252 can't encode
            safe_line = line.encode('ascii', errors='replace').decode('ascii')
            print(f"  {safe_line}", end="")

    exit_status = stdout.channel.recv_exit_status()
    return exit_status


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{BOLD}")
    print("  [DEPLOY] ZenSynora -- SSH Deployment")
    print("  " + "="*47)
    print(f"  Target: {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}")
    print(f"  Source: {LOCAL_DIR}")
    print(f"{RESET}")

    # ── Step 1: Connect ───────────────────────────────────────────────────────
    header("1. Connecting via SSH")
    info(f"Connecting to {REMOTE_USER}@{REMOTE_HOST} ...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(
            hostname=REMOTE_HOST,
            username=REMOTE_USER,
            password=REMOTE_PASS,
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        success(f"Connected to {REMOTE_HOST}")
    except paramiko.AuthenticationException:
        err(f"Authentication failed. Check username/password.")
    except Exception as e:
        err(f"Connection failed: {e}")

    # ── Step 2: Create remote directory ───────────────────────────────────────
    header("2. Preparing remote directory")
    rc = run_remote(ssh, f"mkdir -p {REMOTE_DIR}")
    if rc != 0:
        err("Failed to create remote directory.")
    success(f"Remote directory ready: {REMOTE_DIR}")

    # ── Step 3: Upload files ──────────────────────────────────────────────────
    header("3. Uploading project files")
    info(f"Transferring {LOCAL_DIR} -> {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}")
    info("(Excluded: venv, __pycache__, .git, .pytest_cache, etc.)")

    sftp = ssh.open_sftp()
    file_count = 0

    try:
        items = list(LOCAL_DIR.iterdir())
        total = len([i for i in items if not should_exclude(i.name)])
        done  = 0

        for item in items:
            if should_exclude(item.name):
                continue

            remote_item = f"{REMOTE_DIR}/{item.name}"
            done += 1
            print(f"  [{done}/{total}] {item.name}", end="\r")

            if item.is_dir():
                try:
                    sftp.stat(remote_item)
                except FileNotFoundError:
                    sftp.mkdir(remote_item)
                upload_directory(sftp, item, remote_item)
                file_count += 1
            elif item.is_file():
                try:
                    sftp.put(str(item), remote_item)
                    file_count += 1
                except Exception as e:
                    warn(f"\nSkipped {item.name}: {e}")

        print()  # newline after progress
    finally:
        sftp.close()

    success(f"Uploaded {file_count} items.")

    # ── Step 4: Fix line endings & permissions ────────────────────────────────
    header("4. Fixing file permissions")
    run_remote(ssh, f"find {REMOTE_DIR} -name '*.sh' -exec chmod +x {{}} \\;")
    run_remote(ssh, f"find {REMOTE_DIR} -name '*.py' -exec chmod +x {{}} \\;")
    # Convert CRLF → LF for shell scripts (Windows line endings can break bash)
    run_remote(ssh, f"find {REMOTE_DIR} -name '*.sh' -exec sed -i 's/\\r$//' {{}} \\;")
    success("Permissions and line endings fixed.")

    # ── Step 5: Run installer ─────────────────────────────────────────────────
    header("5. Running install.sh on remote")
    info("This will install system packages, Python venv, and all dependencies.")
    info("Running with --yes --no-optional (fully non-interactive)")
    info("")

    rc = run_remote(
        ssh,
        f"cd {REMOTE_DIR} && bash install.sh --yes --no-optional",
        stream_output=True,
    )

    if rc != 0:
        warn(f"Installer exited with code {rc}. Some optional packages may have failed — check output above.")
    else:
        success("Installer completed successfully.")

    # ── Step 6: Verify ────────────────────────────────────────────────────────
    header("6. Verifying installation")
    rc = run_remote(
        ssh,
        f"cd {REMOTE_DIR} && source venv/bin/activate && python cli.py --help 2>&1 | head -30",
        stream_output=True,
    )

    # ── Done ──────────────────────────────────────────────────────────────────
    print()
    print(f"{GREEN}{BOLD}" + "="*47 + RESET)
    print(f"{GREEN}{BOLD}  [DONE] ZenSynora deployed to {REMOTE_HOST} !{RESET}")
    print(f"{GREEN}{BOLD}" + "="*47 + RESET)
    print()
    print("  Next steps on the remote machine:")
    print()
    print(f"  SSH in:  {CYAN}ssh {REMOTE_USER}@{REMOTE_HOST}{RESET}")
    print(f"           {CYAN}cd {REMOTE_DIR} && source venv/bin/activate{RESET}")
    print()
    print(f"  Onboard: {CYAN}python cli.py onboard{RESET}")
    print(f"  Config:  {CYAN}nano ~/.myclaw/config.json{RESET}")
    print()
    print(f"  Run:     {CYAN}python cli.py agent{RESET}              # console mode")
    print(f"           {CYAN}python cli.py gateway{RESET}            # Telegram bot")
    print(f"           {CYAN}python cli.py gateway --channel whatsapp{RESET}")
    print()

    ssh.close()


if __name__ == "__main__":
    main()
