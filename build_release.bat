@echo off
title EcoVision Sentinel — Release Build
echo ─────────────────────────────────────────────────────────────
echo Building EcoVision Sentinel portable .exe
echo ─────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [1/3] Building Next.js production bundle...
call npm run build
if %errorlevel% neq 0 (
    echo ❌ next build failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [2/3] Packaging Electron app into a portable .exe...
call npx electron-builder --win portable
if %errorlevel% neq 0 (
    echo ❌ electron-builder failed. See log above.
    pause
    exit /b 1
)

echo.
echo [3/3] Done. Your .exe is in the dist\ folder:
dir /b dist\*.exe

echo ─────────────────────────────────────────────────────────────
echo Next: run publish_release.bat to push this to GitHub Releases.
echo ─────────────────────────────────────────────────────────────
pause