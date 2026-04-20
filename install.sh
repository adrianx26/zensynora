#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  ZenSynora (MyClaw) — Universal Install Script
#  Version: 2.0.0
#
#  Supported Platforms: Linux (native/WSL2), macOS 13+, Windows (WSL2 recommended)
#  Deployment Modes: Docker (recommended) | Traditional (venv-based)
#
#  Usage:
#    chmod +x install.sh && ./install.sh
#
#  Options:
#    --yes, -y              Non-interactive mode (assume yes to prompts)
#    --docker               Force Docker deployment
#    --no-docker            Force traditional (venv) deployment
#    --docker-tag TAG       Set Docker image tag (default: zensynora:latest)
#    --docker-build-args    Pass additional args to docker build
#    --docker-profile PROF  Docker Compose profile: default, full, redis, ollama
#    --no-optional          Skip optional components (LLM providers, Ollama, systemd)
#    --recreate-venv        Force recreation of virtual environment (traditional only)
#    --dry-run              Show what would be done without doing it
#    --help, -h             Show this help message
#
#  Environment Variables:
#    ZENSYNORA_INSTALL_MODE=docker|traditional   Override deployment mode
#    DOCKER_DEFAULT_PLATFORM=linux/amd64         Override Docker platform
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# CLI Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────
INTERACTIVE=true
FORCE_DOCKER=false
FORCE_TRADITIONAL=false
SKIP_OPTIONAL=false
RECREATE_VENV=false
DRY_RUN=false
DOCKER_TAG="zensynora:latest"
DOCKER_BUILD_ARGS=""
DOCKER_PROFILE="default"

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            INTERACTIVE=false
            shift
            ;;
        --docker)
            FORCE_DOCKER=true
            shift
            ;;
        --no-docker)
            FORCE_TRADITIONAL=true
            shift
            ;;
        --docker-tag)
            DOCKER_TAG="${2:-zensynora:latest}"
            shift 2
            ;;
        --docker-build-args)
            DOCKER_BUILD_ARGS="$2"
            shift 2
            ;;
        --docker-profile)
            DOCKER_PROFILE="$2"
            shift 2
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
            echo "Deployment Mode:"
            echo "  --docker               Force Docker deployment (recommended)"
            echo "  --no-docker            Force traditional venv deployment"
            echo "  --docker-tag TAG       Docker image tag (default: zensynora:latest)"
            echo "  --docker-build-args    Additional 'docker build' arguments"
            echo "  --docker-profile PROF  Compose profile: default|full|redis|ollama"
            echo ""
            echo "General Options:"
            echo "  --yes, -y              Non-interactive mode"
            echo "  --no-optional          Skip optional components"
            echo "  --recreate-venv        Force recreation of venv (traditional only)"
            echo "  --dry-run              Show what would be done without doing it"
            echo "  --help, -h             Show this help message"
            echo ""
            echo "Environment Variables:"
            echo "  ZENSYNORA_INSTALL_MODE=docker|traditional"
            echo "  DOCKER_DEFAULT_PLATFORM=linux/amd64"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Logging & Utility Functions
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  [INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}  [ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${RESET}  $*"; }
skip()    { echo -e "${DIM}  [SKIP]  $*${RESET}"; }
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

# Prompt user for yes/no; in non-interactive mode, use default
prompt_yes() {
    local prompt="$1"
    local default="${2:-N}"

    if [[ "$INTERACTIVE" == "false" ]]; then
        [[ "$default" == "Y" ]]
        return
    fi

    local response
    read -rp "$prompt [y/N]: " response
    case "${response,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# Prompt user with a default value
prompt_default() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [[ "$INTERACTIVE" == "false" ]]; then
        printf -v "$var_name" '%s' "$default"
        return
    fi

    local response
    read -rp "$prompt [$default]: " response
    if [[ -z "$response" ]]; then
        printf -v "$var_name" '%s' "$default"
    else
        printf -v "$var_name" '%s' "$response"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Platform & Environment Detection
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

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
    [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]
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
        warn "Native Windows detected. ZenSynora works best in WSL2."
        ;;
    *)
        error "Unsupported operating system: $OS"
        ;;
esac

# ─────────────────────────────────────────────────────────────────────────────
# Docker Detection
# ─────────────────────────────────────────────────────────────────────────────
DOCKER_AVAILABLE=false
DOCKER_COMPOSE_AVAILABLE=false
DOCKER_COMPOSE_CMD=""

detect_docker() {
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            DOCKER_AVAILABLE=true
        else
            warn "Docker CLI found but daemon is not running or user lacks permissions."
        fi
    fi

    if [[ "$DOCKER_AVAILABLE" == "true" ]]; then
        # Detect docker compose plugin (v2) or standalone docker-compose (v1)
        if docker compose version >/dev/null 2>&1; then
            DOCKER_COMPOSE_AVAILABLE=true
            DOCKER_COMPOSE_CMD="docker compose"
        elif command -v docker-compose >/dev/null 2>&1; then
            DOCKER_COMPOSE_AVAILABLE=true
            DOCKER_COMPOSE_CMD="docker-compose"
        fi
    fi
}

detect_docker

# ─────────────────────────────────────────────────────────────────────────────
# Deployment Mode Selection
# ─────────────────────────────────────────────────────────────────────────────
DEPLOYMENT_MODE=""

# Respect environment variable override
if [[ -n "${ZENSYNORA_INSTALL_MODE:-}" ]]; then
    DEPLOYMENT_MODE="$ZENSYNORA_INSTALL_MODE"
    info "Using deployment mode from environment: $DEPLOYMENT_MODE"
fi

# Respect CLI flags
if [[ "$FORCE_DOCKER" == "true" ]]; then
    DEPLOYMENT_MODE="docker"
    if [[ "$DOCKER_AVAILABLE" == "false" ]]; then
        error "Docker forced but Docker is not available. Install Docker or remove --docker flag."
    fi
elif [[ "$FORCE_TRADITIONAL" == "true" ]]; then
    DEPLOYMENT_MODE="traditional"
fi

# Auto-detect if not explicitly set
if [[ -z "$DEPLOYMENT_MODE" ]]; then
    if [[ "$DOCKER_AVAILABLE" == "true" && "$DOCKER_COMPOSE_AVAILABLE" == "true" ]]; then
        if [[ "$INTERACTIVE" == "true" ]]; then
            echo -e "\n${BOLD}Docker detected!${RESET} ${CYAN}It is the recommended deployment method.${RESET}"
            if prompt_yes "  Deploy with Docker?" "Y"; then
                DEPLOYMENT_MODE="docker"
            else
                DEPLOYMENT_MODE="traditional"
            fi
        else
            # Non-interactive: prefer Docker when available
            DEPLOYMENT_MODE="docker"
        fi
    else
        DEPLOYMENT_MODE="traditional"
        if [[ "$INTERACTIVE" == "true" ]]; then
            warn "Docker not detected. Falling back to traditional venv installation."
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  🦞  ZenSynora — Installer"
echo "  ═══════════════════════════════════════════════════"
echo -e "  Platform: ${OS} ${ARCH} (${PLATFORM})"
echo -e "  Mode:     ${DEPLOYMENT_MODE}"
echo -e "  Dry-run:  ${DRY_RUN}"
echo -e "${RESET}"

# ═════════════════════════════════════════════════════════════════════════════
# DOCKER DEPLOYMENT PATH
# ═════════════════════════════════════════════════════════════════════════════

if [[ "$DEPLOYMENT_MODE" == "docker" ]]; then
    header "Docker Deployment"

    # ── 1. Prerequisites ─────────────────────────────────────────────────────
    info "Verifying Docker prerequisites..."

    # Check for .env file
    ENV_FILE="$SCRIPT_DIR/.env"
    ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "$ENV_EXAMPLE" ]]; then
            info "Creating .env from .env.example..."
            run cp "$ENV_EXAMPLE" "$ENV_FILE"
            success ".env created. Edit it to configure your API keys before starting."
        else
            warn ".env.example not found. You will need to create .env manually."
        fi
    else
        skip ".env already exists"
    fi

    # Ensure data directory exists for bind mount (if used)
    DATA_DIR="$SCRIPT_DIR/data"
    if [[ ! -d "$DATA_DIR" ]]; then
        info "Creating local data directory for volume bind mount..."
        run mkdir -p "$DATA_DIR"
        success "Created: $DATA_DIR"
    fi

    # ── 2. Docker Network ────────────────────────────────────────────────────
    info "Ensuring Docker network exists..."
    if ! docker network ls --format '{{.Name}}' | grep -q '^zensynora-net$'; then
        run docker network create zensynora-net >/dev/null 2>&1 || true
        success "Created Docker network: zensynora-net"
    else
        skip "Docker network zensynora-net already exists"
    fi

    # ── 3. Build Image ───────────────────────────────────────────────────────
    header "Building Docker Image"
    info "Image tag: $DOCKER_TAG"
    info "Platform:  ${DOCKER_DEFAULT_PLATFORM:-$(docker system info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null || echo 'auto')}"

    BUILD_CMD="docker build -t $DOCKER_TAG"
    if [[ -n "${DOCKER_DEFAULT_PLATFORM:-}" ]]; then
        BUILD_CMD="$BUILD_CMD --platform $DOCKER_DEFAULT_PLATFORM"
    fi
    if [[ -n "$DOCKER_BUILD_ARGS" ]]; then
        BUILD_CMD="$BUILD_CMD $DOCKER_BUILD_ARGS"
    fi
    BUILD_CMD="$BUILD_CMD -f $SCRIPT_DIR/Dockerfile $SCRIPT_DIR"

    if [[ "$DRY_RUN" == "true" ]]; then
        dry_run "$BUILD_CMD"
    else
        eval "$BUILD_CMD"
        success "Docker image built: $DOCKER_TAG"
    fi

    # ── 4. Pre-pull Optional Images (if profile requires) ────────────────────
    if [[ "$DOCKER_PROFILE" == "full" && "$DRY_RUN" == "false" ]]; then
        header "Pre-pulling Optional Images"
        info "Pulling redis:7-alpine..."
        docker pull redis:7-alpine >/dev/null 2>&1 || warn "Could not pre-pull redis image"
        info "Pulling ollama/ollama:latest..."
        docker pull ollama/ollama:latest >/dev/null 2>&1 || warn "Could not pre-pull ollama image"
    fi

    # ── 5. Compose Deployment ────────────────────────────────────────────────
    header "Docker Compose"
    COMPOSE_UP_CMD="$DOCKER_COMPOSE_CMD -f $SCRIPT_DIR/docker-compose.yml --profile $DOCKER_PROFILE up -d"

    if prompt_yes "  Start ZenSynora with Docker Compose now?" "Y"; then
        if [[ "$DRY_RUN" == "true" ]]; then
            dry_run "$COMPOSE_UP_CMD"
        else
            # Ensure volumes exist before starting
            $DOCKER_COMPOSE_CMD -f "$SCRIPT_DIR/docker-compose.yml" up --no-start >/dev/null 2>&1 || true

            eval "$COMPOSE_UP_CMD"
            success "ZenSynora containers started."

            # Wait a moment for health checks to begin
            sleep 3

            # Show container status
            header "Container Status"
            docker ps --filter "name=zensynora" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

            # ── 6. Post-Deploy Verification ────────────────────────────────────
            header "Verification"
            info "Waiting for health checks (max 60s)..."

            HEALTHY=false
            for i in {1..12}; do
                if docker inspect --format='{{.State.Health.Status}}' zensynora 2>/dev/null | grep -q "healthy"; then
                    HEALTHY=true
                    break
                fi
                sleep 5
            done

            if [[ "$HEALTHY" == "true" ]]; then
                success "ZenSynora is healthy!"
            else
                warn "Health check not yet passing. Check logs: docker compose logs -f"
            fi

            # ── 7. Next Steps ──────────────────────────────────────────────────
            header "Next Steps"
            echo "  WebUI:     http://localhost:8000"
            echo "  Logs:      docker compose logs -f"
            echo "  Stop:      docker compose down"
            echo "  Restart:   docker compose restart"
            echo "  Shell:     docker compose exec zensynora bash"
            echo "  CLI:       docker compose exec zensynora zensynora --help"
            echo ""
            echo "  To add optional services:"
            echo "    docker compose --profile full up -d"
        fi
    else
        success "Image built. To start manually:"
        echo "  $DOCKER_COMPOSE_CMD -f $SCRIPT_DIR/docker-compose.yml --profile $DOCKER_PROFILE up -d"
    fi

    success "Docker deployment complete."
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════════
# TRADITIONAL DEPLOYMENT PATH (venv-based)
# ═════════════════════════════════════════════════════════════════════════════

