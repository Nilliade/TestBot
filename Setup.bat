@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title TestBot - Stage 1 (Read-Only) Setup and Start

set "INSTALL_ONLY=0"
set "TEST_ONLY=0"
set "PREFLIGHT_ONLY=0"
set "SKIP_TESTS=0"
set "NO_BROWSER=0"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--install-only" set "INSTALL_ONLY=1"
if /i "%~1"=="--test-only" set "TEST_ONLY=1"
if /i "%~1"=="--preflight-only" set "PREFLIGHT_ONLY=1"
if /i "%~1"=="--skip-tests" set "SKIP_TESTS=1"
if /i "%~1"=="--no-browser" set "NO_BROWSER=1"
shift
goto :parse_args
:args_done

echo ====================================================================
echo   TestBot - Stage 1 (Strict Read-Only Prototype)
echo   One-Click Setup, Self-Check ^& Local Server Launcher
echo ====================================================================
echo.

:: ------------------------------------------------------------------
:: Step 1. Locate Python 3.11
:: ------------------------------------------------------------------
set "PY_CMD="

py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=py -3.11"
    goto :found_python
)

python -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=python"
    goto :found_python
)

echo [ERROR] Python 3.11 was not found on this machine.
echo.
echo Please install Python 3.11 (64-bit) from:
echo   https://www.python.org/downloads/
echo IMPORTANT: Check "Add python.exe to PATH" during installation.
echo.
pause
exit /b 1

:found_python
for /f "delims=" %%v in ('%PY_CMD% --version 2^>^&1') do set "PY_VER=%%v"
echo [1/6] Found %PY_VER% (command: %PY_CMD%)

:: ------------------------------------------------------------------
:: Step 2. Create virtual environment (.venv) if needed
:: ------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/6] Creating virtual environment in .venv ...
    %PY_CMD% -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment .venv.
        pause
        exit /b 1
    )
) else (
    echo [2/6] Virtual environment .venv already exists.
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

:: ------------------------------------------------------------------
:: Step 3. Install dependencies strictly from backend\requirements.lock
:: ------------------------------------------------------------------
if not exist "backend\requirements.lock" (
    echo [ERROR] Lockfile backend\requirements.lock not found.
    pause
    exit /b 1
)

echo [3/6] Installing pinned dependencies from backend\requirements.lock ...
"%VENV_PY%" -m pip install --disable-pip-version-check -r backend\requirements.lock
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install dependencies from backend\requirements.lock.
    echo Check your internet/proxy settings and try again.
    pause
    exit /b 1
)

"%VENV_PY%" -m pip install --disable-pip-version-check --no-deps -e . >nul 2>nul

:: ------------------------------------------------------------------
:: Step 4. Generate WebSocket JSON Schemas
:: ------------------------------------------------------------------
echo [4/6] Generating WebSocket JSON Schemas ...
"%VENV_PY%" -m backend.app.schemas.generate_ws_schemas
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to generate WebSocket JSON Schemas.
    pause
    exit /b 1
)

if "%INSTALL_ONLY%"=="1" (
    echo.
    echo [OK] Setup finished (--install-only).
    exit /b 0
)

:: ------------------------------------------------------------------
:: Step 5. Run Step 1.1 verification test suite
:: ------------------------------------------------------------------
if "%SKIP_TESTS%"=="0" (
    echo [5/6] Running Step 1.1 verification tests ^(pytest^) ...
    "%VENV_PY%" -m pytest backend/tests/ -q
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Verification tests failed. Server will not start.
        pause
        exit /b 1
    )
) else (
    echo [5/6] Skipping pytest ^(--skip-tests^).
)

if "%TEST_ONLY%"=="1" (
    echo.
    echo [OK] All tests passed (--test-only).
    exit /b 0
)

:: ------------------------------------------------------------------
:: Step 6. Run preflight check & launch local read-only server
:: ------------------------------------------------------------------
echo [6/6] Running offline preflight check ...
"%VENV_PY%" -m backend.app.tools.preflight_check --offline
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Offline preflight check reported an error.
    pause
    exit /b 1
)

if "%PREFLIGHT_ONLY%"=="1" (
    echo.
    echo [OK] Preflight check finished (--preflight-only).
    exit /b 0
)

echo.
echo ====================================================================
echo   Starting local Read-Only server at http://127.0.0.1:8765
echo   - Status dashboard : http://127.0.0.1:8765/
echo   - Swagger API docs : http://127.0.0.1:8765/docs
echo   - Health check     : http://127.0.0.1:8765/healthz
echo   - Instruments JSON : http://127.0.0.1:8765/api/v1/instruments
echo   Press Ctrl+C in this window to stop the server.
echo ====================================================================
echo.

if "%NO_BROWSER%"=="0" (
    start "" "http://127.0.0.1:8765/"
)

"%VENV_PY%" -m backend.app.main
set "SERVER_EXIT=%ERRORLEVEL%"

if %SERVER_EXIT% neq 0 (
    echo.
    echo [WARN] Server exited with code %SERVER_EXIT%.
    pause
    exit /b %SERVER_EXIT%
)

pause
exit /b 0
