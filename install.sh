#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MyClaw — Cross-Platform Install Script (Modern & Robust)
#  Tested on: Ubuntu 22.04 / 24.04 (LTS), macOS 13+, Windows (WSL2)
#  Usage:  chmod +x install.sh && ./install.sh
#
#  Options:
#    --yes, -y         Run in non-interactive mode (assume yes to prompts)
#    --no-optional     Skip optional components (LLM providers, Ollama, systemd)
#    --recreate-venv   Force recreation of virtual environment
#    --dry-run         Show what would be done without doing it
#    --help, -h        Show this help message
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# CLI Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────
INTERACTIVE=true
SKIP_OPTIONAL=false
RECREATE_VENV=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            INTERACTIVE=false
            shift
            ;;
        --no-optional)
            SKIP_OPTIONAL=true
            INTERACTIVE=false
            shift
            ;;
        --recreate-venv)
            RECREATE_VENV=true
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
            echo "  --yes, -y         Run in non-interactive mode (assume yes to prompts)"
            echo "  --no-optional     Skip optional components (LLM providers, Ollama, systemd)"
            echo "  --recreate-venv   Force recreation of virtual environment"
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

# Helper to prompt user or use default based on interactive mode
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
skip()    { echo -e "${DIM}  [SKIP]  $*${RESET}"; }
error()   { echo -e "${RED}  [ERR ]${RESET}  $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}── $* ──────────────────────────────────────────${RESET}"; }
dry_run() { echo -e "${DIM}  [DRY]   $*${RESET}"; }

# Dry run wrapper
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        dry_run "$*"
    else
        "$@"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# ── Platform Detection ────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

is_wsl() {
    if [[ "$OS" == "Linux" ]]; then
        if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

is_apple_silicon() {
    if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
        return 0
    fi
    return 1
}

case "$OS" in
    Linux*)
        if is_wsl; then PLATFORM="wsl"; else PLATFORM="linux"; fi
        ;;
    Darwin*)
        PLATFORM="macos"
        ;;
    MINGW*|MSYS*|CYGWIN*)
        PLATFORM="windows"
        warn "Windows detected (native). This script works best with WSL2."
        ;;
    *)
        error "Unsupported operating system: $OS"
        ;;
esac

echo -e "${BOLD}"
echo "  🦞  MyClaw — Installer"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  Platform: ${OS} ${ARCH}"
echo -e "${RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# 1. SYSTEM PACKAGES
# ─────────────────────────────────────────────────────────────────────────────
header "1. System packages"

if [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "wsl" ]; then
    APT_UPDATED=0
    ensure_apt() {
        local pkg="$1"
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
            skip "apt: $pkg already installed"
        else
            if [[ "$APT_UPDATED" -eq 0 ]]; then
                info "Running apt-get update..."
                run sudo apt-get update -qq
                APT_UPDATED=1
            fi
            info "Installing apt package: $pkg"
            run sudo apt-get install -y -qq "$pkg"
            success "apt: $pkg installed"
        fi
    }
    ensure_apt "python3"
    ensure_apt "python3-pip"
    ensure_apt "python3-venv"
    ensure_apt "python3-dev"
    ensure_apt "git"
    ensure_apt "curl"
    ensure_apt "sqlite3"
    ensure_apt "build-essential"
elif [ "$PLATFORM" = "macos" ]; then
    if ! command -v brew &>/dev/null; then
        error "Homebrew not found. Please install it first: https://brew.sh"
    fi
    ensure_brew() {
        local pkg="$1"
        if brew list "$pkg" &>/dev/null; then
            skip "brew: $pkg already installed"
        else
            info "Installing brew package: $pkg"
            run brew install "$pkg"
        fi
    }
    ensure_brew "python3"
    ensure_brew "git"
    ensure_brew "sqlite3"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. PYTHON VERSION CHECK
# ─────────────────────────────────────────────────────────────────────────────
header "2. Python version"

PYTHON_BIN=$(command -v python3 || true)
PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

if [[ $(echo "$PY_VERSION < 3.10" | bc -l) -eq 1 ]]; then
    error "Python $PY_VERSION is too old. MyClaw requires 3.10+."
fi
success "Python $PY_VERSION — OK"

# ─────────────────────────────────────────────────────────────────────────────
# 3. VIRTUAL ENVIRONMENT
# ─────────────────────────────────────────────────────────────────────────────
header "3. Virtual environment"

if [[ "$RECREATE_VENV" == "true" ]]; then
    run rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    run "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
run pip install --quiet --upgrade pip setuptools wheel

# ─────────────────────────────────────────────────────────────────────────────
# 4. CORE PYTHON DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────
header "4. Core Python dependencies"

ensure_pip() {
    local pkg="$1"
    local install_name="${2:-$1}"
    local spec="${3:-}"
    if "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
        skip "pip: $install_name installed"
    else
        info "Installing $install_name..."
        run "$VENV_DIR/bin/pip" install --quiet "${install_name}${spec}"
    fi
}

