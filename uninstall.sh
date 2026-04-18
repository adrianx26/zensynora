#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MyClaw — Uninstall Script
#  Usage:  chmod +x uninstall.sh && ./uninstall.sh
# ─────────────────────────────────────────────────────────────────────────────

INTERACTIVE=true
KEEP_DATA=false
KEEP_CONFIG=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes) INTERACTIVE=false; shift ;;
        --keep-data) KEEP_DATA=true; INTERACTIVE=false; shift ;;
        --keep-config) KEEP_CONFIG=true; INTERACTIVE=false; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options: --yes, --keep-data, --keep-config, --dry-run, --help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

prompt_yes() {
    if [[ "$INTERACTIVE" == "false" ]]; then return 0; fi
    read -rp "$1 [y/N]: " response
    case "${response,,}" in y|yes) return 0 ;; *) return 1 ;; esac
}

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  [INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}  [ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${RESET}  $*"; }
header()  { echo -e "\n${BOLD}── $* ──────────────────────────────────────────${RESET}"; }

run() { if [[ "$DRY_RUN" == "true" ]]; then echo "[DRY] $*"; else "$@"; fi }

# ─────────────────────────────────────────────────────────────────────────────
# 1. STOP ALL PROCESSES (Prioritized)
# ─────────────────────────────────────────────────────────────────────────────
header "1. Stopping all active processes"

# Stop systemd if exists
if command -v systemctl &>/dev/null; then
    if systemctl is-active --quiet myclaw 2>/dev/null; then
        info "Stopping systemd service..."
        run sudo systemctl stop myclaw && run sudo systemctl disable myclaw
    fi
fi

# Kill Python-based CLI processes (agent, gateway, etc.)
if pgrep -f "python.*cli.py" &>/dev/null; then
    info "Terminating MyClaw CLI processes..."
    run pkill -f "python.*cli.py" || true
fi

# Kill Uvicorn (Web UI backend)
if pgrep -f "uvicorn.*myclaw.web.api:app" &>/dev/null; then
    info "Terminating Web UI backend (uvicorn)..."
    run pkill -f "uvicorn.*myclaw.web.api:app" || true
fi

# Kill Node (Web UI frontend dev server)
if pgrep -f "node.*webui" &>/dev/null; then
    info "Terminating Web UI frontend (node)..."
    run pkill -f "node.*webui" || true
fi

success "All processes stopped."

# ─────────────────────────────────────────────────────────────────────────────
# 2. VIRTUAL ENVIRONMENT
# ─────────────────────────────────────────────────────────────────────────────
header "2. Removing virtual environment"
VENV_DIR="$(pwd)/venv"
if [[ -d "$VENV_DIR" ]]; then
    run rm -rf "$VENV_DIR"
    success "venv removed."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. USER DATA CLEANUP
# ─────────────────────────────────────────────────────────────────────────────
header "3. Cleaning user data"
MYCLAW_DIR="$HOME/.myclaw"

if [[ -d "$MYCLAW_DIR" ]]; then
    if [[ "$KEEP_DATA" == "true" ]]; then
        info "Keeping user data as requested."
    elif [[ "$KEEP_CONFIG" == "true" ]]; then
        info "Removing data but keeping config..."
        run rm -rf "$MYCLAW_DIR/workspace" "$MYCLAW_DIR/knowledge" "$MYCLAW_DIR/memory.db" "$MYCLAW_DIR/task_logs" \
                   "$MYCLAW_DIR/mcp" "$MYCLAW_DIR/TOOLBOX" "$MYCLAW_DIR/backups" "$MYCLAW_DIR/skills" \
                   "$MYCLAW_DIR/benchmarks" "$MYCLAW_DIR/newtech" "$MYCLAW_DIR/semantic_cache" \
                   "$MYCLAW_DIR/sandbox" "$MYCLAW_DIR/audit" "$MYCLAW_DIR/medic"
    else
        if prompt_yes "Remove all MyClaw data in $MYCLAW_DIR?"; then
            run rm -rf "$MYCLAW_DIR"
            success "Data directory cleared."
        fi
    fi
fi

# Phase 6.2: Scheduler persistence cleanup
header "3.5 Scheduler persistence"
SCHEDULER_PERSIST="$MYCLAW_DIR/scheduler_jobs.jsonl"
if [[ -f "$SCHEDULER_PERSIST" ]]; then
    run rm -f "$SCHEDULER_PERSIST"
    success "Scheduler persistence file removed."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. WEB UI ARTIFACTS
# ─────────────────────────────────────────────────────────────────────────────
header "4. Cleaning Web UI artifacts"
if [[ -d "webui" ]]; then
    if prompt_yes "Remove node_modules and builds in webui/?"; then
        run rm -rf "webui/node_modules" "webui/dist"
        success "Web UI artifacts removed."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. SYSTEM CLEANUP
# ─────────────────────────────────────────────────────────────────────────────
header "5. System cleanup"
# Remove systemd service file
SERVICE_FILE="/etc/systemd/system/myclaw.service"
if [[ -f "$SERVICE_FILE" ]]; then
    run sudo rm -f "$SERVICE_FILE"
    run sudo systemctl daemon-reload
    success "Service file removed."
fi

# Clear python caches
run find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
run find . -type f -name "*.pyc" -delete 2>/dev/null || true

header "⚠️  ZenSynora Uninstallation Complete."
