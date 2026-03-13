#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MyClaw — Cross-Platform Install Script
#  Tested on: Ubuntu 22.04 / 24.04 (LTS), macOS 13+, Windows (WSL2)
#  Usage:  chmod +x install.sh && ./install.sh
#
#  What this script does:
#   1. Checks and installs every required system package (only if missing)
#   2. Validates Python >= 3.10, installs 3.12 via deadsnakes PPA if needed
#   3. Creates and activates a Python virtual environment
#   4. Checks and installs every pip dependency from requirements.txt
#   5. Prompts to install optional LLM provider SDKs
#   6. Optionally installs Ollama for local model support
#   7. Creates required data directories
#   8. Optionally installs a systemd service for the Telegram gateway
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  [INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}  [ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${RESET}  $*"; }
skip()    { echo -e "${DIM}  [SKIP]  $*${RESET}"; }
error()   { echo -e "${RED}  [ERR ]${RESET}  $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}── $* ──────────────────────────────────────────${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# ── Platform Detection ────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux*)
        PLATFORM="linux"
        ;;
    Darwin*)
        PLATFORM="macos"
        ;;
    MINGW*|MSYS*|CYGWIN*)
        PLATFORM="windows"
        warn "Windows detected. This script works best with WSL2."
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
# System Package Management
# ─────────────────────────────────────────────────────────────────────────────

if [ "$PLATFORM" = "linux" ]; then
    APT_UPDATED=0
    ensure_apt() {
        local pkg="$1"
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
            skip "apt: $pkg already installed"
        else
            if [[ "$APT_UPDATED" -eq 0 ]]; then
                info "Running apt-get update..."
                sudo apt-get update -qq
                APT_UPDATED=1
            fi
            info "Installing apt package: $pkg"
            sudo apt-get install -y -qq "$pkg"
            success "apt: $pkg installed"
        fi
    }
