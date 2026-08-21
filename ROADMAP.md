# Roadmap

Forward-looking plans, distinct from `DECISIONS.md` (an append-only log of
decisions already made and built). Entries here describe intent before
implementation — expect them to be revised as reality pushes back, and to
be superseded by real `DECISIONS.md` entries and user-facing docs once
built. Newest at the bottom.

---

## 2026-08-17 — Device compatibility & camera setup system

**Status: complete as of 2026-08-18** — all four phases below are done.
Kept here rather than deleted since it's still the design rationale behind
`config.py`/`settings.py`/`uvc_enumeration.py`; the authoritative record of
what was actually built is `DECISIONS.md`'s dated entries and
`SUPPORTED_HARDWARE.md`, per this file's own "superseded once built" policy
above.

### Context

This project started as a single-room, single-configuration tool for
NECO: two fixed IDS cameras with hardcoded serials, no third-person view.
It now has a third camera (UVC third-person view, see DECISIONS.md) and
the ambition has grown: build this to work across the range of NBEO
practice setups a school might have — different Keeler BIO and
Haag-Streit slit lamp generations, different mounted cameras, a
third-person webcam that varies by whatever's on hand — and potentially
share it (open source) so other institutions can set it up themselves,
without the original developer hand-configuring each machine.

That means "which physical device is the slit lamp / BIO / third-person
camera" can no longer be a source-code constant. This entry plans the
replacement: what hardware is realistically in scope, and how setup,
configuration, and reconfiguration should work so it stays robust,
flexible, and — critically, since a non-developer technician may run
this — intuitive.

### Hardware scope (decided)

- **In scope:** IDS cameras reachable through IDS peak / GenICam — both
  the legacy uEye Transport Layer bridge (older uEye-family sensors, like
  the current slit lamp camera) and native USB3 Vision (current IDS peak
  cameras, like the current BIO camera). `IdsCamera` was already built
  generically against GenICam's standard node/feature model rather than
  per-model, specifically so this would be cheap — see DECISIONS.md's
  "One IdsCamera class for both real cameras" entry. The main *new* work
  this requires is setup/config, not new camera-handling code.
- **Out of scope for now:** cameras that only work through the old,
  pre-GenICam uEye SDK (a different SDK entirely, not a config
  difference) — the answer there is "buy a currently-supported camera,"
  not "support two SDKs." Also out of scope: GigE-connected IDS cameras
  (untested Transport Layer, not expected to be encountered). Both are
  revisitable if real setups turn up that need them.
- **Third-person camera:** any UVC-compliant USB webcam. This is already
  the general case, not a to-do — UVC is itself the generalization
  (`uvc_camera.py` has no ELP-specific code), so "which webcams work" is
  answered by the USB standard, not by this project. The real work is
  discovery/config robustness, covered below.

### The three moments where "which camera is which" gets decided

1. **Physical install.** Camera mounted to instrument, plugged into USB,
   drivers installed (IDS peak + the relevant Transport Layer for IDS
   cameras; UVC webcams need no special driver on Windows). No software
   decision happens at this step.
