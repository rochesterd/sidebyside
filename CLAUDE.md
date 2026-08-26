# CLAUDE.md

Standing context for this project. Read this before making changes.

## What this is

`sidebyside` records two cameras simultaneously and produces a single
composited video, so optometry students at NECO can watch themselves 
from a third person view alongside the view through the instrument they're 
using.

The purpose is self-directed practice for the NBEO CSE (Clinical Skills
Examination). Students book time, record themselves practising a skill on a
peer, and watch it back. The coordination between what their hands are doing
and what appears through the optics is the entire point of the recording.

## Who uses it

Students, unsupervised, with no technical background and no instructor
present. They will not read documentation, will not open a terminal, and
will not debug anything.

Design consequences:

- Intuitive to Apple's standard, not just "usable." A tech handles
  installation, configuration, and calibration; within a session, the
  student is guided entirely by the app, never by documentation.
- Protecting the student from mistakes is the goal, not minimizing what's
  clickable for its own sake. During recording that protection is
  absolute: nothing but Stop is interactive, since interrupting an
  irreplaceable, in-progress capture is unrecoverable.
- Before recording, the same goal applies with less rigidity. Today it's
  satisfied by a minimal instrument picker + Start (see `app.py`) — not
  because fewer buttons is inherently better. A more guided flow is fine
  there, provided nothing in it can bypass the readiness gates below or
  misconfigure a session.
- Failures must be **loud and early**. A black pane discovered a week later
  is the worst outcome. Prefer refusing to start over recording something
  broken.
- The start button stays disabled until the selected instrument camera and
  the third-person camera are both confirmed live, and there is enough disk
  space. No instrument selected counts as not ready — Start stays disabled
  and the status line prompts for a selection, the same way it prompts for
  a missing camera.
- Never make the irreplaceable data depend on a step that can fail. Frames
  get written as they arrive; anything derived comes afterward.

## Hardware

The software is built around these components. For what else is expected
to work, confirmed-tested alternatives, and what's explicitly excluded,
see `SUPPORTED_HARDWARE.md`.

