# PACKAGING.md

How to build sidebyside's two distributable installers — the clinic-machine
one and the viewer-only one (see "Two installers" below). This is a
**developer-only** procedure — nobody installing sidebyside does any of
this, they just run the `.exe` it produces. See CLAUDE.md's "Who uses it"
and ROADMAP.md's "Distribute a frozen-exe installer, not a Python source
bootstrap" and "Phase 4: two installers" entries for why these exist.

For setting up a *development* machine to work on sidebyside's source
instead, see `SETUP.md` — that's a different audience and a different
procedure.

---

## 1. Build-only tooling

From an activated `.venv` that already has `requirements.txt` installed
(see `SETUP.md` Section 1):

```powershell
python -m pip install -r requirements-packaging.txt
```

This installs PyInstaller only — not needed to run or develop the app
itself, only to build the distributable.

You also need **Inno Setup 6** on this machine (not a Python package —
a separate Windows tool): `winget install --id JRSoftware.InnoSetup -e`,
or download from https://jrsoftware.org. Its command-line compiler,
`ISCC.exe`, installs to `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.

## Two installers

This procedure produces **two** distributables, for two different
machines. See `ROADMAP.md`'s "Phase 4: two installers" entry for why.

| | `sidebyside-setup.exe` | `sidebyside-viewer-setup.exe` |
|---|---|---|
| For | the clinic room machine | a student's or instructor's own laptop |
| Contains | `app.exe`, `settings.exe` | `viewer.exe` |
| IDS peak SDK | bundled, installed silently | none |
| Size | ~490 MB | ~90 MB |
| Privileges | admin | none (per-user) |
| Steps below | 2, 3, 4, 5 | 2, 6 |

The clinic installer deliberately does **not** ship `viewer.exe`:
`app.exe` already contains the viewer (Watch and Past recordings work
from inside the kiosk), so a second full PySide6+OpenCV+PyAV tree would
add hundreds of MB for no capability that machine lacks.

## 2. Freeze the entry points

```powershell
python -m PyInstaller --distpath packaging\dist --workpath packaging\build packaging\app.spec
python -m PyInstaller --distpath packaging\dist --workpath packaging\build packaging\settings.spec
python -m PyInstaller --distpath packaging\dist --workpath packaging\build packaging\viewer.spec
```

(Only `viewer.spec` is needed for the viewer-only installer; only the
other two for the clinic one.)

All three specs are checked into git (`packaging/*.spec`) — this isn't a
from-scratch step, just replaying a known-working build. See
`DECISIONS.md`'s entry on this for what was actually verified and why
no hidden-imports/`--collect-all` overrides were needed. If a future IDS
peak SDK or dependency version bump breaks the build, start by reading
the PyInstaller warnings file it writes to `packaging/build/<name>/
warn-<name>.txt`.

`viewer.spec` lists `ids_peak`, `ids_peak_ipl`, `pygrabber` and
`comtypes` in `excludes`. That's an assertion, not a size tweak: if a
future edit makes `viewer.py` reach anything camera-facing, this build
fails loudly rather than silently gaining an SDK dependency the review
machine can't satisfy. Confirmed on the 2026-09-02 build — the frozen
`viewer.exe` contains none of `recorder`, `camera`, `kiosk`,
`ids_camera` or `uvc_camera`.

**Verify before continuing**, not just "did it build without error":

- `packaging\dist\app\app.exe --synthetic` and
  `packaging\dist\settings\settings.exe` must open with no import/DLL
  errors.
- `packaging\dist\viewer\viewer.exe <a session folder>` must play it, and
  `viewer.exe` with no arguments must show the picker (it logs "no usable
  config.json … using the default recordings folder" and lists nothing —
  that's the correct review-machine path, not a failure).

## 3. Get the IDS peak extended installer into `vendor/`

`packaging/sidebyside.iss` expects the IDS peak **extended** setup
installer (not standard, not runtime — see `ROADMAP.md`'s "why extended,
not IDS Software Suite + runtime setup" reasoning) at exactly:

```
vendor\ids-peak-win-extended-setup-64.exe
```

Download the current version from
https://en.ids-imaging.com/download-peak.html and place/rename it there.
`vendor/` is gitignored (same convention as `vendor/ids_peak_api.txt` —
see `CLAUDE.md`'s Environment section), so this is a manual, per-build-
machine step, not something `git clone` gives you. Always use this exact
filename regardless of the version number in what you downloaded, so
`sidebyside.iss` never needs editing just because IDS shipped a new
release.

## 4. Record a silent-install response file for that exact installer

`sidebyside.iss` drives the IDS peak installer **silently** (see
`DECISIONS.md`'s "Silent IDS peak install" entry for why, and the
verification it relies on instead of a technician watching the wizard).
That needs an InstallShield response file recorded from a real install of
the *same* installer placed in step 3, at:

```
vendor\ids-peak-response.iss
```

**Record it on a machine with no prior IDS peak install** — a fresh
Windows Sandbox session is the easiest way to guarantee that (a
recording taken on a machine that already has IDS peak gets InstallShield's
Modify/Repair/Remove dialog flow instead of a fresh-install flow, which
records the wrong thing entirely). In that clean environment:

```
<the exact filename you downloaded>.exe /r /f1"C:\ids-peak-response.iss"
```

This runs the real, interactive wizard while recording every answer —
it's a genuine install, not a dry run. Click through it for real:
**Custom** install, leave every component checked (or at minimum ensure
**uEye Transport Layer** — listed as `UEyeSupport` in the 26.x-series
component tree — stays checked), leave the destination at its default.
**At the final restart prompt, choose "No, I will restart later"** — this
is not optional: since the replay is silent, whatever gets recorded here
fires automatically and unprompted on every real clinic machine this
response file is later replayed on. Recording "restart now" would mean
every future silent install triggers an unannounced reboot on a real
clinic machine.

Copy the resulting `C:\ids-peak-response.iss` out of the sandbox to
`vendor\ids-peak-response.iss` on the build machine. Gitignored, same
convention as the installer `.exe` itself in step 3.

**Must be re-recorded** (the same way, in a fresh clean environment)
every time the installer `.exe` in step 3 is bumped to a new IDS release
— a response file recorded against one version's dialog layout can
silently produce the wrong result when replayed against a different
version's layout. `IdsPeakAlreadyInstalled()`'s post-install re-check in
`sidebyside.iss` is the safety net if this step gets missed, not a
substitute for actually doing it.

## 5. Compile the clinic installer

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\sidebyside.iss
```

Produces `packaging\installer_output\sidebyside-setup.exe` — this is the
one file a technician actually needs. Gitignored, like the rest of
`packaging/`'s generated output (`packaging/build/`, `packaging/dist/`).

**Before handing this to anyone**, actually run it on a real (or
disposable/VM) Windows machine and confirm: both shortcuts appear and
launch their respective `.exe`s, the IDS peak SDK installs correctly
(check `%ProgramFiles%\IDS\ids_peak` — no wizard appears, this happens
silently now), the Finished page shows the native "restart now / restart
later" choice (not a separate popup — see `DECISIONS.md`'s "Silent IDS
peak install: native restart page" entry for why the install runs
*before* sidebyside's own files specifically to make this work), and
choosing "restart now" genuinely restarts the machine. And — on a machine
that already has a current-enough IDS peak installed — re-running
`sidebyside-setup.exe` skips reinstalling it (`IdsPeakAlreadyInstalled` in
`packaging/sidebyside.iss`'s `[Code]` section) and the Finished page shows
no restart choice, since nothing changed. If the silent install fails or
can't be verified, the installer shows an explicit error dialog rather
than continuing silently — confirm that path too by temporarily renaming
`vendor\ids-peak-response.iss` before a test run.

## 6. Compile the viewer-only installer

Needs only step 2's `viewer.spec` build — no `vendor/` contents, no
response file, nothing from steps 3–5.

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\sidebyside-viewer.iss
```

Produces `packaging\installer_output\sidebyside-viewer-setup.exe` (~90 MB
as of 2026-09-02, against the clinic installer's ~490 MB).

This is what goes to a student or instructor who wants to review
recordings on their own machine. It installs per-user under
`%LOCALAPPDATA%\sidebyside-viewer` with `PrivilegesRequired=lowest`, so
it raises no UAC prompt and needs no admin rights, and it creates a
Desktop shortcut — unlike `settings.exe`, students *are* this program's
audience.

**Before handing it to anyone**, install it on a machine that has never
had sidebyside on it and confirm:

- it installs without prompting for admin,
- the Desktop shortcut opens the viewer,
- with no recordings present it shows the picker saying "No recordings in
  this folder" rather than erroring — a review machine legitimately has
  no `config.json` and no `%PUBLIC%\Documents\sidebyside\sessions`,
- **"Open a recording folder…" finds a session copied from elsewhere**
  (a USB stick, Downloads). This is the path that actually matters on a
  review machine; the default-folder listing will usually be empty there.
- playback, the layout picker and Export all work on that copied session.

Both installers use distinct `AppId`s and install locations, so they
coexist on one machine. Verify that too if you install both: neither
should uninstall or upgrade over the other. **Do not add an explicit
`AppId` to `sidebyside.iss`** — it deliberately leaves it implicit (Inno
derives it from `AppName`), and changing it would stop existing clinic
installs being recognised as upgradable.
