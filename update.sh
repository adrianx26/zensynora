#!/usr/bin/env bash
# ZenSynora safe updater.
#
#   bash update.sh                    # update to the latest version on GitHub (main)
#   bash update.sh --ref v0.6.0       # a specific branch or tag
#   bash update.sh --from-dir PATH    # from a local source folder
#   bash update.sh status --check     # what's installed / is there an update?
#   bash update.sh rollback           # go back to the previous version
#   bash update.sh backup             # back up ~/.myclaw now
#   bash update.sh node               # install Node.js 22 for the web UI (no sudo)
#   bash update.sh --help
#
# Every update: backs up ~/.myclaw, builds the new version in its own folder
# next to the running one, checks it against your config, then switches over
# atomically. Any failure leaves the previous version active.
#
# Requirements are checked first: Python 3.11+ with venv/pip (else: the apt /
# dnf command to run), 1.5 GB free disk, and Node.js 20.19+ for the web UI —
# when no usable node/npm is found, Node.js 22 is downloaded from nodejs.org
# into ~/.local/share/zensynora/node (no sudo).
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

PYTHON=""
for cand in "${ZENSYNORA_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11; do
    [[ -n "$cand" ]] || continue
    if command -v "$cand" >/dev/null 2>&1 \
        && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$(command -v "$cand")"
        break
    fi
done
[[ -n "$PYTHON" ]] || { echo "Python 3.11+ is required to run the updater." >&2; exit 1; }

cd "$HERE"
exec env PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m updater "$@"