elif [ "$PLATFORM" = "macos" ]; then
    ensure_brew() {
        local pkg="$1"
        if brew list "$pkg" &>/dev/null; then
            skip "brew: $pkg already installed"
        else
            info "Installing Homebrew package: $pkg"
            brew install "$pkg"
            success "brew: $pkg installed"
        fi
    }
    # Check if Homebrew is installed
    if ! command -v brew &>/dev/null; then
        info "Homebrew not found. Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add Homebrew to PATH temporarily for this session
        if [ -x "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [ -x "/usr/local/bin/brew" ]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# HELPER: check if a pip package is installed inside the venv; install if not
# ─────────────────────────────────────────────────────────────────────────────
ensure_pip() {
    local pkg="$1"          # import name for Python check
    local install_name="${2:-$1}"  # pip install name (may differ)
    local spec="${3:-}"     # optional version spec e.g. ">=1.0"

    if "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
        skip "pip: $install_name already installed"
    else
        info "Installing pip package: $install_name${spec}"
        "$VENV_DIR/bin/pip" install --quiet "${install_name}${spec}"
        success "pip: $install_name installed"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. SYSTEM PACKAGES
# ─────────────────────────────────────────────────────────────────────────────
header "1. System packages"

if [ "$PLATFORM" = "linux" ]; then
    # Linux (Debian/Ubuntu) packages
    ensure_apt "python3"
    ensure_apt "python3-pip"
    ensure_apt "python3-venv"
    ensure_apt "python3-dev"
    ensure_apt "git"
    ensure_apt "curl"
    ensure_apt "wget"
    ensure_apt "sqlite3"
    ensure_apt "libsqlite3-dev"
    ensure_apt "build-essential"
    ensure_apt "libssl-dev"
    ensure_apt "libffi-dev"
    ensure_apt "ca-certificates"
    ensure_apt "software-properties-common"
elif [ "$PLATFORM" = "macos" ]; then
    # macOS packages
    ensure_brew "python3"
    ensure_brew "git"
    ensure_brew "curl"
    ensure_brew "wget"
    ensure_brew "sqlite3"
else
    # Windows (WSL2) falls through to Linux
    warn "Windows/WSL2 detected, assuming Linux compatibility"
    ensure_apt "python3"
    ensure_apt "python3-pip"
    ensure_apt "python3-venv"
    ensure_apt "python3-dev"
    ensure_apt "git"
    ensure_apt "curl"
    ensure_apt "wget"
    ensure_apt "sqlite3"
    ensure_apt "libsqlite3-dev"
    ensure_apt "build-essential"
    ensure_apt "libssl-dev"
    ensure_apt "libffi-dev"
    ensure_apt "ca-certificates"
    ensure_apt "software-properties-common"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. PYTHON VERSION CHECK
# ─────────────────────────────────────────────────────────────────────────────
header "2. Python version"

PYTHON_BIN=$(command -v python3 || true)
[[ -z "$PYTHON_BIN" ]] && error "python3 not found. Please install Python 3.10+ manually."

PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
    warn "System Python is $PY_VERSION — MyClaw requires Python 3.10+."
    info "Installing Python 3.12 via deadsnakes PPA..."
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    ensure_apt "python3.12"
    ensure_apt "python3.12-venv"
    ensure_apt "python3.12-dev"
    PYTHON_BIN=$(command -v python3.12)
    PY_VERSION="3.12"
    success "Python 3.12 set as active interpreter."
else
    success "Python $PY_VERSION — OK"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. VIRTUAL ENVIRONMENT
# ─────────────────────────────────────────────────────────────────────────────
header "3. Virtual environment"

if [[ -d "$VENV_DIR" && -f "$VENV_DIR/bin/python" ]]; then
    skip "venv already exists at $VENV_DIR"
else
    info "Creating virtual environment..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    success "Virtual environment created at $VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
success "Virtual environment activated"

# Upgrade pip, setuptools, wheel — always safe
info "Upgrading pip / setuptools / wheel..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
success "pip, setuptools, wheel up-to-date"

# ─────────────────────────────────────────────────────────────────────────────
# 4. CORE PYTHON DEPENDENCIES  (from requirements.txt)
#    Each package is checked individually before installing.
# ─────────────────────────────────────────────────────────────────────────────
header "4. Core Python dependencies"

# python-telegram-bot[job-queue]
if "$VENV_DIR/bin/python" -c "import telegram" 2>/dev/null; then
    skip "pip: python-telegram-bot already installed"
else
    info "Installing pip package: python-telegram-bot[job-queue]>=21.4"
    "$VENV_DIR/bin/pip" install --quiet "python-telegram-bot[job-queue]>=21.4"
    success "pip: python-telegram-bot installed"
fi

ensure_pip "requests"   "requests"   ">=2.31.0"
ensure_pip "yaml"       "pyyaml"     ">=6.0"
ensure_pip "rich"       "rich"       ">=13.0"
ensure_pip "pydantic"   "pydantic"   ">=2.0"
ensure_pip "apscheduler" "apscheduler" ">=3.10"
ensure_pip "openai"     "openai"     ">=1.0"
ensure_pip "httpx"      "httpx"      ""
ensure_pip "pytest"     "pytest"     ""
ensure_pip "pytest_asyncio" "pytest-asyncio" ""

# ─────────────────────────────────────────────────────────────────────────────
# 5. OPTIONAL LLM PROVIDER SDKs
# ─────────────────────────────────────────────────────────────────────────────
header "5. Optional LLM provider SDKs"
echo "  MyClaw supports several cloud LLM providers."
echo "  Select which SDKs to install (you can always install more later)."
echo ""

# Anthropic Claude
if "$VENV_DIR/bin/python" -c "import anthropic" 2>/dev/null; then
    skip "pip: anthropic SDK already installed"
else
    read -rp "  Install Anthropic Claude SDK? [y/N]: " OPT
    if [[ "${OPT,,}" == "y" || "${OPT,,}" == "yes" ]]; then
        "$VENV_DIR/bin/pip" install --quiet "anthropic>=0.25"
        success "pip: anthropic installed"
    fi
fi

# Google Gemini
if "$VENV_DIR/bin/python" -c "import google.generativeai" 2>/dev/null; then
    skip "pip: google-generativeai SDK already installed"
else
    read -rp "  Install Google Gemini SDK? [y/N]: " OPT
    if [[ "${OPT,,}" == "y" || "${OPT,,}" == "yes" ]]; then
        "$VENV_DIR/bin/pip" install --quiet "google-generativeai>=0.5"
        success "pip: google-generativeai installed"
    fi
fi

# Groq  (uses openai-compatible SDK, already installed, just confirm)
success "pip: Groq — uses openai SDK (already installed)"

# OpenRouter  (uses openai-compatible SDK as well)
success "pip: OpenRouter — uses openai SDK (already installed)"

# ─────────────────────────────────────────────────────────────────────────────
# 6. OPTIONAL: Ollama (local LLM runner)
# ─────────────────────────────────────────────────────────────────────────────
header "6. Ollama (local LLM runner)"

if command -v ollama &>/dev/null; then
    success "Ollama is already installed: $(ollama --version 2>/dev/null | head -1)"
else
    read -rp "  Install Ollama for local model support? [y/N]: " OPT
    if [[ "${OPT,,}" == "y" || "${OPT,,}" == "yes" ]]; then
        info "Downloading Ollama installer..."
        curl -fsSL https://ollama.com/install.sh | sh
        success "Ollama installed."
        echo "  → Pull a model: ollama pull llama3.2"
    else
        skip "Ollama skipped. Install later with:  curl -fsSL https://ollama.com/install.sh | sh"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. DATA DIRECTORIES
# ─────────────────────────────────────────────────────────────────────────────
header "7. Data directories"

make_dir() {
    if [[ -d "$1" ]]; then
        skip "directory exists: $1"
    else
        mkdir -p "$1"
        success "created: $1"
    fi
}

make_dir "$HOME/.myclaw"
make_dir "$HOME/.myclaw/workspace"
make_dir "$HOME/.myclaw/knowledge"
make_dir "$HOME/.myclaw/knowledge/default"
make_dir "$HOME/.myclaw/tools"

# ─────────────────────────────────────────────────────────────────────────────
# 8. OPTIONAL: systemd service (Telegram gateway auto-start)
# ─────────────────────────────────────────────────────────────────────────────
header "8. systemd service (optional)"

SERVICE_FILE="/etc/systemd/system/myclaw.service"
if [[ -f "$SERVICE_FILE" ]]; then
    skip "systemd service already installed at $SERVICE_FILE"
else
    echo "  Installs a service so the Telegram gateway starts automatically on boot."
    read -rp "  Install systemd service? [y/N]: " OPT
    if [[ "${OPT,,}" == "y" || "${OPT,,}" == "yes" ]]; then
        info "Creating $SERVICE_FILE ..."
        sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MyClaw Telegram Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/cli.py gateway
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable myclaw.service
        success "systemd service installed and enabled."
        echo "  → Start now:  sudo systemctl start myclaw"
        echo "  → View logs:  journalctl -u myclaw -f"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. VERIFICATION — confirm everything is importable
# ─────────────────────────────────────────────────────────────────────────────
header "9. Verification"

FAILED=0
check_import() {
    if "$VENV_DIR/bin/python" -c "import $1" 2>/dev/null; then
        success "import $1"
    else
        warn "import $1 — FAILED (optional or not yet installed)"
        FAILED=$((FAILED + 1))
    fi
}

check_import telegram
check_import requests
check_import yaml
check_import rich
check_import pydantic
check_import apscheduler
check_import openai
check_import httpx
check_import pytest

echo ""
if [[ "$FAILED" -eq 0 ]]; then
    success "All core imports verified."
else
    warn "$FAILED optional package(s) could not be imported — see above."
fi

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  ✅  MyClaw is ready!${RESET}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Activate the venv:"
echo -e "     ${CYAN}source $VENV_DIR/bin/activate${RESET}"
echo ""
echo "  2. Run the onboarding wizard:"
echo -e "     ${CYAN}python cli.py onboard${RESET}"
echo ""
echo "  3. (Optional) Edit config manually:"
echo -e "     ${CYAN}nano ~/.myclaw/config.json${RESET}"
echo ""
echo "  4. Start the console agent:"
echo -e "     ${CYAN}python cli.py agent${RESET}"
echo ""
echo "  5. Or start the Telegram gateway:"
echo -e "     ${CYAN}python cli.py gateway${RESET}"
echo ""
echo "  ─ Knowledge base ───────────────────────────────"
echo "  python cli.py knowledge list"
echo "  python cli.py knowledge search <query>"
echo "  python cli.py knowledge write"
echo ""
echo -e "  📖 README: ${DIM}$SCRIPT_DIR/README.md${RESET}"
echo ""
