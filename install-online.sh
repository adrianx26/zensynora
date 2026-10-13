#!/usr/bin/env bash
# Download a GitHub branch of ZenSynora, then run the repository's Linux installer.
# If ZenSynora is already installed, this performs a safe update instead (see update.sh).
# Usage: curl -fsSL https://raw.githubusercontent.com/adrianx26/zensynora/main/install-online.sh | bash

set -euo pipefail

REPOSITORY="adrianx26/zensynora"
REF="${ZENSYNORA_REF:-main}"
INSTALL_DIR="${ZENSYNORA_INSTALL_DIR:-${HOME}/.local/share/zensynora/source}"

info() {
    printf '  [INFO] %s\n' "$*"
}

error() {
    printf '  [ERROR] %s\n' "$*" >&2
    exit 1
}

case "$(uname -s)" in
    Linux*) ;;
    *)
        error "This bootstrap installer currently supports Linux only. Use install.sh on macOS or Linux/WSL."
        ;;
esac

command -v tar >/dev/null 2>&1 || error "tar is required to extract the GitHub source archive."
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    error "curl or wget is required to download ZenSynora."
fi

if [[ -z "$REF" ]]; then
    error "ZENSYNORA_REF must name a GitHub branch."
fi

TEMP_DIR="$(mktemp -d)" || error "Could not create a temporary directory."
cleanup() {
    rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT

ARCHIVE="$TEMP_DIR/zensynora.tar.gz"
STAGING_DIR="$TEMP_DIR/source"
mkdir -p "$STAGING_DIR"

ARCHIVE_URL="https://codeload.github.com/${REPOSITORY}/tar.gz/refs/heads/${REF}"
info "Downloading ${REPOSITORY} (${REF})..."
if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --retry 3 \
        --connect-timeout 20 "$ARCHIVE_URL" --output "$ARCHIVE" \
        || error "Could not download the GitHub source archive."
else
    wget --https-only --tries=3 --timeout=20 --quiet "$ARCHIVE_URL" \
        --output-document="$ARCHIVE" \
        || error "Could not download the GitHub source archive."
fi

info "Extracting source archive..."
tar -xzf "$ARCHIVE" --strip-components=1 -C "$STAGING_DIR" \
    || error "Could not extract the GitHub source archive."
[[ -f "$STAGING_DIR/install.sh" && -f "$STAGING_DIR/requirements.txt" ]] \
    || error "The downloaded archive does not contain the expected installer and requirements files."

# ── Existing installation? Update it safely instead of copying over it ──────
# Copying new files over a working install can leave it half-updated. The
# updater backs up ~/.myclaw, builds the new version next to the old one,
# verifies it and only then switches (rolling back on any failure).
if [[ -L "$INSTALL_DIR" || -x "$INSTALL_DIR/venv/bin/python" ]]; then
    if [[ "$(basename "$INSTALL_DIR")" == "source" && -f "$STAGING_DIR/update.sh" ]]; then
        info "Existing installation found — running the safe updater..."
        # install.sh options (--no-docker, -y …) do not apply to updates; extra
        # updater options can be passed with ZENSYNORA_UPDATE_ARGS.
        # shellcheck disable=SC2086
        bash "$STAGING_DIR/update.sh" update --from-dir "$STAGING_DIR" \
            --base "$(dirname "$INSTALL_DIR")" ${ZENSYNORA_UPDATE_ARGS:-}
        exit $?
    fi
    error "An installation already exists at $INSTALL_DIR. Run its update.sh, or remove it first."
fi

mkdir -p "$INSTALL_DIR" || error "Could not create install directory: $INSTALL_DIR"
cp -a "$STAGING_DIR/." "$INSTALL_DIR/" \
    || error "Could not copy the downloaded source into: $INSTALL_DIR"

info "Starting the Linux installer from $INSTALL_DIR..."
# Scope the stderr redirect to the probe: a bare `exec 3</dev/tty 2>/dev/null`
# would silence stderr for the rest of the run, hiding every `read -p` prompt.
if { exec 3</dev/tty; } 2>/dev/null; then
    bash "$INSTALL_DIR/install.sh" "$@" <&3 3<&-
    exec 3<&-
else
    bash "$INSTALL_DIR/install.sh" "$@"
fi