2. **Role assignment**, via a new persistent tool (`settings.py`, see
   below) — explicitly *not* a one-shot install script. A technician
   assigns "this detected device → this role" and can re-run it any time:
   replacing a camera, adding a webcam that wasn't attached yet at
   install, fixing a wrong guess. Reconfiguring is "run Settings, pick
   from a dropdown, save, restart the kiosk app" — never a reinstall.
   (This is a statement about *role assignment* staying repeatable, not a
   blanket objection to any setup automation — `setup.ps1`, added later,
   scripts the earlier, genuinely one-shot-per-machine step of getting
   Python/dependencies installed, and hands off to `settings.py` for this
   step. See DECISIONS.md's "setup.ps1" entry.)
3. **Runtime resolution.** Every launch (of `app.py` or a `settings.py`
   rescan) re-resolves each configured role against currently-attached
   hardware using a stable identifier — never a remembered index. A
   configured device that isn't currently found is a normal "not ready"
   state with a clear reason shown, the same "loud and early" preflight
   philosophy CLAUDE.md already applies to camera liveness, just extended
   to cover "not configured" and "configured but currently absent."

### `settings.py` (new, top-level — a peer to `app.py`, not `tools/`)

Not filed under `tools/` because it isn't a one-off diagnostic like
`smoke_test_camera.py` — it's a permanent, repeatedly-used part of the
real product, on the same footing as `app.py`/`preview.py`. Kept separate
from `app.py` itself (not a hidden menu inside the kiosk) for the same
reason `preview.py` already is: CLAUDE.md's "Who uses it" is unambiguous
that students see Start/Stop/the instrument picker and nothing else.
Keeping Settings a fully separate program means nothing reachable from
the student-facing window ever needs a "could a student stumble into
this" review.

Deliberately lean, not a wizard:

- One row per role (`slit_lamp`, `bio`, `third_person`). Each row: a
  dropdown of currently-detected candidate devices of the right kind (IDS
  devices for the two instrument roles, UVC devices for third-person), a
  **Preview** button that opens a small live-preview window for the
  highlighted selection so a wrong guess is visible before it's saved,
  and a global **Rescan** (re-enumerates both device kinds — covers a
  webcam plugged in after `settings.py` was already open) and **Save**.
- A role with no currently-matching device shows clearly as
  "‹not connected›" rather than silently keeping a stale entry.
- No simultaneous multi-device preview grid, no multi-step wizard, no
  drag-and-drop. One dropdown + one preview button per role is enough to
  prevent a wrong guess without being a bigger build than it needs to be
  right now.
- Saving writes `config.json` (below). Does **not** attempt to hot-reload
  a running `app.py` — restart the kiosk app to pick up changes. That's a
  deliberate scope line: "don't reinstall to replace a camera" is fully
  satisfied by rerun-Settings-and-restart; hot-reloading config into a
  live kiosk session is unneeded complexity for a rare technician action.

### Identity strategy per camera type

**IDS instrument roles** — serial number, unchanged from today. GenICam
serials are real, stable, manufacturer-assigned identifiers; nothing new
needed beyond moving the two serials out of hardcoded constants
(`app.py`'s `SLIT_LAMP_SERIAL_DEFAULT`/`BIO_SERIAL_DEFAULT`) and into
config. `settings.py`'s dropdown lists live `DeviceManager.Devices()`
output (serial + model name), the same enumeration `ids_camera.py`
already does at open time.

**Third-person (UVC) role** — no reliable per-unit serial can be assumed
across arbitrary consumer webcams. Confirmed on the specific camera
attached to this machine: its Windows `InstanceId` is a location-derived
ID (`6&3C4E0F5&0&0000`-shaped), not a manufacturer serial — though some
UVC cameras do expose a real one via their USB descriptor, worth checking
per-device rather than assuming either way. Plan:

- Store **VID/PID + friendly name** as the configured identifier, not an
  index.
- At resolution time: if exactly one UVC device is currently attached,
  use it — regardless of what's configured. This covers the common case
  (one third-person camera, nothing else UVC on the machine) with zero
  configuration friction, and self-heals automatically if that single
  camera is swapped for a different model (still "the only one," still
  auto-selected) without anyone touching Settings.
- If more than one UVC device is attached, require a configured VID/PID
  match; if the configured one isn't among them, that's the same "not
  ready — check Settings" state as a missing IDS camera, not a guess.
- Explicitly **not** using physical USB port/hub topology as an identity
  mechanism. It was considered (pinning by port survives even a full
  camera replacement in that port), but per direct feedback, ports aren't
  guaranteed stable enough here to build around — identity (VID/PID) plus
  the single-device fallback is the whole strategy.
- **Decided (spike complete, see DECISIONS.md's "UVC device enumeration"
  entry):** `uvc_enumeration.list_uvc_devices()` reads DirectShow's own
  device enumerator directly via `pygrabber`'s internals, returning
  index + friendly name + VID/PID in one pass, in the same order
  `cv2.VideoCapture(i, cv2.CAP_DSHOW)` opens by — no subprocess call, no
  name-matching correlation step. Chosen over shelling out to
  `Get-PnpDevice`/WMI, which would still have needed a separate
  trial-open-by-index correlation pass. `settings.py`'s dropdown reads
  this directly.

### `config.json` (new)

Repo root, gitignored (like `sessions/`) since it's install-specific, not
source — ship a committed `config.example.json` showing the shape, so a
fresh clone has something to copy and a fresh install with no config
fails loudly rather than silently running against someone else's
hardware.

Schema sketch (this was the Phase 1 shape; the shipped schema also gained
an optional `recording` block once `fps` became config-driven — see
`DECISIONS.md`'s "Config-driven recording fps, device-derived canvas
size" entry — and `config.example.json` is the authoritative current
shape, not this sketch):

```json
{
  "instruments": {
    "slit_lamp": { "kind": "ids", "serial": "4103484089", "label": "Slit Lamp" },
    "bio":       { "kind": "ids", "serial": "4110050487", "label": "BIO" }
  },
  "third_person": { "kind": "uvc", "vid_pid": "32E4:9310", "friendly_name": "HD USB Camera" },
  "recording": { "fps": 30 }
}
```

`instruments` is a dict, not a fixed pair, so a room with only one
instrument — or a third — is a config change, not a code change.
`kiosk.KioskController`'s `instruments: dict[str, BaseCamera]` shape
already supports this; only `app.py`'s currently-hardcoded two-key dict
needs to become config-driven. `label` is what the picker button shows,
so an institution can call it "BIO" or "Indirect Ophthalmoscope" or
whatever they're used to without touching source.

### Failure modes to design for explicitly

Extends today's "loud and early" preflight rather than replacing it:

- No `config.json` present → `app.py` refuses to start with a message
  pointing at `settings.py`, instead of silently falling back to a
  hardcoded default.
- A configured role's device isn't currently found → the same
  "waiting for..." status the picker already shows for a camera still
  warming up, with a clearer reason logged and shown (e.g. "slit_lamp: no
  device with serial ...4089 found — check Settings").
- Third-person VID/PID ambiguous (two matching devices attached) →
  explicit refusal to guess, its own distinct message, never silently
  picking the first match.

All of this reuses `kiosk.py`'s existing preflight/state-machine shape —
it's about what feeds it, not a new state machine.

### Documentation deliverables (once built — not yet)

- `SUPPORTED_HARDWARE.md` (new): confirmed-tested camera models (today:
  the two IDS cameras + the ELP third-person camera, each dated and
  cross-referenced to the DECISIONS.md entry that verified it),
  expected-to-work families (anything reachable via IDS peak's uEye TL or
  native USB3 Vision; any UVC-compliant webcam) with the caveat that
  they're untested, explicitly excluded hardware and why, and how to
  report back a newly-tested device.
- `SETUP.md` gets a new section replacing the implied
  "edit these constants" flow: install drivers → run `settings.py` →
  assign roles → launch `app.py`.
- `DECISIONS.md` gets real entries once implementation choices are
  actually made (e.g. whichever UVC-enumeration approach the spike above
  lands on).

### Phasing

1. **Done.** `config.json` + loader — `app.py`/`kiosk.py` read config
   instead of hardcoded constants.
2. **Done.** `settings.py` — one row per role, dropdown + Preview +
   Rescan/Save, writing `config.json`. See DECISIONS.md's 2026-08-18
   entries.
3. **Done, for the UVC leg.** VID/PID + single-device-fallback resolution
   for the third-person role landed alongside `settings.py` rather than as
   a separate later phase, once the enumeration spike made it free — see
   DECISIONS.md. IDS instrument roles were already serial-based; no
   equivalent work was needed there.
4. **Done.** `SETUP.md`'s "Verify the install" section points at
   `settings.py` instead of hand-editing `config.json`; `SUPPORTED_HARDWARE.md`
   exists (confirmed-tested models, expected-to-work families, explicit
   exclusions, and how to report a newly-tested device); the relevant
   `DECISIONS.md` entries exist.

### Explicitly out of scope for now

- Legacy pre-GenICam uEye SDK cameras, GigE-connected cameras.
- Physical USB port/hub topology as an identity mechanism.
- Hot-reloading config into a running `app.py` — restart after
  reconfiguring is an accepted, explicit requirement, not a gap.
- More than one third-person camera, or a variable number of "views"
  beyond the existing instrument + third-person pair — not requested,
  keeps scope matched to the app's actual purpose.

---

## 2026-08-18 — Other camera/recording settings surveyed, not acted on (yet)

Surfaced while reviewing "what settings matter here" alongside the
UVC autofocus/auto-exposure lock (see DECISIONS.md). Recorded so this
isn't re-derived from scratch later; none of these are committed work.

- **Recording quality (`codec`/`crf`/`preset`) is hardcoded in
  `Recorder`'s constructor defaults** (`libx264`, `crf=23`), never wired
  to `config.json`. Same category `fps` was in before it became
  config-driven — a real quality-vs-disk-space tradeoff that could
  reasonably differ by institution's storage budget. Candidate for the
  same `recording` config section `fps` already lives in, if a real need
  shows up (e.g. an institution needs smaller files than 23 gives).
- **White balance is uncorrected on the IDS cameras** — no
  `BalanceWhiteAuto` handling anywhere in `ids_camera.py`. Deliberately
  not planned: the use case is technique review (hand/instrument
  coordination), not color-critical diagnostic imaging, and nobody's
  flagged it as a problem. Revisit only if that changes.
- **Session-length/stall-timeout/disk-margin constants**
  (`TARGET_SESSION_MINUTES`, `DEFAULT_STALL_TIMEOUT_S`,
  `REQUIRED_SPACE_MULTIPLIER` in `kiosk.py`) are already constructor
  parameters with sensible measured defaults, not config. Deliberately
  not moved to `config.json` — no institution has needed a different
  value yet, and adding config for a hypothetical isn't earned (see
  CLAUDE.md's general anti-speculative-config stance).

---

## 2026-08-18 — In-app exposure/gain calibration for cameras with no auto-exposure (planned, not built)

### Context

The 2026-08-12 "Slit lamp camera smoke test" entry in `DECISIONS.md` left
an open item: that camera has no `ExposureAuto`/`GainAuto` at all (unlike
the Keeler, which self-converges every session), so it needs a one-time
manual `ExposureTime`/`Gain` calibration "via IDS peak Cockpit," and
flagged that whatever value the device last had just persists across
power cycles with **no code-level check** if it's ever stale or wrong.
That item was never closed out. Compounding it, `SUPPORTED_HARDWARE.md`
(written this session) pointed at "see SETUP.md" for that calibration
step — SETUP.md has no such section. Both are corrected by this entry:
the plan below supersedes "use Cockpit," so no SETUP.md section for it is
coming; `SUPPORTED_HARDWARE.md` should stop pointing at one.

### The actual finding

Nothing requires an external tool. `ids_camera.py` already imports
`ids_peak` directly and already reads/writes GenICam nodes on
`self._node_map` — that's exactly how `_converge_auto_exposure()`
controls `ExposureAuto`/`GainAuto` today. `ExposureTime`/`Gain` are just
two more nodes on that same map. IDS peak Cockpit is a vendor GUI over
the identical API; there's nothing it can do that this codebase can't do
directly.

### Planned design

- `settings.py`'s `PreviewDialog`, when opened for an instrument row
  whose camera lacks `ExposureAuto`/`GainAuto` (same `IsAvailable()`
  check `_converge_auto_exposure()` already does), shows live
  `ExposureTime`/`Gain` sliders against the real feed instead of just a
  static preview.
- The calibrated values persist in **`config.json`**, per instrument
  role — not device NVRAM. This fits CLAUDE.md's own convention exactly:
  values belong in config *except* where they can be read from the
  device, and a camera with no auto-exposure has nothing to read — there
  is no sensor-side measurement to query. (`InstrumentConfig` would need
  optional `exposure_time_us`/`gain` fields; `config.py`'s loader treats
  them as optional, present only for instruments that need them.)
- `ids_camera.py`'s `_open()` applies them explicitly from config every
  session, for any instrument whose config carries them. This is what
  actually closes the original "Open" item — not by detecting a stale
  device-side value, but by removing the device-side persistence
  dependency entirely. Every session gets whatever `config.json` says,
  deterministically.

### Rejected: a runtime brightness preflight check

Considered as an alternative way to catch a bad calibration — `kiosk.py`
flagging a suspiciously dark frame before allowing Start, extending
CLAUDE.md's "loud and early" preflight philosophy from "is a frame
arriving at all" (binary, unambiguous — the existing `_cameras_ready()`
check) to "is the frame bright enough" (a fuzzy threshold). Rejected:
unlike a missing frame, "too dark" has real false-positive risk — a
legitimately dim scene shouldn't block a student from starting a session.
The config-driven calibration above solves the root cause deterministically
instead of trying to heuristically detect its absence at runtime.

### Status

Not built — explicitly deferred ("at some point," not now). Tracked here
so the design doesn't need to be re-derived when it's picked up.

---

## 2026-08-19 — Can `setup.ps1` drive the IDS peak SDK installer? (licensing question resolved; mechanism not yet chosen)

### Context

DECISIONS.md's "setup.ps1" entry (same date) deliberately stopped short of
scripting the IDS peak SDK install itself — `setup.ps1` only detects
whether the runtime is importable and points the technician at SETUP.md.
That was framed as a technical limit (real driver-install friction no
script removes) rather than also a legal one. This entry is the follow-up
investigation into whether it's *also* a licensing limit — i.e., whether
IDS's EULA permits a script to accept it on a technician's behalf, or
whether that specifically requires a human to interactively click through
the installer's own UI every time. **Resolved (see "The EULA finding"
below): the license doesn't require interactive per-machine acceptance.
Whether to actually build silent driving is still an open implementation
choice, not a licensing blocker anymore.**

### What was tried, and why it didn't settle the question

Two installers were available for inspection
(`ids-peak-win-standard-setup-64-26.06.1.exe` and
`ids-peak-win-extended-setup-64-26.06.1.exe`, v26.6.1, both InstallShield
bootstrappers bundling several per-driver sub-installers/sub-MSIs):

- Binary string-scanning the outer `.exe` for EULA text found only
  garbled matches — InstallShield's bootstrapper resources aren't stored
  as plaintext. It did confirm the installer supports InstallShield's
  standard silent-install pattern: run once with `/r` to record a
  response file, then replay non-interactively via `/s -f1<path>`. That
  mechanism works technically; whether it's *allowed* by the license is
  the unanswered part.
- Windows Installer's COM API (`WindowsInstaller.Installer`,
  `OpenDatabase` in read-only mode — no execution, no UI, doesn't touch
  the installed product) can read an MSI's embedded license text directly
  from its `Control` table. This worked against the cached sub-MSI for
  "IDS peak common" (found via `HKLM:\SOFTWARE\Microsoft\Windows\
  CurrentVersion\Installer\UserData\S-1-5-18\Products\*\InstallProperties\
  LocalPackage` → `C:\WINDOWS\Installer\<hash>.msi`) — but the license
  text it returned was literal **Lorem ipsum placeholder RTF**, not real
  terms. That sub-package is WiX-built (a different toolset than the
  InstallShield outer wrapper) and is almost certainly not where the real
  terms live.
