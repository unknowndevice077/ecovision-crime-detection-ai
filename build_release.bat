@echo off
title EcoVision Sentinel — Release Build
echo ─────────────────────────────────────────────────────────────
echo Building EcoVision Sentinel portable .exe
echo ─────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [0/5] Verifying python-env and model weights exist (run setup.bat first if missing)...
if not exist "python-env\Scripts\python.exe" (
    echo ❌ python-env not found or incomplete. Run setup.bat before building.
    pause
    exit /b 1
)
if not exist "weights\x3d_xs_violence_best.pt" (
    echo ❌ weights\x3d_xs_violence_best.pt not found -- the packaged app would ship
    echo    non-functional. See README.md "Add Model Weights".
    pause
    exit /b 1
)
if not exist "weights\yolo11s-pose.pt" if not exist "weights\yolo11s-pose.engine" (
    echo ❌ weights\yolo11s-pose.pt (or .engine^) not found.
    pause
    exit /b 1
)
if not exist "weights\weapon_signs.pt" if not exist "weights\weapon_signs.engine" (
    echo ❌ weights\weapon_signs.pt (or .engine^) not found.
    pause
    exit /b 1
)
echo  =^> python-env and weights found.

echo.
echo [1/5] Cleaning previous build output...
if exist dist (
    rmdir /s /q dist
    echo  =^> Removed stale dist\ folder.
) else (
    echo  =^> No previous dist\ folder found, skipping.
)
if exist .next (
    rmdir /s /q .next
    echo  =^> Removed stale .next\ build cache.
)

echo.
echo [2/5] Syncing node_modules with package.json...
call npm install
if %errorlevel% neq 0 (
    echo ❌ npm install failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [3/5] Building Next.js production bundle...
call npm run build
if %errorlevel% neq 0 (
    echo ❌ next build failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [4/5] Packaging Electron app into a portable .exe...
call npx electron-builder --win portable
if %errorlevel% neq 0 (
    echo ❌ electron-builder failed. See log above.
    pause
    exit /b 1
)

echo.
echo [5/5] Done. Your .exe is in the dist\ folder:
dir /b dist\*.exe

echo ─────────────────────────────────────────────────────────────
echo Reminder: this is a CLEAN build — any package.json extraResources
echo changes (backend.py, main.py, schema_final.sql, port_utils.py paths
echo etc.) are picked up fresh here, unlike a build reused from a stale dist\.
echo ─────────────────────────────────────────────────────────────
pause