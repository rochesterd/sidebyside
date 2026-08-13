"""Camera abstraction: a background capture thread feeding a bounded queue
and a "latest frame" slot, so consumers can either drain every frame or
just grab whatever is newest without blocking on capture speed.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    image: np.ndarray  # BGR, HxWx3, uint8
    timestamp: float  # time.monotonic() at capture
    index: int  # source's own frame sequence number -- see BaseCamera._grab


class BaseCamera(ABC):
    """Owns a capture thread, a bounded frame queue, and a latest-frame slot.

    Subclasses implement _open, _close, _grab, and resolution. _grab is
    called repeatedly on the capture thread and should return a fresh
    (image, timestamp, index) triple or None if no frame was available.
    """

    def __init__(self, queue_size: int = 2, label: str | None = None):
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=queue_size)
        self._latest_lock = threading.Lock()
        self._latest: Frame | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # For log messages only -- distinguishes cam-a/cam-b (or a serial
        # number) in a shared log file where "SyntheticCamera" alone
        # wouldn't. Falls back to the class name if a subclass doesn't pass
        # one.
        self.label = label or type(self).__name__

    @abstractmethod
    def _open(self) -> None:
        """Acquire whatever resources are needed to start producing frames."""

    @abstractmethod
    def _close(self) -> None:
        """Release resources acquired in _open."""

    @abstractmethod
    def _grab(self) -> tuple[np.ndarray, float, int] | None:
        """Produce one (BGR image, monotonic timestamp, index) triple, or
        None if no frame was available.

        `index` must be the *source's* own frame sequence number (a real
        camera's own FrameID, or an equivalent counter for a synthetic
        source) rather than anything this class assigns itself. A consumer
        draining frames in order via read() detects gaps by comparing
        consecutive Frame.index values (see recorder.py) -- that only
        catches real drops if the number reported here reflects frames the
        source actually produced, including ones the source itself skipped
        without this class ever seeing them via a None return. Assigning a
        gapless counter here instead (as earlier versions of this class
        did) makes every source look drop-free by construction, silently
        hiding exactly the failure CLAUDE.md's Hardware section warns
        about: USB3 Vision degrading by dropping frames rather than
        raising an error. Not required to start at 0 or be gapless --
        recorder.py's frame counting does not depend on either.
        """

    @property
    @abstractmethod
    def resolution(self) -> tuple[int, int]:
        """(width, height) of frames this camera produces."""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._open()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("%s: capture started", self.label)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        self._close()
        logger.info("%s: capture stopped", self.label)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._grab()
            except Exception:
                # Logged and skipped, not re-raised: an unhandled exception
                # here would silently kill the capture thread with nothing
                # in get_latest()/read() ever indicating why -- the caller
                # would only notice minutes later via a stall. This at
                # least leaves a record of the actual cause. See
                # DECISIONS.md's Frame.index entry for the related history
                # of source-side drops going undetected.
                logger.exception("%s: _grab() raised; skipping this frame", self.label)
                continue
            if result is None:
                continue
            image, timestamp, index = result
            frame = Frame(image=image, timestamp=timestamp, index=index)

            with self._latest_lock:
                self._latest = frame

            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(frame)
                except queue.Full:
                    pass

    def get_latest(self) -> Frame | None:
        """Return the most recently captured frame without blocking."""
        with self._latest_lock:
            return self._latest

    def read(self, timeout: float | None = None) -> Frame | None:
        """Pop the next queued frame, blocking up to timeout seconds."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def __enter__(self) -> "BaseCamera":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
