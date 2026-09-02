"""Tests for app.KioskWindow's camera-start handling.

A real camera's start() can fail (wrong serial, device already open
elsewhere, UVC device not present, ...) in ways SyntheticCamera's never
does, since SyntheticCamera's _open() cannot raise. These exercise that
failure path with fake BaseCamera subclasses rather than requiring real
IDS/UVC hardware -- no PySide6 event loop is entered (no .exec()/.show()),
so this stays headless.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

import app
from app import KioskWindow
from camera import BaseCamera
from config import ConfigError
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


class TestWatchButton(unittest.TestCase):
    """The Watch button opens the just-finished session in the viewer --
    see ROADMAP.md's Recorder/Viewer split entry. open_session is patched
    out: what matters here is the gating and that the live preview is
    paused around it, not the viewer itself (test_viewer.py covers that).
    """

    def _window(self, tmp_root: str) -> tuple[KioskWindow, SyntheticCamera, SyntheticCamera]:
        third_person = SyntheticCamera(160, 120, fps=30)
        instrument = SyntheticCamera(160, 120, fps=30)
        window = KioskWindow(third_person, {"slit_lamp": instrument}, output_root=tmp_root)
        return window, third_person, instrument

    def test_disabled_until_a_session_has_been_recorded(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                window._sync_ui(window.controller.poll_preflight())
                self.assertFalse(window.watch_button.isEnabled())

                window.controller.last_session_dir = Path(tmp_root) / "2026-01-01_1200"
                window._sync_ui(window.controller.poll_preflight())
                self.assertTrue(window.watch_button.isEnabled())
            finally:
                third_person.stop()
                instrument.stop()

    def test_disabled_while_recording(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                window.controller.last_session_dir = Path(tmp_root) / "2026-01-01_1200"
                window.controller.state = State.RECORDING
                window._sync_ui()
                self.assertFalse(window.watch_button.isEnabled())
            finally:
                third_person.stop()
                instrument.stop()

    def test_clicking_opens_the_session_and_restarts_the_preview(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                session_dir = Path(tmp_root) / "2026-01-01_1200"
                window.controller.last_session_dir = session_dir

                with patch("app.open_session") as mock_open:
                    # The live preview must be paused while the modal
                    # viewer is up, and restarted afterwards.
                    mock_open.side_effect = lambda *a, **k: self.assertFalse(
                        window.preview_timer.isActive()
                    )
                    window._on_watch_clicked()

                mock_open.assert_called_once()
                self.assertEqual(mock_open.call_args.args[0], session_dir)
                self.assertTrue(window.preview_timer.isActive())
            finally:
                third_person.stop()
                instrument.stop()

    def test_preview_restarts_even_if_the_viewer_raises(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                window.controller.last_session_dir = Path(tmp_root) / "2026-01-01_1200"
                with patch("app.open_session", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        window._on_watch_clicked()
                self.assertTrue(window.preview_timer.isActive())
            finally:
                third_person.stop()
                instrument.stop()

    def test_does_nothing_when_no_session_has_been_recorded(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                with patch("app.open_session") as mock_open:
                    window._on_watch_clicked()
                mock_open.assert_not_called()
            finally:
                third_person.stop()
                instrument.stop()

    def test_past_recordings_browses_the_sessions_folder(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                with patch("app.browse_sessions") as mock_browse:
                    mock_browse.side_effect = lambda *a, **k: self.assertFalse(
                        window.preview_timer.isActive()
                    )
                    window._on_past_recordings_clicked()

                mock_browse.assert_called_once()
                self.assertEqual(mock_browse.call_args.args[0], Path(tmp_root))
                self.assertTrue(window.preview_timer.isActive())
            finally:
                third_person.stop()
                instrument.stop()

    def test_past_recordings_is_available_before_any_session_but_not_while_recording(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            window, third_person, instrument = self._window(tmp_root)
            try:
                window._sync_ui(window.controller.poll_preflight())
                self.assertTrue(window.past_button.isEnabled())

                window.controller.state = State.RECORDING
                window._sync_ui()
                self.assertFalse(window.past_button.isEnabled())

                with patch("app.browse_sessions") as mock_browse:
                    window._on_past_recordings_clicked()
                mock_browse.assert_not_called()
            finally:
                third_person.stop()
                instrument.stop()


class TestMissingConfigStartup(unittest.TestCase):
    """A missing/malformed config.json must surface visibly, not just to a
    log file. app.exe is built windowed (console=False, see
    packaging/app.spec) and launched from a Desktop shortcut with no
    console attached -- a log-and-exit with no QMessageBox is silent to
    whoever double-clicked the icon. Regression test for that gap, found
    by actually running the frozen installer on a machine with no
    config.json yet (see DECISIONS.md).
    """

    def test_config_error_shows_a_message_box_and_returns_nonzero(self):
        with (
            patch("sys.argv", ["app.py"]),
            patch("app.load_config", side_effect=ConfigError("config.json not found")),
            patch("app.QMessageBox.critical") as mock_critical,
        ):
            result = app.main()

        self.assertEqual(result, 1)
        mock_critical.assert_called_once()
        self.assertIn("config.json not found", mock_critical.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
