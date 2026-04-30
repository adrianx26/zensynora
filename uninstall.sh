#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  ZenSynora (MyClaw) — Universal Uninstall Script
#  Version: 2.1.0  (matches install.sh 2.1.0 — covers Sprints 1–12:
#                   marketplace, vector store, prompt registry, cost
#                   dashboard, plugins, hub, audit logs, tenancy)
#
#  Usage:
#    chmod +x uninstall.sh && ./uninstall.sh
#
#  Options:
#    --yes, -y              Non-interactive mode (assume yes to all prompts)
#    --docker-only          Only remove Docker resources (skip traditional cleanup)
#    --traditional-only     Only remove traditional resources (skip Docker cleanup)
#    --remove-images        Also remove Docker images (default: keep images)
#    --remove-volumes       Also remove Docker volumes and data (default: keep volumes)
#    --remove-networks      Also remove Docker networks (default: keep networks)
#    --keep-data            Preserve user data directories (~/.myclaw)
#    --keep-config          Preserve config but remove other data
#    --dry-run              Show what would be done without doing it
#    --help, -h             Show this help message
#
#  Environment Variables:
#    ZENSYNORA_UNINSTALL_MODE=docker|traditional|all   Override cleanup scope
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# CLI Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────
INTERACTIVE=true
DOCKER_ONLY=false
TRADITIONAL_ONLY=false
REMOVE_IMAGES=false
REMOVE_VOLUMES=false
REMOVE_NETWORKS=false
KEEP_DATA=false
KEEP_CONFIG=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            INTERACTIVE=false
            shift
            ;;
        --docker-only)
            DOCKER_ONLY=true
            shift
            ;;
        --traditional-only)
            TRADITIONAL_ONLY=true
            shift
            ;;
        --remove-images)
            REMOVE_IMAGES=true
            shift
            ;;
        --remove-volumes)
            REMOVE_VOLUMES=true
            shift
            ;;
        --remove-networks)
            REMOVE_NETWORKS=true
            shift
            ;;
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --keep-config)
            KEEP_CONFIG=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Scope:"
            echo "  --docker-only          Only remove Docker resources"
            echo "  --traditional-only     Only remove traditional (venv/system) resources"
            echo ""
            echo "Docker Options:"
            echo "  --remove-images        Also remove Docker images"
            echo "  --remove-volumes       Also remove Docker volumes (DELETES ALL DATA)"
            echo "  --remove-networks      Also remove Docker networks"
            echo ""
            echo "Traditional Options:"
            echo "  --keep-data            Preserve ~/.myclaw data directory"
            echo "  --keep-config          Preserve config but remove other data"
            echo ""
            echo "General:"
            echo "  --yes, -y              Non-interactive mode"
            echo "  --dry-run              Show what would be done without doing it"
            echo "  --help, -h             Show this help message"
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
error()   { echo -e "${RED}  [ERR ]${RESET}  $*" >&2; }
header()  { echo -e "\n${BOLD}── $* ──────────────────────────────────────────${RESET}"; }
dry_run() { echo -e "${DIM}  [DRY]   $*${RESET}"; }

run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        dry_run "$*"
    else
        "$@"
    fi
}

prompt_yes() {
    if [[ "$INTERACTIVE" == "false" ]]; then
        return 0
    fi
    read -rp "$1 [y/N]: " response
    case "${response,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────────────
# Environment Detection
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_AVAILABLE=false
DOCKER_COMPOSE_CMD=""

if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_AVAILABLE=true
    fi
fi

if [[ "$DOCKER_AVAILABLE" == "true" ]]; then
    if docker compose version >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker-compose"
    fi
fi

# Respect environment variable override
if [[ -n "${ZENSYNORA_UNINSTALL_MODE:-}" ]]; then
    case "$ZENSYNORA_UNINSTALL_MODE" in
        docker) DOCKER_ONLY=true; TRADITIONAL_ONLY=false ;;
        traditional) TRADITIONAL_ONLY=true; DOCKER_ONLY=false ;;
        all) DOCKER_ONLY=false; TRADITIONAL_ONLY=false ;;
    esac
