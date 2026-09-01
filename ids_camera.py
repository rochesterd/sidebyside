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

Frame.index is each buffer's own Buffer.FrameID(), not a locally-assigned
counter -- required by BaseCamera._grab's contract so a consumer draining
frames via read() can detect real gaps (including ones the device itself
introduced) rather than a renumbering that's gapless by construction. See
DECISIONS.md's "Frame.index was never actually gap-detectable" entry.

Hardware-verified against both real cameras via tools/smoke_test_camera.py
as of 2026-08-12 -- Keeler (U3-327xCP-C, serial 4110050487) and slit lamp
(UI325xCP-C, uEye Transport Layer, serial 4103484089). See DECISIONS.md's
two "Hardware smoke test" entries for what each surfaced and fixed. Known
platform difference between them, both handled: the uEye Transport Layer
doesn't implement DataStream.PayloadSize() (_payload_size() falls back to
the NodeMap) and has no ExposureAuto/GainAuto (_converge_auto_nodes()
skips both gracefully) -- the slit lamp camera needs a one-time
exposure/gain calibration once mounted on the instrument. That calibration
is done in-app now (needs_manual_calibration()/auto_calibrate()/the manual
get_/set_exposure_time_us()/get_/set_gain() pair below, driven by
settings.py's PreviewDialog), not via an external tool like IDS peak
Cockpit -- see ROADMAP.md's "In-app exposure/gain calibration" entry.

White balance (needs_manual_white_balance()/auto_white_balance()/the manual
get_/set_red_balance_ratio()/get_/set_blue_balance_ratio() pair) and the
acquisition frame-rate cap (_apply_frame_rate_cap()) follow the same
in-app-not-external-tool philosophy, added per ROADMAP.md's 2026-08-26
entry. `_converge_auto_nodes()` generalizes what used to be a single
exposure/gain-specific convergence loop (`_converge_auto_exposure()`) to
also cover `BalanceWhiteAuto`, since all three follow the identical
Once-then-poll-until-Off-then-lock shape.

None of the white-balance/frame-rate-cap additions, nor the generalized
`_converge_auto_nodes()` itself, are hardware-verified the way the rest of
this module is -- this dev machine has no ids_peak installed and no
vendor/ids_peak_api.txt dump to check node names against (see CLAUDE.md's
Environment section). Flagged node-by-node at each call site below;
re-running tools/smoke_test_camera.py against both real cameras is
warranted before trusting any of it, including the exposure/gain path,
since _converge_auto_nodes() changed its control flow even though per-axis
semantics didn't.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl

from camera import BaseCamera
from device_presets import orientation_for_model
from exposure_calibration import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_TARGET_MEDIAN,
    DEFAULT_TOLERANCE,
    DEFAULT_WB_MAX_ITERATIONS,
    DEFAULT_WB_TOLERANCE,
    center_crop,
    channel_medians,
    is_converged,
    is_white_balanced,
    median_brightness,
    next_balance_ratios,
    next_exposure_gain,
)

# Comfortably under BaseCamera.stop()'s 2.0s thread-join timeout, so a
# stop() call isn't left waiting on a blocked _grab().
_ACQUISITION_TIMEOUT_MS = 1000

# Buffers queued with the driver at any time. NumBuffersAnnouncedMinRequired
# is a per-device minimum; padding it gives the driver room to keep filling
# buffers while one is being converted here.
_MIN_BUFFER_COUNT = 4

# Wall-clock and frame-count bounds on the one-time auto-convergence pass
# (exposure/gain/white-balance, whichever axes lack a manually-calibrated
# config value) in _open(). Measured on real hardware (Keeler, serial
# 4110050487) at ~20fps: exposure/gain convergence took ~10 frames (~0.5s).
# Both bounds are generous multiples of that so a genuinely stuck
# convergence still fails loudly within a few seconds rather than hanging
# start(). Not re-measured with BalanceWhiteAuto added to the same pass --
# revisit if it turns out to need materially longer.
_AUTO_CONVERGE_TIMEOUT_S = 5.0
_AUTO_CONVERGE_MAX_FRAMES = 150

