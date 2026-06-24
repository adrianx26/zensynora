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
python -m pip install --upgrade --quiet pip
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
:: Create data subdirectories to prevent runtime errors
if not exist "%MYCLAW_PROFILES_DIR%" mkdir "%MYCLAW_PROFILES_DIR%"
if not exist "%MYCLAW_KNOWLEDGE_DIR%" mkdir "%MYCLAW_KNOWLEDGE_DIR%"
if not exist "%MYCLAW_MEMORY_DIR%" mkdir "%MYCLAW_MEMORY_DIR%"
if not exist "%MYCLAW_PLUGINS_DIR%" mkdir "%MYCLAW_PLUGINS_DIR%"
if not exist "%MYCLAW_CHECKPOINTS_DIR%" mkdir "%MYCLAW_CHECKPOINTS_DIR%"
if not exist "%MYCLAW_LOG_DIR%" mkdir "%MYCLAW_LOG_DIR%"
if not exist "%MYCLAW_WORKSPACE_DIR%" mkdir "%MYCLAW_WORKSPACE_DIR%"
if not exist "%MYCLAW_TOOLBOX_DIR%" mkdir "%MYCLAW_TOOLBOX_DIR%"
if not exist "%MYCLAW_SEMANTIC_CACHE_DIR%" mkdir "%MYCLAW_SEMANTIC_CACHE_DIR%"
if not exist "%MYCLAW_AUDIT_DIR%" mkdir "%MYCLAW_AUDIT_DIR%"
if not exist "%MYCLAW_HUB_DIR%" mkdir "%MYCLAW_HUB_DIR%"

:: --- Launch ZenSynora ---
echo.
echo Starting ZenSynora...
echo All application data will be stored in: %DATA_PATH%
echo Press Ctrl+C to exit.
echo.

:: Execute zensynora, passing all script arguments to it.
:: If no arguments are provided, default to 'gateway' to start the messaging bot.
:: We call the module directly to avoid potential issues with the .exe wrapper on portable drives.
if "%~1"=="" (
    "%PYTHON_EXEC%" -m myclaw.cli gateway
) else (
    "%PYTHON_EXEC%" -m myclaw.cli %*
)

echo.
echo ZenSynora has exited.
pause
endlocal