fi

# Banner
echo -e "${BOLD}"
echo "  🦞  ZenSynora — Uninstaller"
echo "  ═══════════════════════════════════════════════════"
echo -e "  Docker available:  ${DOCKER_AVAILABLE}"
echo -e "  Docker-only:       ${DOCKER_ONLY}"
echo -e "  Traditional-only:  ${TRADITIONAL_ONLY}"
echo -e "  Dry-run:           ${DRY_RUN}"
echo -e "${RESET}"

# ═════════════════════════════════════════════════════════════════════════════
# DOCKER CLEANUP
# ═════════════════════════════════════════════════════════════════════════════

if [[ "$TRADITIONAL_ONLY" == "false" ]]; then
    header "Docker Resource Cleanup"

    # Gather state
    DOCKER_CONTAINERS=""
    DOCKER_IMAGES=""
    DOCKER_VOLUMES=""
    DOCKER_NETWORKS=""

    if [[ "$DOCKER_AVAILABLE" == "true" ]]; then
        DOCKER_CONTAINERS=$(docker ps -a --filter "name=zensynora" --format '{{.Names}}' 2>/dev/null || true)
        DOCKER_IMAGES=$(docker images --filter "reference=zensynora*" --format '{{.Repository}}:{{.Tag}}' 2>/dev/null || true)
        DOCKER_VOLUMES=$(docker volume ls --filter "name=zensynora" --format '{{.Name}}' 2>/dev/null || true)
        DOCKER_NETWORKS=$(docker network ls --filter "name=zensynora-net" --format '{{.Name}}' 2>/dev/null || true)
    fi

    HAS_DOCKER_RESOURCES=false
    [[ -n "$DOCKER_CONTAINERS" ]] && HAS_DOCKER_RESOURCES=true
    [[ -n "$DOCKER_IMAGES" ]] && HAS_DOCKER_RESOURCES=true
    [[ -n "$DOCKER_VOLUMES" ]] && HAS_DOCKER_RESOURCES=true
    [[ -n "$DOCKER_NETWORKS" ]] && HAS_DOCKER_RESOURCES=true

    if [[ "$HAS_DOCKER_RESOURCES" == "false" ]]; then
        skip "No ZenSynora Docker resources found."
    else
        # ── 1. Stop & Remove Containers ───────────────────────────────────────
        if [[ -n "$DOCKER_CONTAINERS" ]]; then
            header "1. Stopping & removing containers"
            echo "$DOCKER_CONTAINERS" | while read -r container; do
                if [[ -n "$container" ]]; then
                    info "Stopping container: $container"
                    run docker stop "$container" >/dev/null 2>&1 || true
                    info "Removing container: $container"
                    run docker rm "$container" >/dev/null 2>&1 || true
                fi
            done
            success "Containers removed."
        fi

        # ── 2. Remove Images ──────────────────────────────────────────────────
        if [[ "$REMOVE_IMAGES" == "true" && -n "$DOCKER_IMAGES" ]]; then
            header "2. Removing Docker images"
            if prompt_yes "  Remove ZenSynora Docker images? This will require re-downloading on reinstall."; then
                echo "$DOCKER_IMAGES" | while read -r image; do
                    if [[ -n "$image" ]]; then
                        info "Removing image: $image"
                        run docker rmi "$image" >/dev/null 2>&1 || warn "Could not remove image: $image"
                    fi
                done
                success "Images removed."
            fi
        elif [[ -n "$DOCKER_IMAGES" ]]; then
            skip "Docker images kept (use --remove-images to delete them)"
        fi

        # ── 3. Remove Volumes ─────────────────────────────────────────────────
        if [[ "$REMOVE_VOLUMES" == "true" && -n "$DOCKER_VOLUMES" ]]; then
            header "3. Removing Docker volumes"
            warn "⚠️  This will DELETE ALL persistent data (config, memory DB, knowledge base)!"
            if prompt_yes "  Permanently delete ZenSynora Docker volumes?"; then
                echo "$DOCKER_VOLUMES" | while read -r volume; do
                    if [[ -n "$volume" ]]; then
                        info "Removing volume: $volume"
                        run docker volume rm "$volume" >/dev/null 2>&1 || warn "Could not remove volume: $volume"
                    fi
                done
                success "Volumes removed."
            fi
        elif [[ -n "$DOCKER_VOLUMES" ]]; then
            skip "Docker volumes kept (use --remove-volumes to delete them)"
        fi

        # ── 4. Remove Networks ────────────────────────────────────────────────
        if [[ "$REMOVE_NETWORKS" == "true" && -n "$DOCKER_NETWORKS" ]]; then
            header "4. Removing Docker networks"
            if prompt_yes "  Remove ZenSynora Docker network?"; then
                echo "$DOCKER_NETWORKS" | while read -r net; do
                    if [[ -n "$net" ]]; then
                        info "Removing network: $net"
                        run docker network rm "$net" >/dev/null 2>&1 || warn "Could not remove network: $net"
                    fi
                done
                success "Networks removed."
            fi
        elif [[ -n "$DOCKER_NETWORKS" ]]; then
            skip "Docker networks kept (use --remove-networks to delete them)"
        fi

        # ── 5. Compose Down (catch-all) ───────────────────────────────────────
        if [[ -n "$DOCKER_COMPOSE_CMD" && -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
            header "5. Docker Compose cleanup"
            info "Running compose down to ensure clean state..."
            if [[ "$DRY_RUN" == "false" ]]; then
                $DOCKER_COMPOSE_CMD -f "$SCRIPT_DIR/docker-compose.yml" down --remove-orphans >/dev/null 2>&1 || true
            else
                dry_run "$DOCKER_COMPOSE_CMD -f $SCRIPT_DIR/docker-compose.yml down --remove-orphans"
            fi
            success "Compose resources cleaned up."
        fi
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# TRADITIONAL CLEANUP
# ═════════════════════════════════════════════════════════════════════════════

if [[ "$DOCKER_ONLY" == "false" ]]; then
    header "Traditional Resource Cleanup"

    # ── 1. STOP ALL PROCESSES ────────────────────────────────────────────────
    header "1. Stopping active processes"

    # Stop systemd if exists
    if command -v systemctl >/dev/null 2>&1; then
        if systemctl is-active --quiet myclaw 2>/dev/null; then
            info "Stopping systemd service..."
            run sudo systemctl stop myclaw && run sudo systemctl disable myclaw
        fi
        if systemctl is-active --quiet zensynora 2>/dev/null; then
            info "Stopping systemd service (zensynora)..."
            run sudo systemctl stop zensynora && run sudo systemctl disable zensynora
        fi
    fi

    # Kill Python-based CLI processes
    if pgrep -f "python.*cli\.py" >/dev/null 2>&1; then
        info "Terminating MyClaw CLI processes..."
        run pkill -f "python.*cli\.py" || true
    fi

    # Kill Uvicorn (Web UI backend)
    if pgrep -f "uvicorn.*myclaw\.web\.api:app" >/dev/null 2>&1; then
        info "Terminating Web UI backend (uvicorn)..."
        run pkill -f "uvicorn.*myclaw\.web\.api:app" || true
    fi

    # Kill Node (Web UI frontend dev server)
    if pgrep -f "node.*webui" >/dev/null 2>&1; then
        info "Terminating Web UI frontend (node)..."
        run pkill -f "node.*webui" || true
    fi

    success "Processes stopped."

    # ── 2. VIRTUAL ENVIRONMENT ───────────────────────────────────────────────
    header "2. Removing virtual environment"
    VENV_DIR="$(pwd)/venv"
    if [[ -d "$VENV_DIR" ]]; then
        run rm -rf "$VENV_DIR"
        success "venv removed."
    else
        skip "No venv directory found."
    fi

    # ── 3. USER DATA CLEANUP ─────────────────────────────────────────────────
    header "3. Cleaning user data"
    MYCLAW_DIR="$HOME/.myclaw"

    if [[ -d "$MYCLAW_DIR" ]]; then
        if [[ "$KEEP_DATA" == "true" ]]; then
            info "Keeping user data as requested (--keep-data)."
        elif [[ "$KEEP_CONFIG" == "true" ]]; then
            info "Removing data but keeping config..."
            run rm -rf "$MYCLAW_DIR/workspace" \
                       "$MYCLAW_DIR/knowledge" \
                       "$MYCLAW_DIR/memory.db" \
                       "$MYCLAW_DIR/task_logs" \
                       "$MYCLAW_DIR/mcp" \
                       "$MYCLAW_DIR/TOOLBOX" \
                       "$MYCLAW_DIR/backups" \
                       "$MYCLAW_DIR/skills" \
                       "$MYCLAW_DIR/benchmarks" \
                       "$MYCLAW_DIR/newtech" \
                       "$MYCLAW_DIR/semantic_cache" \
                       "$MYCLAW_DIR/sandbox" \
                       "$MYCLAW_DIR/audit" \
                       "$MYCLAW_DIR/medic" \
                       "$MYCLAW_DIR/logs"
            # ── Sprint 3-9 additions ──────────────────────────────────────
            # New persistent artifacts that didn't exist before the recent
            # sprints. The cost-tracking DB is data, not config; same for
            # the per-user memory shards (memory_<user>.db). The plugins
            # install dir holds downloaded artifacts — re-fetchable.
            run rm -f  "$MYCLAW_DIR/cost_tracking.db"      # Sprint 3 cost tracker
            run rm -f  "$MYCLAW_DIR/vectors.db"            # Sprint 4 default vector store
            run rm -f  "$MYCLAW_DIR/prompts.jsonl"         # Sprint 3 prompt registry
            run rm -f  "$MYCLAW_DIR/knowledge_gaps.jsonl"  # Sprint 1+ KB gap log
            run rm -f  "$MYCLAW_DIR/scheduler_jobs.jsonl"  # Sprint X scheduler
            run rm -f  "$MYCLAW_DIR/"memory_*.db           # Sprint 11: per-tenant memory shards
            run rm -rf "$MYCLAW_DIR/plugins"               # Sprint 9 marketplace install dir
            run rm -rf "$MYCLAW_DIR/hub"                   # Sprint 9 local hub registry
            success "Data cleared, config preserved."
        else
            if prompt_yes "  Remove all MyClaw data in $MYCLAW_DIR?"; then
                run rm -rf "$MYCLAW_DIR"
                success "Data directory cleared."
            else
                skip "User data kept."
            fi
        fi
    else
        skip "No ~/.myclaw directory found."
    fi

    # ── 3.5 Scheduler persistence cleanup ────────────────────────────────────
    SCHEDULER_PERSIST="$MYCLAW_DIR/scheduler_jobs.jsonl"
    if [[ -f "$SCHEDULER_PERSIST" ]]; then
        header "3.5 Scheduler persistence"
        run rm -f "$SCHEDULER_PERSIST"
        success "Scheduler persistence file removed."
    fi

    # ── 3.6 Marketplace install artifacts (Sprint 9) ─────────────────────────
    # Downloaded plugin payloads under ~/.myclaw/plugins/installed/. Safe
    # to remove on uninstall; reinstall will re-fetch from the configured
    # source (OpenClaw / GitHub releases / local hub).
    if [[ -d "$MYCLAW_DIR/plugins" ]]; then
        header "3.6 Plugin marketplace artifacts"
        if [[ "$KEEP_DATA" == "false" ]] && prompt_yes "  Remove downloaded plugin artifacts ($MYCLAW_DIR/plugins)?"; then
            run rm -rf "$MYCLAW_DIR/plugins"
            success "Plugin install directory removed."
        else
            skip "Plugin artifacts kept."
        fi
    fi

    # ── 3.7 Per-tenant memory shards (Sprint 11) ─────────────────────────────
    # Multi-tenancy creates one memory_<user>.db per UserContext; the
    # original memory.db cleanup above only catches the legacy single-user
    # path. Glob explicitly so we don't miss them.
    if compgen -G "$MYCLAW_DIR/memory_*.db" > /dev/null; then
        header "3.7 Per-tenant memory shards"
        if [[ "$KEEP_DATA" == "false" ]] && prompt_yes "  Remove all per-tenant memory_*.db shards?"; then
            run rm -f "$MYCLAW_DIR"/memory_*.db
            success "Per-tenant memory shards removed."
        else
            skip "Per-tenant memory shards kept."
        fi
    fi

    # ── 3.8 Cost-tracking DB (Sprint 3) ──────────────────────────────────────
    if [[ -f "$MYCLAW_DIR/cost_tracking.db" ]]; then
        header "3.8 Cost-tracking database"
        if [[ "$KEEP_DATA" == "false" ]] && prompt_yes "  Remove cost_tracking.db (Sprint 3 dashboard data)?"; then
            run rm -f "$MYCLAW_DIR/cost_tracking.db"
            success "Cost-tracking DB removed."
        fi
    fi

    # ── 3.9 Vector store + prompt registry (Sprints 3, 4) ────────────────────
    for f in vectors.db prompts.jsonl knowledge_gaps.jsonl; do
        if [[ -f "$MYCLAW_DIR/$f" ]]; then
            run rm -f "$MYCLAW_DIR/$f"
            info "Removed $f"
        fi
    done

    # ── 4. WEB UI ARTIFACTS ──────────────────────────────────────────────────
    header "4. Cleaning Web UI artifacts"
    if [[ -d "webui" ]]; then
        if prompt_yes "  Remove node_modules and builds in webui/?"; then
            run rm -rf "webui/node_modules" "webui/dist"
            success "Web UI artifacts removed."
        fi
    else
        skip "No webui/ directory found."
    fi

    # ── 5. SYSTEM CLEANUP ────────────────────────────────────────────────────
    header "5. System cleanup"

    # Remove systemd service files
    for svc in myclaw zensynora; do
        SERVICE_FILE="/etc/systemd/system/${svc}.service"
        if [[ -f "$SERVICE_FILE" ]]; then
            info "Removing systemd service: $svc"
            run sudo rm -f "$SERVICE_FILE"
            run sudo systemctl daemon-reload >/dev/null 2>&1 || true
            success "Service $svc removed."
        fi
    done

    # Clear Python caches
    info "Clearing Python cache files..."
    run find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    run find . -type f -name "*.pyc" -delete 2>/dev/null || true
    run find . -type f -name "*.pyo" -delete 2>/dev/null || true
    success "Python caches cleared."

    # ── 6. LOCAL DATA BIND MOUNT (if created by install.sh) ──────────────────
    if [[ -d "$SCRIPT_DIR/data" ]]; then
        header "6. Local data bind mount"
        if prompt_yes "  Remove local data directory ($SCRIPT_DIR/data)?"; then
            run rm -rf "$SCRIPT_DIR/data"
            success "Local data directory removed."
        fi
    fi
fi

# ── DONE ─────────────────────────────────────────────────────────────────────
header "⚠️  ZenSynora Uninstallation Complete"

if [[ "$DRY_RUN" == "true" ]]; then
    info "This was a dry run. No actual changes were made."
fi

if [[ "$DOCKER_AVAILABLE" == "true" && "$REMOVE_VOLUMES" == "false" && "$TRADITIONAL_ONLY" == "false" ]]; then
    echo ""
    info "Docker volumes were preserved. To completely erase all data:"
    echo "  ./uninstall.sh --remove-volumes"
fi

if [[ "$KEEP_DATA" == "true" && "$DOCKER_ONLY" == "false" ]]; then
    echo ""
    info "User data was preserved in ~/.myclaw"
fi
