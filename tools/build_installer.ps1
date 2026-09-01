# Build the distributable installer for EcoVision Sentinel with Inno Setup.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1
#
# Run `npm run app:build` first so dist\win-unpacked exists and is current.
#
# Replaces the old 7-Zip SFX pipeline (tools\build_sfx_installer.ps1, retired).
# See tools\installer.iss for why: the SFX wasn't a real installer, and the
# hand-rolled uninstall/registration batch scripts it needed each shipped
# with their own bug. Inno Setup does all of that natively and has no
# payload-size ceiling like electron-builder's NSIS target does.
$ErrorActionPreference = "Stop"

$repo     = Split-Path -Parent $PSScriptRoot
$dist     = Join-Path $repo "dist"
$unpacked = Join-Path $dist "win-unpacked"
$iss      = Join-Path $repo "tools\installer.iss"

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) { throw "Inno Setup (ISCC.exe) not found -- install with: winget install JRSoftware.InnoSetup.7" }
if (-not (Test-Path $unpacked)) { throw "dist\win-unpacked not found -- run 'npm run app:build' first" }

# ---- gate the build -------------------------------------------------------
# preflight checks that every weight config.json names is present AND that
# package.json's extraResources filter actually bundles it. That second check
# exists because the filter is a hardcoded whitelist maintained separately from
# config.json: on 2026-08-22 it still named weapon_signs.pt and
# vandalism_marks.pt after config had been repointed at weapons_v2.pt and
# vandalism_marks_v2.pt, which would have shipped an installer whose config
# named files that were not in it.
Write-Host "[1/3] Preflight..."
Push-Location $repo
& "$repo\.venv\Scripts\python.exe" preflight.py --skip-models
$pf = $LASTEXITCODE
Pop-Location
if ($pf -ne 0) { throw "preflight failed -- the installer would ship a non-functional app" }

# ---- confirm the packaged tree matches the source -------------------------
Write-Host "[2/3] Verifying packaged config lists every model..."
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

# the frontend must be able to serve itself -- see installer.iss's sibling
# history in build_sfx_installer.ps1 (git log) for the original 2026-08-22
# bug this guards against: `next build` doesn't copy .next/static or public/
# into .next/standalone, so a packaged app with no chunks 404s every asset
# and shows a blank window.
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

# ---- compile ----------------------------------------------------------
$version = (Get-Content (Join-Path $repo "package.json") -Raw | ConvertFrom-Json).version
Write-Host "[3/3] Compiling installer with Inno Setup (version $version)..."
Push-Location $repo
& $iscc "/DMyAppVersion=$version" $iss
$ic = $LASTEXITCODE
Pop-Location
if ($ic -ne 0) { throw "ISCC compilation failed (exit $ic)" }

$output = Join-Path $dist "EcoVisionSentinel-Installer-$version.exe"
if (-not (Test-Path $output)) { throw "ISCC reported success but $output does not exist" }

$mb = (Get-Item $output).Length / 1MB
Write-Host ("`nBUILT  {0}  ({1:N0} MB)" -f (Split-Path $output -Leaf), $mb)
Write-Host "NOTE: unsigned, so Windows SmartScreen will warn on first run"
Write-Host "      (More info -> Run anyway). Test on the demo machine beforehand."
