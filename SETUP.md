# SETUP.md

One-time setup per machine, from bare Windows to both cameras enumerating.
Procedural — follow the steps in order. For *why* any of this is the way
it is, see `CLAUDE.md` and `DECISIONS.md`; this doc doesn't repeat that
reasoning, only points at it.

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
section after this (Section 3) — install this section first regardless.

1. Create a free myIDS account at ids-imaging.com if you don't have one.
2. Download the **IDS peak** Windows installer and run it.
   - If this machine will drive the slit lamp camera, choose **Custom**
     setup instead of the default, and enable the **uEye Transport Layer**
     component while you're in there — see Section 3. If you're not sure
     which machine this is, enable it anyway; it's a no-op for the Keeler.
3. The Python bindings are **not on PyPI**. Do not `pip install ids_peak` or
   `pip install ids_peak_ipl` from the internet — the binding version must
   match the installed SDK runtime exactly, and PyPI won't guarantee that.
   (See `CLAUDE.md`'s Environment section.)
4. After installing IDS peak, the correct wheels are sitting locally at:
   ```
   ids_peak:      C:\Program Files\IDS\ids_peak\generic_sdk\api\binding\python\wheel\x86_64
   ids_peak_ipl:  C:\Program Files\IDS\ids_peak\generic_sdk\ipl\binding\python\wheel\x86_64
   ```
   List each folder to see the actual filenames — they encode version and
   platform tags that change between SDK releases, so don't hardcode a
   version number:
   ```powershell
   dir "C:\Program Files\IDS\ids_peak\generic_sdk\api\binding\python\wheel\x86_64"
   dir "C:\Program Files\IDS\ids_peak\generic_sdk\ipl\binding\python\wheel\x86_64"
   ```
5. With `.venv` still activated, install both wheels using the filenames
   you just saw (substitute them in below — these are examples, not real
   filenames):
   ```powershell
   python -m pip install "C:\Program Files\IDS\ids_peak\generic_sdk\api\binding\python\wheel\x86_64\ids_peak-<version>-cp313-*.whl"
   python -m pip install "C:\Program Files\IDS\ids_peak\generic_sdk\ipl\binding\python\wheel\x86_64\ids_peak_ipl-<version>-cp313-*.whl"
   ```

---

## 3. Slit lamp camera only: uEye Transport Layer

The Haag-Streit BI 900's camera (`UI-3250CP-C-HQ Rev. 2`) is a legacy
uEye-family camera, not native USB3 Vision. It needs two extra things the
Keeler doesn't. Skip this whole section on a machine that only drives the
Keeler.

1. **IDS Software Suite, version 4.95 or later**, installed *in addition
   to* IDS peak. uEye camera drivers are not bundled with IDS peak itself —
   without this, the `UI-` camera never shows up at all, regardless of
   Section 2.
2. **The uEye Transport Layer**, enabled as part of the IDS peak install
   (Section 2, step 2 — Custom setup). This is what makes a `UI-` model
   camera appear on the same GenICam interface IDS peak otherwise reserves
   for native USB3 Vision devices like the Keeler. If you already ran the
   default (non-Custom) installer, re-run the IDS peak installer, choose
   Modify, and enable the component there.

**Watch for:** the uEye Transport Layer only exposes a basic feature set —
freerun/triggered acquisition, exposure, pixel clock. If the camera module
for this camera ever needs something beyond that (advanced trigger modes,
certain GenICam features the Keeler's native path exposes fine), that's
the first thing to suspect, not a bug in this codebase.

The Keeler needs none of this section — it's native to IDS peak already.

---

## 4. Verify both cameras enumerate

There's no camera-listing helper in this codebase yet (only `camera.py`'s
`BaseCamera` interface and `SyntheticCamera` exist so far — no IDS-backed
implementation). Confirm the SDK sees both devices with a standalone
script before wiring anything into the app:

```python
from ids_peak import ids_peak

ids_peak.Library.Initialize()
try:
    device_manager = ids_peak.DeviceManager.Instance()
    device_manager.Update()

    devices = device_manager.Devices()
    print(f"{len(devices)} device(s) found:")
    for descriptor in devices:
        print(f"  {descriptor.ModelName()}  serial={descriptor.SerialNumber()}")
finally:
    ids_peak.Library.Close()
```

Expect exactly 2 devices: the `UI-3250CP-C-HQ` and the `U3-327xCP-C`, each
with a serial number printed. (If method names above don't match what's
installed, check the locally-installed API docs under Start Menu → IDS →
IDS peak — they're versioned with the SDK, unlike anything on the web.)

**Confirm by serial number, not by list position or count.** `CLAUDE.md`'s
Architecture section is explicit about this: cameras must be identified by
serial number, never by device index — index order changes across reboots
and USB port changes, and that's the most common way a setup like this
silently swaps which camera is "camera_a" and which is "camera_b." Note
which physical camera has which serial *now*, while you can see both
labels on screen, so a future camera module's config can pin them
correctly instead of guessing by enumeration order.

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