# auto_calibrate()'s per-iteration settle/read bounds. A frame already
# queued when ExposureTime/Gain just changed was captured under the
# *previous* setting -- this is how long to wait before trusting the next
# one read() returns to reflect the new value. Not yet measured against
# real hardware (no camera attached to this dev machine -- see CLAUDE.md's
# Environment section); revisit against tools/smoke_test_camera.py once
# hardware is available, the same way _AUTO_CONVERGE_TIMEOUT_S above was.
_CALIBRATION_SETTLE_S = 0.2
_CALIBRATION_FRAME_TIMEOUT_S = 1.0


class IdsCameraNotFoundError(RuntimeError):
    """No device with the requested serial number is present."""


class IdsCameraConvergenceTimeoutError(RuntimeError):
    """A one-time Once-mode auto-convergence pass (exposure/gain/white
    balance) didn't finish in time."""


class IdsCameraCalibrationError(RuntimeError):
    """auto_calibrate() couldn't get a live frame to measure."""


@dataclass
class IdsDeviceInfo:
    serial: str
    model_name: str


def list_ids_devices() -> list[IdsDeviceInfo]:
    """Currently-attached IDS peak GenICam devices, for settings.py's
    instrument-role dropdowns. Empty list is the correct, expected result
    with none attached -- same "0 found is fine" precedent as SETUP.md's
    verification script, which this mirrors exactly (including bracketing
    Library.Initialize()/Close() itself: unlike _open_device() below,
    which assumes _open() already did that around the whole camera
    lifecycle, this is a standalone one-shot scan with no camera object
    involved).
    """
    ids_peak.Library.Initialize()
    try:
        device_manager = ids_peak.DeviceManager.Instance()
        device_manager.Update()
        return [
            IdsDeviceInfo(serial=d.SerialNumber(), model_name=d.ModelName()) for d in device_manager.Devices()
        ]
    finally:
        ids_peak.Library.Close()


