#!/usr/bin/env bash
# ZenSynora — remove temporary and cache files from the project folder.
#
# Fixed 2026-10-13 (v0.7.0): the old version deleted test_*.py, *.zip and
# *.tmp from the CURRENT directory, so running it from ~ could delete your own
# files. It now only touches the folder this script lives in, never the
# tests/ suite, and shows what it removes (--dry-run to preview).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

remove() {
    local path
    for path in "$@"; do
        [[ -e "$path" ]] || continue
        if $DRY_RUN; then echo "  would remove: ${path#"$ROOT"/}"; else rm -rf -- "$path"; echo "  removed: ${path#"$ROOT"/}"; fi
    done
}

echo "Cleaning temporary files in $ROOT"

# Scratch test scripts in the project root only (the real suite is tests/).
shopt -s nullglob
remove "$ROOT"/test_*.py "$ROOT"/*.tmp "$ROOT"/out*.txt "$ROOT"/putty

# Caches anywhere in the project, except inside virtual environments.
while IFS= read -r -d '' dir; do remove "$dir"; done < <(
    find "$ROOT" \( -path "$ROOT/venv" -o -path "$ROOT/.venv" -o -path "$ROOT/webui/node_modules" \) -prune \
        -o -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -print0)

echo "Done.$($DRY_RUN && echo ' (dry run — nothing was deleted)')"
