@echo off
title EcoVision Sentinel — Release Build
echo ─────────────────────────────────────────────────────────────
echo Building EcoVision Sentinel portable .exe
echo ─────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [0/4] Cleaning previous build output...
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
echo [1/4] Syncing node_modules with package.json...
call npm install
if %errorlevel% neq 0 (
    echo ❌ npm install failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [2/4] Building Next.js production bundle...
call npm run build
if %errorlevel% neq 0 (
    echo ❌ next build failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [3/4] Packaging Electron app into a portable .exe...
call npx electron-builder --win portable
if %errorlevel% neq 0 (
    echo ❌ electron-builder failed. See log above.
    pause
    exit /b 1
)

echo.
echo [4/4] Done. Your .exe is in the dist\ folder:
dir /b dist\*.exe

echo ─────────────────────────────────────────────────────────────
echo Reminder: this is a CLEAN build — any package.json extraResources
echo changes (backend.py, main.py, schema_final.sql paths etc.) are
echo picked up fresh here, unlike a build reused from a stale dist\.
echo ─────────────────────────────────────────────────────────────
pause