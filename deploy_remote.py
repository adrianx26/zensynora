#!/usr/bin/env python3
"""
ZenSynora — SSH Deploy via GitHub Clone
Steps:
  1. SSH connect to 192.168.8.110 (user: adi)
  2. Clone github.com/adrianx26/zensynora
  3. Run install.sh from the cloned repo
"""

import sys
import warnings
import paramiko
import time

warnings.filterwarnings('ignore', category=DeprecationWarning)

# ── Config ────────────────────────────────────────────────────────────────────
REMOTE_HOST = "192.168.8.110"
REMOTE_USER = "adi"
REMOTE_PASS = "Alpin2003@"
ROOT_PASS   = "Alpin@2033@"
REPO_URL    = "https://github.com/adrianx26/zensynora.git"
REMOTE_DIR  = "/home/adi/zensynora"

# ── Colors ────────────────────────────────────────────────────────────────────
C = "\033[0;36m"; G = "\033[0;32m"; Y = "\033[1;33m"; R = "\033[0;31m"
B = "\033[1m"; X = "\033[0m"

def info(m):    print(f"{C}  [INFO]{X}  {m}", flush=True)
def ok(m):      print(f"{G}  [ OK ]{X}  {m}", flush=True)
def warn(m):    print(f"{Y}  [WARN]{X}  {m}", flush=True)
def err(m):     print(f"{R}  [FAIL]{X}  {m}", flush=True)
def header(m):  print(f"\n{B}{'='*60}\n  {m}\n{'='*60}{X}", flush=True)


def run_cmd(ssh, cmd, label=None, sudo=False):
    """
    Run a command on the remote machine via SSH.
    Returns (exit_code, stdout_text, stderr_text).
    Prints output in real time.
    """
    if label:
        info(label)

    if sudo:
        cmd = f"echo '{ROOT_PASS}' | sudo -S bash -c '{cmd}' 2>&1"

    print(f"{C}  $ {cmd}{X}", flush=True)

    stdin, stdout, stderr = ssh.exec_command(cmd, get_pty=True)
    # No timeout — let long-running installs complete
    stdout.channel.settimeout(None)

    out_lines = []
    for line in iter(stdout.readline, ""):
        safe = line.encode('ascii', errors='replace').decode('ascii')
        print(f"  {safe}", end="", flush=True)
        out_lines.append(line)

    exit_code = stdout.channel.recv_exit_status()
    err_text = stderr.read().decode('utf-8', errors='replace')

    if exit_code == 0:
        ok(f"Command succeeded (exit code 0)")
    else:
        err(f"Command FAILED with exit code {exit_code}")
        if err_text.strip():
            print(f"{R}  STDERR: {err_text.strip()}{X}", flush=True)

    return exit_code, "".join(out_lines), err_text


