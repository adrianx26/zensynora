#!/usr/bin/env pwsh
# ─────────────────────────────────────────────────────────────────────────────
#  ZenSynora / MyClaw — SSH Deployment Script
#  Target: Linux machine at 192.168.8.110 (user: adi)
#  Usage:  .\deploy_ssh.ps1
# ─────────────────────────────────────────────────────────────────────────────

# ── Config ────────────────────────────────────────────────────────────────────
$REMOTE_HOST = "192.168.8.110"
$REMOTE_USER = "adi"
$REMOTE_DIR  = "/home/adi/zensynora"
$LOCAL_DIR   = $PSScriptRoot   # directory containing this script

# ── Colors ────────────────────────────────────────────────────────────────────
function info    ($msg) { Write-Host "  [INFO] $msg" -ForegroundColor Cyan }
function success ($msg) { Write-Host "  [ OK ] $msg" -ForegroundColor Green }
function err     ($msg) { Write-Host "  [ERR ] $msg" -ForegroundColor Red; exit 1 }
function header  ($msg) { Write-Host "`n── $msg ──────────────────────────────────────────" -ForegroundColor Yellow }

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Check for plink / sshpass tooling
# ─────────────────────────────────────────────────────────────────────────────
header "1. Checking tools"

$hasSSH  = (Get-Command ssh  -ErrorAction SilentlyContinue) -ne $null
$hasSCP  = (Get-Command scp  -ErrorAction SilentlyContinue) -ne $null

if (-not $hasSSH)  { err "ssh not found. Please install OpenSSH (Settings > Optional Features > OpenSSH Client)." }
if (-not $hasSCP)  { err "scp not found. Please install OpenSSH." }
success "ssh and scp are available."

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Accept host key automatically using StrictHostKeyChecking=no
# ─────────────────────────────────────────────────────────────────────────────
header "2. Preparing SSH options"

$SSH_OPTS = @(
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "PasswordAuthentication=no" # Enforce key-based auth
)

# Build SSH_OPTS into a flat string for scp/ssh invocation
$SSH_OPTS_STR = $SSH_OPTS -join " "

info "SSH options: $SSH_OPTS_STR"
info "This script now requires SSH key-based authentication."

# Helper: run a remote command via SSH (prompts password if no plink)
function Invoke-SSH {
    param([string]$cmd)
    ssh @SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" $cmd
}

# Helper: copy local path to remote via SCP
function Invoke-SCP-ToRemote {
    param([string]$localPath, [string]$remotePath)
    scp @SSH_OPTS -r $localPath "${REMOTE_USER}@${REMOTE_HOST}:${remotePath}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Test SSH connectivity
# ─────────────────────────────────────────────────────────────────────────────
header "3. Testing SSH connectivity"
info "Connecting to ${REMOTE_USER}@${REMOTE_HOST}..."
info ""

try {
    $test = Invoke-SSH "echo 'SSH_OK'"
    if ($test -match "SSH_OK") {
        success "SSH connection established."
    } else {
        err "SSH test command did not return expected output. Got: $test"
    }
} catch {
    err "SSH connection failed: $_`n`nMake sure the target machine is reachable and SSH is enabled."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Create remote directory & transfer project files
# ─────────────────────────────────────────────────────────────────────────────
header "4. Copying project files to remote"

# Create remote directory
info "Creating remote directory: $REMOTE_DIR"
Invoke-SSH "mkdir -p $REMOTE_DIR"

# Determine what to exclude (same as .gitignore)
$excludes = @("venv", "__pycache__", ".pytest_cache", "*.pyc", ".git", "node_modules", "*.egg-info")
$excludeArgs = $excludes | ForEach-Object { "--exclude=$_" }

info "Syncing project files to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR} ..."
info "(This may take a minute on first run — transferring all project files)"

# Use rsync if available (much better for repeated deployments), else scp
$hasRsync = (Get-Command rsync -ErrorAction SilentlyContinue) -ne $null

if ($hasRsync) {
    info "rsync found — using rsync for efficient transfer."
    rsync -avz --progress @excludeArgs -e "ssh $SSH_OPTS_STR" "$LOCAL_DIR/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
} else {
    info "rsync not found — using scp (full copy)."
    info "Using tar to create an archive with exclusions and piping over SSH."
    $tarExcludeArgs = $excludes | ForEach-Object { "--exclude=$_" }
    $tarCmd = "tar @tarExcludeArgs -czf - -C ""$LOCAL_DIR"" ."
    $remoteCmd = "cd $REMOTE_DIR && tar -xzf -"
    Invoke-Command -ScriptBlock { & $tarCmd } | ssh @SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" $remoteCmd
}

success "Project files transferred."

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Run the installer on the remote machine
# ─────────────────────────────────────────────────────────────────────────────
header "5. Running install.sh on remote machine"

info "Making install.sh executable..."
Invoke-SSH "chmod +x $REMOTE_DIR/install.sh"

info "Running installer in non-interactive mode (--yes --no-optional)..."
info "This will install system packages, set up Python venv, and install dependencies."
info ""

Invoke-SSH "cd $REMOTE_DIR && bash install.sh --yes --no-optional 2>&1"

success "Installer completed."

# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Verify the deployment
# ─────────────────────────────────────────────────────────────────────────────
header "6. Verifying deployment"

$verifyOutput = Invoke-SSH "cd $REMOTE_DIR && source venv/bin/activate && python cli.py --help 2>&1 | head -20"
Write-Host $verifyOutput

success "Deployment complete!"

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  ✅  ZenSynora deployed to $REMOTE_HOST"         -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps on the remote machine:"
Write-Host ""
Write-Host "  SSH in:"
Write-Host "    ssh adi@192.168.8.110" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Activate venv & configure:"
Write-Host "    cd $REMOTE_DIR && source venv/bin/activate" -ForegroundColor Cyan
Write-Host "    python cli.py onboard   # run setup wizard" -ForegroundColor Cyan
Write-Host "    nano ~/.myclaw/config.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Run the agent:"
Write-Host "    python cli.py agent              # console mode" -ForegroundColor Cyan
Write-Host "    python cli.py gateway            # Telegram bot" -ForegroundColor Cyan
Write-Host "    python cli.py gateway --channel whatsapp" -ForegroundColor Cyan
Write-Host ""
