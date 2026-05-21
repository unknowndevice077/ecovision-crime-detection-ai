@echo off
TITLE EcoVision Sentinel: Master Control
COLOR 0A

echo [SYS] INITIALIZING ECOVISION SENTINEL v13.8...
echo [SYS] TARGET DIRECTORY: %~dp0
cd /d "%~dp0"

:: 1. Launch the Intelligence Bridge (FastAPI Server)
echo [SYS] SPWNING INTELLIGENCE BRIDGE...
start "SENTINEL: BRIDGE" cmd /k "python server.py"

:: Wait for server to bind to port 8000
timeout /t 3 /nobreak > nul

:: 2. Launch the Neural Engine (AI Processing)
echo [SYS] SPWNING NEURAL ENGINE...
start "SENTINEL: AI ENGINE" cmd /k "python main.py"

:: 3. Launch the Tactical Monitor (Next.js Dashboard)
echo [SYS] SPWNING COMMAND DASHBOARD...
start "SENTINEL: DASHBOARD" cmd /k "npm run dev"

echo.
echo ======================================================
echo    SENTINEL CORE DEPLOYED - CHECK INDIVIDUAL WINDOWS
echo ======================================================
pause