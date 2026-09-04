@echo off
REM BUG FOUND 2026-09-04: this file is saved as UTF-8 (no BOM) and its own
REM echo/title lines used box-drawing bars, an em dash, and an emoji
REM (X). Under the classic US default console codepage (437 -- confirmed
REM via `chcp` on a fresh shell, not something special to any one
REM machine), cmd.exe reading those UTF-8 multi-byte sequences a byte at
REM a time corrupts its own tokenizing of the WHOLE file, not just the
REM line the character sits on: a minimal repro (a handful of plain-ASCII
REM lines, one later line with a box-drawing character, nothing else)
REM reproduced cmd chopping up and trying to execute fragments of an
REM EARLIER, purely-ASCII comment line. Confirmed `chcp 65001 >nul`
REM placed before the offending lines does NOT fix this -- cmd appears to
REM pre-tokenize using whatever codepage was active when it started
REM interpreting the file, before any of the file's own commands (chcp
REM included) have run. In the real script this showed up as real
REM control-flow -- "exit /b 1", the ISCC.exe invocation, the
REM dist_installer path -- coming out chopped into fragments cmd then
REM tried to run as commands, so a preflight failure's own `exit /b 1`
REM never actually took effect and the script limped forward through
REM later stages' echo text in a corrupted state instead of stopping.
REM Plain ASCII throughout is the fix that does not depend on undocumented
REM cmd.exe tokenizing behavior or the invoking console's codepage.
title EcoVision Sentinel - Release Build
echo -----------------------------------------------------------------
echo Building EcoVision Sentinel installer
echo -----------------------------------------------------------------

cd /d "%~dp0"

echo [0/5] Verifying python-env and model weights exist (run setup.bat first if missing)...
if not exist "python-env\python.exe" (
    echo [ERROR] python-env not found or incomplete. Run setup.bat before building.
    pause
    exit /b 1
)
REM The per-file weight checks that used to live here were STALE: they
REM required x3d_xs_violence_best.pt, which is the old per-track checkpoint,
REM while deployment runs scene mode off x3d_xs_violence_scene_corpus_neg.pt.
REM They also predated the robbery model entirely. A build could therefore
REM pass every check and still ship an install with no working detector.
REM preflight.py derives the required list from what is actually deployed, so
REM the two cannot drift apart again.
echo  =^> Running preflight (weights, schema, database mode)...
python-env\python.exe preflight.py --skip-models
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Preflight failed. The packaged app would ship non-functional.
    echo    Fix the items listed above, then re-run this script.
    pause
    exit /b 1
)
echo  =^> python-env, weights and schema verified.

REM ---------------------------------------------------------------------
REM Optional: bundle TensorRT so the installer's "Optimize for this
REM computer" step works with no internet on the target machine.
REM
REM OFF by default, and that is a size decision rather than a technical
REM one: tensorrt_libs alone is ~3.2 GB, against a python-env that already
REM has to fit under NSIS's installer-archive ceiling (see
REM tools\build_python_env.ps1 and START_HERE.md trap list). Without it the
REM app is fully functional -- it runs the .pt weights -- and the optimize
REM step simply reports itself unavailable.
REM
REM Engines still cannot be shipped prebuilt either way: they are locked to
REM the GPU architecture that built them. This flag ships the COMPILER, not
REM the compiled models.
REM
REM     build_release.bat --with-tensorrt
REM ---------------------------------------------------------------------
if /i "%~1"=="--with-tensorrt" (
    echo.
    echo  =^> --with-tensorrt: installing TensorRT into python-env ^(~3.2 GB^)...
    python-env\python.exe -m pip install --upgrade tensorrt
    if %errorlevel% neq 0 (
        echo [ERROR] Could not install TensorRT. Build again without the flag to
        echo    produce the standard installer, which does not need it.
        pause
        exit /b 1
    )
    python-env\python.exe -c "import tensorrt; print('  => TensorRT ' + tensorrt.__version__ + ' bundled')"
    if %errorlevel% neq 0 (
        echo [ERROR] TensorRT installed but will not import. Not shipping a broken optimizer.
        pause
        exit /b 1
    )
) else (
    echo  =^> Standard build ^(no TensorRT^). Pass --with-tensorrt to include the
    echo     optimizer's compiler for offline use on the target machine.
)

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
    echo [ERROR] npm install failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [3/5] Building Next.js production bundle...
call npm run build
if %errorlevel% neq 0 (
    echo [ERROR] next build failed. Fix errors above before packaging.
    pause
    exit /b 1
)

echo.
echo [4/5] Packaging Electron app...
REM win.target is "dir" now, not "msi"/"nsis"/"portable". All three of
REM electron-builder's OWN installer generators failed on a payload this
REM size, for two different reasons: NSIS's makensis.exe is a 32-BIT
REM process that mmaps the ENTIRE combined payload into ONE archive at
REM BUILD time -- a hard ~2 GB ceiling, hit twice (8.65 GB and again at
REM 5.4 GB python-env); portable shares that same macro. MSI (WiX) got
REM past the build but failed to INSTALL on real machines with error 2755,
REM a known electron-builder/WiX weak point with large payloads that is
REM not fixable from package.json config.
REM
REM "dir" just produces the plain dist\win-unpacked folder -- no installer
REM logic, so nothing here can fail the way msi/nsis did. Inno Setup
REM (installer\EcoVisionSentinel.iss, below) wraps that folder instead:
REM no size ceiling, and it's what a lot of commercial Windows software
REM actually ships with.
call node_modules\.bin\electron-builder.cmd --win --dir
if %errorlevel% neq 0 (
    echo [ERROR] electron-builder failed. See log above.
    pause
    exit /b 1
)

echo  =^> Locating Inno Setup compiler...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    where ISCC.exe >nul 2>nul && set "ISCC=ISCC.exe"
)
if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found. Install it ^(free, ~10 MB, no admin needed^):
    echo    https://jrsoftware.org/isdl.php
    echo    Then re-run this script.
    pause
    exit /b 1
)

echo  =^> Compiling installer with Inno Setup...
"%ISCC%" installer\EcoVisionSentinel.iss
if %errorlevel% neq 0 (
    echo [ERROR] Inno Setup compile failed. See log above.
    pause
    exit /b 1
)

echo.
echo [5/5] Done. Your installer is in the dist\ folder, alongside win-unpacked:
dir /b dist\*.exe
echo Give this .exe to users. Running it installs EcoVision Sentinel
echo end-to-end into ONE folder they choose -- shortcut, uninstaller,
echo database, weights, python-env, everything together. No admin/UAC
echo needed for this testing-phase build ^(installer\EcoVisionSentinel.iss
echo has PrivilegesRequired=lowest -- flip that to "admin" before a real
echo production rollout^).

echo -------------------------------------------------------------
echo Reminder: this is a CLEAN build - any package.json extraResources
echo changes (backend.py, main.py, schema_final.sql, port_utils.py paths
echo etc.) are picked up fresh here, unlike a build reused from a stale dist\.
echo -------------------------------------------------------------
pause
