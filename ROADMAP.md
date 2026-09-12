# Roadmap

Open plans only — intent before implementation, expected to change as
reality pushes back. The commit that adds a plan's `DECISIONS.md` entry
deletes it from here; for plans already built, see DECISIONS.md's
2026-09-11 "Retired ROADMAP entries" entry. Newest at the bottom.

---

## 2026-08-18 — Other camera/recording settings surveyed, not acted on (yet)

Surfaced alongside the UVC autofocus/auto-exposure lock (see DECISIONS.md);
recorded so it isn't re-derived later, not committed work.

- **Recording quality (`codec`/`crf`/`preset`)** is hardcoded in
  `Recorder`'s defaults (`libx264`, `crf=23`), never wired to
  `config.json` — the category `fps` was in before it moved, and a real
  quality-vs-disk tradeoff that could differ by an institution's storage
  budget. Move it to the `recording` section if a real need shows up.
- **`MAX_SESSION_MINUTES`, `DEFAULT_STALL_TIMEOUT_S`,
  `REQUIRED_SPACE_MULTIPLIER`** (`kiosk.py`) are constructor parameters
  with measured defaults, deliberately not config: nobody has needed a
  different value, and config for a hypothetical isn't earned.

---

## 2026-08-26 — Health-check tool surveyed, not acted on (yet)

A technician-facing "Doctor": one glanceable green/yellow/red report
consolidating diagnostics that exist but are scattered and reactive — the
SDK version check buried in `packaging/reflex.iss` (install-time only),
`config.py`'s validation (only when `app.exe` launches), `settings.py`'s
"not connected" detection (only if a technician opens it), `kiosk.py`'s
disk preflight (only just before Start).

**Shelved** — possibly overboard at this scale; revisit if
diagnostic-hunting becomes a real recurring problem, not preemptively. If
built it stays diagnosis-only (no silent auto-fix), per CLAUDE.md's "loud
and early" — the one plausible exception being a technician-clicked
"re-run the IDS peak install", which only re-exposes what
`InstallIdsPeakSilently()` already does once, safely.

---

## 2026-09-01 — settings.py: pick from known-compatible devices with presets, not a free dropdown + typed label

Surfaced while fixing the BIO's flipped image (DECISIONS.md's two
"Device-model rotation presets" entries). That fix keys an orientation
correction on the IDS model name and applies it automatically — but
`settings.py` still has the technician pick from a free-form dropdown of
whatever is attached and hand-type a label, surfacing none of the
model-specific knowledge the program holds.

**The idea:** each instrument role offers *compatible device profiles*
(e.g. "Keeler Vantage Plus Digital BIO") drawn from
`SUPPORTED_HARDWARE.md` and matched against what's attached; choosing one
pulls in its presets — orientation, default label, future quirks — instead
of the technician supplying them piecemeal. A **Custom** path is required, not optional:
it keeps today's raw dropdown and typed label, so an unlisted but working
camera can still be set up, and so a new instrument never waits on a code
change. Profiles are the guided path; Custom is the escape hatch.

**Why it's a real change, not a tweak:** `device_presets.py` becomes the
device-profile registry (match rules, default label, quirk set) that
`settings.py` renders from, overlapping the 2026-08-18 settings.py work
that deliberately chose the lean shape; `config.json` may want a `profile`
key beside `serial`, so `app.py` can resolve presets at load time rather
than only `IdsCamera._open()` by model string; and a profile could ship
starting points for the per-role exposure/gain calibration.

**Not started, not designed.** Recorded so the orientation-preset fix
isn't mistaken for the finished shape: the `config.json` `orientation`
override plus `orientation_for_model()` is the minimum that solved the
immediate BIO problem, and this is the fuller direction.

---

## 2026-09-11 — Optional student identifier on a recording (planned, not designed)

From the first round of student feedback: an optional name or nickname for
the student doctor, so recordings can be tracked to whoever made them,
with a nickname allowed out of respect for FERPA. The open questions are
in DECISIONS.md's "First round of student feedback" entry (item E). The
main ones: whether the identifier goes into folder names or only into
`session.json`, and NECO's answer on real names and on who can see whose
recordings. Those need answers before any design. Whatever it becomes, it
must stay optional and must never gate Start.

---

## 2026-09-11 — Use the legacy BIO's picture registers (planned, not built)

DECISIONS.md's "The legacy BIO does have host-side picture controls" entry
found the EM2860 bridge's `R20`-`R25` (contrast, brightness, saturation,
blue/red balance, sharpness) host-settable, replayed today as Keeler's
captured constants. Exposure and colour stay the sensor's, so these are the
only image adjustment this camera can ever have.

- **Confirm on hardware, one register at a time** against a static scene;
  the kernel header names them, nothing here has watched them move.
  `R21_YOFFSET` first — a signed luma offset is the slit lamp's black-level
  knob by another name.
- **Then decide who owns the values** (CLAUDE.md's table): a measured
  per-model preset, `config.json` only if rooms differ; Settings offering
  this camera nothing is right until something is measured.
- **Settle first whether to touch them at all.** Keeler chose them with the
  instrument in front of them; the case is shadow detail, not tidiness.
- **While it is attached, re-derive the START/STOP split.** Replaying the
  captured sequence whole once stopped the bridge; those four writes are
  picture registers and cannot do that, so something else did. The split
  stays either way -- this is about knowing why.

---

## 2026-09-11 — Shadow detail on the slit lamp (planned, not built)

Everything the beam doesn't hit records at 1-2 of 255 (measured
2026-09-11): highlight metering exposes for the beam, and 8-bit leaves the
rest nothing to hold. Cheapest first, each a measurement before it is a
change, and whatever wins is a measured preset, never a technician knob.

- **Black level alone.** The camera's own default subtracts 90 and clamps —
  30% of the frame reads exactly 0, at 120 none of it does — and Reflex
  never writes this node. Measure what a corrected value costs in the beam.
- **10/12-bit plus a tone curve, if that isn't enough.** Both cameras offer
  BayerRG10/12 and `ids_peak_ipl` has `GammaCorrector` (with
  `SetDigitalBlack`), so shadows could be lifted before the 8-bit encode
  rather than lost at it — against ~2x USB bandwidth and per-frame CPU
  (IMAGING.md), both needing re-measurement.
