@echo off
SETLOCAL EnableExtensions
title EcoVision Sentinel Launcher
cls

echo =======================================================================
echo         🚀 ECOVISION SENTINEL: LIVE MULTI-PROCESS RUNTIME
echo =======================================================================
echo  Initializing background engines and user dashboard applications...
echo -----------------------------------------------------------------------

:: 1. Launch the Text Ledger Database Backend Server (Port 8000)
echo 📁 [1/3] Launching local database routing engine [backend.py]...
start "EcoVision Data Ledger (Port 8000)" cmd /k "python backend.py"

:: Give the SQLite file connection pool 2 seconds to bind securely
timeout /t 2 /nobreak >nul

:: 2. Launch the YOLO Vision Tracking Pipeline & Capture (Port 8001)
echo 👁️ [2/3] Initializing screen-capture tracking pipeline [main.py]...
start "EcoVision AI Vision Core (Port 8001)" cmd /k "python main.py"

:: Give the CUDA core layers 2 seconds to stabilize memory allocations
timeout /t 2 /nobreak >nul

:: 3. Jump to the Next.js directory and boot development compilation (Port 3000)
echo 💻 [3/3] Opening user dashboard interface [ecovisioncode]...
start "EcoVision Next.js UI (Port 3000)" cmd /k "cd ecovisioncode && npm run dev"

echo -----------------------------------------------------------------------
echo  ✅ ALL MODULES ROUTED AND RUNNING CONCURRENTLY!
echo  - Shared database pool server : http://localhost:8000
echo  - Live video stream channel   : http://localhost:8001/video_feed
echo  - Next.js development client  : http://localhost:3000
echo =======================================================================
echo  Keep this orchestrator screen open. Shutting individual windows kills modules.
pause