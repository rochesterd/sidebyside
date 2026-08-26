; Inno Setup script for the sidebyside clinic-machine installer -- see
; PACKAGING.md for the full build procedure and ROADMAP.md's "Distribute
; a frozen-exe installer" entry for why this exists instead of shipping
; Python source + setup.ps1 to clinic machines.
;
; Expects three things to already exist before compiling (see PACKAGING.md):
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
;   3. vendor\ids-peak-response.iss -- an InstallShield silent-install
;      response file recorded from a real Custom install of the *same*
;      vendor\ids-peak-win-extended-setup-64.exe currently in vendor\,
;      with the uEye Transport Layer component enabled. Recorded via
;      `ids-peak-win-extended-setup-64.exe /r /f1"<output path>"` on a
;      machine with no prior IDS peak install (a fresh Windows Sandbox
;      session is the easiest way to guarantee that) -- see PACKAGING.md
;      for the exact recording steps. Must be re-recorded, against that
;      new installer, every time vendor\ids-peak-win-extended-setup-64.exe
;      is bumped to a new IDS release -- see DECISIONS.md's "Silent IDS
;      peak install" entry for why a stale response file is the real risk
;      here, and how IdsPeakAlreadyInstalled()'s post-install re-check
;      guards against it silently going unnoticed if that step is missed.

#define AppVersion "1.0"
; ids_peak.dll's own FileVersion, as actually installed by
; vendor/ids-peak-win-extended-setup-64.exe (currently packaging IDS peak
; 26.06.1) -- confirmed empirically on the build machine via
; (Get-Item '...\ids_peak\program\ids_peak.dll').VersionInfo.FileVersion,
; not assumed. IDS's installer-package version string (e.g. "26.06.1")
; and this DLL's own FileVersion are unrelated numbering schemes -- see
; DECISIONS.md's "IdsPeakAlreadyInstalled() checks a version, not just a
; folder" entry for why this specific file+field is what gets compared,
; and why a bare folder-existence check isn't enough. Update this
; whenever vendor/ids-peak-win-extended-setup-64.exe is bumped to a new
; IDS release, by checking that release's own ids_peak.dll the same way.
#define IdsPeakMinDllVersion "1.16.0.0"
#define IdsPeakResponseFile "ids-peak-response.iss"

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
; dontcopy, listed first (solid-compression decompression cost grows with
; position -- see PACKAGING.md): these two are pulled out early via
; ExtractTemporaryFile() in CurStepChanged's ssInstall handler below,
; *before* sidebyside's own Files/Icons are written, not during Setup's
; normal automatic copy phase. That's what lets the silent IDS install run
; early enough for NeedRestart() to see its result -- see DECISIONS.md's
; "Silent IDS peak install: native restart page" entry. dontcopy-extracted
; files are auto-deleted when Setup exits, regardless of outcome, so no
; deleteafterinstall flag is needed here.
Source: "..\vendor\ids-peak-win-extended-setup-64.exe"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\vendor\{#IdsPeakResponseFile}"; DestDir: "{tmp}"; Flags: dontcopy
Source: "dist\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\settings\*"; DestDir: "{app}\settings"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; app.exe only on the Desktop -- this is what closes the "how does a
; student launch this" gap (see ROADMAP.md's entry: no such shortcut
; existed anywhere before this). settings.exe gets a Start Menu entry
; only, no Desktop icon -- CLAUDE.md: "never point a student at it."
Name: "{autodesktop}\sidebyside"; Filename: "{app}\app\app.exe"
Name: "{autoprograms}\sidebyside"; Filename: "{app}\app\app.exe"
Name: "{autoprograms}\sidebyside Settings"; Filename: "{app}\settings\settings.exe"

