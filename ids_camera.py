"""IDS peak GenICam camera implementation of BaseCamera.

One class covers both real cameras (Haag-Streit slit lamp UI-3250CP-C-HQ,
via the uEye Transport Layer, and Keeler U3-327xCP-C, native USB3 Vision):
once the uEye Transport Layer is installed (see SETUP.md Section 3), IDS
peak exposes both as ordinary GenICam devices on the same DeviceManager
list. Nothing in the acquisition path below is model-specific — only the
serial number passed to the constructor differs between the two physical
cameras. See CLAUDE.md's Architecture section: nothing outside this module
may import ids_peak/ids_peak_ipl.

Cameras are native Bayer sensors; frames are converted to BGR8 here so
every consumer downstream of BaseCamera (compositor, recorder, preview)
keeps working against Frame.image unmodified. Source pixel format is read
from each captured buffer rather than assumed, since the two camera
models may use different Bayer patterns.

Hardware-verified against the Keeler (U3-327xCP-C, serial 4110050487) via
tools/smoke_test_camera.py as of 2026-08-12 — see DECISIONS.md's "Hardware
smoke test found two real IdsCamera bugs" entry for what that surfaced and
fixed. The slit lamp camera (UI-3250CP-C-HQ, via the uEye Transport Layer)
has not been hardware-tested; per SETUP.md Section 3 that transport layer
exposes a more limited feature set, so _converge_auto_exposure() in
particular is unverified against it.
"""

from __future__ import annotations

import time

import numpy as np
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl

from camera import BaseCamera

# Comfortably under BaseCamera.stop()'s 2.0s thread-join timeout, so a
# stop() call isn't left waiting on a blocked _grab().
_ACQUISITION_TIMEOUT_MS = 1000

# Buffers queued with the driver at any time. NumBuffersAnnouncedMinRequired
# is a per-device minimum; padding it gives the driver room to keep filling
# buffers while one is being converted here.
_MIN_BUFFER_COUNT = 4

# Wall-clock and frame-count bounds on the one-time auto-exposure/gain
# convergence in _open(). Measured on real hardware (Keeler, serial
# 4110050487) at ~20fps: convergence took ~10 frames (~0.5s). Both bounds
# are generous multiples of that so a genuinely stuck convergence still
# fails loudly within a few seconds rather than hanging start().
_AUTO_EXPOSURE_TIMEOUT_S = 5.0
_AUTO_EXPOSURE_MAX_FRAMES = 150


class IdsCameraNotFoundError(RuntimeError):
    """No device with the requested serial number is present."""


class IdsCameraAutoExposureTimeoutError(RuntimeError):
    """Auto-exposure/gain convergence didn't finish in time."""


