"""BaseCamera implementation for the older Vantage Plus Digital BIO's camera
-- a NET GmbH-OEM'd board built around an eMPIA EM2860 bridge chip (VID
0x20F1, PID 0x0004), distinct from the IDS U3-327xCP-C the newer Vantage
Plus uses (see CLAUDE.md's Hardware table).

This camera has no GenICam/USB3 Vision presence (ids_camera.py can't see it)
and no UVC/DirectShow-enumerable presence either (uvc_camera.py/
uvc_enumeration.py can't see it): its vendor driver registers a DirectShow
filter that must be instantiated directly by CLSID rather than discovered,
and that filter is 32-bit-only COM. See DECISIONS.md's "Net2860Camera:
32-bit helper process for the older Vantage Plus BIO" entry for the full
investigation (chip identification, the rejected generic-eMPIA-driver
alternative, why a subprocess).

Net2860Camera itself is pure stdlib plus subprocess management -- it never
imports comtypes/pygrabber. The actual DirectShow work happens in
net2860_helper.py, which only ever runs under a separate 32-bit Python
(.venv32/, see setup_net2860_helper.ps1) as a child process, communicating
over net2860_protocol.py's framed stdout protocol. This mirrors CLAUDE.md's
"nothing outside a camera module may reference the vendor SDK" rule, with
the process boundary standing in for the module boundary ids_camera.py/
uvc_camera.py normally provide on their own.

Frame.index is assigned by net2860_helper.py, incrementing once per
ISampleGrabberCB.BufferCB callback -- not a vendor-reported sequence number
(this filter's frame-numbering, if it has one, isn't documented or observed
anywhere, so inventing a read of one would be guessing at an undocumented
API surface the same way CLAUDE.md already warns against for the IDS SDK).
This is the same accepted deviation uvc_camera.py's UvcCamera already makes
from ids_camera.py's contract -- a locally-assigned counter standing in for
a real FrameID -- but assigned by the helper rather than by this class,
since the helper is the component actually sitting at the capture callback;
net2860_camera.py is one pipe-read removed from that boundary and re-counting
there would only be counting "frames this process happened to read", not
"frames the source produced".
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import numpy as np

import net2860_protocol
from camera import BaseCamera

logger = logging.getLogger(__name__)

_DEFAULT_STARTUP_TIMEOUT_S = 10.0

# How long _grab() sleeps between (nonexistent) read attempts once the
# helper process has been observed dead, so BaseCamera._run()'s while loop
# doesn't hot-spin logging/polling forever -- see _grab()'s docstring.
_DEAD_POLL_INTERVAL_S = 0.1


class Net2860CameraError(RuntimeError):
    """The helper process couldn't be started, or failed before/instead of
    producing a READY handshake. Mirrors UvcCameraNotFoundError's role in
    uvc_camera.py -- raised from _open(), so BaseCamera.start() leaves
    _thread as None and a later start() call retries from scratch."""


class Net2860Camera(BaseCamera):
    def __init__(
        self,
        queue_size: int = 2,
        label: str = "bio-legacy",
        python_exe: Path | None = None,
        helper_script: Path | None = None,
        startup_timeout: float = _DEFAULT_STARTUP_TIMEOUT_S,
    ):
        """python_exe/helper_script default to .venv32/python.exe and
        net2860_helper.py alongside this file; override for tests so they
        never touch a real subprocess/venv."""
        super().__init__(queue_size=queue_size, label=label)
        module_dir = Path(__file__).parent
        # .venv32/ is a 32-bit embeddable Python distribution (see
        # setup_net2860_helper.ps1), not a normal `python -m venv` --
        # embeddable distributions extract flat, with python.exe at the top
        # level, unlike a conventional venv's Scripts\python.exe.
        self._python_exe = python_exe or module_dir / ".venv32" / "python.exe"
        self._helper_script = helper_script or module_dir / "net2860_helper.py"
        self._startup_timeout = startup_timeout

        self._process: subprocess.Popen | None = None
        self._width = 0
        self._height = 0
        # Set once _grab() observes the helper process is gone -- see
        # _grab()'s docstring for why this degrades to a quiet None instead
        # of repeatedly raising.
        self._dead = False

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    def _open(self) -> None:
        if not self._python_exe.exists():
            raise Net2860CameraError(
                f"32-bit helper interpreter not found at {self._python_exe} "
                f"-- run setup_net2860_helper.ps1 once to create .venv32/"
            )
        if not self._helper_script.exists():
            raise Net2860CameraError(f"helper script not found at {self._helper_script}")

        self._process = subprocess.Popen(
            [str(self._python_exe), str(self._helper_script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._dead = False

        deadline = time.monotonic() + self._startup_timeout
        try:
            kind, payload = self._read_handshake(deadline)
        except net2860_protocol.ProtocolError as exc:
            self._fail_open(f"helper process handshake failed: {exc}")
            return  # unreachable, _fail_open always raises -- for type checkers

        if kind == "error":
            (message,) = payload
            self._fail_open(f"helper process reported: {message}")
            return
        if kind != "ready":
            self._fail_open(f"helper process sent unexpected first message: {kind!r}")
            return

        width, height = payload
        self._width = width
        self._height = height
        logger.info("%s: helper ready, resolution=%dx%d", self.label, width, height)

    def _read_handshake(self, deadline: float) -> tuple[str, tuple]:
        """Reads the helper's first message (READY or ERROR). A plain
        blocking read here -- rather than polling with the deadline -- is
        acceptable because this only runs once, synchronously, inside
        start() before the capture thread exists; a genuinely hung helper
        (never writes anything, never exits) would block past
        self._startup_timeout with no cheap way to interrupt a blocking
        pipe read from another thread. Accepted as a rare-failure-mode gap:
        a helper process that starts, holds the pipe open, and never
        writes is not a failure mode this device has exhibited."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise net2860_protocol.ProtocolError("startup timeout elapsed before first read")
        return net2860_protocol.read_message(self._process.stdout.read)

    def _fail_open(self, message: str) -> None:
        self._terminate_process()
        raise Net2860CameraError(f"{self.label}: {message}")

    def _grab(self) -> tuple[np.ndarray, float, int] | None:
        if self._dead:
            time.sleep(_DEAD_POLL_INTERVAL_S)
            return None

        try:
            kind, payload = net2860_protocol.read_message(self._process.stdout.read)
        except net2860_protocol.ProtocolError as exc:
            # Logged once, then this camera goes quiet rather than raising
            # repeatedly -- BaseCamera._run() has no backoff between _grab()
            # calls, so re-raising every call here would hot-loop and flood
            # the log. A camera that stops producing frames is already a
            # handled failure mode at the KioskController level (stall
            # detection), so no new error-propagation path is needed here.
            logger.error("%s: helper process stream ended (%s)", self.label, exc)
            self._dead = True
            return None

        if kind == "error":
            (message,) = payload
            logger.error("%s: helper process reported mid-stream: %s", self.label, message)
            self._dead = True
            return None
        if kind != "frame":
            logger.error("%s: unexpected message during streaming: %r", self.label, kind)
            self._dead = True
            return None

        timestamp, index, raw = payload
        # .copy(): np.frombuffer() is a read-only view over `raw` -- every
        # other camera in this codebase (cv2.VideoCapture.read(), the IDS
        # SDK) hands back a writable array, and downstream code shouldn't
        # have to know this one source is different.
        image = np.frombuffer(raw, dtype=np.uint8).reshape(self._height, self._width, 3).copy()
        return image, timestamp, index

    def _close(self) -> None:
        self._terminate_process()

    def _terminate_process(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.warning("%s: helper process did not exit after terminate(), killing", self.label)
            self._process.kill()
            self._process.wait(timeout=2.0)
        self._process = None
