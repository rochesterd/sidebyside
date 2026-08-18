"""Tests for app.KioskWindow's camera-start handling.

A real camera's start() can fail (wrong serial, device already open
elsewhere, UVC device not present, ...) in ways SyntheticCamera's never
does, since SyntheticCamera's _open() cannot raise. These exercise that
failure path with fake BaseCamera subclasses rather than requiring real
IDS/UVC hardware -- no PySide6 event loop is entered (no .exec()/.show()),
so this stays headless.
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
    """A camera whose _open() always raises, like IdsCamera/UvcCamera do
    when the device isn't found or is already open elsewhere.
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
    def test_construction_does_not_raise_when_third_person_camera_fails_to_start(self):
        third_person = FailingCamera("could not open UVC device 0")
        instruments = {"slit_lamp": SyntheticCamera(160, 120, fps=30)}
        try:
            window = KioskWindow(third_person, instruments)
        except Exception as exc:
            self.fail(f"KioskWindow construction raised instead of degrading: {exc!r}")
        try:
            self.assertIn("third_person", window._camera_start_errors)
            self.assertIn("could not open UVC device", window._camera_start_errors["third_person"])

            # The third-person failure doesn't surface in the status line
            # until an instrument is picked -- before that, "pick one" is
            # the more useful message.
            status = window.controller.poll_preflight()
            self.assertEqual(window._idle_reason(status), "Select an instrument to begin.")

            window._on_instrument_clicked("slit_lamp")
            status = window.controller.poll_preflight()
            self.assertIn("could not open UVC device", window._idle_reason(status))
        finally:
            third_person.stop()
            instruments["slit_lamp"].stop()

    def test_instrument_start_failure_is_reported(self):
        third_person = SyntheticCamera(160, 120, fps=30)
        instruments = {"slit_lamp": FailingCamera("no IDS device with serial 'X' found")}
        try:
            window = KioskWindow(third_person, instruments)
            window._on_instrument_clicked("slit_lamp")

            self.assertIn("slit_lamp", window._camera_start_errors)
            self.assertIn("no IDS device", window._camera_start_errors["slit_lamp"])

            status = window.controller.poll_preflight()
            self.assertIn("no IDS device", window._idle_reason(status))
        finally:
            third_person.stop()

    def test_retry_recovers_once_the_instrument_starts_succeeding(self):
        third_person = SyntheticCamera(160, 120, fps=30)
        flaky = FlakyCamera(160, 120, fps=30, fail_times=1)
        instruments = {"slit_lamp": flaky}
        try:
            window = KioskWindow(third_person, instruments)
            window._on_instrument_clicked("slit_lamp")
            self.assertIn("slit_lamp", window._camera_start_errors)

            window._try_start_cameras()  # simulates the next retry-timer tick

            self.assertNotIn("slit_lamp", window._camera_start_errors)
        finally:
            third_person.stop()
            flaky.stop()


class TestCloseLockdown(unittest.TestCase):
    """A stray Alt+F4 or X-click shouldn't silently end a recording, but a
    deliberate force-quit must still be possible. See CLAUDE.md 'Who uses
    it' and DECISIONS.md's 2026-08-12 confirm-dialog entry.

    _confirm_stop_and_exit() is monkeypatched rather than driving the real
    QMessageBox, which would block waiting for input in a headless test.
    """

    def test_close_is_ignored_when_user_declines_the_confirm_dialog(self):
        third_person = SyntheticCamera(160, 120, fps=30)
        instruments = {"slit_lamp": SyntheticCamera(160, 120, fps=30)}
        window = KioskWindow(third_person, instruments)
        try:
            window._on_instrument_clicked("slit_lamp")
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
            third_person.stop()
            instruments["slit_lamp"].stop()

    def test_close_stops_recording_and_exits_when_user_confirms(self):
        third_person = SyntheticCamera(160, 120, fps=30)
        instruments = {"slit_lamp": SyntheticCamera(160, 120, fps=30)}
        window = KioskWindow(third_person, instruments)
        try:
            window._on_instrument_clicked("slit_lamp")
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
            third_person.stop()
            instruments["slit_lamp"].stop()

    def test_close_is_accepted_when_not_recording(self):
        third_person = SyntheticCamera(160, 120, fps=30)
        instruments = {"slit_lamp": SyntheticCamera(160, 120, fps=30)}
        window = KioskWindow(third_person, instruments)
        try:
            event = QCloseEvent()
            window.closeEvent(event)

            self.assertTrue(event.isAccepted())
        finally:
            third_person.stop()
            instruments["slit_lamp"].stop()


if __name__ == "__main__":
    unittest.main()
