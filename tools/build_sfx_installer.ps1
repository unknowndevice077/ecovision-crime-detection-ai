# Build the distributable installer for EcoVision Sentinel.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_sfx_installer.ps1
#
# Run `npm run app:build` first so dist\win-unpacked exists and is current.
#
# WHY NOT electron-builder's NSIS TARGET. It was tried twice and fails
# identically:
#     File: failed creating mmap of "...-x64.nsis.7z"
# makensis.exe is 32-bit and memory-maps the entire payload archive. This app's
# payload compresses to 2.16 GB -- just past what a 32-bit address space can
# map -- because torch plus the CUDA runtime is 4.3 GB uncompressed and GPU
# inference genuinely needs it. The second attempt ran against an
# already-packaged win-unpacked, so the fault is the NSIS target itself, not
# packaging. Do not re-add nsis to package.json without first getting the
# payload under roughly 2 GB.
#
# 7-Zip's SFX module has no such ceiling and produces a single double-clickable
# .exe that prompts, extracts, and launches the app.
$ErrorActionPreference = "Stop"

$repo     = Split-Path -Parent $PSScriptRoot
$dist     = Join-Path $repo "dist"
$unpacked = Join-Path $dist "win-unpacked"
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
$sfxMod   = "C:\Program Files\7-Zip\7z.sfx"

if (-not (Test-Path $unpacked)) { throw "dist\win-unpacked not found -- run 'npm run app:build' first" }
if (-not (Test-Path $sevenZip)) { throw "7-Zip not found at $sevenZip" }
if (-not (Test-Path $sfxMod))   { throw "7-Zip SFX module not found at $sfxMod" }

# ---- gate the build -------------------------------------------------------
# preflight checks that every weight config.json names is present AND that
# package.json's extraResources filter actually bundles it. That second check
# exists because the filter is a hardcoded whitelist maintained separately from
# config.json: on 2026-08-22 it still named weapon_signs.pt and
# vandalism_marks.pt after config had been repointed at weapons_v2.pt and
# vandalism_marks_v2.pt, which would have shipped an installer whose config
# named files that were not in it.
Write-Host "[1/4] Preflight..."
Push-Location $repo
& "$repo\.venv\Scripts\python.exe" preflight.py --skip-models
$pf = $LASTEXITCODE
Pop-Location
if ($pf -ne 0) { throw "preflight failed -- the installer would ship a non-functional app" }

# ---- confirm the packaged tree matches the source -------------------------
Write-Host "[2/4] Verifying packaged config lists every model..."
$pkgCfg = Join-Path $unpacked "resources\config.json"
$cfg = Get-Content $pkgCfg -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($m in @("violence","robbery","vandalism","vandalism_marks","weapon")) {
    if (-not $cfg.detection.$m) { throw "packaged config.json is missing detection.$m" }
    $mp = $cfg.detection.$m.model_path
    if ($mp) {
        $leaf = Split-Path $mp -Leaf
        if (-not (Test-Path (Join-Path $unpacked "resources\weights\$leaf"))) {
            throw "packaged config names $leaf but it is not in resources\weights"
        }
    }
    Write-Host ("       OK  detection.{0}" -f $m)
}

# ---- the frontend must be able to serve itself ----------------------------
# BUG FOUND 2026-08-22 by installing the built app and getting a blank window
# with 404s for every /_next/static/* chunk.
#
# `next build` produces .next/standalone/server.js, but Next does NOT copy
# .next/static or public/ into that folder -- tools/copy_standalone_assets.js
# exists precisely to do it, and package.json's "build" script ran it while
# "app:build" did not. So the packaged app shipped a server with no chunks to
# serve: it started, listened on 3000, and answered every asset request with a
# 404. Nothing in the build logs looked wrong.
#
# Checked here because it is invisible until someone installs and launches.
Write-Host "[2b] Verifying the packaged frontend has its static assets..."
$staticDir = Join-Path $unpacked "resources\app.asar.unpacked\.next\standalone\.next\static"
if (-not (Test-Path $staticDir)) {
    throw "packaged app is missing .next/standalone/.next/static -- the app will " +
          "launch to a blank screen. Run 'node tools/copy_standalone_assets.js' " +
          "after 'next build', or use 'npm run app:build' which now chains it."
}
$chunks = @(Get-ChildItem $staticDir -Recurse -File -Filter *.js -EA SilentlyContinue).Count
if ($chunks -lt 1) { throw "static/ exists but contains no JS chunks" }
Write-Host ("       OK  {0} JS chunks present" -f $chunks)

