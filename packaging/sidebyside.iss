; Inno Setup script for the sidebyside clinic-machine installer -- see
; PACKAGING.md for the full build procedure and ROADMAP.md's "Distribute
; a frozen-exe installer" entry for why this exists instead of shipping
; Python source + setup.ps1 to clinic machines.
;
; Expects two things to already exist before compiling (see PACKAGING.md):
;   1. packaging\dist\app\ and packaging\dist\settings\ -- built via
;      `pyinstaller packaging\app.spec` / `pyinstaller packaging\settings.spec`.
;   2. vendor\ids-peak-win-extended-setup-64.exe -- the current IDS peak
;      *extended* setup installer (not standard/runtime -- see
;      ROADMAP.md's "why extended, not IDS Software Suite + runtime
;      setup" reasoning), manually placed there by whoever's building
;      this. Gitignored, same convention this repo already uses for
;      vendor/ids_peak_api.txt. Always use this exact filename regardless
;      of the IDS SDK version currently bundled, so this script never
;      needs editing just because IDS shipped a new version.

#define AppVersion "1.0"

[Setup]
AppName=sidebyside
AppVersion={#AppVersion}
AppPublisher=NECO
DefaultDirName={autopf}\sidebyside
DefaultGroupName=sidebyside
DisableProgramGroupPage=yes
; Writes to Program Files and chain-launches the IDS installer (which
; itself needs admin for driver installation) -- both need elevation.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_output
OutputBaseFilename=sidebyside-setup
Compression=lzma2
SolidCompression=yes
; No SetupIconFile -- this project has no icon assets anywhere (confirmed
; during planning); Inno Setup's own default icon is used instead.

[Files]
Source: "dist\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\settings\*"; DestDir: "{app}\settings"; Flags: ignoreversion recursesubdirs createallsubdirs
; {tmp}, not {app}: this is a ~356MB third-party installer needed only
; once, during setup -- no reason to leave a permanent copy sitting in
; Program Files afterward. deleteafterinstall removes it once this
; installer finishes, success or not.
Source: "..\vendor\ids-peak-win-extended-setup-64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
; app.exe only on the Desktop -- this is what closes the "how does a
; student launch this" gap (see ROADMAP.md's entry: no such shortcut
; existed anywhere before this). settings.exe gets a Start Menu entry
; only, no Desktop icon -- CLAUDE.md: "never point a student at it."
Name: "{autodesktop}\sidebyside"; Filename: "{app}\app\app.exe"
Name: "{autoprograms}\sidebyside"; Filename: "{app}\app\app.exe"
Name: "{autoprograms}\sidebyside Settings"; Filename: "{app}\settings\settings.exe"

[Run]
; Interactive, no silent switches -- the technician sees IDS's real
; installer wizard and clicks through it themselves, same as if they'd
; double-clicked it directly. This is bundling for convenience only (skips
; having to separately find/download it from IDS's site), not a silent
; auto-install -- see ROADMAP.md's "Can setup.ps1 drive the IDS peak SDK
; installer?" entry for why that stays a deliberate non-goal regardless of
; how this installer packages things. waituntilterminated (the default for
; a non-postinstall entry, made explicit here) means this installer's own
; wizard pauses until the technician finishes IDS's.
;
; Skipped entirely if IDS peak already appears to be installed (checked
; via IdsPeakAlreadyInstalled below) -- so re-running this installer later
; (e.g. to update the app itself) doesn't force a technician back through
; IDS's wizard every time.
Filename: "{tmp}\ids-peak-win-extended-setup-64.exe"; StatusMsg: "Launching the IDS peak SDK installer - please complete its setup wizard..."; Flags: waituntilterminated; Check: not IdsPeakAlreadyInstalled

[Code]
function IdsPeakAlreadyInstalled(): Boolean;
begin
  { Extended setup's actual install location, confirmed on a real
    IDS-peak-installed machine during this project's own EULA/licensing
    investigation (see ROADMAP.md) -- more version-independent than
    checking a specific product GUID in the Uninstall registry key, which
    changes across IDS peak releases. }
  Result := DirExists(ExpandConstant('{pf}\IDS\ids_peak'));
end;
