; EcoVision Sentinel installer, built with Inno Setup.
;
;   "C:\Users\User\AppData\Local\Programs\Inno Setup 7\ISCC.exe" ^
;       /DMyAppVersion=1.0.0 tools\installer.iss
;
; Run `npm run app:build` first so dist\win-unpacked exists and is current.
; Normally invoked via tools\build_installer.ps1, which runs the preflight
; checks below, resolves MyAppVersion from package.json, and calls ISCC.
;
; WHY NOT NSIS. electron-builder's NSIS target was tried twice and failed
; identically: "File: failed creating mmap of ...-x64.nsis.7z" -- makensis.exe
; is a 32-bit process that memory-maps the entire compressed payload archive,
; and this app's payload (2+ GB: torch + the CUDA runtime for GPU inference)
; is just past what a 32-bit address space can map. Not a made-up limitation --
; do not re-add nsis to package.json without first getting the payload under
; roughly 2 GB.
;
; WHY NOT THE OLD 7-ZIP SFX APPROACH. A 7z SFX is a self-extracting archive,
; not an installer -- it has no concept of an uninstaller, a versioned
; Add/Remove Programs entry, or "an instance of this app is already running
; and holds these files open". All of that had to be hand-built in batch
; (postinstall_template.bat / uninstall_template.bat, now retired) and every
; one of those hand-built pieces had its own bug: a silent partial-delete
; when the app was still running (taskkill + a hopeful `timeout /t 2`), and
; -mmt=on silently corrupting CUDA DLLs in the archive. Inno Setup does all
; of this natively: CloseApplications below uses the real Windows Restart
; Manager API instead of taskkill-and-hope, and the uninstaller (and its
; Add/Remove entry) is generated, not written by hand.
;
; WHY INNO HANDLES THE SIZE FINE. It doesn't mmap the aggregate archive the
; way makensis does -- it streams. Multi-GB Inno installers (10-50+ GB, e.g.
; game installers) are routine in the wild.

#define MyAppName "EcoVision Sentinel"
#define MyAppExeName "EcoVisionSentinel.exe"
#define MyAppPublisher "EcoVision"
; Fixed once, never regenerate -- this is what lets Inno recognize "this is
; an upgrade of the same app" across versions instead of installing side by
; side. Changing it would orphan every existing install's Add/Remove entry.
; {{ (doubled) is how Inno's preprocessor escapes a literal opening brace --
; a bare {...} here is parsed as a constant reference (like {app}) and
; "9F2C6E41-..." isn't a known one, hence a compile error. The closing "}"
; needs no escaping -- only "{" is ever special -- so doubling IT too (an
; earlier version of this line did) doesn't error, it just silently emits
; an extra literal "}", producing an AppId of "{9F2C6E41-...}}" (note the
; double close) instead of the intended "{9F2C6E41-...}". Confirmed in the
; registry after a real install: the uninstall key was literally named
; ...C0F31}}_is1. Harmless to every check Inno itself does (install/upgrade/
; uninstall all worked anyway) but not the well-formed GUID this was meant
; to be, so fixed properly here rather than left as "works by accident".
#define MyAppId "{{9F2C6E41-9B5F-4C4A-BE8B-5A6E2E9C0F31}"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}

; No admin/UAC prompt, matching the previous HKCU-based install. Requires the
; chosen install dir to be writable by a standard user -- true for the
; default below on a normal single-user Windows box.
PrivilegesRequired=lowest
DefaultDirName=C:\EcoVisionSentinel
UsePreviousAppDir=yes
DisableProgramGroupPage=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; NOT OPTIONAL, unlike the old script's mx=3-for-speed tradeoff: Inno
; refuses outright to emit a single Setup.exe over ~4.2 GB in store mode --
; "Disk spanning must be enabled ... as this approaches the maximum
; supported by Windows" -- and this payload is ~6 GB uncompressed. The
; alternative is DiskSpanning=yes, which splits the output into several
; .bin files the user has to keep together -- the opposite of what this
; migration was for. lzma2/fast + solid compression (thousands of small
; Python stdlib/site-packages files alongside a few huge CUDA DLLs is
; exactly the shape solid compression helps with) gets the single-file
; output under the ceiling while staying far faster than lzma/max.
Compression=lzma2/fast
SolidCompression=yes

; Detect (and offer to close) a running EcoVisionSentinel.exe via the real
; Windows Restart Manager API -- both on install and uninstall. Replaces
; uninstall_template.bat's taskkill /T + timeout /t 2 guess.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no

; Relative paths in this file resolve against the .iss file's own directory
; (tools\), not wherever ISCC was invoked from -- hence ..\ on both of these.
OutputDir=..\dist
OutputBaseFilename=EcoVisionSentinel-Installer-{#MyAppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
SetupLogging=yes
DiskSpanning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; win-unpacked still carries postinstall.bat/uninstall.bat from the retired
; 7z pipeline until that folder is rebuilt fresh -- excluded so a stale
; build never ships them again.
Source: "..\dist\win-unpacked\*"; DestDir: "{app}"; Excludes: "postinstall.bat,uninstall.bat"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Mirrors uninstall_template.bat's old behaviour: recorded clips and the
// incident database are NOT removed by default. Ask, default to keeping
// them (IDYES is an explicit opt-in, matching the old script's [y/N]).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{%USERPROFILE}\EcoVisionSentinelData');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete recorded clips and the incident database?' + #13#10 +
                DataDir + #13#10#13#10 +
                'This cannot be undone.', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