class IdsCamera(BaseCamera):
    """A single IDS peak GenICam device, opened by serial number.

    CLAUDE.md: cameras are identified by serial number, never device
    index — index order changes across reboots and USB port changes.
    """

    def __init__(
        self,
        serial: str,
        queue_size: int = 2,
        exposure_time_us: float | None = None,
        gain: float | None = None,
        red_balance_ratio: float | None = None,
        blue_balance_ratio: float | None = None,
        target_fps: float | None = None,
        orientation: str | None = None,
    ):
        super().__init__(queue_size=queue_size, label=serial, orientation=orientation)
        self._serial = serial
        # None (the default) means "resolve from the device-model preset in
        # _open()" -- e.g. the Keeler BIO camera delivers a vertically-
        # flipped image. An explicit value from config.json's `orientation`
        # is passed through instead and wins over the preset. See
        # device_presets.py.
        self._model_name: str | None = None
        # Per-instrument calibrated values from config.json (InstrumentConfig's
        # optional exposure_time_us/gain/red_balance_ratio/blue_balance_ratio
        # fields) -- see ROADMAP.md's "In-app exposure/gain calibration" entry
        # and its 2026-08-26 follow-up. None means "let _converge_auto_nodes()
        # handle this axis," not "leave whatever the device's NVRAM happens to
        # have," so a camera with no ExposureAuto/GainAuto/BalanceWhiteAuto at
        # all (the slit lamp) and no config values yet just keeps today's
        # pre-calibration behavior. red_balance_ratio/blue_balance_ratio are
        # config.py-validated as a pair -- either both set or neither.
        self._exposure_time_us = exposure_time_us
        self._gain = gain
        self._red_balance_ratio = red_balance_ratio
        self._blue_balance_ratio = blue_balance_ratio
        # Caps this camera's own acquisition rate (distinct from
        # recording.fps, which paces the encoder) -- see
        # _apply_frame_rate_cap()'s docstring. None means untouched free-run,
        # e.g. settings.py's Preview cameras, which deliberately never pass
        # this.
        self._target_fps = target_fps
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
        try:
            self._device = self._open_device()
            self._remote_device = self._device.RemoteDevice()
            self._node_map = self._remote_device.NodeMaps()[0]

            # Resolve a not-yet-decided orientation from the device model
            # now that we know it (self._model_name is set by
            # _open_device()). An explicit config value was already
            # validated in __init__.
            if self._orientation is None:
                self._orientation = orientation_for_model(self._model_name)

            self._width = int(self._node_map.FindNode("Width").Value())
            self._height = int(self._node_map.FindNode("Height").Value())

            data_stream = self._device.DataStreams()[0].OpenDataStream()
            payload_size = self._payload_size(data_stream)
            buffer_count = max(data_stream.NumBuffersAnnouncedMinRequired(), _MIN_BUFFER_COUNT)
            for _ in range(buffer_count):
                buffer = data_stream.AllocAndAnnounceBuffer(payload_size)
                data_stream.QueueBuffer(buffer)
            self._data_stream = data_stream

            self._node_map.FindNode("TLParamsLocked").SetValue(1)
            data_stream.StartAcquisition()
            self._node_map.FindNode("AcquisitionStart").Execute()
            self._node_map.FindNode("AcquisitionStart").WaitUntilDone()

            if self._target_fps is not None:
                self._apply_frame_rate_cap(self._target_fps)

            auto_converge_nodes = []
            if self._exposure_time_us is not None:
                self._ensure_manual_exposure()
                self.set_exposure_time_us(self._exposure_time_us)
            else:
                auto_converge_nodes.append("ExposureAuto")
            if self._gain is not None:
                self._ensure_manual_gain()
                self.set_gain(self._gain)
            else:
                auto_converge_nodes.append("GainAuto")
            if self._red_balance_ratio is not None:
                self._ensure_manual_white_balance()
                self.set_red_balance_ratio(self._red_balance_ratio)
                self.set_blue_balance_ratio(self._blue_balance_ratio)
            else:
                auto_converge_nodes.append("BalanceWhiteAuto")
            # Whichever axes config didn't supply a calibrated value for
            # still get today's one-time auto-converge, all in one pass (a
            # no-op for any axis with no *Auto node at all -- see
            # _converge_auto_nodes()'s docstring).
            self._converge_auto_nodes(auto_converge_nodes)
        except Exception:
            # A failure partway through leaves whatever got opened so far
            # (device, data stream, a running acquisition) dangling with
            # nothing to release it -- the next start() attempt would then
            # fail Control access as "busy" against our own leaked handle,
            # forever. _close() already tolerates being called from any
            # partial-init state (every step it touches is None-guarded).
            self._close()
            raise

    def _payload_size(self, data_stream: ids_peak.DataStream) -> int:
        """DataStream.PayloadSize() raises NotImplementedException on the
        uEye Transport Layer (slit lamp camera) -- STREAM_INFO_PAYLOAD_SIZE
        isn't implemented there, confirmed via hardware smoke test. The
        standard GenICam PayloadSize node on the remote device's node map
        works on both cameras, so use that as the fallback rather than the
        primary path, to avoid changing already-verified behavior on the
        Keeler.
        """
        try:
            return data_stream.PayloadSize()
        except (ids_peak.NotImplementedException, ids_peak.InternalErrorException):
            # The GenTL error code underneath is GC_ERR_NOT_IMPLEMENTED either
            # way, but which Python exception class it surfaces as depends on
            # the transport layer -- confirmed InternalErrorException on the
            # uEye Transport Layer via hardware smoke test; catching both
            # rather than trusting a single mapping across producers.
            return int(self._node_map.FindNode("PayloadSize").Value())

    def _converge_auto_nodes(self, node_names: list[str]) -> None:
        """One-time auto-convergence pass against whatever this camera
        actually sees, for any of `node_names` (each a GenICam *Auto enum
        node -- "ExposureAuto"/"GainAuto"/"BalanceWhiteAuto") that's
        actually available, then left locked for the session.

        Found via hardware smoke test (exposure/gain specifically): the
        sensor's power-on defaults (ExposureTime ~15ms, Gain 1.0) produced a
        near-black frame even pointed directly at a lamp. The right values
        depend on the room/instrument this camera is installed on -- so
        converge once against reality instead of hardcoding a number
        that's already been shown wrong for at least one room. `Once`
        rather than `Continuous` so nothing visibly hunts mid-recording.

        The uEye Transport Layer (slit lamp camera) only exposes a basic
        feature set per SETUP.md Section 3 and may lack any of these nodes
        entirely -- skip whichever isn't available rather than fail camera
        open over it. Returns immediately if none of `node_names` exist.
        `_open()` omits an axis from `node_names` entirely (rather than
        passing a skip flag) when config.json already supplied a manually-
        calibrated value for it -- distinct from "not available," since a
        camera that *has* e.g. ExposureAuto but was given a manual
        exposure_time_us shouldn't have this method fight the value
        _open() just set.

        Generalized from a single exposure/gain-specific convergence loop
        so BalanceWhiteAuto (added 2026-08-26) follows the identical
        Once-then-poll-until-Off shape in the same pass, rather than a
        second, separately-polled loop.
        """
        active_nodes = []
        for name in node_names:
            node = self._node_map.TryFindNode(name)
            if node is not None and node.IsAvailable() and node.IsWriteable():
                node.SetCurrentEntry("Once")
                active_nodes.append(node)

        if not active_nodes:
            return

        deadline = time.monotonic() + _AUTO_CONVERGE_TIMEOUT_S
        for _ in range(_AUTO_CONVERGE_MAX_FRAMES):
            buffer = self._data_stream.WaitForFinishedBuffer(_ACQUISITION_TIMEOUT_MS)
            self._data_stream.QueueBuffer(buffer)

            if all(node.CurrentEntry().SymbolicValue() == "Off" for node in active_nodes):
                return
            if time.monotonic() > deadline:
                break

        raise IdsCameraConvergenceTimeoutError(
            f"auto-convergence for {node_names} didn't finish within "
            f"{_AUTO_CONVERGE_TIMEOUT_S}s for serial {self._serial!r}"
        )

    def needs_manual_calibration(self) -> bool:
        """True when this camera has neither ExposureAuto nor GainAuto (the
        slit lamp camera, per CLAUDE.md's Hardware table) -- the case
        settings.py's PreviewDialog shows exposure/gain sliders and the
        auto-calibrate button for, instead of just a static preview. A
        camera with working auto-exposure (the Keeler) converges on its own
        every session and has nothing for a technician to calibrate.
        Mirrors the same IsAvailable() check _converge_auto_nodes() makes
        per axis. Must be called after start() -- self._node_map doesn't
        exist before _open() has run.
        """
        exposure_node = self._node_map.TryFindNode("ExposureAuto")
        has_auto_exposure = exposure_node is not None and exposure_node.IsAvailable()
        gain_node = self._node_map.TryFindNode("GainAuto")
        has_auto_gain = gain_node is not None and gain_node.IsAvailable()
        return not has_auto_exposure and not has_auto_gain

    def needs_manual_white_balance(self) -> bool:
        """True when this camera has no BalanceWhiteAuto *and* does expose
        the manual BalanceRatioSelector/BalanceRatio nodes to fall back on
        -- the case settings.py's PreviewDialog shows red/blue balance-ratio
        sliders and the Auto White-Balance button for. Must be called after
        start().

        Two cameras report False, for opposite reasons:
        - a camera with working BalanceWhiteAuto (the Keeler) converges on
          its own every session, folded into _converge_auto_nodes(), with
          nothing for a technician to calibrate;
        - a camera whose transport layer exposes neither the auto node nor
          the manual BalanceRatio nodes (the slit lamp via the uEye
          Transport Layer's basic feature set -- see this module's
          docstring) has no white balance to control at all. Reporting True
          there just makes _build_white_balance_controls() crash on the
          missing BalanceRatioSelector node.
        """
        auto_node = self._node_map.TryFindNode("BalanceWhiteAuto")
        if auto_node is not None and auto_node.IsAvailable():
            return False
        manual_node = self._node_map.TryFindNode("BalanceRatioSelector")
        return manual_node is not None and manual_node.IsAvailable()

    def _ensure_manual_exposure(self) -> None:
        node = self._node_map.TryFindNode("ExposureAuto")
        if node is not None and node.IsAvailable() and node.IsWriteable():
            node.SetCurrentEntry("Off")

    def _ensure_manual_gain(self) -> None:
        node = self._node_map.TryFindNode("GainAuto")
        if node is not None and node.IsAvailable() and node.IsWriteable():
            node.SetCurrentEntry("Off")

    def _ensure_manual_white_balance(self) -> None:
        node = self._node_map.TryFindNode("BalanceWhiteAuto")
        if node is not None and node.IsAvailable() and node.IsWriteable():
            node.SetCurrentEntry("Off")

    # NOTE: ExposureTime/Gain's Minimum()/Maximum() accessor names below
    # follow GenApi's standard IFloat node interface (the same convention
    # Value()/SetValue() already use for this file's Width/Height/PayloadSize
    # integer nodes) but, unlike every other node call in this file, are
    # *not* confirmed against vendor/ids_peak_api.txt or a hardware smoke
    # test -- this dev machine has no ids_peak installed to generate that
    # dump against (see CLAUDE.md's Environment section). Verify these
    # against a real ExposureTime/Gain node (regenerate vendor/ids_peak_api.txt
    # on a machine with the SDK, or run tools/smoke_test_camera.py against
    # the slit lamp) before relying on this in a real session.

    def get_exposure_time_us(self) -> float:
        return float(self._node_map.FindNode("ExposureTime").Value())

    def set_exposure_time_us(self, value: float) -> None:
        self._node_map.FindNode("ExposureTime").SetValue(value)

    def exposure_time_range_us(self) -> tuple[float, float]:
        node = self._node_map.FindNode("ExposureTime")
        return float(node.Minimum()), float(node.Maximum())

    def get_gain(self) -> float:
        return float(self._node_map.FindNode("Gain").Value())

    def set_gain(self, value: float) -> None:
        self._node_map.FindNode("Gain").SetValue(value)

    def gain_range(self) -> tuple[float, float]:
        node = self._node_map.FindNode("Gain")
        return float(node.Minimum()), float(node.Maximum())

    # NOTE: BalanceRatioSelector/BalanceRatio below is the standard GenICam
    # SFNC selector+value pattern for per-channel white balance gain (a
    # single BalanceRatio node whose meaning depends on which channel
    # BalanceRatioSelector currently names) but, like ExposureTime/Gain's
    # Minimum()/Maximum() above, is *not* confirmed against
    # vendor/ids_peak_api.txt or a hardware smoke test. Verify before relying
    # on this in a real session.

    def _select_balance_ratio(self, channel: str) -> None:
        self._node_map.FindNode("BalanceRatioSelector").SetCurrentEntry(channel)

    def get_red_balance_ratio(self) -> float:
        self._select_balance_ratio("Red")
        return float(self._node_map.FindNode("BalanceRatio").Value())

    def set_red_balance_ratio(self, value: float) -> None:
        self._select_balance_ratio("Red")
        self._node_map.FindNode("BalanceRatio").SetValue(value)

    def red_balance_ratio_range(self) -> tuple[float, float]:
        self._select_balance_ratio("Red")
        node = self._node_map.FindNode("BalanceRatio")
        return float(node.Minimum()), float(node.Maximum())

    def get_blue_balance_ratio(self) -> float:
        self._select_balance_ratio("Blue")
        return float(self._node_map.FindNode("BalanceRatio").Value())

    def set_blue_balance_ratio(self, value: float) -> None:
        self._select_balance_ratio("Blue")
        self._node_map.FindNode("BalanceRatio").SetValue(value)

    def blue_balance_ratio_range(self) -> tuple[float, float]:
        self._select_balance_ratio("Blue")
        node = self._node_map.FindNode("BalanceRatio")
        return float(node.Minimum()), float(node.Maximum())

    def auto_white_balance(
        self,
        tolerance: float = DEFAULT_WB_TOLERANCE,
        max_iterations: int = DEFAULT_WB_MAX_ITERATIONS,
    ) -> bool:
        """One-shot software white balance for a camera with no
        BalanceWhiteAuto (see needs_manual_white_balance()) -- run once when
        a technician holds a neutral gray/white target in the fixed optical
        path and clicks settings.py's Auto White-Balance button, not a
        continuous loop during real recording. See exposure_calibration.py
        for the actual per-channel-median/correction-step math.

        Returns True once within `tolerance` of balanced; False if
        `max_iterations` ran out first -- not raised, since settings.py's
        sliders remain a valid manual fallback either way. Only raises
        IdsCameraCalibrationError if a live frame never arrives at all.
        """
        self._ensure_manual_white_balance()
        red_range = self.red_balance_ratio_range()
        blue_range = self.blue_balance_ratio_range()

        for _ in range(max_iterations):
            image = center_crop(self._wait_for_fresh_frame())
            b_median, g_median, r_median = channel_medians(image)
            if is_white_balanced(b_median, g_median, r_median, tolerance):
                return True

            new_red, new_blue = next_balance_ratios(
                b_median,
                g_median,
                r_median,
                self.get_red_balance_ratio(),
                red_range,
                self.get_blue_balance_ratio(),
                blue_range,
            )
            self.set_red_balance_ratio(new_red)
            self.set_blue_balance_ratio(new_blue)

        return False

    def _apply_frame_rate_cap(self, target_fps: float) -> None:
        """Caps this camera's own acquisition rate to (not above) target_fps
        -- distinct from recording.fps, which only paces Recorder's encoder
        and does nothing to stop a camera from free-running faster than
        that and burning USB bandwidth for frames recorder.py's
        _drain_latest() then just discards unused. See ROADMAP.md's
        2026-08-26 entry for the bandwidth reasoning.

        Best-effort like every other node access in this file: silently
        does nothing if either node is absent, and clamps to whatever the
        camera can actually do (via Maximum()) rather than failing if
        target_fps exceeds that -- e.g. the slit lamp's own calibrated long
        exposure time is expected to make this a no-op there in practice.

        NOTE (unverified): AcquisitionFrameRateEnable gating
        AcquisitionFrameRate is a real GenICam SFNC variation across camera
        families -- this code tolerates either camera exposing it or not.
        Also unverified: whether AcquisitionFrameRate can be set *after*
        AcquisitionStart the way ExposureAuto/GainAuto already are
        (confirmed working post-start), since it more directly reconfigures
        stream timing than a pure value node. If a hardware test shows it
        must be set before streaming begins, move this call earlier in
        _open(), before data_stream.StartAcquisition().
        """
        enable_node = self._node_map.TryFindNode("AcquisitionFrameRateEnable")
        if enable_node is not None and enable_node.IsAvailable() and enable_node.IsWriteable():
            enable_node.SetValue(True)

        rate_node = self._node_map.TryFindNode("AcquisitionFrameRate")
        if rate_node is None or not rate_node.IsAvailable() or not rate_node.IsWriteable():
            return
        rate_node.SetValue(min(target_fps, float(rate_node.Maximum())))

    def auto_calibrate(
        self,
        target: float = DEFAULT_TARGET_MEDIAN,
        tolerance: float = DEFAULT_TOLERANCE,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> bool:
        """One-shot software auto-exposure for a camera with no
        ExposureAuto/GainAuto (see needs_manual_calibration()) -- run once
        when a technician clicks settings.py's Auto-Calibrate button, not a
        continuous loop during real recording. See exposure_calibration.py
        for the actual median-brightness/correction-step math and
        ROADMAP.md's calibration-UX entry for the full design rationale.

        Returns True once within `tolerance` of `target`; False if
        `max_iterations` ran out first (e.g. a scene brighter/darker than
        the achievable ExposureTime/Gain range can reach) -- not raised,
        since settings.py's sliders remain a valid manual fallback either
        way. Only raises IdsCameraCalibrationError if a live frame never
        arrives at all, which points at the camera/scene, not the
        algorithm.
        """
        self._ensure_manual_exposure()
        self._ensure_manual_gain()
        exposure_range = self.exposure_time_range_us()
        gain_range = self.gain_range()

        for _ in range(max_iterations):
            image = center_crop(self._wait_for_fresh_frame())
            median = median_brightness(image)
            if is_converged(median, target, tolerance):
                return True

            new_exposure, new_gain = next_exposure_gain(
                median, self.get_exposure_time_us(), exposure_range, self.get_gain(), gain_range, target=target
            )
            self.set_exposure_time_us(new_exposure)
            self.set_gain(new_gain)

        return False

    def _wait_for_fresh_frame(self) -> np.ndarray:
        """A frame already queued when ExposureTime/Gain just changed was
        captured under the *previous* setting -- sleep briefly for the
        sensor to apply the new value, drain whatever's now stale in the
        queue, then block for one truly new frame. Uses read() (the
        draining API), not get_latest() -- see camera.py's BaseCamera
        docstring on why a consumer that must know a frame is fresh drains
        the queue instead of peeking the latest-frame slot.
        """
        time.sleep(_CALIBRATION_SETTLE_S)
        while self.read(timeout=0) is not None:
            pass
        frame = self.read(timeout=_CALIBRATION_FRAME_TIMEOUT_S)
        if frame is None:
            raise IdsCameraCalibrationError(f"no frame received while calibrating serial {self._serial!r}")
        return frame.image

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

    def _grab(self) -> tuple[np.ndarray, float, int] | None:
        try:
            buffer = self._data_stream.WaitForFinishedBuffer(_ACQUISITION_TIMEOUT_MS)
        except ids_peak.TimeoutException:
            return None

        timestamp = time.monotonic()
        # The device's own frame sequence number, read before the buffer is
        # requeued -- see BaseCamera._grab's docstring for why this must be
        # the source's own counter rather than one we assign ourselves.
        # Confirmed via hardware smoke test to start at 0 and increment per
        # frame on both real cameras.
        frame_id = buffer.FrameID()
        image = ids_peak_ipl.Image.from_image_view(buffer.ToImageView())
        converted = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
        # Copy out of the converted Image's own buffer before it goes out
        # of scope, rather than trust an unverified zero-copy lifetime.
        array = converted.get_numpy_3D().copy()
        self._data_stream.QueueBuffer(buffer)

        return array, timestamp, frame_id

    def _open_device(self) -> ids_peak.Device:
        device_manager = ids_peak.DeviceManager.Instance()
        device_manager.Update()
        descriptors = device_manager.Devices()
        for descriptor in descriptors:
            if descriptor.SerialNumber() == self._serial:
                # Stashed for orientation_for_model() in _open(); ModelName()
                # is the same accessor list_ids_devices() already uses.
                self._model_name = descriptor.ModelName()
                return descriptor.OpenDevice(ids_peak.DeviceAccessType_Control)
        raise IdsCameraNotFoundError(
            f"no IDS device with serial {self._serial!r} found "
            f"({len(descriptors)} device(s) present)"
        )
