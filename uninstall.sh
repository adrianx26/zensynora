#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MyClaw — Uninstall Script
#  Usage:  chmod +x uninstall.sh && ./uninstall.sh
#
#  Options:
#    --yes, -y         Run in non-interactive mode
#    --keep-data       Keep user data (config, memory, knowledge)
#    --keep-config     Keep configuration only
#    --dry-run         Show what would be done without doing it
#    --help, -h        Show this help message
#
#  What this script does:
#   1. Stops running services (Telegram/WhatsApp gateway)
#   2. Removes virtual environment
#   3. Optionally removes user data
#   4. Removes systemd service (if installed)
#   5. Cleans up temporary files
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# CLI Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────
INTERACTIVE=true
KEEP_DATA=false
KEEP_CONFIG=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            INTERACTIVE=false
            shift
            ;;
        --keep-data)
            KEEP_DATA=true
            INTERACTIVE=false
            shift
            ;;
        --keep-config)
            KEEP_CONFIG=true
            INTERACTIVE=false
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --yes, -y         Run in non-interactive mode"
            echo "  --keep-data       Keep user data (config, memory, knowledge)"
            echo "  --keep-config     Keep configuration only"
            echo "  --dry-run         Show what would be done without doing it"
            echo "  --help, -h        Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

prompt_yes() {
    local prompt="$1"
    local default="${2:-N}"
    
    if [[ "$INTERACTIVE" == "false" ]]; then
        if [[ "$default" == "Y" ]]; then
            return 0
        else
            return 1
        fi
    fi
    
    local response
    read -rp "$prompt [y/N]: " response
    case "${response,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

set -euo pipefail

# ── Colors and Functions ─────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  [INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}  [ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${RESET}  $*"; }
error()   { echo -e "${RED}  [ERR ]${RESET}  $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}── $* ──────────────────────────────────────────${RESET}"; }
dry_run() { echo -e "${DIM}  [DRY]   $*${RESET}"; }

run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        dry_run "$*"
    else
        "$@"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# ─────────────────────────────────────────────────────────────────────────────
# 1. STOP RUNNING SERVICES
# ─────────────────────────────────────────────────────────────────────────────
header "1. Stopping services"

if command -v systemctl &>/dev/null; then
    if systemctl is-active --quiet myclaw 2>/dev/null; then
        info "Stopping myclaw systemd service..."
        run sudo systemctl stop myclaw
        run sudo systemctl disable myclaw
        success "MyClaw service stopped"
    else
        skip "MyClaw service not running"
    fi
else
    skip "systemd not available"
fi

if pgrep -f "python.*cli.py" &>/dev/null; then
    info "Stopping MyClaw processes..."
    run pkill -f "python.*cli.py" || true
    success "MyClaw processes stopped"
else
    skip "No MyClaw processes running"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. REMOVE VIRTUAL ENVIRONMENT
# ─────────────────────────────────────────────────────────────────────────────
header "2. Virtual environment"

if [[ -d "$VENV_DIR" ]]; then
    info "Removing virtual environment at $VENV_DIR..."
    run rm -rf "$VENV_DIR"
    success "Virtual environment removed"
else
    skip "No virtual environment found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. REMOVE USER DATA
# ─────────────────────────────────────────────────────────────────────────────
header "3. User data"

MYCLAW_DIR="$HOME/.myclaw"

if [[ -d "$MYCLAW_DIR" ]]; then
    if [[ "$KEEP_DATA" == "true" ]]; then
        skip "Keeping all user data (--keep-data set)"
    elif [[ "$KEEP_CONFIG" == "true" ]]; then
        info "Removing user data except config..."
        run rm -rf "$MYCLAW_DIR/workspace"
        run rm -rf "$MYCLAW_DIR/memory.db"
        run rm -rf "$MYCLAW_DIR/memory-wal.db"
        run rm -rf "$MYCLAW_DIR/memory-shm.db"
        run rm -rf "$MYCLAW_DIR/knowledge"
        run rm -rf "$MYCLAW_DIR/preferences"
        run rm -rf "$MYCLAW_DIR/plugins"
        run rm -rf "$MYCLAW_DIR/sandbox"
        run rm -rf "$MYCLAW_DIR/skills"
        run rm -rf "$MYCLAW_DIR/backups"
        run rm -rf "$MYCLAW_DIR/TOOLBOX"
        run rm -rf "$MYCLAW_DIR/semantic_cache"
        run rm -rf "$MYCLAW_DIR/swarm"
        success "User data removed (config kept)"
    else
        if prompt_yes "Remove all MyClaw user data in $MYCLAW_DIR"; then
            run rm -rf "$MYCLAW_DIR"
            success "All user data removed"
        else
            skip "User data kept"
        fi
    fi
else
    skip "No user data directory found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. REMOVE SYSTEMD SERVICE
# ─────────────────────────────────────────────────────────────────────────────
header "4. Systemd service"

SERVICE_FILE="/etc/systemd/system/myclaw.service"
if [[ -f "$SERVICE_FILE" ]]; then
    if prompt_yes "Remove systemd service at $SERVICE_FILE"; then
        run sudo systemctl stop myclaw 2>/dev/null || true
        run sudo systemctl disable myclaw 2>/dev/null || true
        run sudo rm -f "$SERVICE_FILE"
        run sudo systemctl daemon-reload
        success "Systemd service removed"
    else
        skip "Systemd service kept"
    fi
else
    skip "No systemd service found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. CLEAN UP TEMPORARY FILES
# ─────────────────────────────────────────────────────────────────────────────
header "5. Cleaning up"

cd "$SCRIPT_DIR"

# Remove test files
run rm -f test_*.py 2>/dev/null || true

# Remove temporary files
run rm -f *.tmp 2>/dev/null || true

# Remove pytest cache
run rm -rf __pycache__ .pytest_cache 2>/dev/null || true

# Remove Python cache in subdirectories
run find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Remove .pyc files
run find . -type f -name "*.pyc" -delete 2>/dev/null || true

success "Temporary files cleaned"

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${YELLOW}${BOLD}  ⚠️  MyClaw Uninstallation Complete${RESET}"
echo -e "${YELLOW}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "  ${YELLOW}This was a dry run. No changes were made.${RESET}"
    echo ""
fi

echo "  What was removed:"
echo "    - Virtual environment ($VENV_DIR)"
if [[ "$KEEP_DATA" == "false" ]]; then
    echo "    - User data (~/.myclaw/)"
fi
echo "    - Temporary files"
echo ""

if [[ "$KEEP_CONFIG" == "false" && "$KEEP_DATA" == "false" ]]; then
    echo "  To reinstall:"
    echo "    ./install.sh"
fi
echo ""
