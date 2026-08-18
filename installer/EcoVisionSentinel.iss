; EcoVision Sentinel installer.
;
; Why this file exists instead of electron-builder's own installer targets:
;   - nsis / portable: both share electron-builder's 32-bit makensis.exe,
;     which mmaps the ENTIRE payload as one archive at build time and hard-
;     fails (ERR_ELECTRON_BUILDER_CANNOT_EXECUTE, "failed creating mmap")
;     above ~2GB. Our python-env + torch/CUDA payload is 6+ GB unpacked.
;   - msi: electron-builder's WiX-based msi target built successfully but
;     fails installing on real machines with error 2755 -- a known
;     electron-builder/WiX weak point with large payloads and custom
;     actions, not something fixable from package.json config.
;
; Inno Setup has no size ceiling (streams from a solid/segmented archive,
; not memory-mapped), is what a large share of commercial Windows software
; actually ships with, and gives a real one-screen "choose a folder"
; wizard with a native progress bar for free -- which also answers the
; "users can't tell if it's doing anything" complaint that started this,
; without a custom splash screen.
;
; electron-builder's job is now ONLY to produce dist\win-unpacked (via
; `electron-builder --dir`). This script wraps that folder as-is.
;
; Build: "C:\Users\User\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\EcoVisionSentinel.iss

#define MyAppName "EcoVision Sentinel"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "EcoVision"
#define MyAppExeName "EcoVisionSentinel.exe"
#define MySourceDir "..\dist\win-unpacked"

[Setup]
; Fixed GUID -- do not regenerate. Lets future updates detect/replace this
; install instead of side-by-side duplicating it.
AppId={{8F2E9C1A-4B3D-4E7F-9A1C-2D5E8F7A6B3C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\EcoVisionSentinel
; The one and only folder-choice screen. No app-side setup.html picker
; runs anymore (python-env now ships pre-built inside win-unpacked), so
; there is nothing left to double this prompt.
DisableDirPage=no
DisableProgramGroupPage=yes
DefaultGroupName=EcoVision Sentinel
; TESTING PHASE: no admin/UAC required, so this installs and runs (and can
; be verified) from a plain user account, and can target any drive the
; account can write to (D:\, etc). Switch to "admin" for a real signed
; production rollout -- one line, no other changes needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/fast
; Payload is mostly already-compressed binaries (torch DLLs, .pyd files);
; solid/max LZMA buys little ratio for a lot of build time here.
SolidCompression=no
OutputDir=..\dist_installer
OutputBaseFilename=EcoVisionSentinel-Setup-{#MyAppVersion}
SetupIconFile=..\build\icon.ico
WizardStyle=modern
DisableWelcomePage=no
DisableReadyPage=yes
ChangesEnvironment=no
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
