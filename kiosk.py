"""Kiosk state machine: idle -> ready -> recording -> error -> idle.

No Qt dependency, deliberately. app.py is a thin PySide6 shell that polls
this class on a timer and reflects what it reports; every actual decision
(when Start may be pressed, when a stall counts as a failure, what the
post-session summary contains) lives here so it can be unit-tested
headlessly with SyntheticCamera, the same way test_recorder.py drives
Recorder headlessly.
"""

from __future__ import annotations

import enum
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from camera import BaseCamera
from recorder import Recorder

logger = logging.getLogger(__name__)

# --- Disk-space preflight -------------------------------------------------
#
# libx264 at crf=23 (Recorder's default) targets quality, not a fixed
# bitrate, but ~0.08 bits/pixel/frame is a standard rule of thumb for
# moderate-motion content at that quality. At Recorder's default 2560x1080
# canvas and 30fps:
#
#   bits/sec  = 0.08 * (2560 * 1080) * 30        ~= 6.64 Mbps
#   bytes/sec = bits/sec / 8                      ~= 830 KB/s
#   10 minutes = bytes/sec * 600                  ~= 498 MB
#
# Recorder.stop() keeps both composite.mkv and composite.mp4 (the remux
# doesn't delete the source, on purpose - see DECISIONS.md), so a completed
# 10-minute session actually occupies about *two* copies of that estimate
# on disk. That's where "2x" comes from below: it isn't a safety margin
# stacked on top, it's what one session literally needs once both files
# exist. See DECISIONS.md for the full reasoning and chosen thresholds.
BITS_PER_PIXEL_ESTIMATE = 0.08
TARGET_SESSION_MINUTES = 10.0
REQUIRED_SPACE_MULTIPLIER = 2.0

# How long a camera's frame index may stay unchanged during a recording
# before it counts as stalled. See DECISIONS.md.
DEFAULT_STALL_TIMEOUT_S = 2.0


def estimate_recording_bytes(
    width: int,
    height: int,
    fps: float,
    minutes: float = TARGET_SESSION_MINUTES,
    bits_per_pixel: float = BITS_PER_PIXEL_ESTIMATE,
) -> float:
    bytes_per_second = (bits_per_pixel * width * height * fps) / 8.0
    return bytes_per_second * minutes * 60.0


def _existing_ancestor(path: Path) -> Path:
    """Walk up from `path` to the nearest directory that actually exists,
    so shutil.disk_usage has something to measure before sessions/ is
    ever created.
    """
    path = path.resolve()
    while not path.exists():
        parent = path.parent
        if parent == path:
            return Path(path.anchor or ".")
        path = parent
    return path


class State(enum.Enum):
    IDLE = "idle"
    READY = "ready"
    RECORDING = "recording"
    ERROR = "error"


@dataclass
class PreflightStatus:
    cameras_ready: bool
    disk_ok: bool
    free_bytes: int
    required_bytes: float

    @property
    def ok(self) -> bool:
        return self.cameras_ready and self.disk_ok


