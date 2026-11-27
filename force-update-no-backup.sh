#!/usr/bin/env bash
# Force the current GitHub main into an existing Linux installation without backup archives.
set -euo pipefail

error() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == Linux* ]] || error "This script is for Linux installations."
[[ $# -le 1 ]] || error "Usage: bash force-update-no-backup.sh [INSTALL_BASE]"
for tool in curl tar mktemp; do
    command -v "$tool" >/dev/null 2>&1 || error "$tool is required. Install it and retry."
done

base="${1:-${ZENSYNORA_HOME:-$HOME/.local/share/zensynora}}"
[[ -d "$base/source" ]] || error "No installation at $base/source. Pass the install base as the first argument."

printf '[WARNING] No backup archives will be created. Failed migrations cannot recover user data.\n'
printf '[WARNING] Stop non-systemd Web UI, agents and Telegram processes before running this script.\n'
work="$(mktemp -d -t zensynora-no-backup.XXXXXXXX)"
trap 'rm -rf -- "$work"' EXIT
mkdir "$work/source"

printf '[INFO] Downloading adrianx26/zensynora main...\n'
curl --fail --location --silent --show-error --retry 3 --connect-timeout 20 --max-time 180 \
    https://codeload.github.com/adrianx26/zensynora/tar.gz/main --output "$work/source.tar.gz"
tar -xzf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
[[ -f "$work/source/update.sh" && -f "$work/source/updater/steps.py" ]] \
    || error "Downloaded source is incomplete; no update was started."

help="$(bash "$work/source/update.sh" update --help)" \
    || error "The downloaded updater could not start. Fix the reported Python requirement and retry."
[[ "$help" == *"--no-backup"* ]] \
    || error "GitHub main does not yet support --no-backup. Publish that updater first; no update was started."

args=(update --from-dir "$work/source" --base "$base" --force --no-backup)
if [[ -n "${ZENSYNORA_DATA_DIR:-}" ]]; then
    args+=(--data-dir "$ZENSYNORA_DATA_DIR")
fi
printf '[INFO] Running forced update without backup archives...\n'
bash "$work/source/update.sh" "${args[@]}"
