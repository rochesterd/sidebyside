"""Camera abstraction: a background capture thread feeding a bounded queue
and a "latest frame" slot, so consumers can either drain every frame or
just grab whatever is newest without blocking on capture speed.
"""

from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    image: np.ndarray  # BGR, HxWx3, uint8
    timestamp: float  # time.monotonic() at capture
    index: int  # sequential, starting at 0


class BaseCamera(ABC):
    """Owns a capture thread, a bounded frame queue, and a latest-frame slot.

    Subclasses implement _open, _close, _grab, and resolution. _grab is
    called repeatedly on the capture thread and should return a fresh
    (image, timestamp) pair or None if no frame was available.
    """

    def __init__(self, queue_size: int = 2):
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=queue_size)
        self._latest_lock = threading.Lock()
        self._latest: Frame | None = None
        self._index = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @abstractmethod
    def _open(self) -> None:
        """Acquire whatever resources are needed to start producing frames."""

    @abstractmethod
    def _close(self) -> None:
        """Release resources acquired in _open."""

    @abstractmethod
    def _grab(self) -> tuple[np.ndarray, float] | None:
        """Produce one (BGR image, monotonic timestamp) pair, or None."""

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

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        self._close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            result = self._grab()
            if result is None:
                continue
            image, timestamp = result
            frame = Frame(image=image, timestamp=timestamp, index=self._index)
            self._index += 1

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
