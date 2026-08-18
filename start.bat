@echo off
setlocal

if not exist "python-env-backend\Scripts\python.exe" (
    echo Backend Python environment not found. Run setup.bat first.
    exit /b 1
)
if not exist "python-env-detector\Scripts\python.exe" (
    echo Detector Python environment not found. Run setup.bat first.
    exit /b 1
)

echo Starting EcoVision Sentinel...
call npx electron .

endlocal