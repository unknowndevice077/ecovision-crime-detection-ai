@echo off
setlocal

echo ===============================================
echo  EcoVision Sentinel -- local setup
echo ===============================================

where node >nul 2>nul
if errorlevel 1 (
    echo ERROR: Node.js not found. Install it from https://nodejs.org first.
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11 from https://python.org first.
    exit /b 1
)

echo.
echo [1/5] Installing frontend dependencies...
call npm install
if errorlevel 1 goto :error

echo.
echo [2/5] Building frontend...
call npm run build
if errorlevel 1 goto :error

echo.
echo [3/5] Creating dev environment (.venv) -- this is what run_dev_system.bat uses...
REM Dev keeps the full monolithic requirements.txt: local hot-reload wants
REM backend + detector + plotting + onnx/tensorrt export tooling all in one
REM interpreter so nothing needs juggling while iterating. Nothing here ships.
if exist .venv (
    echo  =^> .venv already exists, skipping creation. Delete it first to rebuild from scratch.
) else (
    python -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\pip install --upgrade pip
call .venv\Scripts\pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 goto :error

echo.
echo [4/5] Building python-env -- the runtime that gets bundled into the
echo       installer by build_release.bat.
echo.
echo       NOT a venv, deliberately: a `python -m venv` records the exact path
echo       of the interpreter that created it and breaks on any other machine
echo       (see tools\build_python_env.ps1 for the full story -- this shipped
echo       once and broke the app on every machine except the dev box).
echo       Built on the official embeddable Python distribution instead,
echo       which is genuinely relocatable, then verified as such before this
echo       script reports success.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_python_env.ps1
if errorlevel 1 goto :error

echo.
echo [5/5] Setup complete.
echo  - Run run_dev_system.bat for hot-reload local development (uses .venv)
echo  - Run build_release.bat to package the installer (uses python-env)
echo  - Run start.bat to test the packaged-style app locally without building an exe
goto :end

:error
echo.
echo Setup failed -- see the error above.
exit /b 1

:end
endlocal