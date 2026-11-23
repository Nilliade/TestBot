@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title TestBot - Stage 1 (Read-Only) Quick Start

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

:: If .venv is not set up yet or dependencies are missing, run full Setup.bat
if not exist "%VENV_PY%" (
    echo [INFO] Virtual environment not found. Running full Setup.bat ...
    call "%~dp0Setup.bat" %*
    exit /b %ERRORLEVEL%
)

"%VENV_PY%" -c "import fastapi, uvicorn, pydantic, pyarrow, yaml, tzdata" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [INFO] Dependencies are not fully installed yet. Running full Setup.bat ...
    call "%~dp0Setup.bat" %*
    exit /b %ERRORLEVEL%
)

echo ====================================================================
echo   TestBot - Stage 1 (Strict Read-Only Prototype) - Quick Start
echo ====================================================================
echo.

"%VENV_PY%" -m backend.app.tools.preflight_check --offline
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Offline preflight check failed.
    pause
    exit /b 1
)

echo.
echo   Server URL : http://127.0.0.1:8765/
echo   API Docs   : http://127.0.0.1:8765/docs
echo   Press Ctrl+C to stop the server.
echo.

if /i not "%~1"=="--no-browser" (
    start "" "http://127.0.0.1:8765/"
)

"%VENV_PY%" -m backend.app.main
pause
exit /b %ERRORLEVEL%
