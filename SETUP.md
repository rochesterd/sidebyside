# SETUP.md

**This is the developer/source-checkout setup procedure** — building
sidebyside from source to work on it. If you're setting up a clinic
machine to actually run the app, you want the distributable installer a
developer builds via `PACKAGING.md`, not this file — see ROADMAP.md's
"Distribute a frozen-exe installer, not a Python source bootstrap" entry
for why those are two different audiences now.

One-time setup per (dev) machine, from bare Windows to both cameras
enumerating. Procedural — follow the steps in order. For *why* any of
this is the way it is, see `CLAUDE.md` and `DECISIONS.md`; this doc
doesn't repeat that reasoning, only points at it.

**Shortcut:** `setup.ps1` (repo root) scripts Section 1 and the
`requirements-ids.txt` half of Section 2 — run it first, then read
Sections 2-3 below only if it tells you the IDS peak SDK runtime still
needs installing. It's safe to re-run. It does not replace Section 6
(`settings.py`), which stays a separate, always-manual step.

Prefer a window over a terminal prompt? `python setup_wizard.py` (works
with the system Python — it doesn't need the venv to exist yet) is the
same script behind a paged GUI: Welcome → a live-streamed run of
`setup.ps1` → a finish page with a button to launch `settings.py`
directly.

---

## 1. Python and the virtual environment

1. Install **Python 3.13** (get the Windows installer from python.org). Not
   3.11 — see the "Python 3.13, not 3.11" entry in `DECISIONS.md` if you're
   tempted to match an old wheel tag; 3.13 works fine with those wheels.
2. From the repo root, create the venv:
   ```powershell
   python -m venv .venv
   ```
3. Activate it (PowerShell):
   ```powershell
   .venv\Scripts\Activate.ps1
   ```
   If PowerShell refuses to run the script (execution policy), run this
   once first, then retry activation:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
   ```
4. Install the project's Python dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```

At this point `python app.py` will run against `SyntheticCamera` — you
don't need any hardware or the rest of this document to develop against
synthetic cameras. The rest of this file is only for getting the two real
cameras working.

---

## 2. IDS peak SDK (required for both cameras)

The Keeler Vantage Plus (`U3-327xCP-C`) is native USB3 Vision / GenICam and
needs nothing beyond this section. The slit lamp camera needs one more
consideration covered in Section 3 — read it before you install, since it
affects which installer variant you pick in step 2 below.

1. Create a free myIDS account at ids-imaging.com if you don't have one.
2. Download the **IDS peak** Windows installer and run it. IDS ships three
   setup variants — standard, runtime, and extended:
   - If this machine will drive the slit lamp camera (`UI-3250CP-C-HQ`, a
     uEye-family model), choose the **extended** setup — it bundles the
     uEye camera drivers that model needs. Read Section 3 first if this
     machine already has IDS Software Suite installed.
   - Otherwise (Keeler only), **standard** is enough.
   - Whichever variant, choose **Custom** during setup and enable the
     **uEye Transport Layer** component. If you're not sure which machine
     this is, enable it anyway; it's a no-op for the Keeler.
3. Install the Python bindings from PyPI, pinned to exact versions so they
   stay matched to the installed SDK runtime. This is a separate
   requirements file from the rest of the project's dependencies (already
   installed in Section 1) so that machines without the IDS peak runtime
   can still install everything else — see `CLAUDE.md`'s Environment
   section and `DECISIONS.md`'s 2026-08-12 entry for why pinning, not a
   local install, is what enforces the version match now:
   ```powershell
   python -m pip install -r requirements-ids.txt
   ```

---

## 3. Slit lamp camera only: uEye camera drivers

The Haag-Streit BI 900's camera (`UI-3250CP-C-HQ Rev. 2`) is a legacy
uEye-family camera, not native USB3 Vision, so it needs uEye camera
drivers the Keeler doesn't. Skip this whole section on a machine that only
drives the Keeler. There are two ways to get the drivers — pick one before
you install Section 2:

- **Extended setup** (Section 2, step 2): bundles the uEye camera drivers
  directly, so no separate install is needed. If you're choosing this,
  nothing else in this section applies. **Watch for:** running the
  extended setup automatically **uninstalls an existing IDS Software
  Suite** on that machine, if one is present — don't install the Suite
  first and then switch to extended expecting to keep both.
