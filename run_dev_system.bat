@echo off
title EcoVision Live Testing Workspace Shell
echo ──────────────────────────────────────────────────────────────
echo 🛠️  Initializing Local Uncompiled Development Environment...
echo ──────────────────────────────────────────────────────────────

:: Force absolute path resolution anchored to the batch file location
cd /d "%~dp0"

echo [1/4] Scanning for background orphan service instances...
:: Forcefully purge any background processing loops tied to system ports 
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

:: Fallback generic task sanitation kill loop
taskkill /F /IM python.exe /FI "WINDOWTITLE eq EcoVision*" >nul 2>&1
taskkill /F /IM node.exe /FI "WINDOWTITLE eq EcoVision*" >nul 2>&1
echo  =^> Port registries scrubbed and verified clean.

echo.
echo [2/4] Deploying Local Storage Ledger Backend (Port 8000)...
:: NOTE: if this is the very first run against a fresh/empty DB, backend.py
:: bootstraps a DEVTEAM account and prints its username/password ONCE to
:: THIS window ("EcoVision Data Core") -- it is never shown again after
:: this run. Watch that window when the DB is new.
start "EcoVision Data Core" cmd /c "call .venv\Scripts\activate && python app/backend.py"

echo.
echo [3/4] Deploying Real-Time Computer Vision Core (Port 8001)...
start "EcoVision AI Vision" cmd /c "call .venv\Scripts\activate && python maincode/main.py"

echo.
echo [DEV] Checking for DevTeam credentials (development convenience only)...
:: reset_devteam_password.py forcibly resets whatever DEVTEAM account exists
:: and prints the new username/password to THIS console. This only exists
:: for local dev convenience -- it must never ship in a production/release
:: build, since anyone running it wipes the current DevTeam password.
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

:: Next.js execution loop runs inside the master terminal window context
call npm run dev

:: ─── EXIT CONTEXT PROTECTION LAYER ───
:: If the user kills the Next.js process inside this console using Ctrl+C,
:: this section executes to kill the background processes automatically.
echo.
echo 🛑 Shutting down background services...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
pause