# ---- uninstall support ----------------------------------------------------
# BUG FOUND 2026-08-22: the NSIS target was dropped for the mmap failure
# (see the header comment) and nothing replaced what NSIS gave for free --
# an uninstaller, a Start Menu entry, and an Add/Remove Programs listing.
# The SFX by itself just unzips files; nothing ever registers or removes them.
#
# uninstall.bat and postinstall.bat are staged into the payload here so they
# ship inside the installer. postinstall.bat is what RunProgram actually
# launches: it creates the Start Menu shortcut and the Add/Remove Programs
# entry (pointing at uninstall.bat), then starts the app. Both templates live
# in tools\ and are versioned like any other source file.
Write-Host "[2c] Staging uninstaller and shortcut registration..."
Copy-Item (Join-Path $repo "tools\uninstall_template.bat") (Join-Path $unpacked "uninstall.bat") -Force
$pkgVersion = (Get-Content (Join-Path $repo "package.json") -Raw | ConvertFrom-Json).version
(Get-Content (Join-Path $repo "tools\postinstall_template.bat") -Raw) `
    -replace "__VERSION__", $pkgVersion |
    Set-Content (Join-Path $unpacked "postinstall.bat") -Encoding ASCII -NoNewline
Write-Host "       OK  uninstall.bat + postinstall.bat staged"

# ---- compress -------------------------------------------------------------
$payload = Join-Path $dist "EcoVisionSentinel-payload.7z"
$config  = Join-Path $dist "sfx_config.txt"
$version = (Get-Content (Join-Path $repo "package.json") -Raw | ConvertFrom-Json).version
$output  = Join-Path $dist "EcoVisionSentinel-Installer-$version.exe"

# ARCHIVE A ROOT FOLDER, NOT LOOSE CONTENTS.
# This used to be `7z a ... "$unpacked\*"`, where the trailing \* means "the
# CONTENTS of win-unpacked". The SFX then extracted 58,000 loose files straight
# into whatever folder the user chose -- pick Desktop, get a Desktop full of
# Chromium locale files. Archiving the directory itself gives every extraction a
# single tidy root, which is what anyone expects an installer to do.
#
# electron-builder always names its output "win-unpacked", so the folder is
# renamed for the duration of the archive and restored afterwards. A rename on
# the same volume is instantaneous; copying 6 GB would not be.
$staged = Join-Path $dist "EcoVisionSentinel"
if (Test-Path $staged) { Remove-Item $staged -Recurse -Force }
Rename-Item -Path $unpacked -NewName "EcoVisionSentinel"
try {
    # mx=3 rather than mx=9: on a 6 GB payload the stronger setting costs about
    # forty minutes to save roughly 5%, and this gets rebuilt often.
    Write-Host "[3/4] Compressing payload (several minutes)..."
    Remove-Item $payload -Force -EA SilentlyContinue
    & $sevenZip a -t7z -mx=3 -mmt=on $payload $staged | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7z compression failed" }
}
finally {
    # Always restore the name, even if compression threw -- otherwise the next
    # `npm run app:build` writes a second win-unpacked beside a stranded one.
    if (Test-Path $staged) { Rename-Item -Path $staged -NewName "win-unpacked" }
}

# RunProgram is resolved relative to the extraction target, and the archive now
# carries an EcoVisionSentinel root folder, so everything sits one level down.
# It points at postinstall.bat, not the .exe directly -- that script registers
# the Start Menu shortcut and the Add/Remove Programs entry (see [2c] above)
# before launching the app, so a double-click on the .exe behaves like an
# actual install rather than a silent unzip.
@"
;!@Install@!UTF-8!
Title="EcoVision Sentinel $version"
BeginPrompt="Install EcoVision Sentinel?`n`nAbout 6 GB will be extracted into an EcoVisionSentinel folder at the location you choose, including the bundled Python runtime and the five AI models. A Start Menu shortcut and an uninstaller will be added."
InstallPath="C:\\EcoVisionSentinel"
RunProgram="EcoVisionSentinel\postinstall.bat"
GUIMode="1"
;!@InstallEnd@!
"@ | Set-Content -Path $config -Encoding UTF8 -NoNewline

Write-Host "[4/4] Assembling self-extracting installer..."
Remove-Item $output -Force -EA SilentlyContinue
cmd /c "copy /b `"$sfxMod`" + `"$config`" + `"$payload`" `"$output`"" | Out-Null
if (-not (Test-Path $output)) { throw "failed to assemble $output" }

Write-Host "`nVerifying archive integrity..."
& $sevenZip t $output -y | Select-String "Everything is Ok" | ForEach-Object { Write-Host "       $_" }

$mb = (Get-Item $output).Length / 1MB
Write-Host ("`nBUILT  {0}  ({1:N0} MB)" -f (Split-Path $output -Leaf), $mb)
Write-Host "NOTE: unsigned, so Windows SmartScreen will warn on first run"
Write-Host "      (More info -> Run anyway). Test on the demo machine beforehand."