- **Standard/runtime setup + IDS Software Suite**: if you installed
  standard or runtime instead, install **IDS Software Suite, version 4.94
  or later**, in addition to IDS peak. uEye camera drivers aren't bundled
  with standard/runtime IDS peak — without the Suite, the `UI-` camera
  never shows up at all, regardless of Section 2.

Either way, **the uEye Transport Layer** (Section 2, step 2 — Custom
setup) is what makes a `UI-` model camera appear on the same GenICam
interface IDS peak otherwise reserves for native USB3 Vision devices like
the Keeler. If you already ran the default (non-Custom) installer, re-run
the IDS peak installer, choose Modify, and enable the component there.

**Watch for:** the uEye Transport Layer only exposes a basic feature set —
freerun/triggered acquisition, exposure, pixel clock. If the camera module
for this camera ever needs something beyond that (advanced trigger modes,
certain GenICam features the Keeler's native path exposes fine), that's
the first thing to suspect, not a bug in this codebase.

The Keeler needs none of this section — it's native to IDS peak already.

---

## 4. Verify the install

`ids_camera.list_ids_devices()` and `settings.py` (Section 6 below) are
the normal way to see what's attached once the app itself works. Before
that, to check the SDK/runtime install in isolation, run:

```powershell
python tools\check_ids.py
```

(a standalone script — imports `ids_peak` directly, nothing from this
project's camera modules) for two different checks depending on the
machine:

**Check 1 — bindings/runtime match, any machine:** the script should run
to completion without raising. On a development machine with no cameras
attached, **0 devices found is the expected, correct result** — it means
the bindings loaded and matched the installed runtime, not that anything
is broken. Per `CLAUDE.md`'s Environment section, that's the normal state
for a dev box working against `SyntheticCamera`.

**Check 2 — camera enumeration, machine with hardware attached only:**
expect exactly 2 devices, the `UI-3250CP-C-HQ` and the `U3-327xCP-C`, each
with a serial number printed. (If `tools\check_ids.py` errors on a method
name, check the locally-installed API docs under Start Menu → IDS → IDS
peak — they're versioned with the SDK, unlike anything on the web.)

**Confirm by serial number, not by list position or count.** `CLAUDE.md`'s
Architecture section is explicit about this: cameras must be identified by
serial number, never by device index — index order changes across reboots
and USB port changes, and that's the most common way a setup like this
silently swaps which camera is "camera_a" and which is "camera_b."

---

## 5. Troubleshooting: camera enumerates but the frame rate looks wrong

If both cameras show up in Section 4 but frames arrive slower than
expected once you're actually capturing, **this is not a driver
problem.** Per `CLAUDE.md`'s Hardware section: USB3 Vision degrades by
silently dropping frames rather than raising an error, and the two
cameras together need roughly 300 MB/s at full resolution and frame rate,
against a realistic 350-400 MB/s ceiling per USB 3.0 host controller. A
low framerate with no errors anywhere is exactly what saturating that
ceiling looks like.

Before reinstalling anything:

- Put the two cameras on **separate host controllers** if the machine has
  more than one (separate physical USB 3.0 ports that trace back to
  different controllers on the motherboard, not just separate ports on a
  single hub or controller).
- Treat **measured throughput as authoritative over datasheet numbers** —
  the datasheet ceiling assumes ideal conditions this host setup may not
  meet.
- Target 30fps rather than each camera's native ~58-60fps ceiling; that's
  the number the bandwidth math in `CLAUDE.md` was done against.

---

## 6. Assign roles with settings.py

Once the SDK/driver checks above pass, run:

```
python settings.py
```

Each row (Slit Lamp, BIO, Third-Person) shows a dropdown of currently-
detected candidates. Pick the right device for each role, give the two
instrument rows a label (what the picker button on the kiosk screen will
say), and use **Preview** on a highlighted selection if you're not sure
which physical camera it is. **Rescan** re-checks what's attached (e.g.
after plugging in a camera that wasn't connected yet); **Save** writes
`config.json`. `app.py` refuses to start with a clear error if
`config.json` is missing or malformed — see `config.py`.

Re-run `settings.py` any time a camera is replaced or a setting needs to
change — it's a normal, repeatable tool, not a one-shot installer.
Restart `app.py` afterward; Save does not hot-reload a running kiosk
session.
