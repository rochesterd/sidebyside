"""Headless tests for kiosk.KioskController, the Qt-free state machine
behind app.py. Drives real SyntheticCamera instances and a real Recorder,
the same integration-style approach as test_recorder.py, rather than
mocking internals.
"""

from __future__ import annotations

import tempfile
import time
import types
import unittest

from kiosk import KioskController, State, estimate_recording_bytes
from synthetic_camera import SyntheticCamera


class FakeClock:
    """Manually-advanced clock so stall-detection tests don't need to
    actually sleep past the timeout.
    """

    def __init__(self, start: float = 0.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class _FailOnceCamera(SyntheticCamera):
    """Fails _open() exactly once, then behaves like a normal
    SyntheticCamera -- for exercising a failed instrument switch.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._should_fail = True

    def _open(self) -> None:
        if self._should_fail:
            self._should_fail = False
            raise RuntimeError("simulated start failure")
        super()._open()


def dynamic_disk_usage(free_holder: dict):
    """Fake shutil.disk_usage backed by a mutable dict, so a test can
    change the reported free space between polls without reaching into
    KioskController's internals.
    """

    def _disk_usage(_path: str):
        return types.SimpleNamespace(total=10**12, used=0, free=free_holder["free"])

    return _disk_usage


class TestEstimateRecordingBytes(unittest.TestCase):
    def test_scales_with_resolution_and_minutes(self):
        base = estimate_recording_bytes(1280, 720, 30, minutes=10)
        double_res = estimate_recording_bytes(2560, 1440, 30, minutes=10)
        double_minutes = estimate_recording_bytes(1280, 720, 30, minutes=20)
        self.assertAlmostEqual(double_res, base * 4, delta=base * 0.01)
        self.assertAlmostEqual(double_minutes, base * 2, delta=base * 0.01)


class TestKioskControllerPreflight(unittest.TestCase):
    def test_start_stays_blocked_with_no_instrument_selected(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            third_person = SyntheticCamera(160, 120, fps=30)
            instrument = SyntheticCamera(160, 120, fps=30)
            third_person.start()
            instrument.start()
            try:
                controller = KioskController(third_person, {"instrument": instrument}, output_root=tmp_root)
                time.sleep(0.2)
                status = controller.poll_preflight()

                self.assertIsNone(controller.selected_instrument)
                self.assertFalse(status.cameras_ready)
                self.assertEqual(controller.state, State.IDLE)
            finally:
                third_person.stop()
                instrument.stop()

    def test_start_stays_blocked_until_both_cameras_have_a_frame(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            instrument = SyntheticCamera(160, 120, fps=30)
            third_person = SyntheticCamera(160, 120, fps=30)
            instrument.start()
            # third_person is deliberately never started: get_latest() stays None.
            try:
                controller = KioskController(third_person, {"instrument": instrument}, output_root=tmp_root)
                controller.select_instrument("instrument")
                time.sleep(0.2)  # let instrument actually produce a frame
                status = controller.poll_preflight()

                self.assertFalse(status.cameras_ready)
                self.assertEqual(controller.state, State.IDLE)
            finally:
                instrument.stop()

    def test_disk_space_gate_blocks_and_then_releases_start(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            instrument = SyntheticCamera(160, 120, fps=30)
            third_person = SyntheticCamera(160, 120, fps=30)
            instrument.start()
            third_person.start()
            try:
                free_holder = {"free": 0}
                controller = KioskController(
                    third_person,
                    {"instrument": instrument},
                    output_root=tmp_root,
                    disk_usage_fn=dynamic_disk_usage(free_holder),
                )
                controller.select_instrument("instrument")
                time.sleep(0.2)

                status = controller.poll_preflight()
                self.assertTrue(status.cameras_ready)
                self.assertFalse(status.disk_ok)
                self.assertEqual(controller.state, State.IDLE)

                free_holder["free"] = 10**15
                status = controller.poll_preflight()
                self.assertTrue(status.disk_ok)
                self.assertEqual(controller.state, State.READY)
            finally:
                instrument.stop()
                third_person.stop()


class TestInstrumentSelection(unittest.TestCase):
    def test_select_instrument_starts_and_stops_cameras(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            third_person = SyntheticCamera(160, 120, fps=30)
            cam_a = SyntheticCamera(160, 120, fps=30)
            cam_b = SyntheticCamera(160, 120, fps=30)
            third_person.start()
            try:
                controller = KioskController(third_person, {"a": cam_a, "b": cam_b}, output_root=tmp_root)
                self.assertIsNone(controller.selected_instrument)

                controller.select_instrument("a")
                self.assertEqual(controller.selected_instrument, "a")
                time.sleep(0.1)
                self.assertIsNotNone(cam_a.get_latest())

                controller.select_instrument("b")
                self.assertEqual(controller.selected_instrument, "b")
                time.sleep(0.1)
                self.assertIsNotNone(cam_b.get_latest())
                # Switching must stop the camera that's no longer selected --
                # only one instrument camera runs at a time (see CLAUDE.md's
                # Architecture section and DECISIONS.md's "Third-person UVC
                # camera" entry).
                self.assertIsNone(cam_a._thread)
            finally:
                third_person.stop()
                cam_a.stop()
                cam_b.stop()

    def test_select_instrument_is_a_noop_when_already_selected(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            third_person = SyntheticCamera(160, 120, fps=30)
            cam_a = SyntheticCamera(160, 120, fps=30)
            third_person.start()
            try:
                controller = KioskController(third_person, {"a": cam_a}, output_root=tmp_root)
                controller.select_instrument("a")
                thread_before = cam_a._thread
                controller.select_instrument("a")
                self.assertIs(cam_a._thread, thread_before)
            finally:
                third_person.stop()
                cam_a.stop()

    def test_select_instrument_raises_while_recording(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            third_person = SyntheticCamera(160, 120, fps=30)
            cam_a = SyntheticCamera(160, 120, fps=30)
            cam_b = SyntheticCamera(160, 120, fps=30)
            third_person.start()
            try:
                controller = KioskController(
                    third_person,
                    {"a": cam_a, "b": cam_b},
                    output_root=tmp_root,
                    width=160,
                    height=120,
                    fps=30,
                )
                controller.select_instrument("a")
                time.sleep(0.2)
                controller.poll_preflight()
                controller.start_recording()
                try:
                    with self.assertRaises(RuntimeError):
                        controller.select_instrument("b")
                finally:
                    controller.stop_recording()
            finally:
                third_person.stop()
                cam_a.stop()
                cam_b.stop()

    def test_failed_switch_leaves_no_instrument_selected(self):
        """Regression test: if the newly-selected camera's start() raises,
        the previous camera has already been stopped -- selected_instrument
        must not keep pointing at it, or preflight would trust a frozen
        stale frame from a camera that isn't actually running anymore.
        """
        with tempfile.TemporaryDirectory() as tmp_root:
            third_person = SyntheticCamera(160, 120, fps=30)
            cam_a = SyntheticCamera(160, 120, fps=30)
            cam_b = _FailOnceCamera(160, 120, fps=30)
            third_person.start()
            try:
                controller = KioskController(third_person, {"a": cam_a, "b": cam_b}, output_root=tmp_root)
                controller.select_instrument("a")
                self.assertEqual(controller.selected_instrument, "a")

                with self.assertRaises(RuntimeError):
                    controller.select_instrument("b")

                self.assertIsNone(controller.selected_instrument)
            finally:
                third_person.stop()
                cam_a.stop()
                cam_b.stop()


class TestKioskControllerSession(unittest.TestCase):
    def test_happy_path_full_session(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            instrument = SyntheticCamera(160, 120, fps=30, name="instrument")
            third_person = SyntheticCamera(160, 120, fps=30, name="third-person")
            instrument.start()
            third_person.start()
            try:
                controller = KioskController(
                    third_person,
                    {"instrument": instrument},
                    output_root=tmp_root,
                    width=160,
                    height=120,
                    fps=30,
                )
                controller.select_instrument("instrument")
                time.sleep(0.2)
                status = controller.poll_preflight()
                self.assertTrue(status.ok)
                self.assertEqual(controller.state, State.READY)

                controller.start_recording()
                self.assertEqual(controller.state, State.RECORDING)
                time.sleep(2.0)
                controller.poll_recording()
                self.assertEqual(controller.state, State.RECORDING)

                session_info = controller.stop_recording()
                self.assertEqual(controller.state, State.IDLE)
                self.assertIsNone(controller.error_message)

                self.assertGreater(session_info["composite"]["frame_count"], 0)
                for cam in session_info["cameras"].values():
                    self.assertGreaterEqual(cam["frame_count"], 1)
                    self.assertGreaterEqual(cam["dropped_frames"], 0)
                names = {cam["name"] for cam in session_info["cameras"].values()}
                self.assertEqual(names, {"instrument", "third_person"})

                mp4_path = controller.last_session_dir / "composite.mp4"
                self.assertTrue(mp4_path.exists())
                self.assertGreater(mp4_path.stat().st_size, 0)

                # Re-running preflight after the session ends should let
                # Start become available again.
                status = controller.poll_preflight()
                self.assertTrue(status.ok)
                self.assertEqual(controller.state, State.READY)
            finally:
                instrument.stop()
                third_person.stop()

    def test_mid_recording_stall_triggers_loud_error_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            instrument = SyntheticCamera(160, 120, fps=30, name="instrument")
            third_person = SyntheticCamera(160, 120, fps=30, name="third-person")
            instrument.start()
            third_person.start()
            try:
                fake_clock = FakeClock()
                controller = KioskController(
                    third_person,
                    {"instrument": instrument},
                    output_root=tmp_root,
                    width=160,
                    height=120,
                    fps=30,
                    stall_timeout_s=1.0,
                    clock=fake_clock,
                )
                controller.select_instrument("instrument")
                time.sleep(0.2)
                controller.poll_preflight()
                self.assertEqual(controller.state, State.READY)

                controller.start_recording()
                time.sleep(0.3)  # let a few real frames get encoded first
                controller.poll_recording()  # baseline reflecting those real frames
                self.assertEqual(controller.state, State.RECORDING)

                third_person.drop_rate = 1.0  # simulate the third-person camera going silent
                fake_clock.advance(1.5)  # past stall_timeout_s, no real wait needed
                controller.poll_recording()

                self.assertEqual(controller.state, State.ERROR)
                self.assertIsNotNone(controller.error_message)
                self.assertIn("third_person", controller.error_message)

                # The recording must have been stopped cleanly, not abandoned.
                self.assertIsNotNone(controller.last_session_info)
                self.assertIn("composite", controller.last_session_info)
                mp4_path = controller.last_session_dir / "composite.mp4"
                self.assertTrue(mp4_path.exists())
                self.assertGreater(mp4_path.stat().st_size, 0)

                # The state machine itself isn't stuck: the next preflight
                # poll re-evaluates and moves on, even though the banner
                # (error_message / last_session_info) stays put for the UI.
                controller.poll_preflight()
                self.assertNotEqual(controller.state, State.ERROR)
            finally:
                instrument.stop()
                third_person.stop()


if __name__ == "__main__":
    unittest.main()
