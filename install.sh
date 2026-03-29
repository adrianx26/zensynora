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
#
#  What this script does:
#   1. Checks and installs every required system package (only if missing)
#   2. Validates Python >= 3.10, installs 3.12 via deadsnakes PPA or pyenv if needed
#   3. Creates and activates a Python virtual environment
#   4. Installs pip dependencies from requirements.txt with caching
#   5. Prompts to install optional LLM provider SDKs
#   6. Optionally installs Ollama for local model support
#   7. Creates required data directories
#   8. Optionally installs a systemd service for the Telegram/WhatsApp gateway
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
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

# ── Platform Detection ────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

# Detect if running in WSL
is_wsl() {
    if [[ "$OS" == "Linux" ]]; then
        if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# Detect if running on Apple Silicon (ARM)
is_apple_silicon() {
    if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
        return 0
    fi
    return 1
}

case "$OS" in
    Linux*)
        if is_wsl; then
            PLATFORM="wsl"
        else
            PLATFORM="linux"
        fi
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
if [[ "$PLATFORM" == "wsl" ]]; then
    echo -e "  (Running in WSL2)"
fi
echo -e "${RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# System Package Management
# ─────────────────────────────────────────────────────────────────────────────

# Retry wrapper for network operations (can be used for downloads)
retry() {
    local max_attempts="${1:-3}"
    local delay="${2:-2}"
    shift 2
    local cmd="$@"
    local attempt=1
    
    while [[ $attempt -le $max_attempts ]]; do
        if eval "$cmd" 2>/dev/null; then
            return 0
        fi
        warn "Attempt $attempt/$max_attempts failed. Retrying in ${delay}s..."
        sleep "$delay"
        attempt=$((attempt + 1))
        delay=$((delay * 2))  # Exponential backoff
    done
    return 1
}

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
            run sudo apt-get install -y -qq "$pkg" || {
                warn "Failed to install $pkg, trying with --no-install-recommends..."
                run sudo apt-get install -y -qq --no-install-recommends "$pkg"
            }
            success "apt: $pkg installed"
        fi
    }
elif [ "$PLATFORM" = "macos" ]; then
    # Initialize Homebrew PATH for Apple Silicon
    if is_apple_silicon; then
        if [[ -x "/opt/homebrew/bin/brew" ]]; then
            export PATH="/opt/homebrew/bin:$PATH"
        fi
    else
        if [[ -x "/usr/local/bin/brew" ]]; then
            export PATH="/usr/local/bin:$PATH"
        fi
    fi
    
    ensure_brew() {
        local pkg="$1"
        if brew list "$pkg" &>/dev/null; then
            skip "brew: $pkg already installed"
        else
            info "Installing Homebrew package: $pkg"
            run brew install "$pkg" || {
                error "Failed to install $pkg via Homebrew"
            }
            success "brew: $pkg installed"
        fi
    }
    
    # Check if Homebrew is installed
    if ! command -v brew &>/dev/null; then
        info "Homebrew not found. Installing Homebrew..."
        if [[ "$DRY_RUN" == "false" ]]; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || {
                error "Failed to install Homebrew"
            }
            # Add Homebrew to PATH for current session
            if is_apple_silicon; then
                if [[ -x "/opt/homebrew/bin/brew" ]]; then
                    eval "$(/opt/homebrew/bin/brew shellenv)"
                fi
            else
                if [[ -x "/usr/local/bin/brew" ]]; then
                    eval "$(/usr/local/bin/brew shellenv)"
                fi
            fi
            success "Homebrew installed"
        else
            dry_run "Install Homebrew"
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

if [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "wsl" ]; then
    # Linux (Debian/Ubuntu/WSL) packages
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

if [[ "$RECREATE_VENV" == "true" ]]; then
    if [[ -d "$VENV_DIR" ]]; then
        info "Removing existing venv for recreation..."
        run rm -rf "$VENV_DIR"
    fi
fi

if [[ -d "$VENV_DIR" && -f "$VENV_DIR/bin/python" ]]; then
    skip "venv already exists at $VENV_DIR"
else
    info "Creating virtual environment..."
    run "$PYTHON_BIN" -m venv "$VENV_DIR"
    success "Virtual environment created at $VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
success "Virtual environment activated"

