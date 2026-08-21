# PACKAGING.md

How to build the distributable clinic-machine installer. This is a
**developer-only** procedure — a technician setting up a clinic machine
never does any of this, they just run the `.exe` this produces. See
CLAUDE.md's "Who uses it" and ROADMAP.md's "Distribute a frozen-exe
installer, not a Python source bootstrap" entry for why this exists.

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

## 2. Freeze `app.py` and `settings.py`

```powershell
python -m PyInstaller --distpath packaging\dist --workpath packaging\build packaging\app.spec
python -m PyInstaller --distpath packaging\dist --workpath packaging\build packaging\settings.spec
```

Both specs are checked into git (`packaging/*.spec`) — this isn't a
from-scratch step, just replaying a known-working build. See
`DECISIONS.md`'s entry on this for what was actually verified and why
no hidden-imports/`--collect-all` overrides were needed. If a future IDS
peak SDK or dependency version bump breaks the build, start by reading
the PyInstaller warnings file it writes to `packaging/build/<name>/
warn-<name>.txt`.

**Verify before continuing**, not just "did it build without error":
run `packaging\dist\app\app.exe --synthetic` and
`packaging\dist\settings\settings.exe` directly and confirm both
actually open (no import/DLL errors) before handing them to Inno Setup.

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

## 4. Compile the installer

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\sidebyside.iss
```

Produces `packaging\installer_output\sidebyside-setup.exe` — this is the
one file a technician actually needs. Gitignored, like the rest of
`packaging/`'s generated output (`packaging/build/`, `packaging/dist/`).

**Before handing this to anyone**, actually run it on a real (or
disposable/VM) Windows machine and confirm: both shortcuts appear and
launch their respective `.exe`s, the IDS peak installer's own wizard
launches and completes, and — on a machine that already has IDS peak
installed — re-running `sidebyside-setup.exe` skips that step
(`IdsPeakAlreadyInstalled` in `packaging/sidebyside.iss`'s `[Code]`
section).