class KioskController:
    """third_person_camera runs for the controller's whole lifetime, same
    as both cameras used to. `instruments` holds one BaseCamera per
    selectable instrument (e.g. "slit_lamp", "bio"), constructed but not
    started -- only one is ever running at a time, since only one
    instrument is ever physically in use in a session. select_instrument()
    owns starting/stopping them; nothing else does. See CLAUDE.md's
    Architecture section and DECISIONS.md's "Third-person UVC camera"
    entry.
    """

    def __init__(
        self,
        third_person_camera: BaseCamera,
        instruments: dict[str, BaseCamera],
        third_person_name: str = "third_person",
        output_root: str | Path = "sessions",
        width: int = 2560,
        height: int = 1080,
        fps: int = 30,
        stall_timeout_s: float = DEFAULT_STALL_TIMEOUT_S,
        target_minutes: float = TARGET_SESSION_MINUTES,
        bits_per_pixel: float = BITS_PER_PIXEL_ESTIMATE,
        disk_usage_fn: Callable[[str], object] = shutil.disk_usage,
        recorder_factory: Callable[[BaseCamera, str], Recorder] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.third_person_camera = third_person_camera
        self.instruments = instruments
        self.output_root = Path(output_root)
        self.width = width
        self.height = height
        self.fps = fps
        self.stall_timeout_s = stall_timeout_s

        self._third_person_name = third_person_name
        self._disk_usage_fn = disk_usage_fn
        self._clock = clock
        self._required_bytes = REQUIRED_SPACE_MULTIPLIER * estimate_recording_bytes(
            width, height, fps, minutes=target_minutes, bits_per_pixel=bits_per_pixel
        )
        self._recorder_factory = recorder_factory or (
            lambda instrument_camera, instrument_name: Recorder(
                instrument_camera,
                self.third_person_camera,
                name_a=instrument_name,
                name_b=third_person_name,
                output_root=str(self.output_root),
                width=self.width,
                height=self.height,
                fps=self.fps,
            )
        )

        self.state = State.IDLE
        self.error_message: str | None = None
        self.last_session_info: dict | None = None
        self.last_session_dir: Path | None = None
        self.selected_instrument: str | None = None

        self._recorder: Recorder | None = None
        self._last_index: dict[str, int] = {"instrument": -1, "third_person": -1}
        self._last_progress_time: dict[str, float] = {}

    # --- Instrument selection -------------------------------------------

    def select_instrument(self, key: str) -> None:
        """Stop whichever instrument camera was running (if any) and start
        the newly chosen one. Only one instrument camera is ever live at a
        time -- see the class docstring. A no-op if `key` is already
        selected, so callers (app.py's picker buttons) don't need to guard
        against re-clicking the active choice.
        """
        if key not in self.instruments:
            raise ValueError(f"unknown instrument {key!r}")
        if self.state == State.RECORDING:
            raise RuntimeError("cannot change instrument while recording")
        if key == self.selected_instrument:
            return
        if self.selected_instrument is not None:
            self.instruments[self.selected_instrument].stop()
        # Cleared before start(), not after: a real camera's start() can
        # raise (wrong serial, device busy elsewhere -- see IdsCamera). If
        # it does, the old camera is already stopped, so leaving
        # selected_instrument pointing at it would make _cameras_ready()
        # trust a frozen last frame from a camera that isn't actually
        # running. None here means "nothing confirmed running," which is
        # the truth until start() below succeeds.
        self.selected_instrument = None
        self.instruments[key].start()
        self.selected_instrument = key
        logger.info("instrument selected: %s", key)

    # --- Idle / ready -------------------------------------------------

    def poll_preflight(self) -> PreflightStatus:
        """Call regularly while not recording. Moves between IDLE and
        READY based on current camera/disk conditions; also where an ERROR
        state resolves back to IDLE/READY once conditions look healthy
        again. error_message / last_session_info are left in place so the
        UI can keep showing the last failure until the next session starts.
        """
        usage = self._disk_usage_fn(str(_existing_ancestor(self.output_root)))
        status = PreflightStatus(
            cameras_ready=self._cameras_ready(),
            disk_ok=usage.free >= self._required_bytes,
            free_bytes=usage.free,
            required_bytes=self._required_bytes,
        )
        if self.state != State.RECORDING:
            new_state = State.READY if status.ok else State.IDLE
            if new_state != self.state:
                logger.info(
                    "%s -> %s (cameras_ready=%s, disk_ok=%s)",
                    self.state.value,
                    new_state.value,
                    status.cameras_ready,
                    status.disk_ok,
                )
            self.state = new_state
        return status

    def _cameras_ready(self) -> bool:
        # No instrument picked yet counts as not ready, the same as a
        # picked-but-not-yet-live instrument camera -- Start stays
        # disabled either way, and app.py's status line prompts for a
        # selection using the same "waiting for..." messaging.
        if self.selected_instrument is None:
            return False
        instrument_camera = self.instruments[self.selected_instrument]
        return self.third_person_camera.get_latest() is not None and instrument_camera.get_latest() is not None

    # --- Recording ------------------------------------------------------

    def start_recording(self) -> None:
        if self.state != State.READY:
            raise RuntimeError(f"cannot start recording from state {self.state}")

        instrument_camera = self.instruments[self.selected_instrument]
        self._recorder = self._recorder_factory(instrument_camera, self.selected_instrument)
        self._recorder.start()
        self.error_message = None
        self.last_session_info = None
        self.last_session_dir = None

        now = self._clock()
        for key, camera in (("instrument", instrument_camera), ("third_person", self.third_person_camera)):
            frame = camera.get_latest()
            self._last_index[key] = frame.index if frame is not None else -1
            self._last_progress_time[key] = now

        self.state = State.RECORDING
        logger.info(
            "recording started: session_dir=%s instrument=%s",
            self._recorder.session_dir,
            self.selected_instrument,
        )

    def poll_recording(self) -> None:
        """Call regularly while RECORDING. If a camera's frame index hasn't
        advanced for stall_timeout_s, stops the recording and moves to
        ERROR immediately - loud and early, per CLAUDE.md, rather than
        letting a broken recording run to completion.
        """
        if self.state != State.RECORDING:
            return

        instrument_camera = self.instruments[self.selected_instrument]
        display_names = {"instrument": self.selected_instrument, "third_person": self._third_person_name}

        now = self._clock()
        stalled = []
        for key, camera in (("instrument", instrument_camera), ("third_person", self.third_person_camera)):
            frame = camera.get_latest()
            index = frame.index if frame is not None else -1
            if index != self._last_index[key]:
                self._last_index[key] = index
                self._last_progress_time[key] = now
                continue
            if now - self._last_progress_time[key] >= self.stall_timeout_s:
                stalled.append(key)

        if stalled:
            names = ", ".join(display_names[key] for key in stalled)
            self._fail(
                f"No new frames from {names} for {self.stall_timeout_s:.1f}s+. "
                "Recording stopped."
            )

    def stop_recording(self) -> dict:
        if self.state != State.RECORDING:
            raise RuntimeError(f"cannot stop recording from state {self.state}")
        session_info = self._recorder.stop()
        self.last_session_dir = self._recorder.session_dir
        self._recorder = None
        self.last_session_info = session_info
        self.error_message = None
        self.state = State.IDLE
        dropped = {
            cam["name"]: cam["dropped_frames"] for cam in session_info.get("cameras", {}).values()
        }
        logger.info(
            "recording stopped: session_dir=%s frame_count=%s dropped=%s",
            self.last_session_dir,
            session_info.get("composite", {}).get("frame_count"),
            dropped,
        )
        return session_info

    def _fail(self, message: str) -> None:
        logger.error("recording failed: %s", message)
        self.error_message = message
        if self._recorder is not None:
            try:
                self.last_session_info = self._recorder.stop()
            except Exception as exc:
                self.last_session_info = {"error": f"recorder failed to stop cleanly: {exc}"}
            self.last_session_dir = self._recorder.session_dir
            self._recorder = None
        self.state = State.ERROR
