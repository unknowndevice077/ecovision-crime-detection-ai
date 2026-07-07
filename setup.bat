@echo off
title EcoVision Turn-Key Setup Wizard
echo ─────────────────────────────────────────────────────────────────
echo 🚀 EcoVision Initialization Matrix Engagement
echo ─────────────────────────────────────────────────────────────────

:: Check Python installation boundaries
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Error: Python is not installed or not mapped to your system PATH.
    echo Please install Python 3.11+ before running this deployment shell.
    goto error
)

:: Check Node.js installation boundaries
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Error: Node.js (npm) is not installed or not mapped to your system PATH.
    echo Please install Node.js before initializing the frontend web ecosystem.
    goto error
)

echo.
echo [1/4] Rebuilding ignored local directory infrastructures...
if not exist "weights" mkdir weights
if not exist "recordings" mkdir recordings
if not exist "logs" mkdir logs
if not exist "maincode\static\screenshots\x3d_crops" mkdir maincode\static\screenshots\x3d_crops
echo  =^> Directory trees verified cleanly.

echo.
echo [2/4] Verifying localized Python isolation sandbox...
if not exist ".venv" (
    echo  =^> No environment found. Provisioning clean virtual runtime partition...
    python -m venv .venv
) else (
    echo  =^> Existing .venv partition found. Skipping creation.
)

echo.
echo [3/4] Hydrating Python backend deep learning dependencies...
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo [4/4] Hydrating Next.js frontend application package trees...
call npm install

echo.
echo ─────────────────────────────────────────────────────────────────
echo 🎉 ECOVISION ENVIRONMENT SYSTEM DEPLOYED SUCCESSFUL
echo ─────────────────────────────────────────────────────────────────
echo ⚠️  CRITICAL REQUIREMENT DETECTED:
echo    Because your .gitignore cleanly excludes heavy model binary maps,
echo    your tracking weights (*.pt) were not pulled via GitHub.
echo.
echo    You MUST manually drop your model files:
echo    - x3d_xs_violence_best.pt
echo    - weapon_signs.pt
echo    - yolo11s-pose.pt
echo    directly into the "weights/" folder before booting the system!
echo ─────────────────────────────────────────────────────────────────
echo.
echo Environment is locked and ready. You can now use your start.bat script.
pause
exit /b 0

:error
echo.
echo ❌ Setup sequence encountered a critical system fault.
pause
exit /b 1