header "Traditional Deployment"

# ── 1. SYSTEM PACKAGES ───────────────────────────────────────────────────────
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
    # jq is useful for config manipulation (optional, don't fail if unavailable)
    (ensure_apt "jq") || skip "jq not available (optional)"

elif [ "$PLATFORM" = "macos" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        error "Homebrew not found. Please install it first: https://brew.sh"
    fi
    ensure_brew() {
        local pkg="$1"
        if brew list "$pkg" >/dev/null 2>&1; then
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

# ── 2. PYTHON VERSION CHECK ──────────────────────────────────────────────────
header "2. Python version"

PYTHON_BIN=$(command -v python3 || true)
if [[ -z "$PYTHON_BIN" ]]; then
    error "python3 not found. Please install Python 3.11 or newer."
fi

PY_MAJOR=$($PYTHON_BIN -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON_BIN -c "import sys; print(sys.version_info.minor)")
PY_VERSION="${PY_MAJOR}.${PY_MINOR}"

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
    error "Python $PY_VERSION is too old. ZenSynora requires 3.11+."
fi
success "Python $PY_VERSION — OK"

# ── 3. VIRTUAL ENVIRONMENT ───────────────────────────────────────────────────
header "3. Virtual environment"

if [[ "$RECREATE_VENV" == "true" && -d "$VENV_DIR" ]]; then
    info "Removing existing venv (--recreate-venv)..."
    run rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    run "$PYTHON_BIN" -m venv "$VENV_DIR"
    success "Virtual environment created at $VENV_DIR"
else
    skip "Virtual environment exists at $VENV_DIR"
fi

# Upgrade core packaging tools
run "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel

# ── 4. CORE PYTHON DEPENDENCIES ──────────────────────────────────────────────
header "4. Core Python dependencies"

if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    info "Installing dependencies from requirements.txt..."
    run "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
    success "Dependencies installed from requirements.txt"
else
    warn "requirements.txt not found. Attempting pyproject.toml install..."
    if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        run "$VENV_DIR/bin/pip" install --quiet -e "$SCRIPT_DIR"
        success "Package installed in editable mode"
    else
        error "No requirements.txt or pyproject.toml found."
    fi
fi

# Install scrapling browser engines (idempotent)
if [[ -f "$VENV_DIR/bin/scrapling" ]]; then
    info "Installing/verifying Scrapling browsers..."
    run "$VENV_DIR/bin/scrapling" install --force || warn "Scrapling browser install returned non-zero"
fi

# Install package in editable mode if not already done via requirements
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
    if ! "$VENV_DIR/bin/python" -c "import myclaw" 2>/dev/null; then
        info "Installing ZenSynora package in editable mode..."
        run "$VENV_DIR/bin/pip" install --quiet -e "$SCRIPT_DIR"
    fi
fi

# ── 5. OPTIONAL LLM PROVIDER SDKs ────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "5. Optional LLM SDKs"
    if prompt_yes "  Install Anthropic Claude SDK?"; then
        run "$VENV_DIR/bin/pip" install --quiet anthropic
    fi
    if prompt_yes "  Install Google Gemini SDK?"; then
        run "$VENV_DIR/bin/pip" install --quiet google-generativeai
    fi
fi

# ── 6. OPTIONAL: Ollama ──────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "6. Ollama (local LLMs)"
    if command -v ollama >/dev/null 2>&1; then
        success "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
    else
        if prompt_yes "  Install Ollama for local LLMs?"; then
            info "Downloading and installing Ollama..."
            run curl -fsSL https://ollama.com/install.sh | sh
            success "Ollama installed."
        fi
    fi
fi

# ── 6.5 OPTIONAL: Redis ──────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "6.5 Redis (optional)"
    if prompt_yes "  Install Redis Python client for multi-worker state sharing?"; then
        run "$VENV_DIR/bin/pip" install --quiet "redis>=4.0"
        success "redis>=4.0 installed."
        info "To enable: set ZEN_REDIS_URL=redis://localhost:6379/0"
    fi
fi

# ── 6.6 WEB UI (Node.js & npm) ──────────────────────────────────────────────
header "6.6 Web UI frontend"

if command -v npm >/dev/null 2>&1; then
    info "Node.js/npm found: $(node --version 2>/dev/null || echo 'unknown')"
    if [[ -d "$SCRIPT_DIR/webui" ]]; then
        info "Installing WebUI dependencies..."
        if [[ "$DRY_RUN" == "false" ]]; then
            (cd "$SCRIPT_DIR/webui" && npm install --quiet)
            success "WebUI dependencies installed."
            # Try to build the UI if a build script exists
            if grep -q '"build"' "$SCRIPT_DIR/webui/package.json" 2>/dev/null; then
                info "Building WebUI for production..."
                (cd "$SCRIPT_DIR/webui" && npm run build --quiet 2>&1) || warn "WebUI build had warnings or errors"
            fi
        else
            dry_run "cd webui && npm install && npm run build"
        fi
    else
        skip "No webui/ directory found"
    fi
else
    warn "Node.js/npm NOT found. Skipping Web UI frontend setup."
fi

# ── 7. DATA DIRECTORIES ──────────────────────────────────────────────────────
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
make_dir "$HOME/.myclaw/logs"

# ── 8. OPTIONAL: systemd service ─────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" && "$PLATFORM" != "macos" && "$PLATFORM" != "windows" ]]; then
    header "8. systemd service"
    SERVICE_FILE="/etc/systemd/system/myclaw.service"
    if [[ ! -f "$SERVICE_FILE" ]] && prompt_yes "  Install systemd service?"; then
        # Determine which CLI to use
        if [[ -f "$SCRIPT_DIR/cli.py" ]]; then
            CLI_PATH="$SCRIPT_DIR/cli.py"
        else
            CLI_PATH="$VENV_DIR/bin/zensynora"
        fi

        run sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=ZenSynora (MyClaw) Gateway
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
Environment="MYCLAW_CONFIG_DIR=$HOME/.myclaw"
ExecStart=$VENV_DIR/bin/python $CLI_PATH gateway
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=myclaw

[Install]
WantedBy=multi-user.target
EOF
        run sudo systemctl daemon-reload
        run sudo systemctl enable myclaw.service
        success "Service installed. Start with: sudo systemctl start myclaw"
    fi
fi

# ── 9. ENVIRONMENT FILE ──────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/.env" && -f "$SCRIPT_DIR/.env.example" ]]; then
    header "9. Environment configuration"
    info "Creating .env from .env.example..."
    run cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    success ".env created. Edit it to configure your API keys."
fi

# ── 10. VERIFICATION ─────────────────────────────────────────────────────────
header "10. Verification"

if [[ "$DRY_RUN" == "false" ]]; then
    FAILED=0
    check_import() {
        local name="$1"
        local module="${2:-$1}"
        if ! "$VENV_DIR/bin/python" -c "import $module" 2>/dev/null; then
            warn "Import $name FAILED"
            FAILED=$((FAILED + 1))
        else
            success "Import $name OK"
        fi
    }

    check_import "telegram"
    check_import "httpx"
    check_import "fastapi"
    check_import "mcp"
    check_import "aiosqlite"
    check_import "paramiko"
    check_import "psutil"
    check_import "numpy"
    check_import "pydantic"
    check_import "rich"
    check_import "yaml"
    check_import "uvicorn"
    check_import "apscheduler"
    check_import "scrapling"

    # Check ZenSynora internal modules
    if ! "$VENV_DIR/bin/python" -c "import myclaw.state_store, myclaw.async_scheduler" 2>/dev/null; then
        warn "Internal modules state_store / async_scheduler import FAILED"
        FAILED=$((FAILED + 1))
    else
        success "Internal modules OK"
    fi

    # Check CLI entry point
    if "$VENV_DIR/bin/python" -c "from myclaw.cli import cli" 2>/dev/null; then
        success "CLI entry point OK"
    else
        warn "CLI entry point check FAILED"
        FAILED=$((FAILED + 1))
    fi

    if [[ "$FAILED" -eq 0 ]]; then
        success "All core components verified."
    else
        warn "$FAILED verification(s) failed. Check output above."
    fi
fi

# ── DONE ─────────────────────────────────────────────────────────────────────
header "✅  ZenSynora is ready!"

echo ""
echo "  Next steps:"
echo "  1. source $VENV_DIR/bin/activate"
echo "  2. python cli.py onboard"
echo "  3. python cli.py agent"
echo "  4. python cli.py webui  (Dashboard)"
echo ""
echo "  ─ CLI Examples ───────────────────────────────"
echo "  python cli.py knowledge sync"
echo "  python cli.py knowledge search 'how to use'"
echo ""
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    echo "  Remember to edit $SCRIPT_DIR/.env with your API keys."
fi
echo ""
