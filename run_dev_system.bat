@echo off
title EcoVision Live Testing Workspace Shell
echo ──────────────────────────────────────────────────────────────
echo 🛠️  Initializing Local Uncompiled Development Environment...
echo ──────────────────────────────────────────────────────────────

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo ❌ .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

if not exist "weights\yolo11s-pose.pt" if not exist "weights\yolo11s-pose.engine" (
    echo ⚠️  weights\yolo11s-pose.pt (or .engine^) not found -- the AI core will crash on startup.
    echo    See README.md "Add Model Weights" for what needs to go in weights\.
    pause
)
if not exist "weights\weapon_signs.pt" if not exist "weights\weapon_signs.engine" (
    echo ⚠️  weights\weapon_signs.pt (or .engine^) not found -- the AI core will crash on startup.
    pause
)
if not exist "weights\x3d_xs_violence_best.pt" (
    echo ⚠️  weights\x3d_xs_violence_best.pt not found -- the AI core will crash on startup.
    pause
)

:: maincode\main.py does "from port_utils import ..." but port_utils.py lives
:: in app\. The PACKAGED build works because package.json's extraResources
:: copies app\port_utils.py into BOTH backend\ and maincode\ -- dev mode has
:: no such copy, so running "python maincode/main.py" put sys.path[0] at
:: maincode\ and the import died with ModuleNotFoundError. Putting app\ on
:: PYTHONPATH reproduces the packaged layout's import resolution without
:: duplicating the file in the repo (a second copy would silently drift).
set "PYTHONPATH=%~dp0app;%PYTHONPATH%"
echo  =^> PYTHONPATH set for shared modules (port_utils).

echo [1/4] Scanning for background orphan service instances...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

taskkill /F /IM python.exe /FI "WINDOWTITLE eq EcoVision*" >nul 2>&1
taskkill /F /IM node.exe /FI "WINDOWTITLE eq EcoVision*" >nul 2>&1
echo  =^> Port registries scrubbed and verified clean.

echo.
echo [2/4] Deploying Local Storage Ledger Backend (Port 8000)...
:: /k keeps the window open on crash so you can actually read the traceback
start "EcoVision Data Core" cmd /k "call .venv\Scripts\activate && python app/backend.py || pause"

echo.
echo [3/4] Deploying Real-Time Computer Vision Core (Port 8001)...
start "EcoVision AI Vision" cmd /k "call .venv\Scripts\activate && python maincode/main.py || pause"

echo.
echo [DEV] Checking for DevTeam credentials (development convenience only)...
timeout /t 3 /nobreak >nul
call .venv\Scripts\activate
python app/reset_devteam_password.py
echo  =^> DevTeam credentials printed above. Save them now.
echo ──────────────────────────────────────────────────────────────

echo.
echo [4/4] Mounting Interface Template with Hot-Reloading (Port 3000)...
echo ──────────────────────────────────────────────────────────────
echo 🔥 SYSTEM LIVE: Edit your code files anywhere; changes apply instantly.
echo ──────────────────────────────────────────────────────────────

call npm run dev

echo.
echo 🛑 Shutting down background services...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
pause
