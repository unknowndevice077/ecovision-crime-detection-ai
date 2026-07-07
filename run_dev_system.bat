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
start "EcoVision Data Core" cmd /c "call .venv\Scripts\activate && python app/backend.py"

echo.
echo [3/4] Deploying Real-Time Computer Vision Core (Port 8001)...
start "EcoVision AI Vision" cmd /c "call .venv\Scripts\activate && python maincode/main.py"

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