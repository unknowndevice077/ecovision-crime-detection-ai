@echo off
REM Runs once, automatically, right after the SFX finishes extracting.
REM Registers the app so it behaves like something that was actually
REM installed rather than just unzipped: a Start Menu shortcut and an entry
REM in Add/Remove Programs pointing at uninstall.bat (shipped alongside this
REM file inside the payload). Then launches the app.
setlocal
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "VER=__VERSION__"

REM --- Start Menu shortcut ---------------------------------------------
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs\EcoVision Sentinel.lnk"
powershell -NoProfile -WindowStyle Hidden -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SM%');" ^
  "$s.TargetPath='%HERE%\EcoVisionSentinel.exe';" ^
  "$s.WorkingDirectory='%HERE%';" ^
  "$s.IconLocation='%HERE%\EcoVisionSentinel.exe';" ^
  "$s.Save()" >nul 2>&1

REM --- Add/Remove Programs entry -----------------------------------------
REM HKCU, not HKLM: no elevation prompt, and this is a per-user install
REM (InstallPath in the SFX config already defaults under the user's choice,
REM not Program Files). DisplayIcon/Publisher/EstimatedSize are cosmetic but
REM make it look like a real entry rather than a stray registry key.
set "REGKEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\EcoVisionSentinel"
reg add "%REGKEY%" /v DisplayName /t REG_SZ /d "EcoVision Sentinel" /f >nul 2>&1
reg add "%REGKEY%" /v DisplayVersion /t REG_SZ /d "%VER%" /f >nul 2>&1
reg add "%REGKEY%" /v Publisher /t REG_SZ /d "EcoVision" /f >nul 2>&1
reg add "%REGKEY%" /v InstallLocation /t REG_SZ /d "%HERE%" /f >nul 2>&1
reg add "%REGKEY%" /v DisplayIcon /t REG_SZ /d "%HERE%\EcoVisionSentinel.exe" /f >nul 2>&1
reg add "%REGKEY%" /v UninstallString /t REG_SZ /d "\"%HERE%\uninstall.bat\"" /f >nul 2>&1
reg add "%REGKEY%" /v NoModify /t REG_DWORD /d 1 /f >nul 2>&1
reg add "%REGKEY%" /v NoRepair /t REG_DWORD /d 1 /f >nul 2>&1
REM ~6 GB unpacked; EstimatedSize is in KB.
reg add "%REGKEY%" /v EstimatedSize /t REG_DWORD /d 6291456 /f >nul 2>&1

REM --- launch -------------------------------------------------------------
start "" "%HERE%\EcoVisionSentinel.exe"
exit /b 0