- The other cached sub-installers (`eth_installer_64.exe`,
  `usb_installer_64.exe`, `icv_setup_x64.exe`, `libcommon_setup_x64.msi`,
  under `C:\Program Files (x86)\InstallShield Installation
  Information\{8515B45A-...}\`) were never checked the same way — the
  driver installers specifically are the ones a redistribution question
  would actually turn on, and remain unexamined.
- An administrative extract (`/a`) of the outer bootstrapper, meant to
  unpack files without installing/registering anything, failed silently
  (empty output directory, no error, no process left running, nothing
  changed on the test machine) — most likely wrong switch syntax for
  InstallShield's specific `/a` handling, not confirmed as a dead end.

### The runtime setup package, and the EULA it surfaced

IDS publishes a separate **"IDS peak runtime setup"** package — ~118MB
(v26.6.1) vs. the standard installer's ~262MB, drivers-only (no dev
environment, no Cockpit, no DirectShow interface), the more plausible
candidate for something meant for OEM/third-party redistribution as
opposed to the developer/technician-facing standard/extended installers.
Downloaded from
[the official download page](https://en.ids-imaging.com/download-peak.html)
(direct `WebFetch` of that page is bot-blocked — a human has to click
through it) as `ids-peak-win-runtime-setup-64-26.06.1.exe`. Clicking
"Download" on that page also surfaces a separate document — the actual
license terms, `ids-license-terms-de-en.pdf`, distinct from anything
embedded in any of the three installer `.exe`s and the real answer to
the question this entry opened with.

### The EULA finding

"License Terms for IDS Software Suite und/and IDS peak" (last revised
2020-07-20, bilingual German/English, German controlling per clause 7.3).
The clauses that actually settle this:

- **Redistribution is explicitly granted, not just tolerated** (clause
  2.1/2.1.1): a "non-exclusive, non-transferable worldwide right to...
  integrate part or all of the Software in [Licensee's] own products
  *only if they operate with IDS cameras*" and to "duplicate or
  reproduce... and distribute these products to end users or third
  parties." sidebyside qualifies directly — it only ever operates with
  IDS cameras.
- **No clause anywhere requires interactive, per-installation
  acceptance.** Clause 5.1 ties the license's effective date to
  "successful download of the Software, not later than the commencement
  of use" — a download/use-triggered agreement, not a
  click-through-this-specific-dialog one. This is what actually answers
  the original question: nothing here blocks `setup.ps1` from replaying
  a previously-recorded response file (`/s -f1<path>`, found earlier)
  non-interactively on a new machine.
- **What's actually prohibited** (clause 2.3): reselling/renting the
  Software standalone (2.3.1 — not what we'd be doing), decompiling it
  (2.3.2), using it with non-IDS cameras (2.3.3 — CLAUDE.md's hardware
  table already only ever names IDS cameras for the two instrument
  roles), building a competing "comparable control software" (2.3.4), and
  sublicensing beyond direct subcontractors (2.3.5). None of these bear
  on how the installer gets invoked.
- Scope caveat: this document is titled for "IDS Software Suite and IDS
  peak" generally — treated here as governing the runtime-setup package
  specifically because it's literally what IDS's own download flow
  surfaced when that package was requested, not because every word was
  independently confirmed to be package-specific.

### Mechanism: still an open implementation choice, no longer a licensing one

With the licensing question resolved, both previously-considered
mechanisms are viable and the choice is now pure engineering tradeoff:

- **Silent replay** (`/r` once to record a response file, `/s -f1<path>`
  to replay non-interactively on future machines) — fully unattended,
  but the technician never sees a native "did this actually succeed"
  signal beyond `setup.ps1`'s own exit code / post-hoc
  `ids_peak.Library.Initialize()` check.
- **Interactive launch** (`setup.ps1` starts the installer with no
  silent flags and waits on it) — the technician drives IDS's own wizard
  directly, so success/failure is self-evident from that UI, at the cost
  of not being hands-off.

Still open regardless of which is chosen: how the script locates the
installer `.exe`, since CLAUDE.md already rules out bundling it in the
repo (kernel drivers; also just too large) — prompt for a path each
time, look in a documented fixed location with a prompt fallback, or
leave this step manual and unscripted as it is today.

### Also clarified along the way

`pip install -r requirements-ids.txt` only ever installs the
`ids_peak`/`ids_peak_ipl` **Python bindings** — a thin wrapper calling
into the native SDK. It never installs the native runtime or kernel
drivers (USB3 Vision, GenICam TL, uEye TL); nothing on PyPI can. A
machine that only ever runs `SyntheticCamera` genuinely never needs the
real installer at all — already implicitly true of `setup.ps1`'s
`-DriveIds No` path, which skips this whole question.

### Status

Licensing question resolved: the EULA permits redistribution and doesn't
require interactive per-machine acceptance. No code changed yet — the
remaining decision is purely which mechanism (silent replay vs.
interactive launch) and how the script locates the installer `.exe`.
Tracked here so the next session doesn't have to re-derive what's already
been settled before making that call.
