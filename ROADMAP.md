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
  `BalanceWhiteAuto` handling anywhere in `ids_camera.py`. Originally
  deliberately not planned here (the use case is technique review, not
  color-critical diagnostic imaging), but revisited and actually planned as
  part of a broader camera-configuration pass — see the 2026-08-26 entry
  below.
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

### Calibration UX (added 2026-08-25, still not built): auto-calibrate
### assist, not sliders alone

Worked through against the actual constraints this feature has to fit,
before writing any code:

- **Who does this and how skilled are they.** Never a student (Settings
  is technician-only, per CLAUDE.md's "Who uses it"). The technician
  isn't necessarily a machine-vision or photography person either —
  today's process (IDS peak Cockpit) only assumes someone comfortable
  dragging a slider and judging a live image by eye. That's the right
  skill level to design for, not photometry knowledge, and not "no
  knowledge at all" either.
- **No physical tools available or needed.** No light meter, no
  reference chart — whatever the calibration process measures has to
  come from the image itself, in software.
- **Lighting is instrument-controlled, not ambient.** The slit lamp
  supplies its own illumination; once positioned, sensor brightness is
  mostly a function of the instrument's own lamp intensity, not the
  room. That determinism is exactly what makes a *software* auto-
  calibrate viable at all — the scene isn't something that needs
  re-tuning per session or per room, just once per instrument/camera
  pairing.
- **Why sliders alone (the originally planned design, above) aren't
  enough on their own.** They reduce to the same "eyeball it and hope"
  process Cockpit already was — no reproducibility between technicians
  or installs, and no way to verify a slider-guess was actually good,
  which is the same gap this whole entry exists to close. Pure manual
  entry of raw `ExposureTime`/`Gain` numbers is worse still — too
  technical for the target skill level, useful only as a readout of
  current values, not as the primary interaction.

**Resulting design direction:** a one-shot "auto-calibrate" button
alongside the sliders, not instead of them.

- Auto-calibrate is a *software* algorithm this codebase runs once when
  the technician clicks it — not the hardware's own `ExposureAuto`/
  `GainAuto` (which this camera doesn't have) and not a continuous loop
  running during real recording. Capture a handful of live frames,
  compute the image's **median** brightness (robust against a bright
  reflection or dark surround skewing a plain mean), compare to a target
  brightness, adjust `ExposureTime`/`Gain` proportionally, repeat for a
  bounded number of iterations, stop.
- **Raise `ExposureTime` before `Gain`** when more brightness is needed,
  falling back to `Gain` only once exposure time is maxed or
  impractically slow. Gain amplifies sensor noise, exposure time
  doesn't — worth the explicit priority since this footage gets reviewed
  by students studying their own technique, where noise costs more than
  it would in a typical machine-vision application.
- Sliders stay as the override/fallback for whatever the algorithm gets
  wrong (a hot reflection off a metal instrument part, an unusual scene)
  — auto-calibrate proposes a starting point, the technician can still
  nudge it watching the live preview, not an either/or.

None of this changes the "Planned design" section above (sliders in
`PreviewDialog`, values persisted in `config.json`, applied by
`ids_camera.py`'s `_open()` every session) — it's specifically about
*how the technician arrives at good values* in the first place, which
that section left as "eyeball it," same as Cockpit did.

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

**Built 2026-08-25** — see DECISIONS.md's "Built the exposure/gain
calibration feature" entry for what actually landed, including the
`exposure_calibration.py` split (for unit-testability without the IDS peak
SDK) and the one part of this that's still unverified: the
`ExposureTime`/`Gain` node accessor names, since this dev machine has no
`ids_peak` installed to check them against `vendor/ids_peak_api.txt` or
real hardware. Confirming those, the same way `_converge_auto_exposure()`
already was via `tools/smoke_test_camera.py`, is the one remaining step
before trusting this in a real session.

---

## 2026-08-19 — Can `setup.ps1` drive the IDS peak SDK installer? (resolved: stays manual)

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

### Decision

**Stays manual — `setup.ps1` doesn't launch the IDS installer at all.**
Weighing interactive-launch against silent-replay (see table of
tradeoffs above), the deciding factor was CLAUDE.md's project-wide
"failures must be loud and early" principle: silent replay's worst
failure mode is a response file drifting out of sync with a newer IDS
installer version and silently installing the wrong feature set (e.g.
missing the uEye Transport Layer) with no error at all — exactly the
class of failure this project designs against everywhere else.
Interactive launch avoids that, but at that point it isn't saving the
technician anything `setup.ps1` doesn't already do today (detect the
runtime, point at SETUP.md) — finding and double-clicking the installer
themselves is not meaningfully more work than watching a script launch
it for them. So no new code: `setup.ps1`'s existing detect-only behavior
*is* the decision, not a placeholder for one. This also makes "how the
script locates the installer `.exe`" moot — it never needs to.

This thread also raised a related question — extended setup vs.
standard/runtime + a separately-installed IDS Software Suite, for
machines driving the slit lamp — that turned out to already be answered,
in `SETUP.md` Sections 2-3, written before this thread: extended is
already the recommended path (bundles the uEye camera drivers directly,
no separate Suite needed, and avoids a real footgun where extended setup
silently uninstalls an existing Suite). Nothing needed to change there;
this entry just confirms it's still the right call rather than
duplicating it.

### Also clarified along the way

`pip install -r requirements-ids.txt` only ever installs the
`ids_peak`/`ids_peak_ipl` **Python bindings** — a thin wrapper calling
into the native SDK. It never installs the native runtime or kernel
drivers (USB3 Vision, GenICam TL, uEye TL); nothing on PyPI can. A
machine that only ever runs `SyntheticCamera` genuinely never needs the
real installer at all — though as of 2026-08-20, `setup.ps1` no longer
asks and just always installs the bindings, on the assumption that any
machine running it is a real deployment (see DECISIONS.md's "Removed the
'will this machine drive real IDS cameras?' question" entry); a
dev-only machine simply doesn't run `setup.ps1`.

### Status

Resolved. Licensing was never actually the blocker (the EULA permits
redistribution and doesn't require interactive per-machine acceptance);
the real deciding factor was CLAUDE.md's failure-mode philosophy, which
favors keeping this step manual. No code changed — `setup.ps1` and
`SETUP.md` already reflect the decision as they stand.

---

## 2026-08-20 — Distribute a frozen-exe installer, not a Python source bootstrap (built)

### Context

Everything built so far in this area (`setup.ps1`, `setup_wizard.py`)
assumes the target machine ends up with a full Python environment: a
`.venv`, `pip install`ed dependencies, and `app.py` run via
`python app.py`. That was never actually questioned until now — and it
doesn't hold up against CLAUDE.md's own bar for the student-facing side
("intuitive to Apple's standard"). There is, in fact, **no documented way
a student launches the app at all** today — the only instruction anywhere
is `python app.py` from a terminal (`SETUP.md` line 45). That's a
dev-workflow artifact that leaked into being the only launch path,
not a designed one.

The actual goal, stated directly: hand a technician one installer, they
follow a guide, they end up with a running app — no Python, no source
code, no terminal, ever touched by them.

### Planned design

- **Freeze `app.py` and `settings.py` into standalone `.exe`s via
  PyInstaller**, built on the developer's machine (which already has the
  full venv — base deps plus `ids_peak`/`ids_peak_ipl`). PyInstaller
  bundles whatever's importable in that venv, so the frozen exes carry
  the IDS Python bindings automatically — no `pip install` of anything on
  the target machine, ever.
- **`config.json` relocates off the source tree.** There's no repo
  checkout on a target machine in this model, so `config.py`'s path
  resolution needs a real home for it — likely
  `%ProgramData%\sidebyside\config.json`. Not yet implemented; existing
  `load_config()`/`config.example.json` behavior otherwise unaffected.
- **Inno Setup builds the actual installer** (chosen over WiX Toolset —
  lower learning curve, single `.exe` output, matches this project's
  scale). Its job: copy `app.exe`/`settings.exe` to Program Files, create
  a desktop/Start-menu shortcut for `app.exe` (this is what actually
  closes the "how does a student launch this" gap above — nothing today
  does), and chain-launch the bundled IDS peak installer (see below).
- **The IDS peak *extended* setup installer is bundled inside the Inno
  Setup installer** as an embedded payload, launched via Inno's `[Run]`
  section — **interactively, no silent flags**, waiting for it to exit
  before continuing. This is bundling for convenience, not a reopening of
  the "should this be silently driven" question closed in the entry
  above — the technician still sees IDS's real wizard and clicks through
  it themselves; only the "go find and download it yourself" step goes
  away. `SETUP.md`'s current step 1 (create a myIDS account, download the
  installer from IDS's site) becomes unnecessary for anyone using this
  combined installer.
- **`setup.ps1`/`setup_wizard.py` become developer-only tooling.** Not
  removed — still exactly what someone modifying source code needs to set
  up a dev environment — but no longer part of the path a clinic machine
  ever goes through.

### Decision: bundle extended setup, not IDS Software Suite + runtime setup

Considered instead: bundling the smaller `runtime setup` package
alongside a separately-bundled `IDS Software Suite` installer (the
combination `SETUP.md` Section 3 already documents as the non-extended
route to uEye camera drivers). Rejected in favor of extended alone:

- **Self-contained vs. two unknowns chained together.** Extended is one
  vendor-tested installer that already bundles the uEye camera drivers
  directly. IDS Software Suite is a separate, older product line never
  investigated this session — unknown size, unknown installer behavior —
  and per IDS's own docs it exists specifically as a compatibility bridge
  for the uEye Transport Layer, not clearly something IDS keeps investing
  in as their product line moves toward native GenICam/peak. Depending on
  it is the same "unexplored legacy second product with its own footprint
  and version constraints" tradeoff the IDS-installer entry above already
  rejected once, for the same underlying reason.
- **One chain link vs. two.** Software Suite + runtime setup means
  launching two installers in sequence, in the right order, each able to
  fail or drift out of version-sync independently. Extended is one
  `[Run]` step.
- The one real point in Suite+runtime's favor — a smaller installed
  footprint on the clinic machine, since it skips Cockpit/dev tools/
  sample code extended drags in — is disk-space tidiness, not something
  CLAUDE.md treats as a real constraint anywhere, and not worth trading
  reliability for.

### Rejected

- **A true single MSI that also silently installs Python itself** for the
  purpose of running raw source, instead of freezing the app. Once the
  app is frozen, Python on the target machine stops being relevant at
  all — this alternative was only ever solving a problem the frozen-exe
  approach removes outright, not a real remaining option.
- **PyInstaller-freezing `setup_wizard.py` alone** (discussed before this
  entry) — superseded. That would have produced a nicer front end for
  bootstrapping a Python dev environment on the target machine, but the
  target machine no longer needs one at all in this model.

### Status

**Built.** See DECISIONS.md's 2026-08-20 "Frozen-exe installer built"
entry for what was actually implemented, what got revised along the way
(`sessions_dir` became technician-configurable rather than a fixed
default — see that entry), and the empirical PyInstaller findings.
`packaging/installer_output/sidebyside-setup.exe` compiles successfully
end to end; running the resulting installer on a real/disposable machine
to verify the full technician-facing flow is the one remaining step, not
done as part of this work — see `PACKAGING.md`'s "before handing this to
anyone" checklist.

**Revised 2026-08-25:** this entry's body text above describes the
bundled IDS peak installer as launched "interactively, no silent flags."
That's superseded — it's now driven silently, with its own verification
layered on. See DECISIONS.md's 2026-08-25 "Silent IDS peak install, with
verification Kinexis doesn't have" entry for the reversal and what was
actually built.

---

## 2026-08-26 — Vignette-aware metering, white balance, acquisition fps cap, backlight compensation (planned, not built)

### Context

Follow-up to the exposure/gain calibration work above, from a broader
"what else should be configured for the best experience, short of fancy
calibration tools" discussion. Four concrete, scoped items surfaced:

1. `auto_calibrate()` measures whole-frame median brightness — but
   slit-lamp/BIO video coupled through an eyepiece/beam-splitter likely
   shows a circular illuminated image surrounded by true black
   (unconfirmed against real footage, but common for this kind of optical
   coupling), which skews that measurement dark and causes
   over-brightening. Fixed by a centered crop before measuring — also just
   ordinary center-weighted metering practice regardless of whether a
   vignette is actually present, so it's a safe default either way.
2. White balance is uncorrected everywhere (see the revised bullet in the
   2026-08-18 entry above). Two cases depending on hardware, both handled:
   a camera with `BalanceWhiteAuto` gets it fully automatically (folded
   into the same convergence pass as exposure/gain); a camera without it
   (the likely case for the slit lamp) gets a manual software calibration
   path mirroring the exposure/gain one exactly — a technician holds a
   neutral gray/white target (a real photography white-balance card or
   PTFE sheet, **not** 3D-printed filament, which isn't spectrally flat or
   diffuse enough to trust as a reference) in the fixed optical path,
   clicks Auto White-Balance in `settings.py`'s Preview dialog, and the
   result persists to `config.json`.
3. Neither camera's own acquisition rate was capped — `recorder.py`'s
   `_tick()`/`_drain_latest()` already discards anything a camera captures
   faster than `recording.fps` with zero benefit, so free-running at
   native ~58-60fps burns exactly the tight USB bandwidth margin
   CLAUDE.md's Hardware section flags, for nothing. `SETUP.md` already
   tells a technician this fact as manual troubleshooting guidance;
   nothing in code enforced it.
4. The third-person UVC camera had no backlight compensation, which
   could matter if a student sits with a bright window/light behind them.

(Black balance — the shadow-end analog of white balance — was raised and
considered, then dropped: it turned out to be conflated with backlight
compensation above, not a separate thing actually wanted.)

### Built

All four items above landed together. Summary (see `DECISIONS.md`'s
"Built the vignette/white-balance/fps-cap/backlight-compensation
follow-ups" entry for the full account once it's written):

- `exposure_calibration.py` gained `center_crop()` (used by both
  `auto_calibrate()` and the new `auto_white_balance()`),
  `channel_medians()`, `is_white_balanced()`, `next_balance_ratios()` —
  all pure, SDK-free, directly unit-tested.
- `ids_camera.py`'s exposure/gain-specific convergence loop
  (`_converge_auto_exposure()`) was generalized into
  `_converge_auto_nodes(node_names)`, since `BalanceWhiteAuto` needed the
  identical Once-then-poll-until-Off-then-lock shape a third time — one
  combined pass instead of a second, separately-polled loop.
  `needs_manual_white_balance()`/`auto_white_balance()`/the manual
  `get_/set_red_balance_ratio()`/`get_/set_blue_balance_ratio()` pair
  mirror the exposure/gain accessors exactly. `_apply_frame_rate_cap()`
  sets `AcquisitionFrameRate` (gated by `AcquisitionFrameRateEnable` where
  present), best-effort and clamped to what the camera can currently do.
- `config.py`'s `InstrumentConfig` gained optional
  `red_balance_ratio`/`blue_balance_ratio` fields, validated as a pair
  (exactly one present is a `ConfigError` — `BalanceWhiteAuto=Once`
  converges both together, there's no partial manual white balance).
- `settings.py`'s `PreviewDialog` shows red/blue balance-ratio sliders and
  an Auto White-Balance button whenever `needs_manual_white_balance()` is
  true — a **separate** control block and status label from
  exposure/gain's, not merged, since a camera can plausibly need both at
  once and a shared status label would have one calibration's message
  clobber the other's.
- `uvc_camera.py` gained `_apply_frame_rate_cap()` (`cv2.CAP_PROP_FPS`,
  logs a warning rather than failing on a mismatch) and backlight
  compensation (`cv2.CAP_PROP_BACKLIGHT`, set before the existing
  autofocus/auto-exposure warmup so that warmup benefits from it too).
  `_lock_autofocus_and_exposure()` renamed to `_configure_capture()`
  since it now does more than autofocus/exposure.
- `app.py` threads `cfg.recording.fps` into both instrument and
  third-person camera construction as `target_fps`, and the two new
  instrument config fields into `IdsCamera`. `settings.py`'s Preview
  cameras deliberately never receive `target_fps` — Preview is
  single-camera and technician-supervised, so the bandwidth concern the
  cap exists for never applies there.

### Unverified, flagged explicitly rather than assumed

Same constraint the exposure/gain feature shipped under: this dev machine
has no `ids_peak` installed and no `vendor/ids_peak_api.txt` dump to check
node names against. Specifically unverified pending a hardware smoke test:

- `BalanceWhiteAuto`, and the `BalanceRatioSelector`/`BalanceRatio`
  selector+value pair (standard GenICam SFNC pattern, not confirmed
  against either real camera).
- `AcquisitionFrameRateEnable` gating `AcquisitionFrameRate` — a real
  GenICam SFNC variation across camera families; whichever camera doesn't
  need the enable node should still work since the code tolerates either
  shape.
- Whether `AcquisitionFrameRate` can be set *after* `AcquisitionStart` the
  way `ExposureAuto`/`GainAuto` already are (confirmed working
  post-start), or whether it needs to be set before streaming begins —
  more directly reconfigures stream timing than a pure value node does.
  If hardware testing shows it must happen earlier, move the call before
  `data_stream.StartAcquisition()` in `_open()`.
- The `_converge_auto_nodes()` generalization itself is a refactor of
  previously-hardware-verified control flow (exposure/gain), not purely
  additive — re-running `tools/smoke_test_camera.py` against both real
  cameras is warranted even though per-axis semantics didn't change.
- The `0.5` center-crop fraction in `center_crop()`'s default is a
  starting guess, not a measurement against real slit-lamp/BIO footage.

`cv2.CAP_PROP_BACKLIGHT`/`CAP_PROP_FPS`/`CAP_PROP_AUTO_WB` are, by
contrast, confirmed to exist on this dev machine (cv2 is installed here) —
lower risk than anything IDS-specific, though their real-device *effect*
still isn't verified.

---

## 2026-08-26 — `Net2860Camera` brightness/gain control (surveyed, not decided)

Raised while investigating why the older Vantage Plus BIO's captured frames
were dim/green in low light (see `DECISIONS.md`'s two `Net2860Camera`
entries) — confirmed to be ordinary light starvation (CCD noise floor,
skewed green by the Bayer filter's 2x green photosites), not a hardware
fault, once a bright light was used. Not acted on: whether the vendor
DirectShow filter exposes `IAMVideoProcAmp` (the standard DirectShow
interface for brightness/gain/exposure, distinct from `IAMStreamConfig`,
which `DECISIONS.md` already ruled out for frame-rate control on this
camera) is unknown — never checked. If it does, `net2860_helper.py` could
expose `settings.py`-driven controls mirroring the IDS cameras' manual
calibration path (see the 2026-08-18 entry above) rather than requiring a
bright external light source every time. Candidate for whenever this
camera is revisited — e.g. alongside packaging (`DECISIONS.md`'s
"What's still NOT done" note on the wiring entry).

---

## 2026-08-26 — Health-check tool and installer cleanup scope surveyed, not acted on (yet)

Surfaced during a "what would benefit the app" brainstorm while waiting for
hardware room access. Recorded so neither is re-derived from scratch later;
neither is committed work.

- **A technician-facing "Doctor"/health-check tool** — one pass
  consolidating diagnostics that already exist but are scattered and
  reactive: an `IdsPeakAlreadyInstalled()`-equivalent SDK version check
  (today buried in `packaging/sidebyside.iss`, install-time only),
  `config.py`'s validation (today only fires when `app.exe` happens to
  launch), `settings.py`'s per-camera "not connected" detection (today only
  visible if a technician opens it), `kiosk.py`'s disk-space preflight
  (today only runs right before Start). A single glanceable report instead
  of hunting across four surfaces. Specifically liked: a green/yellow/red
  status indicator (seen in other tools) summarizing overall health at a
  glance. **Shelved for now** — judged possibly overboard for this app's
  current scale; revisit if the diagnostic-hunting friction becomes a real
  recurring problem, not preemptively. If built, should stay
  diagnosis-only (no silent auto-fix), matching CLAUDE.md's "loud and
  early" philosophy — the one plausible exception being an explicit,
  technician-clicked "re-run the IDS peak install" button, since that only
  re-exposes what `InstallIdsPeakSilently()` already does once, safely, on
  demand.
- **The default Inno Setup uninstaller's actual scope was never verified
  or written down.** It only removes what `[Files]` explicitly copied
  under `{app}` — today that means it has no awareness of
  `%ProgramData%\sidebyside\config.json`, `%PUBLIC%\Documents\sidebyside\
  sessions\`, or the separately-installed IDS peak SDK. Leaving recordings
  and the IDS SDK untouched is almost certainly correct behavior, but right
  now that's an accident of what's absent from `[Files]`, not a tested,
  documented guarantee — worth confirming explicitly given CLAUDE.md's
  stance that recordings are irreplaceable. Real open item, unlike the
  shelved tool above — just not yet acted on.

---

## 2026-08-26 — Recorder/Viewer split under consideration (architecture, not decided)

### Context

Surfaced from the same "what would benefit the app" session above, in
response to a specific question: does the current single-composite-file
model actually serve the app's purpose best? Today `recorder.py` composites
both cameras' frames in real time into one `composite.mkv` → `composite.mp4`
per session — the layout (side-by-side) is permanently baked in at capture
time, and there's no way to later view a session any other way, or extract
just one camera's stream, without re-deriving it from the composite.

### The idea

Split the app into a **Recorder** (captures and preserves each camera's
stream separately, synchronized but not pre-composited) and a **Viewer**
(reads a recorded session and lays it out on demand — side-by-side,
picture-in-picture, single-camera, whatever's useful for reviewing a
specific moment). Two stated motivations:

- **Flexible review.** A student (or instructor) could choose how to watch
  a session after the fact, rather than being locked into whatever layout
  was chosen at record time.
- **Future integration.** If sidebyside is ever asked to feed recordings
  into Canvas (NECO's LMS, used for sharing media with students) or another
  external system, having each camera's stream separately addressable is a
  fundamentally better starting point than trying to extract one view back
  out of an already-composited file. Not a specific present request —
  explicitly about not painting the architecture into a corner if it
  becomes one.

### Why this is a real architectural fork, not a small feature

- `BaseCamera`/`Frame`'s per-frame monotonic timestamps already exist
  specifically so consumers don't assume nominal frame timing (CLAUDE.md's
  Architecture section) — exactly the raw material a Viewer-side
  synchronization step would need, which suggests this pivot is more
  feasible than it might look at first glance, not something needing new
  capture-side plumbing from scratch.
- It's still a real change in what gets recorded and stored: `recorder.py`
  would need to preserve two separately-decodable streams (today it
  discards each camera's individual frame once it's composited) and
  `session.json` would need to carry whatever sync data a Viewer needs to
  re-align them, not just frame/drop counts as it does today. Storage
  footprint per session likely grows — two full streams instead of one
  already-downscaled, letterboxed composite.
- Every layout function in `compositor.py` (`side_by_side`,
  `picture_in_picture`) already exists and is aspect-preserving/letterboxed
  — a Viewer wouldn't need new layout math, just a different place to call
  it from (at watch time against two decoded streams, instead of at
  capture time against two live `BaseCamera` feeds).

### Status

**Under consideration, not decided.** No design work started. Directly
affects the priority of in-app playback/session-history (surfaced in the
same conversation) — deliberately not building those against today's
single-file model if this split might reshape what "a recorded session"
even means. Revisit together before starting either.

---

## 2026-09-01 — settings.py: pick from known-compatible devices with presets, not a free dropdown + typed label

### Context

Surfaced while fixing the BIO camera image coming in flipped (see
DECISIONS.md's two "Device-model rotation presets" entries). That fix keys
an orientation correction on the IDS model name in `device_presets.py` and
applies it automatically. It works, but it exposed a gap in `settings.py`:
the technician still picks a camera from a free-form dropdown of whatever's
attached and hand-types a label. The program has model-specific knowledge
(this orientation today; plausibly more later) that the setup UI doesn't
surface at all.

### The idea

Restructure `settings.py`'s per-role selection around **known device
profiles** rather than raw enumeration:

- Each instrument role offers a dropdown of *compatible device profiles*
  (e.g. "Keeler Vantage Plus Digital BIO", "Haag-Streit BI 900 slit lamp"),
  drawn from `SUPPORTED_HARDWARE.md`'s confirmed list, matched against
  what's actually attached.
- Selecting a profile pulls in its presets (orientation, sensible default
  label, and any future per-model quirks) instead of the technician
  supplying them piecemeal.
- An "other / unlisted" path stays, falling back to today's raw
  dropdown + manual label, so an unrecognized-but-working camera isn't
  locked out.

### Why it's a real change, not a tweak

- `device_presets.py` today is a single orientation lookup. This would grow
  it into the actual device-profile registry (identity match rules, default
  label, quirk set) and make it the thing `settings.py` renders from —
  overlapping the already-"complete" 2026-08-17 "Device compatibility &
  camera setup system" work, which deliberately chose the lean free-dropdown
  shape.
- `config.json`'s `instruments.<role>` shape may want a `profile` key
  alongside `serial`, so `app.py` can re-resolve presets at load time
  rather than only `IdsCamera._open()` doing it by model string.
- Interacts with `settings.py`'s existing per-role calibration state
  (exposure/gain/white-balance) — a profile could ship starting points for
  those too.

### Status

**Not started, not designed.** Recorded now so the orientation-preset fix
isn't mistaken for the finished shape. The `config.json` `orientation`
override and `device_presets.orientation_for_model()` are the minimum that
solves the immediate BIO problem; this is the fuller direction.
