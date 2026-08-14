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
REM The per-file weight checks that used to live here were STALE: they
REM required x3d_xs_violence_best.pt, which is the old per-track checkpoint,
REM while deployment runs scene mode off x3d_xs_violence_scene_corpus_neg.pt.
REM They also predated the robbery model entirely. A build could therefore
REM pass every check and still ship an install with no working detector.
REM preflight.py derives the required list from what is actually deployed, so
REM the two cannot drift apart again.
echo  =^> Running preflight (weights, schema, database mode)...
python-env\Scripts\python.exe preflight.py --skip-models
if %errorlevel% neq 0 (
    echo.
    echo ❌ Preflight failed. The packaged app would ship non-functional.
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
REM one: tensorrt_libs alone is 3,245 MB against a python-env that is
REM already 5.13 GB, so bundling it nearly doubles the download to make an
REM OPTIONAL speedup available offline. Without it the app is fully
REM functional -- it runs the .pt weights -- and the optimize step simply
REM reports itself unavailable.
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
    python-env\Scripts\python.exe -m pip install --upgrade tensorrt
    if %errorlevel% neq 0 (
        echo ❌ Could not install TensorRT. Build again without the flag to
        echo    produce the standard installer, which does not need it.
        pause
        exit /b 1
    )
    python-env\Scripts\python.exe -c "import tensorrt; print('  => TensorRT ' + tensorrt.__version__ + ' bundled')"
    if %errorlevel% neq 0 (
        echo ❌ TensorRT installed but will not import. Not shipping a broken optimizer.
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