def main():
    print(f"\n{B}")
    print("  ZenSynora — SSH Deploy via GitHub Clone")
    print("  " + "=" * 50)
    print(f"  Target : {REMOTE_USER}@{REMOTE_HOST}")
    print(f"  Repo   : {REPO_URL}")
    print(f"  Dir    : {REMOTE_DIR}")
    print(f"{X}\n")

    # ── Step 1: SSH Connect ───────────────────────────────────────────────────
    header("STEP 1: SSH Connect")
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
        # Keep SSH alive during long operations
        transport = ssh.get_transport()
        transport.set_keepalive(30)
        ok(f"SSH connected to {REMOTE_HOST}")
    except paramiko.AuthenticationException:
        err("Authentication failed — check username/password.")
        sys.exit(1)
    except Exception as e:
        err(f"Connection failed: {e}")
        sys.exit(1)

    # Verify we can run commands
    rc, out, _ = run_cmd(ssh, "whoami && hostname && uname -a", label="Verifying SSH session")
    if rc != 0:
        err("Cannot run commands on remote. Aborting.")
        sys.exit(1)

    # ── Step 2: Check/Install git ─────────────────────────────────────────────
    header("STEP 2: Ensure git is installed")
    rc, out, _ = run_cmd(ssh, "which git", label="Checking for git")
    if rc != 0:
        warn("git not found — installing via apt...")
        rc, _, _ = run_cmd(ssh, "apt-get update && apt-get install -y git", sudo=True)
        if rc != 0:
            err("Failed to install git. Aborting.")
            ssh.close()
            sys.exit(1)

    # ── Step 3: Clone the repository ──────────────────────────────────────────
    header("STEP 3: Clone repository from GitHub")

    # Check if directory already exists
    rc, _, _ = run_cmd(ssh, f"test -d {REMOTE_DIR}/.git && echo 'EXISTS'", label="Checking if repo already exists")

    if rc == 0:
        info("Repository directory already exists. Pulling latest changes...")
        rc, _, _ = run_cmd(ssh, f"cd {REMOTE_DIR} && git fetch --all && git reset --hard origin/main 2>&1 || git reset --hard origin/master 2>&1",
                           label="Pulling latest from GitHub")
        if rc != 0:
            warn("Git pull failed — removing old directory and re-cloning...")
            run_cmd(ssh, f"rm -rf {REMOTE_DIR}", label="Removing old directory")
            rc, _, _ = run_cmd(ssh, f"git clone {REPO_URL} {REMOTE_DIR}", label="Cloning fresh")
            if rc != 0:
                err("Failed to clone repository. Aborting.")
                ssh.close()
                sys.exit(1)
    else:
        # Remove any non-git directory that might exist
        run_cmd(ssh, f"rm -rf {REMOTE_DIR}", label="Cleaning any old non-git directory")
        rc, _, _ = run_cmd(ssh, f"git clone {REPO_URL} {REMOTE_DIR}", label="Cloning repository")
        if rc != 0:
            err("Failed to clone repository. Aborting.")
            ssh.close()
            sys.exit(1)

    ok("Repository ready.")

    # Verify clone
    run_cmd(ssh, f"ls -la {REMOTE_DIR}/", label="Listing cloned files")

    # ── Step 4: Fix permissions & line endings ────────────────────────────────
    header("STEP 4: Fix permissions")
    run_cmd(ssh, f"find {REMOTE_DIR} -name '*.sh' -exec chmod +x {{}} \\;", label="Making .sh files executable")
    run_cmd(ssh, f"find {REMOTE_DIR} -name '*.sh' -exec sed -i 's/\\r$//' {{}} \\;", label="Fixing CRLF line endings")

    # ── Step 5: Run install.sh ────────────────────────────────────────────────
    header("STEP 5: Run install.sh")
    info("Running install.sh with --yes --no-optional (non-interactive)")
    info("This may take several minutes...")
    print()

    rc, out, _ = run_cmd(
        ssh,
        f"cd {REMOTE_DIR} && bash install.sh --yes --no-optional 2>&1",
        label="Running installer"
    )

    if rc != 0:
        warn(f"Installer exited with code {rc}. Check output above for errors.")
    else:
        ok("Installer completed successfully!")

    # ── Step 6: Verify ────────────────────────────────────────────────────────
    header("STEP 6: Verify installation")
    run_cmd(
        ssh,
        f"cd {REMOTE_DIR} && source venv/bin/activate && python cli.py --help 2>&1 | head -30",
        label="Testing cli.py --help"
    )

    # ── Done ──────────────────────────────────────────────────────────────────
    print()
    print(f"{G}{B}{'='*60}{X}")
    print(f"{G}{B}  DEPLOYMENT COMPLETE — ZenSynora on {REMOTE_HOST}{X}")
    print(f"{G}{B}{'='*60}{X}")
    print()
    print(f"  SSH in:    {C}ssh {REMOTE_USER}@{REMOTE_HOST}{X}")
    print(f"  Activate:  {C}cd {REMOTE_DIR} && source venv/bin/activate{X}")
    print(f"  Run:       {C}python cli.py agent{X}")
    print()

    ssh.close()


if __name__ == "__main__":
    main()
