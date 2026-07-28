@echo off
setlocal

echo ===============================================
echo  EcoVision Sentinel -- local setup
echo ===============================================

where node >nul 2>nul
if errorlevel 1 (
    echo ERROR: Node.js not found. Install it from https://nodejs.org first.
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11 from https://python.org first.
    exit /b 1
)

echo.
echo [1/4] Installing frontend dependencies...
call npm install
if errorlevel 1 goto :error

echo.
echo [2/4] Building frontend...
call npm run build
if errorlevel 1 goto :error

echo.
echo [3/4] Creating Python environment (CPU build by default)...
python -m venv python-env
call python-env\Scripts\pip install --upgrade pip
call python-env\Scripts\pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [4/4] Setup complete. python-env is ready for build_release.bat.
echo Run start.bat (or npx electron .) to launch the app locally.
goto :end

:error
echo.
echo Setup failed -- see the error above.
exit /b 1

:end
endlocal