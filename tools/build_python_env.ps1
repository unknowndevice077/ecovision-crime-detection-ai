<#
.SYNOPSIS
  Builds python-env/ -- the COMPLETE, self-contained Python runtime that
  ships inside the installer, ready to run with zero setup on the target
  machine.

.WHY THIS EXISTS INSTEAD OF `python -m venv`
  A venv is not relocatable. `python -m venv python-env` writes the exact
  path of whatever interpreter created it into python-env\pyvenv.cfg
  (`home = C:\...`), and python-env\python.exe is a small launcher stub that
  reads that file at startup to find its base DLLs. Copy that folder to a
  machine where the recorded path doesn't exist -- which is every machine
  except the one that built it -- and it fails to start. This shipped once
  (18 Aug build) and broke the app on every machine except the dev box; see
  START_HERE.md trap list.

  Fix: build on the official embeddable Python distribution instead. It has
  no "home" pointer -- it IS the interpreter, not a wrapper delegating to one
  -- so a straight folder copy runs anywhere. Verified by building here, then
  running the copy from a different path with no shared ancestry to the
  build machine (torch/fastapi/opencv/ultralytics all importing correctly).

.WHY PYTHONNOUSERSITE MATTERS HERE
  Enabling site-packages in the embeddable distribution's _pth file also
  turns on Python's default per-USER site-packages scan
  (%APPDATA%\Python\Python311\site-packages) -- a folder outside this
  project's control that can hold anything, or nothing, depending on whose
  machine is doing the building. Without PYTHONNOUSERSITE=1 forced on every
  pip command here, pip can silently install a package there instead of into
  python-env's own site-packages (this happened once, with PyYAML -- worked
  on the build machine where that folder happened to have a copy, broke
  everywhere else with "No module named 'yaml'"). Forcing it from the very
  first pip command (bootstrapping pip itself) is what makes the environment
  actually self-contained rather than accidentally-self-contained.

.USAGE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_python_env.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_python_env.ps1 -WithTensorRT
#>
param(
    [switch]$WithTensorRT
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvDir = Join-Path $RepoRoot "python-env"
$PyVersion = "3.11.9"
$EmbedUrl = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
# Published MD5 for this exact file, from python.org's own release page --
# checked here because there is no SHA256 listed there to check instead.
$ExpectedMd5 = "6d9aa08531d48fcc261ba667e2df17c4"

if (Test-Path $EnvDir) {
    Write-Host " => python-env already exists, skipping. Delete it first to rebuild from scratch."
} else {
    $tmp = Join-Path $env:TEMP "ecovision-python-embed-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $zipPath = Join-Path $tmp "python-embed.zip"

    Write-Host " => Downloading Python $PyVersion embeddable distribution..."
    Invoke-WebRequest -Uri $EmbedUrl -OutFile $zipPath -UseBasicParsing

    $actualMd5 = (Get-FileHash -Path $zipPath -Algorithm MD5).Hash
    if ($actualMd5 -ne $ExpectedMd5.ToUpper()) {
        throw "python-embed.zip checksum mismatch (expected $ExpectedMd5, got $actualMd5) -- refusing to use it."
    }
    Write-Host " => Checksum verified."

    New-Item -ItemType Directory -Path $EnvDir -Force | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $EnvDir -Force

    # Enable site-packages. Left at the embeddable default (commented out),
    # pip-installed packages would be invisible to `import` entirely.
    #
    # The ..\backend and ..\maincode lines matter just as much and are easy
    # to miss: a _pth file puts the interpreter in "isolated launcher" mode,
    # which does NOT add the directory of the script being run to sys.path
    # (unlike a normal python.exe) -- and does NOT honor PYTHONPATH either,
    # confirmed by testing both directly against this exact build. Without
    # these two lines, `python.exe backend\backend.py` fails with
    # "ModuleNotFoundError: No module named 'db'" (db.py sits right next to
    # it) on every packaged install, because backend.py's own directory was
    # never on sys.path in the first place. package.json's extraResources
    # always lays out python-env, backend, and maincode as fixed siblings
    # under resources\ regardless of where the user installs, so a relative
    # path here is safe and needs no per-machine computation.
    $pthFile = Join-Path $EnvDir "python311._pth"
    @"
python311.zip
.
Lib\site-packages
..\backend
..\maincode

import site
"@ | Set-Content -Path $pthFile -Encoding ASCII

    $pythonExe = Join-Path $EnvDir "python.exe"

    Write-Host " => Bootstrapping pip..."
    $getPipPath = Join-Path $tmp "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath -UseBasicParsing
    $env:PYTHONNOUSERSITE = "1"
    & $pythonExe $getPipPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }

    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$pythonExe = Join-Path $EnvDir "python.exe"
$env:PYTHONNOUSERSITE = "1"

# requirements.txt is the single source of truth (kept that way deliberately
# -- a prior split into requirements-backend.txt/requirements-detector.txt
# meant hand-copying every version bump twice and once caused real drift).
# But it also carries packages nothing SHIPPED actually imports at runtime
# -- verified by grepping backend.py, db.py, main.py, x3d_violence_detector.py,
# robbery_vandalism.py, preflight.py and optimize_weights.py:
#   pandas, matplotlib, alembic, SQLAlchemy  -- dev/analysis tooling only
#   tensorrt_cu12*                           -- optional, see --with-tensorrt below
# Shipping them anyway is what took this env from ~5 GB to ~8.6 GB uncompressed
# and pushed the packaged installer over NSIS's archive-embedding ceiling.
# Filtered OUT of the shipped env, not out of requirements.txt itself, so
# `.venv` (setup.bat step 3, local dev) keeps the full convenience set.
$shipExclude = @("pandas", "matplotlib", "alembic", "SQLAlchemy", "tensorrt_cu12", "tensorrt_cu12_bindings", "tensorrt_cu12_libs")
$allLines = Get-Content (Join-Path $RepoRoot "requirements.txt")
$shipLines = $allLines | Where-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#") -or $line.StartsWith("--")) { return $true }
    $pkgName = ($line -split "==")[0].Trim()
    return -not ($shipExclude -contains $pkgName)
}
$shipReqPath = Join-Path $RepoRoot "_ship_requirements.tmp.txt"
$shipLines | Set-Content -Path $shipReqPath -Encoding ASCII

