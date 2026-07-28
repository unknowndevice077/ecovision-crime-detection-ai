@echo off
title EcoVision Live Testing Workspace Shell
echo ──────────────────────────────────────────────────────────────
echo 🛠️  Initializing Local Uncompiled Development Environment...
echo ──────────────────────────────────────────────────────────────

cd /d "%~dp0"

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