[Code]
var
  (* Set True only after InstallIdsPeakSilently() actually performs and
     verifies a fresh IDS peak install this run -- read by NeedRestart()
     below. This works now (it didn't the first time it was tried -- see
     DECISIONS.md's "Silent IDS peak install: the restart prompt never
     showed" entry) specifically because InstallIdsPeakSilently() runs
     from ssInstall, confirmed empirically to be before Inno's one
     internal NeedRestart() query, not from ssPostInstall, confirmed to
     be after it. *)
  IdsPeakInstalledThisRun: Boolean;

function IdsPeakAlreadyInstalled(): Boolean;
var
  DllPath: String;
  VersionMS, VersionLS: Cardinal;
  InstalledVersion, MinRequiredVersion: Int64;
begin
  (* A bare DirExists check (this function's original form) treats *any*
     version as good enough, including a much older one silently installed
     by other IDS-camera-adjacent software already on the machine before
     sidebyside ever runs -- confirmed for real, not hypothetical: Keeler's
     own Kinexis/Vantage Plus Digital installer silently drives IDS peak
     2.9.0.0 (see DECISIONS.md) into this exact Program Files\IDS\ids_peak
     location, a version far older than what
     vendor/ids-peak-win-extended-setup-64.exe currently bundles. A
     technician on a machine that's only ever run Kinexis would have this
     check wrongly skip the bundled installer, silently leaving that old
     SDK in place instead of the current one sidebyside's pinned
     requirements-ids.txt bindings actually expect.
     ids_peak\program\ids_peak.dll's own FileVersion is what's compared --
     the extended setup's actual install location for that specific file,
     confirmed on a real IDS-peak-installed machine during this project's
     EULA/licensing investigation (see ROADMAP.md) -- rather than a
     specific product GUID in the Uninstall registry key, which changes
     across IDS peak releases. *)
  Result := False;
  DllPath := ExpandConstant('{pf}\IDS\ids_peak\program\ids_peak.dll');
  if FileExists(DllPath) and GetVersionNumbers(DllPath, VersionMS, VersionLS) then
  begin
    InstalledVersion := PackVersionNumbers(VersionMS, VersionLS);
    StrToVersion('{#IdsPeakMinDllVersion}', MinRequiredVersion);
    Result := ComparePackedVersion(InstalledVersion, MinRequiredVersion) >= 0;
  end;
end;

procedure InstallIdsPeakSilently();
(* Silent, via InstallShield's /s /f1<response file> replay -- not
   interactive. See DECISIONS.md's "Silent IDS peak install" entry for
   the full reasoning; summary: a real device vendor (Keeler, for its own
   Kinexis/Vantage Plus Digital installer) ships exactly this mechanism
   in production, and the two failure modes it risks are both guarded
   against below rather than trusted blindly the way Kinexis's own
   install script does (it doesn't check its own exit code or log at
   all):
     1. A hard failure (Windows blocks it, install crashes) -- caught via
        Exec's own ResultCode and the /f2 log file's ResultCode line.
     2. A "successful" replay that silently picked the wrong components,
        because vendor\ids-peak-win-extended-setup-64.exe moved on to a
        version whose dialog layout no longer matches
        vendor\ids-peak-response.iss -- Exec/the log can't see this at
        all, since from the installer's own point of view nothing went
        wrong. Caught by re-running IdsPeakAlreadyInstalled()'s real
        ids_peak.dll version check afterward, not by trusting the exit
        code.

   Runs from ssInstall, before sidebyside's own Files/Icons -- a
   deliberate reversal of the original ssPostInstall-based design (see
   DECISIONS.md's "Silent IDS peak install" and "...: native restart
   page" entries for the full back-and-forth). The original ordering
   protected sidebyside's own install from an indefinite hang in this
   step; that protection is given up here in exchange for the restart
   choice working as Inno's real native Finished-page mechanism instead
   of a separate popup -- a deliberate call, not an oversight, made after
   weighing how a genuine hang actually resolves (a technician force-
   killing the process either way, with no real rollback possible in
   Inno regardless of ordering) against how much that ordering was
   actually buying. *)
var
  ExePath, ResponsePath, LogPath, Params: String;
  ResultCode: Integer;
begin
  if IdsPeakAlreadyInstalled() then
    Exit;

  (* Both dontcopy-flagged in [Files] specifically so they're available
     this early -- Setup's normal automatic copy phase (and the {app}
     directory's own creation) doesn't happen until after ssInstall
     returns, confirmed empirically, not assumed (see DECISIONS.md). *)
  ExtractTemporaryFile('ids-peak-win-extended-setup-64.exe');
  ExtractTemporaryFile('{#IdsPeakResponseFile}');
  ForceDirectories(ExpandConstant('{app}'));

  ExePath := ExpandConstant('{tmp}\ids-peak-win-extended-setup-64.exe');
  ResponsePath := ExpandConstant('{tmp}\{#IdsPeakResponseFile}');
  LogPath := ExpandConstant('{app}\ids-peak-install.log');
  Params := Format('/s /f1"%s" /f2"%s"', [ResponsePath, LogPath]);

  WizardForm.StatusLabel.Caption := 'Installing the IDS peak SDK...';

  if not Exec(ExePath, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('The IDS peak SDK installer could not be launched (Windows error ' +
      IntToStr(ResultCode) + '). sidebyside needs IDS peak to control the ' +
      'instrument cameras -- see SETUP.md to install it manually, then ' +
      'restart sidebyside.', mbError, MB_OK);
    Exit;
  end;

  (* InstallShield's own ResultCode=0 in the /f2 log only rules out a hard
     failure -- see the procedure comment above for why a nonzero code
     here still isn't the whole story, and why the IdsPeakAlreadyInstalled
     recheck below is the real verification. *)
  if ResultCode <> 0 then
  begin
    MsgBox('The IDS peak SDK installer reported an error (exit code ' +
      IntToStr(ResultCode) + '). sidebyside needs IDS peak to control the ' +
      'instrument cameras -- see SETUP.md to install it manually, then ' +
      'restart sidebyside.' + #13#10 + #13#10 + 'Installer log: ' + LogPath,
      mbError, MB_OK);
    Exit;
  end;

  if not IdsPeakAlreadyInstalled() then
  begin
    MsgBox('The IDS peak SDK installer finished, but sidebyside could not ' +
      'verify it installed the expected components (this can happen if ' +
      'vendor\ids-peak-response.iss is out of date for the bundled IDS ' +
      'peak version). Please install IDS peak manually via SETUP.md ' +
      'before using sidebyside, or contact the developer.', mbError, MB_OK);
    Exit;
  end;

  IdsPeakInstalledThisRun := True;
end;

function NeedRestart(): Boolean;
begin
  (* Inno's real native mechanism -- shows the actual restart-choice
     radio buttons on the Finished page, the same UI IDS's own installer
     uses, rather than a separate popup. Works here (confirmed
     empirically, see DECISIONS.md) because IdsPeakInstalledThisRun is
     set from ssInstall, before Inno's single internal query of this
     function -- setting it from ssPostInstall, tried first, was
     confirmed too late for Inno to ever see. *)
  Result := IdsPeakInstalledThisRun;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  (* ssInstall, not ssPostInstall -- see InstallIdsPeakSilently()'s own
     comment and DECISIONS.md for why this ordering was deliberately
     reversed. *)
  if CurStep = ssInstall then
    InstallIdsPeakSilently();
end;
