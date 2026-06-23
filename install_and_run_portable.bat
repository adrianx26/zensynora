@echo off
setlocal
:: ZenSynora Portable Launcher for Windows
:: This script handles initial setup and subsequent runs.
:: It creates a self-contained environment on the portable drive.

:: --- Path Setup ---
:: Get the directory where this script is located.
set "BASE_PATH=%~dp0"
set "APP_PATH=%BASE_PATH%app"
set "VENV_PATH=%BASE_PATH%venv"
set "DATA_PATH=%BASE_PATH%data"
set "PYTHON_EXEC=%VENV_PATH%\Scripts\python.exe"

echo --- ZenSynora Portable Launcher ---
echo Base Path: %BASE_PATH%
echo ---------------------------------

:: --- Python and Virtual Environment Setup ---
if not exist "%PYTHON_EXEC%" (
    echo Python virtual environment not found. Creating one...
    echo This requires Python 3.11+ to be installed and in your system's PATH.

    python -m venv "%VENV_PATH%"
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment. Please ensure Python is installed and in your PATH.
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
)

:: Activate the virtual environment
call "%VENV_PATH%\Scripts\activate.bat"

:: --- Install/Update Dependencies ---
echo Checking and installing/updating dependencies...
python -m pip install --upgrade pip > nul
python -m pip install --quiet -e "%APP_PATH%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies from pyproject.toml.
    pause
    exit /b 1
)
echo Dependencies are up to date.

:: --- Set Environment Variables for Portability ---
echo Setting portable environment variables...
set "MYCLAW_DATA_DIR=%DATA_PATH%"
set "MYCLAW_PROFILES_DIR=%DATA_PATH%\profiles"
set "MYCLAW_KNOWLEDGE_DIR=%DATA_PATH%\knowledge"
set "MYCLAW_MEMORY_DIR=%DATA_PATH%\memory"
set "MYCLAW_PLUGINS_DIR=%DATA_PATH%\plugins"
set "MYCLAW_CHECKPOINTS_DIR=%DATA_PATH%\checkpoints"
set "MYCLAW_LOG_DIR=%DATA_PATH%\logs"
set "MYCLAW_WORKSPACE_DIR=%DATA_PATH%\workspace"
set "MYCLAW_TOOLBOX_DIR=%DATA_PATH%\tools"
set "MYCLAW_SEMANTIC_CACHE_DIR=%DATA_PATH%\semantic_cache"
set "MYCLAW_AUDIT_DIR=%DATA_PATH%\audit"
set "MYCLAW_HUB_DIR=%DATA_PATH%\hub"

:: Create the main data directory if it doesn't exist
if not exist "%DATA_PATH%" mkdir "%DATA_PATH%"

:: --- Launch ZenSynora ---
echo.
echo Starting ZenSynora...
echo All application data will be stored in: %DATA_PATH%
echo Press Ctrl+C to exit.
echo.

:: Execute zensynora, passing all script arguments to it
zensynora %*

echo.
echo ZenSynora has exited.
pause
endlocal