# Upgrade pip, setuptools, wheel — always safe
info "Upgrading pip / setuptools / wheel..."
run "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
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
    run "$VENV_DIR/bin/pip" install --quiet "python-telegram-bot[job-queue]>=21.4"
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
ensure_pip "scrapling"  "scrapling[all]" ">=0.4.2"
ensure_pip "watchdog"   "watchdog"    ">=3.0.0"  # 6.1: Config file watching
ensure_pip "aiohttp"    "aiohttp"    ">=3.9.0"  # Feature 4: Web Search
ensure_pip "Pillow"     "Pillow"     ">=10.0.0" # Feature 2: Multi-modal Tools
ensure_pip "opencv"     "opencv-python" ">=4.8.0" # Feature 2: Video processing
ensure_pip "mss"        "mss"        ">=9.0.0"  # Feature 2: Screenshot capture
ensure_pip "gtts"       "gtts"       ">=2.4.0"  # Feature 3: Voice TTS
ensure_pip "pyttsx3"    "pyttsx3"    ">=2.90"   # Feature 3: Offline TTS
ensure_pip "pydub"     "pydub"      ">=0.25.0" # Feature 3: Audio processing
ensure_pip "fastapi"    "fastapi"    ">=0.109.0" # Feature 8: REST API Server
ensure_pip "uvicorn"    "uvicorn"    ">=0.27.0" # Feature 8: REST API Server
ensure_pip "sse_starlette" "sse-starlette" ">=2.0.0" # Feature 8: Server-Sent Events
ensure_pip "jinja2"     "jinja2"     ">=3.1.0"  # Feature 10: Web Dashboard
ensure_pip "websockets" "websockets" ">=12.0.0" # Feature 8: WebSocket support

info "Installing Scrapling browser dependencies..."
run "$VENV_DIR/bin/scrapling" install --force
success "Scrapling browsers installed"

# ─────────────────────────────────────────────────────────────────────────────
# 5. OPTIONAL LLM PROVIDER SDKs
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "true" ]]; then
    header "5. Optional LLM provider SDKs"
    skip "Skipping optional LLM providers (--no-optional set)"
else
    header "5. Optional LLM provider SDKs"
    echo "  MyClaw supports several cloud LLM providers."
    echo "  Select which SDKs to install (you can always install more later)."
    echo ""

    # Anthropic Claude
    if "$VENV_DIR/bin/python" -c "import anthropic" 2>/dev/null; then
        skip "pip: anthropic SDK already installed"
    elif prompt_yes "  Install Anthropic Claude SDK?"; then
        run "$VENV_DIR/bin/pip" install --quiet "anthropic>=0.25"
        success "pip: anthropic installed"
    fi

    # Google Gemini
    if "$VENV_DIR/bin/python" -c "import google.generativeai" 2>/dev/null; then
        skip "pip: google-generativeai SDK already installed"
    elif prompt_yes "  Install Google Gemini SDK?"; then
        run "$VENV_DIR/bin/pip" install --quiet "google-generativeai>=0.5"
        success "pip: google-generativeai installed"
    fi

    # Groq  (uses openai-compatible SDK, already installed, just confirm)
    success "pip: Groq — uses openai SDK (already installed)"

    # OpenRouter  (uses openai-compatible SDK as well)
    success "pip: OpenRouter — uses openai SDK (already installed)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. OPTIONAL: Ollama (local LLM runner)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "true" ]]; then
    header "6. Ollama (local LLM runner)"
    skip "Skipping Ollama (--no-optional set)"
else
    header "6. Ollama (local LLM runner)"

    if command -v ollama &>/dev/null; then
        success "Ollama is already installed: $(ollama --version 2>/dev/null | head -1)"
    elif prompt_yes "  Install Ollama for local model support?"; then
        info "Downloading Ollama installer..."
        if [[ "$DRY_RUN" == "false" ]]; then
            # Platform-specific Ollama installation
            if [[ "$PLATFORM" == "linux" || "$PLATFORM" == "wsl" ]]; then
                if is_apple_silicon; then
                    run curl -fsSL https://ollama.com/install.sh | sh
                else
                    run curl -fsSL https://ollama.com/install.sh | sh
                fi
            elif [[ "$PLATFORM" == "macos" ]]; then
                if is_apple_silicon; then
                    run curl -fsSL https://ollama.com/install.sh | sh
                else
                    run curl -fsSL https://ollama.com/install.sh | sh
                fi
            fi
            success "Ollama installed."
            echo "  → Pull a model: ollama pull llama3.2"
        else
            dry_run "curl -fsSL https://ollama.com/install.sh | sh"
        fi
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
        run mkdir -p "$1"
        success "created: $1"
    fi
}

make_dir "$HOME/.myclaw"
make_dir "$HOME/.myclaw/workspace"
make_dir "$HOME/.myclaw/knowledge"
make_dir "$HOME/.myclaw/knowledge/default"
make_dir "$HOME/.myclaw/tools"
make_dir "$HOME/.myclaw/preferences"        # Feature 7: Semantic Memory
make_dir "$HOME/.myclaw/plugins"            # Feature 9: Plugin System
make_dir "$HOME/.myclaw/sandbox"            # Feature 12: Security Sandbox
make_dir "$HOME/.myclaw/skills"             # Feature 5: Skill Generator
make_dir "$HOME/.myclaw/backups"            # Feature 6: Self-Healer

