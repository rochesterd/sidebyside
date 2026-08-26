"""UVC webcam implementation of BaseCamera, for the third-person view
(ELP-USB100W03M-L21) -- an ordinary UVC device via OpenCV's
cv2.VideoCapture, not a GenICam machine vision camera. See CLAUDE.md's
Hardware table.

Three deliberate deviations from ids_camera.py's contract, all accepted --
see DECISIONS.md's "Third-person UVC camera" entry for why:

- Frame.index is a locally-assigned counter, not a source-reported
  sequence number, since UVC/DirectShow exposes no such counter. This
  camera can't detect true source-side drops the way IdsCamera can; only
  consumer-side queue evictions show up in session.json's dropped_frames.
- Two identification modes, exactly one used per instance (constructor
  raises ValueError otherwise):
  - `device`: open this literal DirectShow index directly, no resolution.
    Used by settings.py's Preview, which wants to open exactly the
    highlighted dropdown entry.
  - `vid_pid`: resolved to an index via uvc_enumeration.resolve_device()
    inside _open() -- not at construction time -- so a camera that isn't
    attached yet when this object is constructed falls into BaseCamera's
    normal start()-fails/retry flow (see camera.py: start() only creates
    the capture thread after _open() succeeds, so a raise here leaves
    _thread as None and the next start() call retries from scratch) rather
    than raising once, synchronously, before any retry loop exists. Used
    by app.py's real runtime path. A resulting UvcDeviceResolutionError
    propagates unwrapped -- it's a distinct failure ("couldn't decide
    which device") from UvcCameraNotFoundError ("knew which device, cv2
    couldn't open it"), and callers already just stringify whichever one
    they get. See DECISIONS.md's "UVC third-person identity moves to
    VID/PID" entry.
- Autofocus/auto-exposure are let run for a short fixed warmup window at
  open, then turned off (best-effort), rather than polled until converged
  the way ids_camera.py's ExposureAuto/GainAuto "Once" mode is. UVC via
  cv2.VideoCapture exposes no "has it converged yet" signal to poll --
  see _configure_capture()'s docstring. Warmup frame/timeout constants
  below are a starting guess, unverified against real hardware as of
  2026-08-18 -- see DECISIONS.md's "Lock UVC autofocus/auto-exposure
  after a warmup window" entry.

Also caps acquisition rate (_apply_frame_rate_cap(), verified cv2.CAP_PROP_FPS
constant) and enables backlight compensation (verified cv2.CAP_PROP_BACKLIGHT
constant) -- see ROADMAP.md's 2026-08-26 entry. Both are best-effort, same
as the autofocus/exposure lock: a device that doesn't support a given
control just silently keeps its own default.
"""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np

import uvc_enumeration
from camera import BaseCamera

logger = logging.getLogger(__name__)

# How long to let autofocus/auto-exposure run before locking them --
# mirrors ids_camera.py's exposure-convergence intent (adapt to the real
# scene once, then stop adjusting so the image doesn't visibly drift or
# hunt mid-recording) but can't poll for an actual "converged" signal the
# way GenICam's ExposureAuto/GainAuto nodes allow -- see module docstring.
_AUTOEXPOSURE_WARMUP_FRAMES = 10
_AUTOEXPOSURE_WARMUP_TIMEOUT_S = 2.0

# How far cap.get(CAP_PROP_FPS) is allowed to drift from the requested
# target_fps before _apply_frame_rate_cap() logs a mismatch -- some UVC
# devices only support fixed fps steps per resolution and silently pick the
# nearest one rather than the exact value requested.
_FPS_MISMATCH_LOG_TOLERANCE = 0.5


class UvcCameraNotFoundError(RuntimeError):
    """The requested device index/path could not be opened."""


