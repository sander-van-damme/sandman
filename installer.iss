; Inno Setup script for Sandman
; Builds a Windows installer that places Sandman.exe in the right location,
; adds Start Menu + (optional) Desktop shortcuts, and registers an uninstaller.
;
; Build locally with:
;   iscc installer.iss
;
; Or pass a version override from CI:
;   iscc /DMyAppVersion=1.2.3 installer.iss
;
; Requires Inno Setup 6 (https://jrsoftware.org/isinfo.php). Pre-installed on
; GitHub Actions windows-latest runners.

#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif

#define MyAppName "Sandman"
#define MyAppPublisher "Sander van Damme"
#define MyAppURL "https://github.com/sander-van-damme/sandman"
#define MyAppExeName "Sandman.exe"

[Setup]
; A unique AppId keeps upgrades/uninstalls tidy. Do NOT change this value
; between releases or Windows will treat new versions as a different product.
AppId={{7B5C9E42-3D8A-4F1B-9E6C-2A8D4F1E7B03}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}

; Install per-user by default (no admin required). The user can elevate from
; the UAC dialog if they prefer a machine-wide install under Program Files.
; {autopf} resolves to %LOCALAPPDATA%\Programs when running unprivileged and
; to %ProgramFiles% when elevated — matching how VS Code, Signal, etc. install.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 64-bit only; Sandman targets modern Windows 10/11.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Output
OutputDir=installer
OutputBaseFilename=Sandman-Setup-{#MyAppVersion}
SetupIconFile=sandman\assets\icon_active.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start {#MyAppName} automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Bedtime nudge app"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Bedtime nudge app"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Preserve the user's config at %USERPROFILE%\.sandman by default. Only clean
; up files we placed under {app}.
Type: filesandordirs; Name: "{app}"
