# Roadmap

Open plans only — intent before implementation, expected to change as
reality pushes back. The commit that adds a plan's `DECISIONS.md` entry
deletes it from here; for plans already built, see DECISIONS.md's
2026-09-11 "Retired ROADMAP entries" entry. Newest at the bottom.

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
- **Session-length/stall-timeout/disk-margin constants**
  (`MAX_SESSION_MINUTES`, `DEFAULT_STALL_TIMEOUT_S`,
  `REQUIRED_SPACE_MULTIPLIER` in `kiosk.py`) are already constructor
  parameters with sensible measured defaults, not config. Deliberately
  not moved to `config.json` — no institution has needed a different
  value yet, and adding config for a hypothetical isn't earned.

---

## 2026-08-26 — Health-check tool surveyed, not acted on (yet)

Surfaced during a "what would benefit the app" brainstorm while waiting for
hardware room access. Recorded so it isn't re-derived from scratch later;
not committed work.

- **A technician-facing "Doctor"/health-check tool** — one pass
  consolidating diagnostics that already exist but are scattered and
  reactive: an `IdsPeakAlreadyInstalled()`-equivalent SDK version check
  (today buried in `packaging/reflex.iss`, install-time only),
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
  overlapping the already-complete 2026-08-18 settings.py work, which
  deliberately chose the lean free-dropdown shape.
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
