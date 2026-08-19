@echo off
title EcoVision Live Testing Workspace Shell
echo ──────────────────────────────────────────────────────────────
echo 🛠️  Initializing Local Uncompiled Development Environment...
echo ──────────────────────────────────────────────────────────────

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo ❌ .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

if not exist "weights\yolo11s-pose.pt" if not exist "weights\yolo11s-pose.engine" (
    echo ⚠️  weights\yolo11s-pose.pt (or .engine^) not found -- the AI core will crash on startup.
    echo    See README.md "Add Model Weights" for what needs to go in weights\.
    pause
)
if not exist "weights\weapon_signs.pt" if not exist "weights\weapon_signs.engine" (
    echo ⚠️  weights\weapon_signs.pt (or .engine^) not found -- the AI core will crash on startup.
    pause
)
if not exist "weights\x3d_xs_violence_best.pt" (
    echo ⚠️  weights\x3d_xs_violence_best.pt not found -- the AI core will crash on startup.
    pause
)

:: maincode\main.py does "from port_utils import ..." but port_utils.py lives
:: in app\. The PACKAGED build works because package.json's extraResources
:: copies app\port_utils.py into BOTH backend\ and maincode\ -- dev mode has
:: no such copy, so running "python maincode/main.py" put sys.path[0] at
:: maincode\ and the import died with ModuleNotFoundError. Putting app\ on
:: PYTHONPATH reproduces the packaged layout's import resolution without
:: duplicating the file in the repo (a second copy would silently drift).
set "PYTHONPATH=%~dp0app;%PYTHONPATH%"
echo  =^> PYTHONPATH set for shared modules (port_utils).

:: BUG FOUND 2026-08-19: backend.py and main.py each fall back independently
:: to %%USERPROFILE%%\EcoVisionSentinelData when ECOVISION_WRITABLE_DIR isn't
:: set -- normally identical, so they still agree. But if ANYTHING in this
:: environment has ever set that variable persistently (setx, an earlier
:: installer-test session, a stray export in a profile script), backend and
:: main.py -- launched here as two separate cmd windows -- can each inherit
:: a DIFFERENT value if it changed between the two `start` calls below, or
:: just silently agree on some other machine's leftover value instead of
:: this repo. Real, observed consequence: main.py wrote every screenshot and
:: recording to one folder while backend.py's database (and the frontend
:: reading it) expected them in another -- every AI-triggered incident
:: showed a broken image. Setting it explicitly here, once, and passing it
:: to both windows makes this dev script immune to whatever the ambient
:: environment happens to have -- data/ is already gitignored for exactly
:: this.
set "ECOVISION_WRITABLE_DIR=%~dp0"
echo  =^> ECOVISION_WRITABLE_DIR pinned to %~dp0 for both backend and AI core.

:: BUG FOUND 2026-08-19: Windows' console codepage (cp1252 here, not UTF-8)
:: can't encode the emoji this codebase's console logging uses everywhere
:: (backend.py alone has 7+ prints like "print(f'⚠️  [SIREN] ...')").
:: A print() that fails to encode RAISES UnicodeEncodeError -- so a route
:: that correctly caught a real error (ESP32 unreachable) and tried to just
:: log it crashed instead, on the LOGGING call itself, turning a handled,
:: harmless failure into an unhandled 500. Real observed case: siren_activate/
:: siren_deactivate 500ing on every call, no ESP32 configured or not, because
:: logging that fact was the actual crash. electron/main.js's spawnPython()
:: already sets these two for the packaged build; this script never did.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo  =^> PYTHONIOENCODING/PYTHONUTF8 set so emoji console logging can't crash a request.