class UvcCamera(BaseCamera):
    def __init__(
        self,
        device: int | str | None = None,
        vid_pid: str | None = None,
        name: str = "third-person",
        queue_size: int = 2,
        target_fps: float | None = None,
    ):
        if (device is None) == (vid_pid is None):
            raise ValueError("UvcCamera requires exactly one of device or vid_pid")
        super().__init__(queue_size=queue_size, label=name)
        self._device = device
        self._vid_pid = vid_pid
        self._cap: cv2.VideoCapture | None = None
        self._width = 0
        self._height = 0
        self._counter = 0
        # Caps this camera's own acquisition rate -- see
        # _apply_frame_rate_cap()'s docstring. None means untouched
        # free-run, e.g. settings.py's Preview cameras, which deliberately
        # never pass this.
        self._target_fps = target_fps

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    def _open(self) -> None:
        device = self._device
        if device is None:
            device = uvc_enumeration.resolve_device(self._vid_pid).index

        cap = cv2.VideoCapture(device, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise UvcCameraNotFoundError(f"could not open UVC device {device!r}")

        if self._target_fps is not None:
            self._apply_frame_rate_cap(cap)
        self._configure_capture(cap)

        # Queried rather than hardcoded -- CLAUDE.md's Conventions section:
        # measured values, not datasheet numbers, and this device's actual
        # resolution isn't known until it's opened.
        self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._counter = 0
        self._cap = cap

    def _apply_frame_rate_cap(self, cap: cv2.VideoCapture) -> None:
        """Caps this camera's own acquisition rate to (not above) target_fps
        -- same bandwidth/no-benefit reasoning as ids_camera.py's method of
        the same name (recorder.py's _drain_latest() discards anything
        captured faster than recording.fps anyway). Called before
        _configure_capture() since changing fps can force a brief stream
        renegotiation on some UVC devices, and the autofocus/exposure
        warmup that follows depends on a stable stream.

        Best-effort: cv2.VideoCapture.set() returning False (or silently
        picking the nearest supported step) just means this device doesn't
        support the exact rate requested -- logged, not raised, the same
        as every other cap.set() call in this file.
        """
        cap.set(cv2.CAP_PROP_FPS, self._target_fps)
        actual = cap.get(cv2.CAP_PROP_FPS)
        if actual and abs(actual - self._target_fps) > _FPS_MISMATCH_LOG_TOLERANCE:
            logger.warning(
                "%s: requested %.1ffps, device reports %.1ffps", self.label, self._target_fps, actual
            )

    def _configure_capture(self, cap: cv2.VideoCapture) -> None:
        """Backlight compensation, then let autofocus/auto-exposure settle
        against whatever this camera actually sees, then turn both off --
        otherwise a UVC webcam's driver defaults typically keep both
        continuously active, which can visibly hunt (refocus, re-expose)
        mid-recording, exactly the distracting-artifact problem
        ids_camera.py's `Once`-not-Continuous exposure convergence was
        built to avoid for the two instrument cameras (see DECISIONS.md).
        Never fails camera open: cv2.VideoCapture.set() returning False
        just means this device doesn't support that control, the same way
        ids_camera.py's IsAvailable() guard skips an axis the hardware
        doesn't expose.

        Backlight compensation is enabled first, before the warmup loop,
        so the auto-exposure convergence the warmup lets run happens
        *with* it already active -- it changes what auto-exposure is
        compensating for, so it needs to be in effect during, not after,
        that convergence.

        Fixed warmup window, not poll-until-converged: unlike GenICam's
        ExposureAuto/GainAuto nodes (which report back "Off" once `Once`
        mode finishes), UVC/DirectShow via cv2.VideoCapture exposes no
        readable "has this converged yet" signal to poll. See
        DECISIONS.md for why the specific numbers below are a starting
        guess, not a measured value.
        """
        cap.set(cv2.CAP_PROP_BACKLIGHT, 1)

        deadline = time.monotonic() + _AUTOEXPOSURE_WARMUP_TIMEOUT_S
        for _ in range(_AUTOEXPOSURE_WARMUP_FRAMES):
            if time.monotonic() > deadline:
                break
            cap.read()

        cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)

    def _close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _grab(self) -> tuple[np.ndarray, float, int] | None:
        ok, image = self._cap.read()
        if not ok or image is None:
            return None

        timestamp = time.monotonic()
        index = self._counter
        self._counter += 1
        return image, timestamp, index
