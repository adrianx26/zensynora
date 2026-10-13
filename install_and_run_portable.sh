#!/bin/bash
# ZenSynora Portable Launcher for Linux and macOS
# This script handles initial setup and subsequent runs.
# It creates a self-contained environment on the portable drive.

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Path Setup ---
# Get the absolute path of the directory where this script is located.
# This is a portable way that works on both Linux and macOS.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Define paths relative to the script's location.
# The code may sit in ./app (portable-drive layout) or next to this script
# (a git checkout). Fixed 2026-10-13 (v0.7.0): only ./app was supported.
APP_PATH="$SCRIPT_DIR/app"
[ -f "$APP_PATH/pyproject.toml" ] || APP_PATH="$SCRIPT_DIR"
VENV_PATH="$SCRIPT_DIR/venv"
DATA_PATH="$SCRIPT_DIR/data"
PYTHON_EXEC="$VENV_PATH/bin/python"

echo "--- ZenSynora Portable Launcher ---"
echo "Base Path: $SCRIPT_DIR"
echo "---------------------------------"

# --- Python and Virtual Environment Setup ---
if [ -e "$VENV_PATH" ] && {
    [ ! -x "$PYTHON_EXEC" ] \
        || ! "$PYTHON_EXEC" -c 'import sys; raise SystemExit(sys.prefix == sys.base_prefix)' >/dev/null 2>&1
}; then
    echo "The existing virtual environment is incomplete or invalid: $VENV_PATH"
    read -r -p "Recreate it? This removes only the portable venv directory. [y/N] " RECREATE_VENV
    case "$RECREATE_VENV" in
        y|Y|yes|YES)
            rm -rf "$VENV_PATH"
            ;;
        *)
            echo "ERROR: Repair the virtual environment or choose to recreate it before continuing."
            exit 1
            ;;
    esac
fi

if [ ! -x "$PYTHON_EXEC" ]; then
    echo "Python virtual environment not found. Creating one..."
    echo "This requires Python 3.11+ to be installed on your system."

    # Find a suitable python executable
    PYTHON_CMD="python3"
    if ! command -v $PYTHON_CMD &> /dev/null; then
        PYTHON_CMD="python"
        if ! command -v $PYTHON_CMD &> /dev/null; then
            echo "ERROR: Could not find 'python3' or 'python' in your PATH."
            exit 1
        fi
    fi

    if ! "$PYTHON_CMD" -m venv "$VENV_PATH"; then
        if command -v apt-get >/dev/null 2>&1; then
            read -r -p "Virtual environment support may be missing. Install python3-venv and retry? [y/N] " INSTALL_VENV
            case "$INSTALL_VENV" in
                y|Y|yes|YES)
                    if [ "$(id -u)" -eq 0 ]; then
                        apt-get update
                        apt-get install -y python3-venv
                    elif command -v sudo >/dev/null 2>&1; then
                        sudo apt-get update
                        sudo apt-get install -y python3-venv
                    else
                        echo "ERROR: root privileges or sudo are required to install python3-venv."
                        exit 1
                    fi
                    "$PYTHON_CMD" -m venv "$VENV_PATH" || {
                        echo "ERROR: Could not create the venv. Install the matching python3.X-venv package and retry."
                        exit 1
                    }
                    ;;
                *)
                    echo "ERROR: Install python3-venv (Debian/Ubuntu), then rerun this launcher."
                    exit 1
                    ;;
            esac
        else
            echo "ERROR: Could not create the virtual environment. Ensure Python 3.11+ includes venv/ensurepip support."
            exit 1
        fi
    fi
    echo "Virtual environment created successfully."
fi

# Activate the virtual environment for this script's session
source "$VENV_PATH/bin/activate"

# Ensure pip is available before installing or updating dependencies.
if ! "$PYTHON_EXEC" -m pip --version >/dev/null 2>&1; then
    echo "pip is missing from the virtual environment. Bootstrapping with ensurepip..."
    if ! "$PYTHON_EXEC" -m ensurepip --upgrade; then
        echo "ERROR: pip is missing and could not be bootstrapped with ensurepip."
        exit 1
    fi
fi
if ! "$PYTHON_EXEC" -m pip --version >/dev/null 2>&1; then
    echo "ERROR: pip is unavailable in the virtual environment."
    exit 1
fi

# --- Install/Update Dependencies ---
echo "Checking and installing/updating dependencies..."
"$PYTHON_EXEC" -m pip install --upgrade --quiet pip
if ! "$PYTHON_EXEC" -m pip install --quiet -e "$APP_PATH"; then
    echo "ERROR: Failed to install dependencies from pyproject.toml."
    exit 1
fi
echo "Dependencies are up to date."

# --- Keep all data on the portable drive ---
# Fixed 2026-10-13 (v0.7.0): the old MYCLAW_*_DIR variables were never read by
# ZenSynora, so data silently went to ~/.myclaw on the host computer. All data
# lives under $HOME/.myclaw, so HOME is pointed at the drive for the app only.
mkdir -p "$DATA_PATH"
export HOME="$DATA_PATH"

# --- Launch ZenSynora ---
echo ""
echo "Starting ZenSynora..."
echo "All application data will be stored in: $DATA_PATH/.myclaw"
echo "Press Ctrl+C to exit."
echo ""

# Execute zensynora, passing all script arguments to it
## We call the module directly to avoid potential issues with wrapper scripts.
"$PYTHON_EXEC" -m myclaw "$@"

echo ""
echo "ZenSynora has exited."