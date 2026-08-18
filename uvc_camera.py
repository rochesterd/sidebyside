"""UVC webcam implementation of BaseCamera, for the third-person view
(ELP-USB100W03M-L21) -- an ordinary UVC device via OpenCV's
cv2.VideoCapture, not a GenICam machine vision camera. See CLAUDE.md's
Hardware table.

Two deliberate deviations from ids_camera.py's contract, both accepted --
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
"""

from __future__ import annotations

import time

import cv2
import numpy as np

import uvc_enumeration
from camera import BaseCamera


class UvcCameraNotFoundError(RuntimeError):
    """The requested device index/path could not be opened."""


class UvcCamera(BaseCamera):
    def __init__(
        self,
        device: int | str | None = None,
        vid_pid: str | None = None,
        name: str = "third-person",
        queue_size: int = 2,
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

        # Queried rather than hardcoded -- CLAUDE.md's Conventions section:
        # measured values, not datasheet numbers, and this device's actual
        # resolution isn't known until it's opened.
        self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._counter = 0
        self._cap = cap

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