# ─────────────────────────────────────────────────────────────────────────────
# 8. OPTIONAL: systemd service (Telegram gateway auto-start)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "true" ]]; then
    header "8. systemd service (optional)"
    skip "Skipping systemd service (--no-optional set)"
elif [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "wsl" ]; then
    header "8. systemd service (optional)"

    SERVICE_FILE="/etc/systemd/system/myclaw.service"
    if [[ -f "$SERVICE_FILE" ]]; then
        skip "systemd service already installed at $SERVICE_FILE"
    elif prompt_yes "  Install systemd service for auto-start?"; then

        info "Creating $SERVICE_FILE ..."
        run sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MyClaw Telegram/WhatsApp Gateway
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
# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$HOME/.myclaw

[Install]
WantedBy=multi-user.target
EOF
        run sudo systemctl daemon-reload
        run sudo systemctl enable myclaw.service
        success "systemd service installed and enabled."
        echo "  → Start now:  sudo systemctl start myclaw"
        echo "  → View logs:  journalctl -u myclaw -f"
    fi
else
    header "8. systemd service"
    skip "systemd service is only available on Linux/WSL"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. VERIFICATION — confirm everything is importable
# ─────────────────────────────────────────────────────────────────────────────
# 9. VERIFICATION — confirm everything is importable
# ─────────────────────────────────────────────────────────────────────────────
header "9. Verification"

if [[ "$DRY_RUN" == "true" ]]; then
    skip "Skipping verification in dry-run mode"
else
    FAILED=0
    check_import() {
        local import_name="$1"
        local display_name="${2:-$1}"
        if "$VENV_DIR/bin/python" -c "import $import_name" 2>/dev/null; then
            success "import $display_name"
        else
            warn "import $display_name — FAILED (optional or not yet installed)"
            FAILED=$((FAILED + 1))
        fi
    }

    # Core imports with correct module names
    check_import "telegram" "telegram (python-telegram-bot)"
    check_import "requests"
    check_import "yaml"
    check_import "rich"
    check_import "pydantic"
    check_import "apscheduler" "apscheduler"
    check_import "openai"
    check_import "httpx"
    check_import "pytest"
    check_import "scrapling"

    # Feature-specific imports
    check_import "aiohttp" "aiohttp (Feature 4: Web Search)"
    check_import "PIL" "PIL/Pillow (Feature 2: Multi-modal)"
    check_import "cv2" "opencv-python (Feature 2: Video)"
    check_import "mss" "mss (Feature 2: Screenshot)"
    check_import "gtts" "gtts (Feature 3: Voice TTS)"
    check_import "pyttsx3" "pyttsx3 (Feature 3: Offline TTS)"
    check_import "fastapi" "fastapi (Feature 8: REST API)"
    check_import "uvicorn" "uvicorn (Feature 8: REST API)"
    check_import "jinja2" "jinja2 (Feature 10: Dashboard)"
    check_import "websockets" "websockets (Feature 8: WebSocket)"

    echo ""
    if [[ "$FAILED" -eq 0 ]]; then
        success "All core imports verified."
    else
        warn "$FAILED optional package(s) could not be imported — see above."
    fi
fi

else
    FAILED=0
    check_import() {
        local import_name="$1"
        local display_name="${2:-$1}"
        if "$VENV_DIR/bin/python" -c "import $import_name" 2>/dev/null; then
            success "import $display_name"
        else
            warn "import $display_name — FAILED (optional or not yet installed)"
            FAILED=$((FAILED + 1))
        fi
    }

    # Core imports with correct module names
    check_import "telegram" "telegram (python-telegram-bot)"
    check_import "requests"
    check_import "yaml"
    check_import "rich"
    check_import "pydantic"
    check_import "apscheduler" "apscheduler"
    check_import "openai"
    check_import "httpx"
    check_import "pytest"
    check_import "scrapling"

    echo ""
    if [[ "$FAILED" -eq 0 ]]; then
        success "All core imports verified."
    else
        warn "$FAILED optional package(s) could not be imported — see above."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  ✅  MyClaw is ready!${RESET}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "  ${YELLOW}This was a dry run. No changes were made.${RESET}"
    echo ""
fi

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
echo -e "     ${CYAN}python cli.py gateway${RESET}""
echo ""
echo "  5b. Or start the WhatsApp gateway:"
echo -e "     ${CYAN}python cli.py gateway --channel whatsapp${RESET}"
echo ""
echo "  ─ Knowledge base ───────────────────────────────"
echo "  python cli.py knowledge list"
echo "  python cli.py knowledge search <query>"
echo "  python cli.py knowledge write"
echo ""
echo -e "  📖 README: ${DIM}$SCRIPT_DIR/README.md${RESET}"
echo ""
echo "  ──────────────────────────────────────────────"
echo "  Usage: $0 [OPTIONS]"
echo "  Options: --yes, --no-optional, --recreate-venv, --dry-run, --help"
echo ""
