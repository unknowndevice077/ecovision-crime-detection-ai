@echo off
REM EcoVision Sentinel uninstaller.
REM
REM This lives INSIDE the install folder and removes it, so it deletes the
REM folder it is running from. That's a real risk if it ran from the wrong
REM place (an unrelated call site, a shortcut pointing somewhere else) --
REM so it locates itself and refuses to run unless it can see its own
REM sibling, EcoVisionSentinel.exe, confirming this is genuinely an install
REM folder and not some other directory.
REM
REM USES "choice", NOT "set /p", FOR THE PROMPTS, AND "goto", NOT
REM PARENTHESISED "if (...)" BLOCKS, FOR EVERYTHING AFTER THEM. Both were
REM found by testing under redirected/non-console stdin (a real invocation
REM path: launchers, CI, remote installs), not just interactive double-click:
REM
REM   - "set /p" immediately followed by "if" fails outright with "The
REM     syntax of the command is incorrect" -- a documented cmd.exe parser
REM     quirk when stdin is not an interactive console. choice has no such
REM     issue and is the standard tool for exactly this.
REM   - even with choice, a LATER multi-line "if "%VAR%"=="Y" ( ... )" block
REM     was silently skipped under the same redirected-stdin conditions --
REM     no error, it simply never removed anything. goto-based branching
REM     verified reliable in both interactive and redirected-stdin testing,
REM     which is why it is used throughout below instead.
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

if exist "%HERE%\EcoVisionSentinel.exe" goto :is_install_dir
echo This does not look like an EcoVision Sentinel install folder.
echo Refusing to delete "%HERE%" -- nothing was removed.
pause
exit /b 1

:is_install_dir
echo ============================================================
echo  EcoVision Sentinel - Uninstall
echo ============================================================
echo.
echo This will remove:
echo   %HERE%
echo   (the application, Python runtime, and AI models - about 6 GB)
echo.
echo It will NOT remove your data by default:
echo   %%USERPROFILE%%\EcoVisionSentinelData
echo   (recorded clips, the incident database, and settings)
echo.
choice /c YN /n /m "Continue? [Y/N]: "
if errorlevel 2 goto :cancelled

echo.
choice /c YN /n /m "Also delete recorded clips and the incident database? [y/N]: "
if errorlevel 2 goto :keep_data
goto :wipe_data

:cancelled
echo.
echo Cancelled. Nothing was removed.
pause
exit /b 0

:wipe_data
echo Removing application data...
rd /s /q "%USERPROFILE%\EcoVisionSentinelData" >nul 2>&1
goto :do_uninstall

:keep_data
REM nothing to do -- fall through

:do_uninstall
REM Remove the Start Menu shortcut and the Add/Remove Programs entry first --
REM both point at files that are about to be deleted, and a stale shortcut or
REM a registry entry with no uninstaller behind it is worse than none.
echo.
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs\EcoVision Sentinel.lnk"
if exist "%SM%" del /q "%SM%" >nul 2>&1

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\EcoVisionSentinel" /f >nul 2>&1

echo Removing application files...
REM cmd cannot delete the folder it is currently running FROM while it is
REM running -- the batch file itself is inside HERE. Writing a small deleter
REM to %TEMP% and launching THAT, rather than an inline `start cmd /c "..."`
REM one-liner, avoids the nested-quote nightmare of embedding a quoted path
REM inside an already-quoted /c argument -- that construction is fragile and
REM version-dependent across Windows releases.
set "DELETER=%TEMP%\ecovision_uninstall_%RANDOM%.bat"
> "%DELETER%" echo @echo off
>> "%DELETER%" echo cd /d "%%TEMP%%"
>> "%DELETER%" echo timeout /t 2 /nobreak ^>nul
>> "%DELETER%" echo rd /s /q "%HERE%"
>> "%DELETER%" echo del "%%~f0"
start "" /min cmd /c "%DELETER%"

echo.
echo Done. EcoVision Sentinel has been removed.
echo (this window will close automatically)
timeout /t 3 >nul 2>&1
exit /b 0
