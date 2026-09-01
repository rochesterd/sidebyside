# Supported Hardware

What's actually been run against this codebase, what's expected to work
but hasn't been tried, and what's explicitly out of scope. CLAUDE.md's
Hardware table describes *this kiosk's* current fixed cameras; this file
is broader — it's the reference for "will camera X work here" at a new
institution. See ROADMAP.md's "Device compatibility & camera setup
system" entry for the project this supports.

## Confirmed tested

| Camera | Interface | Tested | Notes |
|---|---|---|---|
| IDS UI-3250CP-C-HQ Rev. 2 (serial 4103484089), mounted on a Haag-Streit BI 900 slit lamp | uEye Transport Layer | 2026-08-12 — DECISIONS.md "Slit lamp camera smoke test" | No `ExposureAuto`/`GainAuto` on this camera — needs a one-time manual exposure/gain calibration once mounted on the instrument, done in-app via `settings.py`'s Preview dialog (see ROADMAP.md's "In-app exposure/gain calibration" entry). Gain ceiling 4.0x, well below the Keeler's ~25x. |
| IDS U3-327xCP-C (serial 4110050487), mounted on a Keeler Vantage Plus Digital BIO | Native USB3 Vision | 2026-08-12 — DECISIONS.md "Hardware smoke test found two real IdsCamera bugs; both fixed"; 2026-09-01 — rotation preset | Auto-exposure/gain converges once at open (`ExposureAuto`/`GainAuto` = `Once`), ~10 frames / ~0.5s, then locks for the session. Camera is mounted upside down in the headset — `device_presets.py` matches model `U3-327xCP-C` and applies a 180° rotation automatically (see DECISIONS.md's "Device-model rotation presets" entry). |
| "HD USB Camera" (VID 32E4:PID 9310), presumed ELP-USB100W03M-L21 | UVC (`cv2.VideoCapture`, `CAP_DSHOW`) | 2026-08-17 — DECISIONS.md "UvcCamera hardware-verified against the real ELP camera" | Model identity inferred from VID/PID + physical inspection, not an on-device product-string match — treat as "an ELP-USB100W03M-L21 or equivalent," not a confirmed exact model. Time to first frame 0.9-1.6s, slower and less consistent than the IDS cameras' convergence. **Autofocus/auto-exposure lock (2026-08-18) is implemented but unverified against this device** — the camera was unplugged when it was added; see DECISIONS.md's "Lock UVC autofocus/auto-exposure after a warmup window" entry for what specifically still needs confirming. |
| NET GmbH "KS722OUP" board camera (eMPIA EM2860 bridge, VID 0x20F1:PID 0x0004), mounted on an older-model Keeler Vantage Plus Digital BIO, selectable via `kind: "net2860"` on the `bio` instrument role | Vendor DirectShow filter instantiated by CLSID, driven from a 32-bit helper subprocess (`net2860_camera.py`/`net2860_helper.py`) | 2026-08-26 — DECISIONS.md "`Net2860Camera`: 32-bit helper process for the older Vantage Plus BIO" (build) and its follow-up entry (wiring + a real end-to-end recording) | Wired into `config.py`/`app.py`/`settings.py` and verified with a real recorded session (`KioskController`, real camera, real composite.mp4) — usable today from a source checkout with `.venv32/` set up (`setup_net2860_helper.ps1`). **Not packaged**: `net2860_helper.py` isn't frozen into an exe, so a clinic install built from `PACKAGING.md`'s current process still can't use it (needs a 32-bit Python present, which the frozen-exe distribution doesn't provide). |

**Bandwidth caveat:** DECISIONS.md's 2026-08-12 dual-camera test confirmed
**zero** device-level frame drops with both IDS cameras streaming
simultaneously at their native (uncalibrated, sub-30fps) rates — the only
losses observed were this process's own capture-queue evictions, not USB
saturation or a device-level drop. The actual worst case CLAUDE.md's
Hardware section describes (both cameras at full resolution *and*
30fps simultaneously) has **not** been directly measured — that's blocked
on the slit lamp's exposure/gain calibration (above) bringing its
framerate up to target first. Treat 30fps-simultaneous as expected to
work per the datasheet math, not yet independently confirmed under load.

## Expected to work, untested

- **Any IDS peak / GenICam camera via native USB3 Vision** — same class as
  the Keeler above. `ids_camera.py`'s `IdsCamera` is built generically
  against GenICam's standard node/feature model rather than per-model
  (see DECISIONS.md's "One IdsCamera class for both real cameras" entry),
  specifically so this would be cheap.
- **Any IDS peak / GenICam camera via the legacy uEye Transport Layer
  bridge** — same class as the slit lamp above. Requires the uEye
  Transport Layer installed separately (SETUP.md Section 3); that
  transport layer only exposes a basic GenICam feature set (freerun/
  triggered acquisition, exposure, pixel clock) — a camera needing
  something beyond that may hit a gap `IdsCamera` doesn't handle.
- **Any UVC-compliant USB webcam**, for the third-person role.
  `uvc_camera.py` has no ELP-specific code — UVC is itself the
  generalization, and resolution is queried at runtime rather than
  assumed. `uvc_enumeration.py`'s VID/PID identification works the same
  way regardless of make/model.

## Explicitly excluded

- **Cameras that only work through the old, pre-GenICam uEye SDK.** A
  different SDK entirely, not a config difference — the uEye Transport
  Layer above is what makes an *older uEye-family sensor* reachable
  through modern IDS peak/GenICam. A camera with no GenICam path at all
  needs the legacy SDK, which this codebase doesn't implement. "Buy a
  currently-supported camera" is the answer here, not "support two SDKs."
- **GigE-connected IDS cameras.** Untested Transport Layer, not expected
  to be encountered in a single-host-PC setup like this one.

Both exclusions are revisitable if a real setup turns up that needs them
— see ROADMAP.md's "Hardware scope" section.

## Reporting a newly-tested device

1. Confirm identity: for an IDS camera, the serial + `ModelName()` from
   `ids_camera.list_ids_devices()` (or `settings.py`'s dropdown). For a
   UVC camera, the friendly name + VID:PID from
   `uvc_enumeration.list_uvc_devices()` (or `settings.py`'s dropdown).
2. Add a dated entry to `DECISIONS.md` describing what was tested and
   found — what worked, what didn't, any limitation (like the slit lamp's
   fixed exposure or the ELP's warm-up latency above). This file stays a
   thin index into those entries, not a duplicate of them.
3. Add a row to "Confirmed tested" above, cross-referencing that entry —
   this is how the three cameras already listed got here.
