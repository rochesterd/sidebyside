; Inno Setup script for the Reflex **viewer-only** installer -- the
; build a student or instructor installs on their own laptop to review
; recordings made in the clinic. See PACKAGING.md for the build procedure
; and ROADMAP.md's "Phase 4: two installers" entry for why this exists
; separately from reflex.iss.
;
; Differences from the clinic installer (reflex.iss), all deliberate:
;
;   * Ships viewer.exe only -- no app.exe, no settings.exe. A review
;     machine has no cameras to record with or configure.
;   * Bundles no IDS peak SDK. That's ~356MB of machine-vision SDK and
;     kernel drivers whose only purpose is talking to cameras this machine
;     doesn't have. Requiring it is the kind of friction that ends with
;     nobody reviewing anything.
;   * Installs per-user under {localappdata} with PrivilegesRequired=lowest,
;     so it needs no admin rights and raises no UAC prompt -- students
;     often don't have admin on their own machines, and nothing here
;     writes outside the user's profile.
;   * Distinct AppId, so this and the clinic install coexist on one machine
;     without either uninstalling or upgrading over the other. Both are
;     pinned explicitly rather than derived from AppName, and neither may
;     change once shipped, or existing installs stop being recognised as
;     upgradable.
;
; Expects packaging\dist\viewer\ to exist, built via
; `pyinstaller packaging\viewer.spec`.

#define AppVersion "1.0"

[Setup]
AppId=reflex-viewer
AppName=Reflex Viewer
AppVersion={#AppVersion}
AppPublisher=NECO
DefaultDirName={localappdata}\Reflex Viewer
DefaultGroupName=Reflex
DisableProgramGroupPage=yes
; No admin: nothing is written outside the user's own profile, and there
; are no drivers to install.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_output
OutputBaseFilename=reflex-viewer-setup
Compression=lzma2
SolidCompression=yes
; The viewer's own mark, not app.exe's -- both installers can land on
; the same machine, so they need telling apart in a Downloads folder.
SetupIconFile=..\assets\reflex-viewer.ico
UninstallDisplayIcon={app}\viewer.exe

[Files]
Source: "dist\viewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Desktop shortcut, unlike settings.exe in the clinic installer: students
; *are* this program's audience (CLAUDE.md's "Who uses it"), and finding it
; has to be as easy as finding the recorder is in the clinic.
Name: "{autodesktop}\Reflex Viewer"; Filename: "{app}\viewer.exe"
Name: "{autoprograms}\Reflex Viewer"; Filename: "{app}\viewer.exe"

[Run]
Filename: "{app}\viewer.exe"; Description: "Open Reflex Viewer now"; Flags: nowait postinstall skipifsilent
