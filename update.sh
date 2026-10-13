#!/usr/bin/env bash
# ZenSynora safe updater.
#
#   bash update.sh                    # update to the latest version on GitHub (main)
#   bash update.sh --ref v0.6.0       # a specific branch or tag
#   bash update.sh --from-dir PATH    # from a local source folder
#   bash update.sh status --check     # what's installed / is there an update?
#   bash update.sh rollback           # go back to the previous version
#   bash update.sh backup             # back up ~/.myclaw now
#   bash update.sh --help
#
# Every update: backs up ~/.myclaw, builds the new version in its own folder
# next to the running one, checks it against your config, then switches over
# atomically. Any failure leaves the previous version active.
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
