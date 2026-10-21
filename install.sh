#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  ZenSynora (MyClaw) — Universal Install Script
#  Version: 2.1.0  (covers Sprints 1–12: marketplace, multi-tenancy,
#                   tracing, vector store, prompt registry, eval harness,
#                   structured-output, browser tools, cost dashboard,
#                   class-based agent decomposition, agent registry YAML)
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
#    --no-node              Do not install Node.js 22 / build the Web UI
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
SKIP_NODE=false

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
        --no-node)
            SKIP_NODE=true
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
            echo "  --no-node              Do not install Node.js 22 / build the Web UI"
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
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

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

    # ── 2. Data folder ownership ─────────────────────────────────────────────
    # Fixed 2026-10-13 (v0.7.0): a zensynora-net network was created here but
    # docker compose makes its own (<project>_zensynora-net), so it was unused.
    # Instead, build the image with your UID so the container can write ./data.
    export ZENSYNORA_UID="${ZENSYNORA_UID:-$(id -u)}" ZENSYNORA_GID="${ZENSYNORA_GID:-$(id -g)}"

    # ── 3. Build Image ───────────────────────────────────────────────────────
    header "Building Docker Image"
    info "Image tag: $DOCKER_TAG"
    info "Platform:  ${DOCKER_DEFAULT_PLATFORM:-$(docker system info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null || echo 'auto')}"

    BUILD_CMD="docker build -t $DOCKER_TAG --build-arg ZENSYNORA_UID=$ZENSYNORA_UID --build-arg ZENSYNORA_GID=$ZENSYNORA_GID"
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
            for _ in {1..12}; do
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
# Rewritten 0.7.0: supports apt, dnf, pacman and zypper (before, any non-Debian
# Linux failed here because dpkg/apt-get were assumed), installs only what is
# actually missing, and never needs sudo when everything is already present.
header "1. System packages"

PKG_MGR=""
for m in apt-get dnf pacman zypper; do
    if command -v "$m" >/dev/null 2>&1; then PKG_MGR="$m"; break; fi
done
PKG_REFRESHED=0

pkg_install() {  # pkg_install <package>...
    [[ $# -gt 0 ]] || return 0
    case "$PKG_MGR" in
        apt-get)
            if [[ "$PKG_REFRESHED" -eq 0 ]]; then
                info "Running apt-get update..."
                # Keep debconf/needrestart from opening dialogs (env survives sudo's env_reset)
                run sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq
                PKG_REFRESHED=1
            fi
            run sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq "$@" ;;
        dnf)    run sudo dnf install -y -q "$@" ;;
        pacman) run sudo pacman -S --needed --noconfirm "$@" ;;
        zypper) run sudo zypper --non-interactive install "$@" ;;
        *)      return 1 ;;
    esac
}

