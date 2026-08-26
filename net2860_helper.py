"""32-bit-only DirectShow capture helper for the older Vantage Plus BIO's
NET GmbH KS722OUP camera. Never imported by the main sidebyside app -- see
net2860_camera.py, which launches this as a subprocess and speaks
net2860_protocol.py's framed stdout protocol to it.

Must run under a 32-bit Python interpreter: the vendor's DirectShow filter
(netvecam4.ax, CLSID KS722OUP_CLSID below) and the Sample Grabber
registration it needs are only registered in Windows' WOW6432Node (32-bit)
COM view -- see DECISIONS.md's "Net2860Camera: 32-bit helper process for
the older Vantage Plus BIO" entry for the full investigation. Run under
.venv32/ (setup_net2860_helper.ps1 builds it), never under the project's
normal (64-bit) .venv.

Protocol: writes exactly one RDY1 (with the device-derived resolution) or
ERR1 message to stdout, then either streams FRM1 messages forever (RDY1
case) or exits (ERR1 case). No message-pump loop is needed to keep
ISampleGrabberCB.BufferCB callbacks firing -- confirmed empirically in the
proof-of-concept this module is built from.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# The 32-bit embeddable distribution's python*._pth file overrides sys.path
# entirely (that's the whole point of a ._pth file) and does not include
# the launched script's own directory the way a normal Python install does
# -- so net2860_protocol, which lives right next to this file, would
# otherwise not be importable. Same fix tools/smoke_test_camera.py applies
# for its own (different reason: different directory) cross-module import.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import comtypes
import numpy as np
from comtypes import GUID, client

from pygrabber.dshow_core import qedit
from pygrabber.dshow_graph import Filter, FilterGraph, FilterType, SampleGrabber
from pygrabber.dshow_ids import MediaSubtypes, MediaTypes

import net2860_protocol

# Identified via registry: HKLM\SOFTWARE\WOW6432Node\Classes\CLSID\{...}
# (default) = "KS722OUP", InprocServer32 = netvecam4.ax -- see DECISIONS.md.
KS722OUP_CLSID = "{6B83EF35-8FB5-45CB-BFF4-0876FF6F31D5}"

_stdout_lock = threading.Lock()


def _write(message: bytes) -> None:
    # Serialized because BufferCB fires on a DirectShow-internal thread, not
    # this module's main thread -- sys.stdout.buffer.write() itself is
    # thread-safe, but a lock keeps each message's bytes from interleaving
    # with another (there's only ever one writer in practice here, but this
    # is cheap insurance against that changing later).
    with _stdout_lock:
        sys.stdout.buffer.write(message)
        sys.stdout.buffer.flush()


class _StreamingCallback(comtypes.COMObject):
    """Forwards every ISampleGrabberCB.BufferCB call as a FRM1 message.

    Unlike pygrabber's own SampleGrabberCallback (dshow_graph.py), which is
    a single-shot "take one photo on demand" design gated by a keep_photo
    flag intended for its preview-window use case, this forwards
    continuously -- BaseCamera needs a steady stream, not on-demand
    snapshots. image_resolution is set by SampleGrabber.
    initialize_after_connection() after the graph connects, the same hook
    pygrabber's own callback class relies on.
    """

    _com_interfaces_ = [qedit.ISampleGrabberCB]

    def __init__(self):
        super().__init__()
        self.image_resolution: tuple[int, int] = (0, 0)
        self._index = 0

    def SampleCB(self, this, SampleTime, pSample) -> int:
        return 0

    def BufferCB(self, this, SampleTime, pBuffer, BufferLen) -> int:
        timestamp = time.monotonic()
        width, height = self.image_resolution
        img = np.ctypeslib.as_array(pBuffer, shape=(height, width, 3))
        # DirectShow's RGB24 rows are bottom-up; Frame.image (camera.py) is
        # documented top-down -- same correction pygrabber's own BufferCB
        # makes (dshow_graph.py's SampleGrabberCallback.BufferCB).
        img = np.flip(img, axis=0)
        _write(net2860_protocol.pack_frame(timestamp, self._index, img.tobytes()))
        self._index += 1
        return 0


def _build_and_run_graph() -> None:
    fg = FilterGraph()

    # Bypasses add_video_input_device()'s normal enumeration path: this
    # camera has no UVC/DirectShow-video-capture-source-category presence
    # to enumerate (see DECISIONS.md) -- the source filter must be
    # instantiated directly by CLSID, the same mechanism Keeler's own
    # "Kapture" app uses internally.
    instance = client.CreateObject(GUID(KS722OUP_CLSID), interface=qedit.IBaseFilter)
    video_input = Filter(instance, "KS722OUP", fg.capture_builder)
    fg.filters[FilterType.video_input] = video_input
    fg.filter_graph.AddFilter(video_input.instance, video_input.Name)

    # Bypasses FilterGraph.add_sample_grabber(): that convenience method
    # always wraps the callback in pygrabber's single-shot
    # SampleGrabberCallback, so building the SampleGrabber filter directly
    # here is what lets _StreamingCallback (continuous, not on-demand) be
    # used instead.
    sample_grabber = SampleGrabber(fg.capture_builder)
    fg.filters[FilterType.sample_grabber] = sample_grabber
    fg.filter_graph.AddFilter(sample_grabber.instance, sample_grabber.Name)
    callback = _StreamingCallback()
    sample_grabber.set_callback(callback, 1)
    sample_grabber.set_media_type(MediaTypes.Video, MediaSubtypes.RGB24)

    fg.add_null_render()

    # Connects video_input -> sample_grabber -> render, then calls
    # sample_grabber.initialize_after_connection(), which sets
    # callback.image_resolution from the now-connected media type --
    # device-derived, not hardcoded (CLAUDE.md's Conventions section).
    fg.prepare_preview_graph()

    width, height = callback.image_resolution
    _write(net2860_protocol.pack_ready(width, height))

    fg.run()

    # No message-pump loop needed to keep BufferCB firing (confirmed in the
    # proof-of-concept). The parent process (net2860_camera.py) owns this
    # process's lifetime entirely -- it terminates/kills us to stop; there
    # is no graceful-shutdown handshake to wait for here. See DECISIONS.md.
    threading.Event().wait()


def main() -> None:
    comtypes.CoInitialize()
    try:
        _build_and_run_graph()
    except Exception as exc:
        _write(net2860_protocol.pack_error(str(exc)))
        sys.exit(1)


if __name__ == "__main__":
    main()
