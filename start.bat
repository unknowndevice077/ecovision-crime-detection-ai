@echo off
setlocal

if not exist "python-env\Scripts\python.exe" (
    echo Python environment not found. Run setup.bat first.
    exit /b 1
)

echo Starting EcoVision Sentinel...
call npx electron .

endlocal