pkg_installed() {  # true if the distro package is installed (apt only; others use commands)
    [[ "$PKG_MGR" == "apt-get" ]] && dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

if [[ "$PLATFORM" == "linux" || "$PLATFORM" == "wsl" ]]; then
    # command needed -> package name per package manager (apt|dnf|pacman|zypper)
    declare -A NEED=(
        [python3]="python3|python3|python|python3"
        [git]="git|git|git|git"
        [curl]="curl|curl|curl|curl"
        [sqlite3]="sqlite3|sqlite|sqlite|sqlite3"
        [cc]="build-essential|gcc gcc-c++ make|base-devel|gcc gcc-c++ make"
    )
    case "$PKG_MGR" in apt-get) col=1 ;; dnf) col=2 ;; pacman) col=3 ;; zypper) col=4 ;; *) col=0 ;; esac
    missing=()
    for cmd in python3 git curl sqlite3 cc; do
        if command -v "$cmd" >/dev/null 2>&1; then
            skip "$cmd already available"
        elif [[ "$col" -gt 0 ]]; then
            # Package names for this manager (space-separated, e.g. "gcc gcc-c++ make").
            read -r -a pkgs <<< "$(cut -d'|' -f"$col" <<< "${NEED[$cmd]}")"
            missing+=("${pkgs[@]}")
        else
            warn "$cmd is missing and no supported package manager was found — install it yourself."
        fi
    done
    # Python headers are needed to build a few wheels on new Python versions.
    if [[ "$PKG_MGR" == "apt-get" ]] && ! pkg_installed python3-dev; then missing+=(python3-dev); fi
    if [[ "$PKG_MGR" == "dnf" || "$PKG_MGR" == "zypper" ]] && ! rpm -q python3-devel >/dev/null 2>&1; then
        missing+=(python3-devel)
    fi
    if [[ ${#missing[@]} -gt 0 ]]; then
        info "Installing system packages with $PKG_MGR: ${missing[*]}"
        pkg_install "${missing[@]}" || warn "Could not install: ${missing[*]} (continuing; see messages above)"
    fi
    if ! command -v jq >/dev/null 2>&1; then
        (pkg_install jq >/dev/null 2>&1) || skip "jq not available (optional)"
    fi

elif [[ "$PLATFORM" == "macos" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        error "Homebrew not found. Please install it first: https://brew.sh"
    fi
    for pkg in python3 git sqlite3; do
        if brew list "$pkg" >/dev/null 2>&1; then skip "brew: $pkg already installed"
        else info "Installing brew package: $pkg"; run brew install "$pkg"; fi
    done
fi

# ── 2. PYTHON VERSION ────────────────────────────────────────────────────────
# Pick the newest Python >= 3.11 available (ZENSYNORA_PYTHON overrides), not
# just whatever `python3` is — e.g. Ubuntu 22.04 has python3 = 3.10 but may
# also have python3.11/3.12 installed.
header "2. Python version"

PYTHON_BIN=""
for cand in "${ZENSYNORA_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
    [[ -n "$cand" ]] || continue
    if command -v "$cand" >/dev/null 2>&1 \
        && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON_BIN="$(command -v "$cand")"
        break
    fi
done
if [[ -z "$PYTHON_BIN" ]]; then
    error "Python 3.11 or newer was not found. Install it (e.g. sudo apt install python3.12) and rerun."
fi
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
success "Python $PY_VERSION — OK ($PYTHON_BIN)"

# venv/ensurepip support (Debian/Ubuntu ship it separately as python3.X-venv)
if ! "$PYTHON_BIN" -c "import venv, ensurepip" >/dev/null 2>&1; then
    warn "Python $PY_VERSION lacks virtual-environment support."
    if [[ "$PKG_MGR" == "apt-get" ]] && prompt_yes "  Install python${PY_VERSION}-venv now?" "Y"; then
        pkg_install "python${PY_VERSION}-venv" || pkg_install python3-venv || true
    fi
    "$PYTHON_BIN" -c "import venv, ensurepip" >/dev/null 2>&1 \
        || error "Install the venv package for Python $PY_VERSION (Debian/Ubuntu: python${PY_VERSION}-venv), then rerun install.sh."
else
    skip "Python virtual-environment support is available"
fi

# ── 3. VIRTUAL ENVIRONMENT ───────────────────────────────────────────────────
header "3. Virtual environment"

if [[ "$RECREATE_VENV" == "true" && -d "$VENV_DIR" ]]; then
    info "Removing existing venv (--recreate-venv)..."
    run rm -rf "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [[ -d "$VENV_DIR" && "$DRY_RUN" != "true" ]] \
    && { [[ ! -x "$VENV_PYTHON" ]] || ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(sys.prefix == sys.base_prefix)' 2>/dev/null; }; then
    warn "An existing virtual environment is incomplete or invalid: $VENV_DIR"
    if prompt_yes "  Recreate this virtual environment before installing?" "Y"; then
        run rm -rf "$VENV_DIR"
    else
        error "Repair it with --recreate-venv, or remove $VENV_DIR and rerun install.sh."
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    if ! run "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        if { [[ "$PLATFORM" == "linux" ]] || [[ "$PLATFORM" == "wsl" ]]; } \
            && prompt_yes "  Virtual environment creation failed. Install python3-venv and retry?" "Y"; then
            pkg_install "python${PY_VERSION}-venv" || pkg_install python3-venv
            run "$PYTHON_BIN" -m venv "$VENV_DIR" \
                || error "Could not create the virtual environment. Install the matching python3.X-venv package and retry."
        else
            error "Could not create the virtual environment. On Debian/Ubuntu install python3-venv, then rerun install.sh."
        fi
    fi
    success "Virtual environment created at $VENV_DIR"
else
    skip "Virtual environment exists at $VENV_DIR"
fi

# Ensure pip is available in the virtual environment before using it.
if [[ "$DRY_RUN" == "true" ]]; then
    dry_run "$VENV_PYTHON -m pip --version (bootstrap with ensurepip if unavailable)"
else
    if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
        info "pip is missing from the virtual environment. Bootstrapping with ensurepip..."
        "$VENV_PYTHON" -m ensurepip --upgrade || error "pip is missing and could not be bootstrapped with ensurepip."
    fi
    "$VENV_PYTHON" -m pip --version >/dev/null 2>&1 || error "pip is unavailable in the virtual environment."
fi

# Upgrade core packaging tools
run "$VENV_PYTHON" -m pip install --quiet --upgrade pip setuptools wheel

# ── 4. CORE PYTHON DEPENDENCIES ──────────────────────────────────────────────
header "4. Core Python dependencies"

if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    info "Installing dependencies from requirements.txt (this can take several minutes)..."
    run "$VENV_PYTHON" -m pip install --progress-bar on -r "$SCRIPT_DIR/requirements.txt"
    success "Dependencies installed from requirements.txt"
else
    warn "requirements.txt not found. Attempting pyproject.toml install..."
    if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        info "Installing from pyproject.toml (this can take several minutes)..."
        run "$VENV_PYTHON" -m pip install --progress-bar on -e "$SCRIPT_DIR"
        success "Package installed in editable mode"
    else
        error "No requirements.txt or pyproject.toml found."
    fi
fi

# Install scrapling browser engines (idempotent: without --force, scrapling
# skips browsers that are already present instead of re-downloading them)
if [[ -f "$VENV_DIR/bin/scrapling" ]]; then
    info "Installing/verifying Scrapling browsers (large download, may ask for sudo)..."
    run "$VENV_DIR/bin/scrapling" install || warn "Scrapling browser install returned non-zero"
fi

# Install the package itself (editable) so the `zensynora` / `myclaw`
# commands exist in the venv. Checking `import myclaw` is not enough: when the
# installer is run from the source directory, the CWD is on sys.path, the
# import succeeds, and the package (and its console scripts) were never installed.
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
    if [[ ! -x "$VENV_DIR/bin/zensynora" ]] \
        || ! "$VENV_PYTHON" -m pip show --quiet zensynora >/dev/null 2>&1; then
        info "Installing ZenSynora package in editable mode..."
        run "$VENV_PYTHON" -m pip install --quiet -e "$SCRIPT_DIR"
    else
        skip "ZenSynora package already installed in the venv"
    fi
fi

# ── 5. OPTIONAL LLM PROVIDER SDKs ────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "5. Optional LLM SDKs"
    if prompt_yes "  Install Anthropic Claude SDK?"; then
        run "$VENV_PYTHON" -m pip install --quiet anthropic
    fi
    if prompt_yes "  Install Google Gemini SDK?"; then
        run "$VENV_PYTHON" -m pip install --quiet google-generativeai
    fi
fi

# ── 5.5 OPTIONAL FEATURE EXTRAS (Sprints 2–9) ────────────────────────────────
# These map to pyproject.toml optional-dependencies. Each is a no-op when
# not installed (modules import fine, helpers degrade to errors/no-ops),
# but the operator usually wants them on for production.
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "5.5 Optional feature extras"

    # OpenTelemetry tracing — Sprint 2 module + Sprint 10 wiring
    if prompt_yes "  Install OpenTelemetry tracing SDK ([tracing])?"; then
        run "$VENV_PYTHON" -m pip install --quiet \
            "opentelemetry-api>=1.20" "opentelemetry-sdk>=1.20" "opentelemetry-exporter-otlp>=1.20"
        success "Tracing extras installed. Enable with ZENSYNORA_TRACING_ENABLED=true."
    fi

    # Playwright browser tools — Sprint 3
    if prompt_yes "  Install Playwright headless browser tools ([browser])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "playwright>=1.42"
        info "Installing Playwright Chromium (large download, can take a minute)…"
        run "$VENV_DIR/bin/playwright" install chromium || warn "Playwright Chromium install non-zero"
        success "Browser tools ready."
    fi

    # Jinja2 prompt templates — Sprint 3
    if prompt_yes "  Install Jinja2 for prompt templates ([prompts])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "jinja2>=3.1"
    fi

    # JSON-schema validation — Sprint 3 alternative to Pydantic
    if prompt_yes "  Install jsonschema for structured-output validation ([jsonschema])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "jsonschema>=4.21"
    fi

    # Qdrant vector backend — Sprint 4
    if prompt_yes "  Install Qdrant client for the vector store ([qdrant])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "qdrant-client>=1.7"
        info "Run a Qdrant server with: docker run -p 6333:6333 qdrant/qdrant"
    fi

    # JWT verification + OAuth — Sprint 6
    if prompt_yes "  Install PyJWT for OAuth/JWT auth ([auth])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "PyJWT[crypto]>=2.8"
    fi

    # Discord channel — Sprint 6
    if prompt_yes "  Install discord.py for the Discord channel ([discord])?"; then
        run "$VENV_PYTHON" -m pip install --quiet "discord.py>=2.3"
    fi

    # PyYAML — Sprint 12 agent registry YAML loader
    # PyYAML is technically already a core dep (pyproject lists it) but
    # double-check so a cached install without it doesn't silently fall
    # back to the embedded literal at runtime.
    if ! "$VENV_DIR/bin/python" -c "import yaml" 2>/dev/null; then
        info "Installing PyYAML (required for the agent registry data file)…"
        run "$VENV_PYTHON" -m pip install --quiet "pyyaml>=6.0"
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
            if [[ "$DRY_RUN" == "true" ]]; then
                dry_run "curl -fsSL https://ollama.com/install.sh | sh"
            else
                curl -fsSL https://ollama.com/install.sh | sh
            fi
            success "Ollama installed."
        fi
    fi
fi

# ── 6.5 OPTIONAL: Redis ──────────────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" ]]; then
    header "6.5 Redis (optional)"
    if prompt_yes "  Install Redis Python client for multi-worker state sharing?"; then
        run "$VENV_PYTHON" -m pip install --quiet "redis>=4.0"
        success "redis>=4.0 installed."
        info "To enable: set ZEN_REDIS_URL=redis://localhost:6379/0"
    fi
fi

# ── 6.6 WEB UI (Node.js 22 & npm) ───────────────────────────────────────────
# Rewritten 2026-10-21: the Web UI is built with Vite, which needs Node.js
# 20.19+. A new-enough node/npm on PATH is used; otherwise Node.js 22 LTS is
# downloaded from nodejs.org (checked against its SHA-256 sums) into
# ZenSynora's program folder (~/.local/share/zensynora/node) — no sudo, and the
# updater uses the same copy. Before, a missing npm only printed a warning and
# every later update reused an old Web UI build.
header "6.6 Web UI frontend (Node.js 22)"

# Program folder: the parent of releases/ for a managed install, else the default.
ZS_BASE="${ZENSYNORA_HOME:-$HOME/.local/share/zensynora}"
if [[ "$(basename "$(dirname "$SCRIPT_DIR")")" == "releases" ]]; then
    ZS_BASE="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi

if [[ "$SKIP_NODE" == "true" ]]; then
    skip "Web UI build skipped (--no-node)"
elif [[ ! -f "$SCRIPT_DIR/webui/package.json" ]]; then
    skip "No webui/ directory found"
elif [[ "$DRY_RUN" == "true" ]]; then
    dry_run "find Node.js 20.19+ or download Node.js 22 into $ZS_BASE/node; cd webui && npm ci && npm run build"
else
    NODE_BIN=""
    # Prints the folder with node + npm on stdout; progress and errors go to the terminal.
    if NODE_BIN="$(PYTHONPATH="$SCRIPT_DIR" "$PYTHON_BIN" -m updater.nodejs --base "$ZS_BASE")"; then
        NODE_BIN="$(tail -n 1 <<< "$NODE_BIN")"
        success "Node.js $("$NODE_BIN/node" --version) — npm $(PATH="$NODE_BIN:$PATH" "$NODE_BIN/npm" --version 2>/dev/null) ($NODE_BIN)"
    else
        NODE_BIN=""
    fi
    if [[ -n "$NODE_BIN" ]]; then
        info "Building the Web UI (npm ci + npm run build, a few minutes)..."
        if (cd "$SCRIPT_DIR/webui" && export PATH="$NODE_BIN:$PATH" \
                && { if [[ -f package-lock.json ]]; then npm ci --no-fund --no-audit; else npm install --no-fund --no-audit; fi; } \
                && npm run build); then
            success "Web UI built: webui/dist"
        else
            warn "Web UI build failed — see the messages above. Run 'cd webui && npm ci && npm run build' later."
        fi
    elif [[ -f "$SCRIPT_DIR/webui/dist/index.html" ]]; then
        warn "Using the Web UI build that came with this copy. Install Node.js 22 (https://nodejs.org) to rebuild it."
    else
        warn "No Node.js — the Web UI was not built. Install Node.js 22 (https://nodejs.org) and rerun install.sh."
    fi
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
# ── New directories introduced by Sprints 3-9 ───────────────────────────────
# Pre-creating them avoids the first-write directory-creation race when
# multiple workers boot simultaneously (e.g., systemd + cron).
make_dir "$HOME/.myclaw/plugins"            # Sprint 9: marketplace install root
make_dir "$HOME/.myclaw/plugins/installed"  # Sprint 9: per-artifact storage
make_dir "$HOME/.myclaw/hub"                # Sprint 9: local hub registry
make_dir "$HOME/.myclaw/hub/skills"
make_dir "$HOME/.myclaw/api"                # API-key store (api_server.py)

# ── 8. OPTIONAL: systemd service ─────────────────────────────────────────────
if [[ "$SKIP_OPTIONAL" == "false" && "$PLATFORM" != "macos" && "$PLATFORM" != "windows" ]]; then
    header "8. systemd service"
    SERVICE_FILE="/etc/systemd/system/myclaw.service"
    if [[ ! -f "$SERVICE_FILE" ]] && prompt_yes "  Install systemd service?"; then
        # Fixed 2026-10-13 (v0.7.0): a managed install lives in
        # releases/<version>-<stamp>/ and is reached through the stable
        # "source" symlink. The service must use that symlink, otherwise it
        # keeps running the old release after `zensynora update`.
        SERVICE_DIR="$SCRIPT_DIR"
        if [[ "$(basename "$(dirname "$SCRIPT_DIR")")" == "releases" ]]; then
            SERVICE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")/source"
        fi
        SERVICE_VENV="$SERVICE_DIR/venv"
        [[ -x "$SERVICE_VENV/bin/python" ]] || SERVICE_VENV="$VENV_DIR"
        CLI_PATH="$SERVICE_VENV/bin/zensynora"

        run sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=ZenSynora (MyClaw) Gateway
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SERVICE_DIR
Environment="PATH=$SERVICE_VENV/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$CLI_PATH gateway
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
        # Run from / so a source checkout in the CWD cannot mask a missing install.
        if ! (cd / && "$VENV_DIR/bin/python" -c "import $module") 2>/dev/null; then
            warn "Import $name FAILED"
            FAILED=$((FAILED + 1))
        else
            success "Import $name OK"
        fi
    }

    check_import "telegram"
    check_import "httpx"
    check_import "h2 (httpx HTTP/2 support)" "h2"
    check_import "fastapi"
    check_import "mcp"
    check_import "aiosqlite"
    check_import "paramiko"
    check_import "openai (llama.cpp / LM Studio / OpenAI providers)" "openai"
    check_import "psutil"
    check_import "numpy"
    check_import "pydantic"
    check_import "rich"
    check_import "yaml"
    check_import "uvicorn"
    check_import "websockets (Web UI chat)" "websockets"
    check_import "apscheduler"
    check_import "scrapling"

    # Check ZenSynora internal modules — original surface
    if ! "$VENV_DIR/bin/python" -c "import myclaw.state_store, myclaw.async_scheduler" 2>/dev/null; then
        warn "Internal modules state_store / async_scheduler import FAILED"
        FAILED=$((FAILED + 1))
    else
        success "Internal modules (state_store, async_scheduler) OK"
    fi

    # ── New module surface from Sprints 2-12 ─────────────────────────────────
    # Each is import-only; optional-dep features stay no-ops without the
    # underlying SDK, so the import test always succeeds.
    check_sprint_module() {
        local name="$1"
        local module="$2"
        if "$VENV_DIR/bin/python" -c "import $module" 2>/dev/null; then
            success "Module $name (Sprint module) OK"
        else
            warn "Module $name FAILED to import"
            FAILED=$((FAILED + 1))
        fi
    }

    check_sprint_module "observability"          "myclaw.observability"     # Sprint 2: tracing
    check_sprint_module "resilience"             "myclaw.resilience"        # Sprint 2: circuit breaker
    check_sprint_module "prompts"                "myclaw.prompts"           # Sprint 3: prompt registry
    check_sprint_module "structured_output"      "myclaw.structured_output" # Sprint 3: validate + repair
    check_sprint_module "vector backends"        "myclaw.vector"            # Sprint 4: vector store
    check_sprint_module "agent_internals"        "myclaw.agent_internals"   # Sprint 5+9: decomposition
    check_sprint_module "auth (JWT + OAuth)"     "myclaw.auth"              # Sprint 6
    check_sprint_module "tenancy (UserContext)"  "myclaw.tenancy"           # Sprint 6+11
    check_sprint_module "evals harness"          "myclaw.evals"             # Sprint 7
    check_sprint_module "messaging broker"       "myclaw.messaging"         # Sprint 7
    check_sprint_module "marketplace"            "myclaw.marketplace"       # Sprint 9
    check_sprint_module "caching primitives"     "myclaw.caching"           # Sprint 11
    check_sprint_module "defaults"               "myclaw.defaults"          # Sprint 10

    # Verify the agent registry YAML file shipped (Sprint 12)
    if [[ -f "$SCRIPT_DIR/myclaw/agents/data/agents.yaml" ]]; then
        success "Agent registry YAML present"
    else
        warn "Agent registry YAML NOT FOUND — registry will fall back to embedded literal"
    fi

    # Check CLI entry point (module import + installed console script)
    if (cd / && "$VENV_DIR/bin/python" -c "from myclaw.cli import cli") >/dev/null 2>&1; then
        success "CLI entry point OK"
    else
        warn "CLI entry point check FAILED"
        FAILED=$((FAILED + 1))
    fi
    if (cd / && "$VENV_DIR/bin/zensynora" --help) >/dev/null 2>&1; then
        success "zensynora command OK ($VENV_DIR/bin/zensynora)"
    else
        warn "zensynora command check FAILED"
        FAILED=$((FAILED + 1))
    fi

    if [[ "$FAILED" -eq 0 ]]; then
        success "All core components verified."
    else
        warn "$FAILED verification(s) failed. Check output above."
    fi
fi

# ── 11. DATA MIGRATIONS ──────────────────────────────────────────────────────
# Record which data migrations this version already satisfies, so the
# updater only runs newer ones later (see updater/migrations/).
if [[ "$DRY_RUN" == "false" && -d "$SCRIPT_DIR/updater" ]]; then
    header "11. Data migrations"
    if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" -m updater migrate); then
        success "User data is up to date"
    else
        warn "Data migrations reported a problem (see output above)"
    fi
fi

# ── DONE ─────────────────────────────────────────────────────────────────────
header "✅  ZenSynora is ready!"

echo ""
echo "  Next steps:"
echo "  1. source $VENV_DIR/bin/activate"
echo "  2. zensynora webui      → http://localhost:8000"
echo "     Models: pick your model · Chat: #general or click an agent"
echo "     (terminal alternatives: zensynora onboard, zensynora agent)"
echo ""
echo "  ─ CLI Examples ───────────────────────────────"
echo "  zensynora knowledge search 'how to use'"
echo "  zensynora backup create     # settings, API keys, knowledge (v0.7.1)"
echo "  zensynora doctor           # health check"
echo "  (or, from $SCRIPT_DIR: python cli.py <command>)"
echo ""
echo "  ─ Updating later (keeps your settings, backs up first, auto-rollback) ─"
echo "  zensynora update            # or: bash $SCRIPT_DIR/update.sh"
echo "  zensynora update rollback   # back to the previous version"
echo ""
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    echo "  Remember to edit $SCRIPT_DIR/.env with your API keys."
fi
echo ""