Write-Host " => Installing requirements (ship subset -- excludes dev-only tooling; this is still the big one, torch + CUDA)..."
& $pythonExe -m pip install --no-warn-script-location `
    -r $shipReqPath `
    --extra-index-url https://download.pytorch.org/whl/cu121
$shipInstallOk = ($LASTEXITCODE -eq 0)
Remove-Item -Force $shipReqPath -ErrorAction SilentlyContinue
if (-not $shipInstallOk) { throw "requirements install failed" }

if ($WithTensorRT) {
    Write-Host " => --with-tensorrt: installing TensorRT (~3.2 GB)..."
    $trtLines = $allLines | Where-Object { ($_ -split "==")[0].Trim() -in @("tensorrt_cu12", "tensorrt_cu12_bindings", "tensorrt_cu12_libs") }
    & $pythonExe -m pip install --no-warn-script-location $trtLines
    if ($LASTEXITCODE -ne 0) { throw "TensorRT install failed" }
    & $pythonExe -c "import tensorrt; print('  => TensorRT ' + tensorrt.__version__ + ' bundled')"
    if ($LASTEXITCODE -ne 0) { throw "TensorRT installed but will not import -- not shipping a broken optimizer" }
}

# Prove it's actually relocatable rather than assuming: run the SAME check
# from a copy at a different path with no relation to $EnvDir. If this ever
# fails, something (a stray absolute path, a leaked per-user site-packages
# hit) has made the environment machine-specific again -- exactly the class
# of bug this whole script exists to prevent.
Write-Host " => Verifying relocatability (the actual point of this script)..."
$checkDir = Join-Path $env:TEMP "ecovision-relocation-check-$([guid]::NewGuid())"
Copy-Item -Recurse -Path $EnvDir -Destination $checkDir
$env:PYTHONNOUSERSITE = "1"
& (Join-Path $checkDir "python.exe") -c "import torch, fastapi, cv2, ultralytics, uvicorn; print('relocation check OK:', torch.__version__, 'cuda:', torch.cuda.is_available())"
$relocOk = ($LASTEXITCODE -eq 0)
Remove-Item -Recurse -Force $checkDir -ErrorAction SilentlyContinue
if (-not $relocOk) {
    throw "python-env failed to run from a different path -- it is NOT relocatable. Do not ship it."
}

Write-Host " => python-env built and verified relocatable."
