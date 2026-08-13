"""Tests for app.KioskWindow's camera-start handling.

A real camera's start() can fail (wrong serial, device already open
elsewhere, ...) in ways SyntheticCamera's never does, since SyntheticCamera's
_open() cannot raise. These exercise that failure path with fake BaseCamera
subclasses rather than requiring real IDS hardware -- no PySide6 event loop
is entered (no .exec()/.show()), so this stays headless.
"""

from __future__ import annotations

import time
import unittest

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from app import KioskWindow
from camera import BaseCamera
from kiosk import State
from synthetic_camera import SyntheticCamera

_qt_app = QApplication.instance() or QApplication([])


class FailingCamera(BaseCamera):
    """A camera whose _open() always raises, like IdsCamera does when the
    serial isn't found or the device is already open elsewhere.
    """

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    @property
    def resolution(self) -> tuple[int, int]:
        return (0, 0)

    def _open(self) -> None:
        raise RuntimeError(self._message)

    def _close(self) -> None:
        pass

    def _grab(self):
        return None


class FlakyCamera(SyntheticCamera):
    """Fails _open() the first `fail_times` calls, then behaves like a
    normal SyntheticCamera -- for exercising retry-until-recovered.
    """

    def __init__(self, *args, fail_times: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_times = fail_times
        self._attempts = 0

    def _open(self) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_times:
            raise RuntimeError(f"attempt {self._attempts} failed")
        super()._open()


class TestCameraStartFailure(unittest.TestCase):
    def test_construction_does_not_raise_when_a_camera_fails_to_start(self):
        camera_a = FailingCamera("no IDS device with serial 'X' found")
        camera_b = SyntheticCamera(160, 120, fps=30)
        try:
            window = KioskWindow(camera_a, camera_b)
        except Exception as exc:
            self.fail(f"KioskWindow construction raised instead of degrading: {exc!r}")
        try:
            self.assertIn("camera_a", window._camera_start_errors)
            self.assertIn("no IDS device", window._camera_start_errors["camera_a"])

            status = window.controller.poll_preflight()
            reason = window._idle_reason(status)
            self.assertIn("no IDS device", reason)
        finally:
            camera_a.stop()
            camera_b.stop()

    def test_retry_recovers_once_the_camera_starts_succeeding(self):
        camera_a = FlakyCamera(160, 120, fps=30, fail_times=1)
        camera_b = SyntheticCamera(160, 120, fps=30)
        try:
            window = KioskWindow(camera_a, camera_b)
            self.assertIn("camera_a", window._camera_start_errors)

            window._try_start_cameras()  # simulates the next retry-timer tick

            self.assertNotIn("camera_a", window._camera_start_errors)
        finally:
            camera_a.stop()
            camera_b.stop()


class TestCloseLockdown(unittest.TestCase):
    """A stray Alt+F4 or X-click shouldn't silently end a recording, but a
    deliberate force-quit must still be possible. See CLAUDE.md 'Who uses
    it' and DECISIONS.md's 2026-08-12 confirm-dialog entry.

    _confirm_stop_and_exit() is monkeypatched rather than driving the real
    QMessageBox, which would block waiting for input in a headless test.
    """

    def test_close_is_ignored_when_user_declines_the_confirm_dialog(self):
        camera_a = SyntheticCamera(160, 120, fps=30)
        camera_b = SyntheticCamera(160, 120, fps=30)
        window = KioskWindow(camera_a, camera_b)
        try:
            time.sleep(0.2)  # let both cameras actually produce a frame
            window.controller.poll_preflight()
            window.controller.start_recording()
            window._confirm_stop_and_exit = lambda: False

            event = QCloseEvent()
            window.closeEvent(event)

            self.assertFalse(event.isAccepted())
            self.assertEqual(window.controller.state, State.RECORDING)
        finally:
            if window.controller.state == State.RECORDING:
                window.controller.stop_recording()
            camera_a.stop()
            camera_b.stop()

    def test_close_stops_recording_and_exits_when_user_confirms(self):
        camera_a = SyntheticCamera(160, 120, fps=30)
        camera_b = SyntheticCamera(160, 120, fps=30)
        window = KioskWindow(camera_a, camera_b)
        try:
            time.sleep(0.2)  # let both cameras actually produce a frame
            window.controller.poll_preflight()
            window.controller.start_recording()
            window._confirm_stop_and_exit = lambda: True

            event = QCloseEvent()
            window.closeEvent(event)

            self.assertTrue(event.isAccepted())
            self.assertNotEqual(window.controller.state, State.RECORDING)
            self.assertIsNotNone(window.controller.last_session_info)
        finally:
            camera_a.stop()
            camera_b.stop()

    def test_close_is_accepted_when_not_recording(self):
        camera_a = SyntheticCamera(160, 120, fps=30)
        camera_b = SyntheticCamera(160, 120, fps=30)
        window = KioskWindow(camera_a, camera_b)
        try:
            event = QCloseEvent()
            window.closeEvent(event)

            self.assertTrue(event.isAccepted())
        finally:
            camera_a.stop()
            camera_b.stop()


if __name__ == "__main__":
    unittest.main()
