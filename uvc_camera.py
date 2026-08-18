"""UVC webcam implementation of BaseCamera, for the third-person view
(ELP-USB100W03M-L21) -- an ordinary UVC device via OpenCV's
cv2.VideoCapture, not a GenICam machine vision camera. See CLAUDE.md's
Hardware table.

Two deliberate deviations from ids_camera.py's contract, both accepted --
see DECISIONS.md's "Third-person UVC camera" entry for why:

- Identified by device index, not serial number. cv2.VideoCapture's
  DirectShow backend has no stable per-device identifier to open by, only
  an enumeration index -- acceptable as long as this is the only UVC
  camera attached.
- Frame.index is a locally-assigned counter, not a source-reported
  sequence number, since UVC/DirectShow exposes no such counter. This
  camera can't detect true source-side drops the way IdsCamera can; only
  consumer-side queue evictions show up in session.json's dropped_frames.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from camera import BaseCamera


class UvcCameraNotFoundError(RuntimeError):
    """The requested device index/path could not be opened."""


class UvcCamera(BaseCamera):
    def __init__(self, device: int | str, name: str = "third-person", queue_size: int = 2):
        super().__init__(queue_size=queue_size, label=name)
        self._device = device
        self._cap: cv2.VideoCapture | None = None
        self._width = 0
        self._height = 0
        self._counter = 0

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    def _open(self) -> None:
        cap = cv2.VideoCapture(self._device, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise UvcCameraNotFoundError(f"could not open UVC device {self._device!r}")

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
