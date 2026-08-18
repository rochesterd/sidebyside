# Roadmap

Forward-looking plans, distinct from `DECISIONS.md` (an append-only log of
decisions already made and built). Entries here describe intent before
implementation — expect them to be revised as reality pushes back, and to
be superseded by real `DECISIONS.md` entries and user-facing docs once
built. Newest at the bottom.

---

## 2026-08-17 — Device compatibility & camera setup system

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

Schema sketch:

```json
{
  "instruments": {
    "slit_lamp": { "kind": "ids", "serial": "4103484089", "label": "Slit Lamp" },
    "bio":       { "kind": "ids", "serial": "4110050487", "label": "BIO" }
  },
  "third_person": { "kind": "uvc", "vid_pid": "32E4:9310", "friendly_name": "HD USB Camera" }
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

1. `config.json` + loader. Mechanical, unblocks everything else —
   `app.py`/`kiosk.py` read config instead of hardcoded constants; the
   current two serials become this dev machine's `config.example.json`.
2. `settings.py`. The UVC-enumeration spike above is done
   (`uvc_enumeration.py`) — remaining: the dropdown/preview UI built
   around it, and writing `config.json`.
3. Runtime resolution hardening — VID/PID + single-device-fallback logic
   for UVC, the explicit failure-mode messages above.
4. Documentation — `SUPPORTED_HARDWARE.md`, `SETUP.md` rewrite, real
   `DECISIONS.md` entries for whatever Phase 2's spike settles on.

### Explicitly out of scope for now

- Legacy pre-GenICam uEye SDK cameras, GigE-connected cameras.
- Physical USB port/hub topology as an identity mechanism.
- Hot-reloading config into a running `app.py` — restart after
  reconfiguring is an accepted, explicit requirement, not a gap.
- More than one third-person camera, or a variable number of "views"
  beyond the existing instrument + third-person pair — not requested,
  keeps scope matched to the app's actual purpose.