class IdsCamera(BaseCamera):
    """A single IDS peak GenICam device, opened by serial number.

    CLAUDE.md: cameras are identified by serial number, never device
    index — index order changes across reboots and USB port changes.
    """

    def __init__(self, serial: str, queue_size: int = 2):
        super().__init__(queue_size=queue_size)
        self._serial = serial
        # Every one of these must be kept as an instance attribute, not a
        # local in _open(). They wrap child GenTL handles (NodeMap,
        # DataStream) whose validity is tied to their parent's Python
        # wrapper staying alive -- a local `device` variable gets garbage
        # collected the moment _open() returns, which invalidates the
        # DataStream/NodeMap handles derived from it and makes the very
        # next WaitForFinishedBuffer() call raise InvalidInstanceException
        # from the capture thread. Found via hardware smoke test.
        self._device = None
        self._remote_device = None
        self._node_map = None
        self._data_stream = None
        self._width = 0
        self._height = 0

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    def _open(self) -> None:
        ids_peak.Library.Initialize()
        self._device = self._open_device()
        self._remote_device = self._device.RemoteDevice()
        self._node_map = self._remote_device.NodeMaps()[0]

        self._width = int(self._node_map.FindNode("Width").Value())
        self._height = int(self._node_map.FindNode("Height").Value())

        data_stream = self._device.DataStreams()[0].OpenDataStream()
        payload_size = data_stream.PayloadSize()
        buffer_count = max(data_stream.NumBuffersAnnouncedMinRequired(), _MIN_BUFFER_COUNT)
        for _ in range(buffer_count):
            buffer = data_stream.AllocAndAnnounceBuffer(payload_size)
            data_stream.QueueBuffer(buffer)
        self._data_stream = data_stream

        self._node_map.FindNode("TLParamsLocked").SetValue(1)
        data_stream.StartAcquisition()
        self._node_map.FindNode("AcquisitionStart").Execute()
        self._node_map.FindNode("AcquisitionStart").WaitUntilDone()

        self._converge_auto_exposure()

    def _converge_auto_exposure(self) -> None:
        """One-time auto-exposure/auto-gain pass against whatever light this
        camera actually sees, then left locked for the session.

        Found via hardware smoke test: the sensor's power-on defaults
        (ExposureTime ~15ms, Gain 1.0) produced a near-black frame even
        pointed directly at a lamp. The right exposure/gain depends on the
        room this camera is installed in, and there's no config system yet
        to hold a measured-per-install value (CLAUDE.md's Conventions
        section) -- so converge once against reality instead of hardcoding
        a number that's already been shown wrong for at least one room.
        `Once` rather than `Continuous` so exposure doesn't visibly hunt
        mid-recording.

        The uEye Transport Layer (slit lamp camera) only exposes a basic
        feature set per SETUP.md Section 3 and may lack these nodes
        entirely -- skip whichever axis isn't available rather than fail
        camera open over it.
        """
        exposure_node = self._node_map.TryFindNode("ExposureAuto")
        if exposure_node is not None and exposure_node.IsAvailable() and exposure_node.IsWriteable():
            exposure_node.SetCurrentEntry("Once")
        else:
            exposure_node = None

        gain_node = self._node_map.TryFindNode("GainAuto")
        if gain_node is not None and gain_node.IsAvailable() and gain_node.IsWriteable():
            gain_node.SetCurrentEntry("Once")
        else:
            gain_node = None

        if exposure_node is None and gain_node is None:
            return

        deadline = time.monotonic() + _AUTO_EXPOSURE_TIMEOUT_S
        for _ in range(_AUTO_EXPOSURE_MAX_FRAMES):
            buffer = self._data_stream.WaitForFinishedBuffer(_ACQUISITION_TIMEOUT_MS)
            self._data_stream.QueueBuffer(buffer)

            exposure_done = exposure_node is None or exposure_node.CurrentEntry().SymbolicValue() == "Off"
            gain_done = gain_node is None or gain_node.CurrentEntry().SymbolicValue() == "Off"
            if exposure_done and gain_done:
                return
            if time.monotonic() > deadline:
                break

        raise IdsCameraAutoExposureTimeoutError(
            f"auto-exposure/gain didn't converge within {_AUTO_EXPOSURE_TIMEOUT_S}s "
            f"for serial {self._serial!r}"
        )

    def _close(self) -> None:
        try:
            if self._data_stream is not None:
                self._node_map.FindNode("AcquisitionStop").Execute()
                self._node_map.FindNode("AcquisitionStop").WaitUntilDone()
                self._data_stream.StopAcquisition()
                self._data_stream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for buffer in list(self._data_stream.AnnouncedBuffers()):
                    self._data_stream.RevokeBuffer(buffer)
        finally:
            self._device = None
            self._remote_device = None
            self._node_map = None
            self._data_stream = None
            ids_peak.Library.Close()

    def _grab(self) -> tuple[np.ndarray, float] | None:
        try:
            buffer = self._data_stream.WaitForFinishedBuffer(_ACQUISITION_TIMEOUT_MS)
        except ids_peak.TimeoutException:
            return None

        timestamp = time.monotonic()
        image = ids_peak_ipl.Image.from_image_view(buffer.ToImageView())
        converted = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
        # Copy out of the converted Image's own buffer before it goes out
        # of scope, rather than trust an unverified zero-copy lifetime.
        array = converted.get_numpy_3D().copy()
        self._data_stream.QueueBuffer(buffer)

        return array, timestamp

    def _open_device(self) -> ids_peak.Device:
        device_manager = ids_peak.DeviceManager.Instance()
        device_manager.Update()
        descriptors = device_manager.Devices()
        for descriptor in descriptors:
            if descriptor.SerialNumber() == self._serial:
                return descriptor.OpenDevice(ids_peak.DeviceAccessType_Control)
        raise IdsCameraNotFoundError(
            f"no IDS device with serial {self._serial!r} found "
            f"({len(descriptors)} device(s) present)"
        )