echo [1/4] Scanning for background orphan service instances...
:: BUG FOUND 2026-08-19: "taskkill /IM python.exe /FI WINDOWTITLE eq EcoVision*"
:: was never actually killing anything. python.exe run via `cmd /k "python ..."`
:: has NO window title of its own -- the title belongs to the cmd.exe/conhost
:: window that's hosting it, not the child process -- so a filter that
:: requires BOTH image name python.exe AND that window title never matches.
:: This line has been a no-op the entire time; any prior EcoVision Data
:: Core / AI Vision window left running (closed uncleanly, or just never
:: closed) survived every "clean restart" silently. Filtering on the window
:: title alone with /T (kill the whole process tree under that window,
:: which includes the python.exe running inside it) actually works.
taskkill /F /T /FI "WINDOWTITLE eq EcoVision Data Core*" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq EcoVision AI Vision*" >nul 2>&1

:: BUG FOUND 2026-08-19 -- the one that cost this whole day. A uvicorn
:: --reload WORKER (a multiprocessing child) can outlive its parent
:: reloader. When that happens the child keeps the listening socket on
:: :8000, but netstat still attributes that socket to the PARENT's pid --
:: which no longer exists. So the loop below did `taskkill /PID <dead pid>`,
:: silently failed, and reported "scrubbed and verified clean" while a
:: 9-hour-old backend from an installer-test build kept answering EVERY
:: request on :8000. Symptoms it produced: no request logs in the visible
:: backend window, incidents/screenshots/recordings written to one folder
:: tree while a different backend served another, and code fixes that
:: appeared to do nothing because the process running them was never the
:: one being talked to.
:: Killing by COMMAND LINE instead of by port sidesteps the dead-pid
:: attribution entirely. Scoped to python processes running backend.py or
:: main.py specifically, so unrelated Python (training runs, notebooks)
:: is untouched.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*backend.py*' -or $_.CommandLine -like '*main.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

:: Verify rather than assume -- the old script printed "verified clean"
:: without ever checking, which is how the orphan stayed invisible.
netstat -aon | findstr :8000 | findstr LISTENING >nul 2>&1 && (
    echo  =^> WARNING: something is STILL listening on :8000 after cleanup.
    echo     Run this to see what, then end it in Task Manager:
    echo       powershell "Get-CimInstance Win32_Process ^| Where-Object { $_.Name -like 'python*' } ^| Select ProcessId,CommandLine"
    pause
) || echo  =^> Ports :8000/:8001/:3000 confirmed clear.

echo.
echo [2/4] Deploying Local Storage Ledger Backend (Port 8000)...
:: BUG FOUND 2026-08-19: "call .venv\Scripts\activate && python ..." trusts
:: PATH resolution to find the venv's python.exe -- found TWO backend.py AND
:: two main.py processes running simultaneously, one pair resolving "python"
:: to the venv, the other to a bare "C:\Program Files\Python311\python.exe"
:: on PATH ahead of (or instead of) the activated venv. Whichever one a
:: given browser request happened to hit was down to chance -- one could be
:: running old code, the other new, with no way to tell from the outside.
:: Calling the venv's python.exe by its full path removes the ambiguity
:: entirely: there is no "python" to resolve incorrectly.
:: /k keeps the window open on crash so you can actually read the traceback
start "EcoVision Data Core" cmd /k "%~dp0.venv\Scripts\python.exe app/backend.py || pause"

echo.
echo [3/4] Deploying Real-Time Computer Vision Core (Port 8001)...
start "EcoVision AI Vision" cmd /k "%~dp0.venv\Scripts\python.exe maincode/main.py || pause"

echo.
echo [DEV] Checking for DevTeam credentials (development convenience only)...
timeout /t 3 /nobreak >nul
call .venv\Scripts\activate
python app/reset_devteam_password.py
echo  =^> DevTeam credentials printed above. Save them now.
echo ──────────────────────────────────────────────────────────────

echo.
echo [4/4] Mounting Interface Template with Hot-Reloading (Port 3000)...
echo ──────────────────────────────────────────────────────────────
echo 🔥 SYSTEM LIVE: Edit your code files anywhere; changes apply instantly.
echo ──────────────────────────────────────────────────────────────

call npm run dev

echo.
echo 🛑 Shutting down background services...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
pause
