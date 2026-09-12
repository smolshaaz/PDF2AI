; PDF2AI Inno Setup Installer
; Build: ISCC.exe installer\PDF2AI.iss   (from the repository root)
; Output: release\PDF2AI-Setup.exe
;
; Requirements (developer machine only):
;   Inno Setup 6.x  – https://jrsoftware.org/isdl.php
;   PyInstaller one-folder build already produced in dist\PDF2AI\
;
; The end user needs nothing except PDF2AI-Setup.exe.

#define MyAppName      "PDF2AI"
#define MyAppVersion   "1.1.0"
#define MyAppPublisher "PDF2AI"
#define MyAppExeName   "PDF2AI.exe"
; Stable GUID – do NOT change between releases; Inno Setup uses it to detect
; existing installations and enable in-place upgrades without duplicate folders.
#define MyAppId        "{{A7F3C2D1-84BE-4E5A-9032-1F6B8D0E5A23}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}

; Per-user install — no administrator rights required.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Allow silent upgrade of an existing installation.
; Inno Setup will uninstall the previous version before installing the new one.
AppendDefaultDirName=no
DirExistsWarning=no
; Wizard appearance
WizardStyle=modern
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes

; Output
OutputDir={#SourcePath}\..\release
OutputBaseFilename=PDF2AI-Setup
Compression=lzma2/ultra64
SolidCompression=yes

; Metadata shown in Windows Settings > Apps
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; Icon: use the executable's own icon (works even with PyInstaller default).
; If you later add a dedicated .ico, set: SetupIconFile=..\assets\pdf2ai.ico
; and UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Package the entire PyInstaller one-folder build.
; dist\PDF2AI\ contains PDF2AI.exe, _internal\, and bundled docs.
; Never package src/, tests/, .venv/, .git/, build/ — PyInstaller never puts
; those into dist\PDF2AI\ so they are simply not present.
Source: "{#SourcePath}\..\dist\PDF2AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
; Desktop (optional, controlled by the task checkbox above)
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Offer to launch the app immediately after installation.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the application directory entirely after uninstall.
; This cleans up any log files written by the app under its install folder.
; User-generated output files (Documents\PDF2AI Output\*.ai.md, PDFs, etc.)
; are NEVER touched — they live in the user's Documents folder.
Type: filesandordirs; Name: "{app}"

; ── Notes for maintainers ──────────────────────────────────────────────────
;
; UPGRADE BEHAVIOUR
;   The stable AppId GUID above causes Inno Setup to detect the previous
;   installation and offer an in-place upgrade. No duplicate entries appear
;   in Windows Settings > Apps.
;
; SILENT INSTALL (enterprise / IT deployment)
;   PDF2AI-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
;
; CODE SIGNING (optional, for SmartScreen suppression)
;   signtool sign /fd SHA256 /tr http://timestamp.url /td sha256
;          /f certificate.pfx release\PDF2AI-Setup.exe
;   Add to build_release_windows.bat after ISCC step if a certificate exists.
