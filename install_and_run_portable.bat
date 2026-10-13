@echo off
setlocal
:: ZenSynora Portable Launcher for Windows
:: This script handles initial setup and subsequent runs.
:: It creates a self-contained environment on the portable drive.

:: --- Path Setup ---
:: Get the directory where this script is located.
set "BASE_PATH=%~dp0"
:: Code in .\app (portable layout) or next to this script (git checkout).
set "APP_PATH=%BASE_PATH%app"
if not exist "%APP_PATH%\pyproject.toml" set "APP_PATH=%BASE_PATH:~0,-1%"
set "VENV_PATH=%BASE_PATH%venv"
set "DATA_PATH=%BASE_PATH%data"
set "PYTHON_EXEC=%VENV_PATH%\Scripts\python.exe"

echo --- ZenSynora Portable Launcher ---
echo Base Path: %BASE_PATH%
echo ---------------------------------

:: --- Python and Virtual Environment Setup ---
if exist "%VENV_PATH%" (
    if not exist "%PYTHON_EXEC%" (
        goto repair_venv
    )
    "%PYTHON_EXEC%" -c "import sys; raise SystemExit(sys.prefix == sys.base_prefix)" >nul 2>&1
    if errorlevel 1 goto repair_venv
)

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
goto venv_ready

:repair_venv
echo The existing virtual environment is incomplete or invalid: %VENV_PATH%
choice /c YN /m "Recreate it? This removes only the portable venv directory"
if errorlevel 2 (
    echo ERROR: Repair the virtual environment or choose to recreate it before continuing.
    pause
    exit /b 1
)
rmdir /s /q "%VENV_PATH%"
python -m venv "%VENV_PATH%"
if errorlevel 1 (
    echo ERROR: Could not recreate the virtual environment. Reinstall Python with venv support and retry.
    pause
    exit /b 1
)

:venv_ready

:: Activate the virtual environment
call "%VENV_PATH%\Scripts\activate.bat"

:: Ensure pip is available before installing or updating dependencies
"%PYTHON_EXEC%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip is missing from the virtual environment. Bootstrapping with ensurepip...
    "%PYTHON_EXEC%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo ERROR: pip is missing and could not be bootstrapped with ensurepip.
        pause
        exit /b 1
    )
)
"%PYTHON_EXEC%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip is unavailable in the virtual environment.
    pause
    exit /b 1
)

:: --- Install/Update Dependencies ---
echo Checking and installing/updating dependencies...
"%PYTHON_EXEC%" -m pip install --upgrade --quiet pip
"%PYTHON_EXEC%" -m pip install --quiet -e "%APP_PATH%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies from pyproject.toml.
    pause
    exit /b 1
)
echo Dependencies are up to date.

:: --- Keep all data on the portable drive ---
:: Fixed 2026-10-13 (v0.7.0): the old MYCLAW_*_DIR variables were never read,
:: so data went to %USERPROFILE%\.myclaw on the host computer. ZenSynora keeps
:: everything in the home folder's .myclaw, so the home is pointed at the drive.
if not exist "%DATA_PATH%" mkdir "%DATA_PATH%"
set "USERPROFILE=%DATA_PATH%"
set "HOME=%DATA_PATH%"

:: --- Launch ZenSynora ---
echo.
echo Starting ZenSynora...
echo All application data will be stored in: %DATA_PATH%\.myclaw
echo Press Ctrl+C to exit.
echo.

:: Execute zensynora, passing all script arguments to it.
:: If no arguments are provided, default to 'gateway' to start the messaging bot.
:: We call the module directly to avoid potential issues with the .exe wrapper on portable drives.
if "%~1"=="" (
    "%PYTHON_EXEC%" -m myclaw gateway
) else (
    "%PYTHON_EXEC%" -m myclaw %*
)

echo.
echo ZenSynora has exited.
pause
endlocal