| Instrument | Camera | Interface | Notes |
|---|---|---|---|
| Haag-Streit BI 900 slit lamp | IDS UI-3250CP-C-HQ Rev. 2 | USB 3.0 | 1600x1200, ~60fps, legacy uEye family — needs the uEye Transport Layer |
| Keeler Vantage Plus Digital | IDS U3-327xCP-C | USB 3.0 | 2056x1542, ~58fps, USB3 Vision — native to IDS peak |
| Third-person (student's hands) | ELP-USB100W03M-L21 | USB 2.0, UVC | Plain UVC webcam, not machine vision — resolution queried at runtime rather than hardcoded (see Conventions). Identified by VID/PID, set in `config.json` via `settings.py`; see DECISIONS.md for the identification strategy and the single-device fallback. Any UVC-compliant webcam is expected to work, not just this model. |

The two instrument cameras are machine vision cameras, not webcams: no
RTSP, no ONVIF, no DirectShow-by-default. They deliver raw frames through
the IDS peak SDK and require a host PC. The third-person camera is an
ordinary UVC webcam and goes through OpenCV's `cv2.VideoCapture` instead —
see `uvc_camera.py`.

**Bandwidth is a real concern.** At full resolution and frame rate,
8-bit, the two cameras together need roughly 300 MB/s. A single USB 3.0 host
controller realistically delivers 350-400 MB/s. USB3 Vision degrades by
silently dropping frames rather than raising an error. Target 30fps, use
separate host controllers where possible, and treat measured throughput as
authoritative over datasheet numbers.

## Architecture

`BaseCamera` in `camera.py` is the boundary. It owns the capture thread, the
bounded queue, and the latest-frame slot. Subclasses implement only `_open`,
`_close`, `_grab`, and `resolution`.

**Nothing outside a camera module may import or reference the IDS SDK.**
Everything downstream — compositor, encoder, GUI — works against
`BaseCamera` and `Frame`. This is what allows development against
`SyntheticCamera` without hardware attached, and what will make a third
instrument cheap to add.

Frames carry a monotonic timestamp taken at grab time. Never assume frames
arrive at the nominal rate; always use the timestamps.

Cameras are identified by **serial number**, never by device index. Index
order changes across reboots and USB port changes and is the most common way
setups like this silently break. The third-person UVC camera is the one
documented exception — see the Hardware table.

Only one instrument camera runs at a time. The student selects which
instrument (slit lamp or BIO) is in use before starting; the unselected
one's camera stays stopped rather than idling in the background, and
switching the selection stops whichever instrument camera was running and
starts the newly selected one. The third-person camera runs for the app's
whole lifetime, the same way both cameras used to. `kiosk.py`'s
`KioskController` owns this lifecycle — `select_instrument()` is the only
thing that starts or stops an instrument camera.

`BaseCamera` exposes frames two ways, and consumers must pick the one that
matches what they're doing:

- **`get_latest()`** — peeks the most recent frame without touching the
  queue. For display: `preview.py` polls this every UI tick and is allowed
  to skip frames it never asked for.
- **`read(timeout)`** — pops the next queued frame, so a consumer that
  drains it in order can detect gaps in `Frame.index` and count them as
  drops. For recording: `recorder.py` drains both queues this way precisely
  so `session.json` can report real dropped-frame counts.

Getting these backwards is the likely failure mode if a third consumer gets
added: `get_latest()` in a recorder undercounts drops silently, `read()` in
a UI poll loop can stall the display waiting on a queue.

## Modules

| File | Role |
|---|---|
| `camera.py` | `Frame` dataclass and abstract `BaseCamera` (capture thread, bounded queue, latest-frame slot). |
| `synthetic_camera.py` | `SyntheticCamera` — generated frames with a burned-in counter, timestamp, and sweeping bar; `latency`/`drop_rate` knobs for exercising failure paths without hardware. |
| `uvc_camera.py` | `UvcCamera` — `BaseCamera` for the third-person UVC webcam via `cv2.VideoCapture`. Two identification modes: `device` (a literal DirectShow index, used by `settings.py`'s Preview) and `vid_pid` (resolved to an index at `start()` time via `uvc_enumeration.resolve_device()`, used by `app.py`'s real runtime path — see DECISIONS.md for why resolution happens there, not at construction). `Frame.index` is self-counted, not source-reported. |
| `uvc_enumeration.py` | `list_uvc_devices()` — index/name/VID:PID for every attached UVC device, in `cv2.CAP_DSHOW`'s own open order, via `pygrabber`'s DirectShow internals. `resolve_device()` — single-device-fallback/ambiguity logic turning a configured `vid_pid` into one `UvcDeviceInfo`. |
| `ids_camera.py` | `IdsCamera` — `BaseCamera` for the two IDS peak GenICam cameras, opened by serial number. `list_ids_devices()` — serial/model for every currently-attached IDS device, for `settings.py`'s instrument dropdowns. |
| `net2860_camera.py` | `Net2860Camera` — `BaseCamera` for the older Vantage Plus BIO's NET GmbH KS722OUP camera, a `kind: "net2860"` alternative to `kind: "ids"` for the `bio` instrument role (not a new role — see DECISIONS.md). Never imports `comtypes`/`pygrabber` itself; manages `net2860_helper.py` as a 32-bit child process (its vendor DirectShow filter is 32-bit-only COM) and speaks `net2860_protocol.py`'s framed stdout protocol to it. No serial (there's exactly one of this camera) and no exposure/gain/white-balance calibration. |
| `net2860_helper.py` | 32-bit-only DirectShow capture helper for `net2860_camera.py`, never imported by the main app — only ever run as a subprocess under `.venv32/` (see `setup_net2860_helper.ps1`). `CoCreateInstance`s the vendor filter directly by CLSID (it isn't enumerable) and streams frames continuously via a custom `ISampleGrabberCB` callback. |
| `net2860_protocol.py` | Pure-stdlib framed wire protocol (`RDY1`/`FRM1`/`ERR1`) shared by `net2860_camera.py` and `net2860_helper.py` across the process boundary. |
| `config.py` | `load_config()` — reads `config.json` (gitignored; `config.example.json` is the committed template) into which physical camera fills each role. Raises `ConfigError` loudly, before `QApplication` exists, if missing/malformed. `resolve_default_config_path()`/`resolve_default_sessions_dir()` split on `is_frozen()` (PyInstaller's `sys.frozen`) — relative to CWD in dev/test, under `%ProgramData%`/`%PUBLIC%\Documents` in a frozen install, since there's no repo checkout to be relative to. `sessions_dir` is an optional `AppConfig` field, technician-set via `settings.py`'s Browse field — see ROADMAP.md's "Distribute a frozen-exe installer" entry. |
| `compositor.py` | `side_by_side`, `picture_in_picture`, and `draw_timer` — all aspect-preserving, letterboxed into a fixed-size canvas. |
| `qt_image.py` | `bgr_to_pixmap()` — the one BGR-ndarray-to-`QPixmap` conversion, shared by every window that shows a live camera feed (`app.py`, `preview.py`, `settings.py`). |
| `preview.py` | Live PySide6 preview window, two cameras, layout dropdown, frame-index/skew status line. Uses `get_latest()`. |
| `recorder.py` | Background-threaded recorder: drains both cameras' queues, composites with `side_by_side`, overlays elapsed time, encodes to MKV via PyAV, remuxes to MP4 on stop, writes `session.json`. Uses `read()`. |
| `kiosk.py` | `KioskController` — the actual state machine (idle/ready/recording/error) behind the kiosk app: instrument selection/lifecycle (`select_instrument()` starts/stops the chosen instrument camera), preflight checks (camera liveness, disk space), stall detection during recording, session summaries. No Qt import. Unit-testable headlessly. |
| `app.py` | The kiosk entry point (see CLAUDE.md "Who uses it"). Thin PySide6 shell: instrument picker, Start button, Stop button — today's minimal shape of "protect the student from mistakes," not a fixed ceiling (see "Who uses it"). Picker disables once recording starts. Polls `KioskController` on a timer and reflects what it reports; owns no decisions itself. |
| `settings.py` | Technician tool: one row per role (dropdown of currently-detected candidates, Preview button, editable label for instrument roles), Rescan, Save. Writes `config.json`; does not hot-reload a running `app.py`. Fully separate program from `app.py` — see CLAUDE.md "Who uses it". |
| `setup.ps1` | Bootstraps a **developer's** machine for working on source: venv + `requirements.txt` + `requirements-ids.txt`, then checks whether the IDS peak SDK runtime is actually importable. Doesn't touch `config.json` or role assignment — hands off to `settings.py` for that. Safe to re-run. Not part of any path a clinic machine goes through — see `PACKAGING.md`/ROADMAP.md's "Distribute a frozen-exe installer" entry. |
| `setup_wizard.py` | tkinter GUI front end over `setup.ps1` (Welcome → live-streamed run → finish, with a button to launch `settings.py`). tkinter, not PySide6, since it has to run before `requirements.txt` — which installs PySide6 — exists on a fresh machine. Same developer-only scope as `setup.ps1`. |
| `packaging/app.spec`, `packaging/settings.spec` | PyInstaller specs freezing `app.py`/`settings.py` into standalone `app.exe`/`settings.exe` — no Python, venv, or `pip install` needed on the machine that runs them. See `PACKAGING.md`. |
| `packaging/sidebyside.iss` | Inno Setup script building the actual distributable: copies the frozen exes into Program Files, creates `app.exe`'s Desktop/Start-menu shortcut (`settings.exe` gets Start-menu only — never point a student at it, same rule as below) and chain-launches a bundled copy of the IDS peak *extended* installer, interactively, no silent flags. See `PACKAGING.md` and ROADMAP.md's "why extended, not IDS Software Suite + runtime setup" entry. |
| `test_recorder.py` | Integration test: records 10s from two real `SyntheticCamera` instances and checks the actual decoded MP4 (frame count, duration), not just that a file was written. |
| `test_kiosk.py` | Integration tests for `KioskController`: preflight gating (stale camera, low disk space), a full happy-path session, and a mid-recording stall triggering the error path — all against real `SyntheticCamera`/`Recorder`, using an injectable clock to skip real sleeps for the stall test. |
| `test_app.py` | Headless tests for `KioskWindow`'s camera-start handling: fake `BaseCamera` subclasses (`FailingCamera`, `FlakyCamera`) exercise start-failure/retry paths, plus the close-during-recording confirm-dialog guard, without real hardware or `.show()`/`.exec()`. |
| `test_compositor.py` | Correctness tests for `side_by_side`/`picture_in_picture` written after a perf rewrite made the fill logic less obviously correct — see DECISIONS.md. |
| `test_config.py` | Tests for `config.load_config()`'s schema/error messages in isolation — valid config, missing/malformed file, missing/wrong-typed/badly-shaped keys, `vid_pid` case-normalization. |
| `test_settings.py` | Headless tests for `SettingsWindow`/`DeviceRow` with injected fake enumeration functions (no real hardware or IDS SDK needed): startup pre-population, Save gating (including the same-camera-two-roles conflict check), Rescan, malformed-config warning, Preview wiring. |
| `test_uvc_camera.py` | Tests for `UvcCamera`'s `device`/`vid_pid` mutual-exclusivity contract, the autofocus/auto-exposure lock, and that resolution failure surfaces from `start()` rather than `__init__`. |
| `test_net2860_protocol.py` | Real (unmocked) round-trip/error-case tests for `net2860_protocol.py`'s wire format — pure logic, no subprocess or hardware. |
| `test_net2860_camera.py` | Tests for `Net2860Camera`'s `_open`/`_grab`/`_close`, mocking at the `subprocess.Popen` boundary (same pattern `test_uvc_camera.py` uses for `cv2.VideoCapture`) with real `net2860_protocol.py` bytes, not a stand-in for the wire format. |
| `test_uvc_enumeration.py` | Tests for `list_uvc_devices()`/`resolve_device()` — the latter's single-device-fallback/ambiguity logic via canned device lists; the former verified end to end against real hardware where attached. |

`app.py` is what a student actually runs — as the frozen `app.exe` a
clinic machine's Inno Setup installer places a Desktop shortcut for, not
`python app.py` from a terminal (see `PACKAGING.md`). `preview.py` is a
development tool (layout dropdown, skew readout) for eyeballing
compositing changes without going through a full record/stop cycle —
never point a student at it, it has no Start/Stop discipline.
`settings.py` is a technician tool — same rule: never point a student at
it (it gets a Start-menu entry on a clinic machine, no Desktop shortcut).
So are `setup.ps1`/`setup_wizard.py`, but those aren't part of a clinic
machine's path at all anymore — they're developer tooling for working on
source (see `SETUP.md`), separate from `PACKAGING.md`'s build-the-
installer procedure that actually produces what a technician runs.

## Recording output

Each recording writes to `<sessions_dir>/<YYYY-MM-DD_HHMM>/`
(minute-collision gets a `_2`, `_3`, ... suffix rather than overwriting).
`sessions_dir` defaults to a relative `sessions/` folder in dev/test, or
`%PUBLIC%\Documents\sidebyside\sessions` in a frozen install unless a
technician picked somewhere else via `settings.py`'s Browse field — see
`config.py`'s `resolve_default_sessions_dir()` and ROADMAP.md's
"Distribute a frozen-exe installer" entry for why this needs to be
technician-choosable rather than fixed:

- `composite.mkv` — written live during capture. Interruption-safe.
- `composite.mp4` — remuxed from the MKV once `stop()` is called. No
  re-encode, so this step is fast but depends on the MKV having closed
  cleanly.
- `session.json` — camera names, resolutions, each camera's first-frame
  timestamp, per-camera and composite frame counts, and per-camera
  dropped-frame counts (computed from gaps in `Frame.index`, not estimated).

The default relative `sessions/` path is gitignored. Nothing under
`sessions_dir` is a build artifact of source control; it's the actual
deliverable handed to a student, so treat contents under it as data, not
something to regenerate.

## Environment

- Windows, Python 3.13, venv at `.venv` (activate before running anything;
  `setup.ps1`/`setup_wizard.py` script this and the rest of Environment —
  see SETUP.md)
- Dependencies: numpy, opencv-python, av (PyAV), PySide6, pygrabber +
  comtypes (UVC device enumeration via DirectShow — see
  `uvc_enumeration.py`), pinned in `requirements.txt`.
- IDS peak (drivers and transport layers) must be installed separately per
  machine — it includes kernel drivers and cannot be bundled. The
  `ids_peak`/`ids_peak_ipl` Python bindings come from PyPI instead, pinned
  in a separate `requirements-ids.txt` so they stay matched to the
  installed runtime without being required on machines that don't have it.
  See `SETUP.md`.
- Development happens on a machine without cameras attached; use
  `SyntheticCamera` and don't hard-code anything that must be measured
  against real hardware.
- The `ids_peak`/`ids_peak_ipl` bindings are compiled, with no readable
  source. `vendor/ids_peak_api.txt` (gitignored, regenerate locally via
  `inspect`/`dir()`) is the authoritative reference for what the real API
  surface actually is. Don't invent IDS method names or signatures — if
  something isn't in that dump, say so instead of guessing.

## Conventions

- Values that depend on measurement (frame rate, resolution, inter-camera
  latency offset) belong in a config file, not in source — except where
  the value can instead be *read from the device itself* at the moment
  it's needed, which beats config entirely: nothing to set, nothing to
  get stale when hardware changes. Which physical camera fills which role
  (instrument serials/labels, the third-person VID/PID) is config-driven —
  `config.json` (gitignored, install-specific; `config.example.json` is
  the committed template), loaded by `config.py`, and assigned via
  `settings.py` rather than hand-edited. The recording canvas size is
  device-derived, not config: `Recorder`/`KioskController` default
  `width`/`height` to `None`, meaning "sum of both cameras' actual
  `.resolution` at record time" (mirrors `compositor.side_by_side`'s own
  default) — self-heals if a camera is swapped for a different
  resolution model, with no config edit needed. The recording `fps`
  target stays config-driven (`recording.fps` in `config.json`,
  `config.py`'s `DEFAULT_RECORDING_FPS` if absent), deliberately *not*
  read from the device: a camera's nominal/datasheet fps is exactly the
  number this file's Hardware section already warns not to trust — the
  slit lamp advertises ~60fps but currently sustains far less, limited by
  exposure time, not by anything a device query would reveal. See
  `DECISIONS.md`'s "config-driven recording fps, device-derived canvas
  size" entry.
- Record to MKV during capture, remux to MP4 afterward. An interrupted MKV
  is still playable; an interrupted MP4 is lost. When remuxing, filter
  packets on `packet.size == 0`, not `packet.dts is None` — see
  `DECISIONS.md`.
- Prefer dropping frames over blocking a capture thread. A blocked capture
  thread stalls the device.
- When a decision has a non-obvious reason behind it, add an entry to
  `DECISIONS.md` rather than a comment.
- Tests use stdlib `unittest`, not pytest — pytest isn't a project
  dependency. Prefer integration-style tests that spin up real
  `SyntheticCamera` instances and check real decoded output over mocking
  internals; that's what caught the remux bug above.
