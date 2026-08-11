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
  latency offset) belong in a config file, not in source.
- Record to MKV during capture, remux to MP4 afterward. An interrupted MKV
  is still playable; an interrupted MP4 is lost.
- Prefer dropping frames over blocking a capture thread. A blocked capture
  thread stalls the device.
- When a decision has a non-obvious reason behind it, add an entry to
  `DECISIONS.md` rather than a comment.