ensure_pip "telegram" "python-telegram-bot[job-queue]" ">=21.4"
ensure_pip "requests" "requests" ">=2.31.0"
ensure_pip "yaml" "pyyaml" ">=6.0"
ensure_pip "rich" "rich" ">=13.0"
ensure_pip "pydantic" "pydantic" ">=2.0"
ensure_pip "apscheduler" "apscheduler" ">=3.10"
ensure_pip "openai" "openai" ">=1.0"
ensure_pip "httpx" "httpx" ""
ensure_pip "scrapling" "scrapling[all]" ">=0.4.2"
ensure_pip "fastapi" "fastapi" ">=0.109.0"
ensure_pip "uvicorn" "uvicorn" ">=0.27.0"
ensure_pip "websockets" "websockets" ">=12.0.0"
ensure_pip "mcp" "mcp" ">=1.1.2"
ensure_pip "numpy" "numpy" ">=1.24.0"
ensure_pip "sentence_transformers" "sentence-transformers" ">=2.5.0"
ensure_pip "watchdog" "watchdog" ">=4.0.0"

run "$VENV_DIR/bin/scrapling" install --force

# ─────────────────────────────────────────────────────────────────────────────
# 5. OPTIONAL LLM PROVIDER SDKs
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "5. Optional SDKs"
    if prompt_yes "  Install Anthropic Claude SDK?"; then
        run pip install --quiet anthropic
    fi
    if prompt_yes "  Install Google Gemini SDK?"; then
        run pip install --quiet google-generativeai
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. OPTIONAL: Ollama
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "6. Ollama"
    if ! command -v ollama &>/dev/null; then
        if prompt_yes "  Install Ollama for local LLMs?"; then
            run curl -fsSL https://ollama.com/install.sh | sh
        fi
    else
        success "Ollama already installed."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6.5 WEB UI (Node.js & npm)
# ─────────────────────────────────────────────────────────────────────────────
header "6.5 Web UI frontend"

if command -v npm &>/dev/null; then
    info "Node.js/npm found: $(node --version 2>/dev/null)"
    if [[ -d "$SCRIPT_DIR/webui" ]]; then
        info "Installing WebUI dependencies (npm install)..."
        if [[ "$DRY_RUN" == "false" ]]; then
            (cd "$SCRIPT_DIR/webui" && npm install --quiet)
            success "WebUI dependencies installed."
        else
            dry_run "cd webui && npm install"
        fi
    fi
else
    warn "Node.js/npm NOT found. Skipping Web UI frontend setup."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. DATA DIRECTORIES
# ─────────────────────────────────────────────────────────────────────────────
header "7. Data directories"

make_dir() {
    if [[ ! -d "$1" ]]; then
        run mkdir -p "$1"
        success "Created: $1"
    else
        skip "Exists: $1"
    fi
}

make_dir "$HOME/.myclaw"
make_dir "$HOME/.myclaw/workspace"
make_dir "$HOME/.myclaw/knowledge"
make_dir "$HOME/.myclaw/tools"
make_dir "$HOME/.myclaw/skills"
make_dir "$HOME/.myclaw/backups"
make_dir "$HOME/.myclaw/task_logs"
make_dir "$HOME/.myclaw/benchmarks"
make_dir "$HOME/.myclaw/newtech"
make_dir "$HOME/.myclaw/semantic_cache"
make_dir "$HOME/.myclaw/sandbox"
make_dir "$HOME/.myclaw/audit"
make_dir "$HOME/.myclaw/medic"

# ─────────────────────────────────────────────────────────────────────────────
# 8. OPTIONAL: systemd service
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" && "$PLATFORM" != "macos" ]]; then
    header "8. systemd service"
    SERVICE_FILE="/etc/systemd/system/myclaw.service"
    if [[ ! -f "$SERVICE_FILE" ]] && prompt_yes "  Install systemd service?"; then
        run sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MyClaw Gateway
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/cli.py gateway
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
        run sudo systemctl daemon-reload
        run sudo systemctl enable myclaw.service
        success "Service installed."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────
header "9. Verification"

if [[ "$DRY_RUN" == "false" ]]; then
    FAILED=0
    check_import() {
        if ! "$VENV_DIR/bin/python" -c "import $1" 2>/dev/null; then
            warn "Import $1 FAILED"
            FAILED=$((FAILED + 1))
        fi
    }
    check_import "telegram"
    check_import "requests"
    check_import "mcp"
    check_import "fastapi"
    
    if [[ "$FAILED" -eq 0 ]]; then success "All core components verified."; fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
header "✅  MyClaw is ready!"

echo "  Next steps:"
echo "  1. source venv/bin/activate"
echo "  2. python cli.py onboard"
echo "  3. python cli.py agent"
echo "  4. python cli.py webui  (Dashboard)"
echo ""
echo "  ─ CLI Examples ───────────────────────────────"
echo "  python cli.py knowledge sync"
echo "  python cli.py knowledge search 'how to use'"
echo ""
