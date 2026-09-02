"""Headless tests for viewer.ViewerDialog against real recorded sessions.

No .show()/.exec() -- the dialog is constructed, driven directly, and torn
down, the same approach test_settings.py uses for PreviewDialog. The
teardown test is the important one: this dialog holds open PyAV decoders,
which is the same class of leak DECISIONS.md's "settings.py Preview leaked
the IDS device" entry describes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from recorder import INSTRUMENT_STREAM, THIRD_PERSON_STREAM
from session_reader import Session
from test_session_reader import record_session
from viewer import ViewerDialog, _mmss

_qt_app = QApplication.instance() or QApplication([])


class MmSsTest(unittest.TestCase):
    def test_formats_as_minutes_and_seconds(self):
        self.assertEqual(_mmss(0), "0:00")
        self.assertEqual(_mmss(9.9), "0:09")
        self.assertEqual(_mmss(65), "1:05")
        self.assertEqual(_mmss(-3), "0:00")


class ViewerDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One real recording shared across the tests -- recording is the
        # slow part, and none of these mutate the session.
        cls._tmp = tempfile.TemporaryDirectory()
        cls.session_dir = record_session(cls._tmp.name, 3)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _dialog(self) -> ViewerDialog:
        dialog = ViewerDialog(Session.load(self.session_dir))
        self.addCleanup(dialog._shutdown)
        return dialog

    def test_opens_paused_at_the_start_with_a_frame_shown(self):
        dialog = self._dialog()

        self.assertFalse(dialog._playing)
        self.assertEqual(dialog.player.position, 0.0)
        self.assertGreater(dialog.player.duration, 1.0)
        self.assertFalse(dialog.video_label.pixmap().isNull())
        self.assertIn("/", dialog.time_label.text())

    def test_every_layout_mode_renders(self):
        dialog = self._dialog()
        seen = set()
        for index in range(dialog.layout_box.count()):
            dialog.layout_box.setCurrentIndex(index)
            dialog._render()
            pixmap = dialog.video_label.pixmap()
            self.assertFalse(pixmap.isNull(), dialog.layout_box.currentData())
            seen.add(dialog.layout_box.currentData())
        self.assertEqual(
            seen, {"side_by_side", "picture_in_picture", "instrument", "third_person"}
        )

    def test_single_camera_layouts_use_the_full_canvas(self):
        dialog = self._dialog()
        dialog.video_label.resize(800, 400)
        for mode in ("instrument", "third_person"):
            index = dialog.layout_box.findData(mode)
            dialog.layout_box.setCurrentIndex(index)
            dialog._render()
            size = dialog.video_label.pixmap().size()
            self.assertEqual((size.width(), size.height()), (800, 400), mode)

    def test_scrubbing_seeks_and_pauses_then_resumes(self):
        dialog = self._dialog()
        dialog._set_playing(True)
        self.assertTrue(dialog._playing)

        dialog._on_scrub_start()
        self.assertFalse(dialog._playing)  # paused while dragging
        self.assertTrue(dialog._resume_after_scrub)

        dialog._on_scrub_move(500)  # halfway
        self.assertAlmostEqual(dialog.player.position, dialog.player.duration / 2, delta=0.05)

        dialog._on_scrub_end()
        self.assertTrue(dialog._playing)  # resumed, because it was playing before

    def test_scrub_from_paused_stays_paused(self):
        dialog = self._dialog()
        dialog._on_scrub_start()
        dialog._on_scrub_move(250)
        dialog._on_scrub_end()
        self.assertFalse(dialog._playing)

    def test_play_from_the_end_restarts_from_the_beginning(self):
        dialog = self._dialog()
        dialog.player.seek(dialog.player.duration)
        dialog._toggle_play()
        self.assertTrue(dialog._playing)
        self.assertEqual(dialog.player.position, 0.0)

    def test_tick_advances_media_time_while_playing(self):
        dialog = self._dialog()
        dialog._set_playing(True)
        # Pretend playback started a second ago rather than sleeping.
        dialog._play_started_wall -= 1.0
        dialog._tick()
        self.assertAlmostEqual(dialog.player.position, 1.0, delta=0.1)

    def test_playback_stops_at_the_end(self):
        dialog = self._dialog()
        dialog._set_playing(True)
        dialog._play_started_wall -= dialog.player.duration + 5.0
        dialog._tick()
        self.assertFalse(dialog._playing)
        self.assertAlmostEqual(dialog.player.position, dialog.player.duration, delta=0.01)

    def test_reject_releases_the_decoders(self):
        """Esc routes through QDialog.reject(), which delivers no
        QCloseEvent -- teardown must hang off `finished` or the PyAV
        containers leak. Same bug class as the settings.py Preview leak."""
        dialog = ViewerDialog(Session.load(self.session_dir))
        self.assertTrue(dialog.player._cursors)

        dialog.reject()

        self.assertFalse(dialog.player._cursors)
        self.assertFalse(dialog.timer.isActive())

    def test_shutdown_is_idempotent(self):
        dialog = ViewerDialog(Session.load(self.session_dir))
        dialog._shutdown()
        dialog._shutdown()

    def test_status_line_names_both_streams(self):
        dialog = self._dialog()
        text = dialog.status_label.text()
        session = Session.load(self.session_dir)
        self.assertIn(session.streams[INSTRUMENT_STREAM].label, text)
        self.assertIn(session.streams[THIRD_PERSON_STREAM].label, text)


if __name__ == "__main__":
    unittest.main()
