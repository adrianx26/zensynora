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
APP_PATH="$SCRIPT_DIR/app"
VENV_PATH="$SCRIPT_DIR/venv"
DATA_PATH="$SCRIPT_DIR/data"
PYTHON_EXEC="$VENV_PATH/bin/python"

echo "--- ZenSynora Portable Launcher ---"
echo "Base Path: $SCRIPT_DIR"
echo "---------------------------------"

# --- Python and Virtual Environment Setup ---
if [ ! -f "$PYTHON_EXEC" ]; then
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

    $PYTHON_CMD -m venv "$VENV_PATH"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create virtual environment."
        exit 1
    fi
    echo "Virtual environment created successfully."
fi

# Activate the virtual environment for this script's session
source "$VENV_PATH/bin/activate"

# --- Install/Update Dependencies ---
echo "Checking and installing/updating dependencies..."
python -m pip install --upgrade --quiet pip
python -m pip install --quiet -e "$APP_PATH"
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install dependencies from pyproject.toml."
    exit 1
fi
# Explicitly install requests, as it seems to be missing from pyproject.toml
python -m pip install --quiet requests
echo "Dependencies are up to date."

# --- Set Environment Variables for Portability ---
echo "Setting portable environment variables..."
export MYCLAW_DATA_DIR="$DATA_PATH"
export MYCLAW_PROFILES_DIR="$DATA_PATH/profiles"
export MYCLAW_KNOWLEDGE_DIR="$DATA_PATH/knowledge"
export MYCLAW_MEMORY_DIR="$DATA_PATH/memory"
export MYCLAW_PLUGINS_DIR="$DATA_PATH/plugins"
export MYCLAW_CHECKPOINTS_DIR="$DATA_PATH/checkpoints"
export MYCLAW_LOG_DIR="$DATA_PATH/logs"
export MYCLAW_WORKSPACE_DIR="$DATA_PATH/workspace"
export MYCLAW_TOOLBOX_DIR="$DATA_PATH/tools"
export MYCLAW_SEMANTIC_CACHE_DIR="$DATA_PATH/semantic_cache"
export MYCLAW_AUDIT_DIR="$DATA_PATH/audit"
export MYCLAW_HUB_DIR="$DATA_PATH/hub"

# Create the main data directory if it doesn't exist
mkdir -p "$DATA_PATH"
# Create data subdirectories to prevent runtime errors
mkdir -p "$MYCLAW_PROFILES_DIR"
mkdir -p "$MYCLAW_KNOWLEDGE_DIR"
mkdir -p "$MYCLAW_MEMORY_DIR"
mkdir -p "$MYCLAW_PLUGINS_DIR"
mkdir -p "$MYCLAW_CHECKPOINTS_DIR"
mkdir -p "$MYCLAW_LOG_DIR"
mkdir -p "$MYCLAW_WORKSPACE_DIR"
mkdir -p "$MYCLAW_TOOLBOX_DIR"
mkdir -p "$MYCLAW_SEMANTIC_CACHE_DIR"
mkdir -p "$MYCLAW_AUDIT_DIR"
mkdir -p "$MYCLAW_HUB_DIR"

# --- Launch ZenSynora ---
echo ""
echo "Starting ZenSynora..."
echo "All application data will be stored in: $DATA_PATH"
echo "Press Ctrl+C to exit."
echo ""

# Execute zensynora, passing all script arguments to it
## We call the module directly to avoid potential issues with wrapper scripts.
"$PYTHON_EXEC" -m myclaw.cli "$@"

echo ""
echo "ZenSynora has exited."