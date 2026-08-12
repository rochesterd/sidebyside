# CLAUDE.md

Standing context for this project. Read this before making changes.

## What this is

`sidebyside` records two cameras simultaneously and produces a single
composited video, so optometry students at NECO can watch their own hand
movements alongside the view through the instrument they're using.

The purpose is self-directed practice for the NBEO CSE (Clinical Skills
Examination). Students book time, record themselves practising a skill on a
peer, and watch it back. The coordination between what their hands are doing
and what appears through the optics is the entire point of the recording.

## Who uses it

Students, unsupervised, with no technical background and no instructor
present. They will not read documentation, will not open a terminal, and
will not debug anything.

Design consequences:

- One start button, one stop button. Nothing else clickable during a session.
- Failures must be **loud and early**. A black pane discovered a week later
  is the worst outcome. Prefer refusing to start over recording something
  broken.
- The start button stays disabled until both cameras are confirmed live and
  there is enough disk space.
- Never make the irreplaceable data depend on a step that can fail. Frames
  get written as they arrive; anything derived comes afterward.

## Hardware

| Instrument | Camera | Interface | Notes |
|---|---|---|---|
| Haag-Streit BI 900 slit lamp | IDS UI-3250CP-C-HQ Rev. 2 | USB 3.0 | 1600x1200, ~60fps, legacy uEye family — needs the uEye Transport Layer |
| Keeler Vantage Plus Digital | IDS U3-327xCP-C | USB 3.0 | 2056x1542, ~58fps, USB3 Vision — native to IDS peak |

Both are machine vision cameras. They are **not** webcams: no RTSP, no
ONVIF, no DirectShow-by-default. They deliver raw frames through the IDS
peak SDK and require a host PC.

**Bandwidth is the binding constraint.** At full resolution and frame rate,
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
setups like this silently break.

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
| `compositor.py` | `side_by_side`, `picture_in_picture`, and `draw_timer` — all aspect-preserving, letterboxed into a fixed-size canvas. |
| `preview.py` | Live PySide6 preview window, two cameras, layout dropdown, frame-index/skew status line. Uses `get_latest()`. |
| `recorder.py` | Background-threaded recorder: drains both cameras' queues, composites with `side_by_side`, overlays elapsed time, encodes to MKV via PyAV, remuxes to MP4 on stop, writes `session.json`. Uses `read()`. |
| `kiosk.py` | `KioskController` — the actual state machine (idle/ready/recording/error) behind the kiosk app: preflight checks (camera liveness, disk space), stall detection during recording, session summaries. No Qt import. Unit-testable headlessly. |
| `app.py` | The kiosk entry point (see CLAUDE.md "Who uses it"). Thin PySide6 shell: one Start button, one Stop button, nothing else clickable. Polls `KioskController` on a timer and reflects what it reports; owns no decisions itself. |
| `test_recorder.py` | Integration test: records 10s from two real `SyntheticCamera` instances and checks the actual decoded MP4 (frame count, duration), not just that a file was written. |
| `test_kiosk.py` | Integration tests for `KioskController`: preflight gating (stale camera, low disk space), a full happy-path session, and a mid-recording stall triggering the error path — all against real `SyntheticCamera`/`Recorder`, using an injectable clock to skip real sleeps for the stall test. |

`app.py` is what a student actually runs. `preview.py` is a development
tool (layout dropdown, skew readout) for eyeballing compositing changes
without going through a full record/stop cycle — never point a student at
it, it has no Start/Stop discipline.

## Recording output

Each recording writes to `sessions/<YYYY-MM-DD_HHMM>/` (minute-collision
gets a `_2`, `_3`, ... suffix rather than overwriting):

- `composite.mkv` — written live during capture. Interruption-safe.
- `composite.mp4` — remuxed from the MKV once `stop()` is called. No
  re-encode, so this step is fast but depends on the MKV having closed
  cleanly.
- `session.json` — camera names, resolutions, each camera's first-frame
  timestamp, per-camera and composite frame counts, and per-camera
  dropped-frame counts (computed from gaps in `Frame.index`, not estimated).

`sessions/` is gitignored. Nothing in it is a build artifact of source
control; it's the actual deliverable handed to a student, so treat contents
under it as data, not something to regenerate.

## Environment

- Windows, Python 3.13, venv at `.venv` (activate before running anything)
- Dependencies: numpy, opencv-python, av (PyAV), PySide6
- IDS peak must be installed separately per machine — it includes kernel
  drivers and cannot be bundled. The `ids_peak` and `ids_peak_ipl` wheels
  must be installed from the local IDS peak installation, **not** from PyPI,
  because the binding version must match the installed runtime. See
  `SETUP.md`.
- Development happens on a machine without cameras attached; use
  `SyntheticCamera` and don't hard-code anything that must be measured
  against real hardware.

## Conventions

- Values that depend on measurement (frame rate, resolution, inter-camera
  latency offset) belong in a config file, not in source. **Not yet true in
  practice**: no config file exists, so the 2560x1080 canvas and 30fps
  target currently live as constructor defaults in `recorder.py`. Move them
  out when the config system lands instead of letting more values pile up
  in source.
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
