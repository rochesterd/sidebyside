"""Tests for reflex_mark.ReflexMark, and that it agrees with branding/*.svg.

The widget draws the mark itself (it has to, to animate between states),
and the SVGs are what the .ico files are built from -- two copies of one
geometry. The agreement tests are what keep them from drifting apart.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import neco_reflex_theme as theme
from reflex_mark import (
    CENTER,
    IDLE_PUPIL,
    RECORDING_PUPIL,
    RING_RADIUS,
    RING_STROKE,
    TRANSITION_MS,
    ReflexMark,
    pupil_at,
)

_qt_app = QApplication.instance() or QApplication([])

BRANDING_DIR = Path(__file__).resolve().parent / "branding"
SVG = "{http://www.w3.org/2000/svg}"


def _shapes(filename: str) -> tuple[ET.Element, dict[str, ET.Element]]:
    root = ET.parse(BRANDING_DIR / filename).getroot()
    return root, {el.get("id"): el for el in root.iter() if el.get("id")}


def _settle(mark: ReflexMark) -> None:
    """Jump the running transition to its end, rather than sleeping."""
    mark._animation.setCurrentTime(mark._animation.duration())


def _grayscale(mark: ReflexMark) -> np.ndarray:
    image = QImage(mark.width(), mark.height(), QImage.Format.Format_ARGB32)
    image.fill(QColor(theme.CHARCOAL))
    mark.render(image)
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    rows = np.frombuffer(gray.constBits(), dtype=np.uint8).reshape(gray.height(), gray.bytesPerLine())
    return rows[:, : gray.width()].astype(int)


class TestSvgAgreement(unittest.TestCase):
    def test_both_marks_share_the_viewbox_and_ring(self):
        for filename in ("mark-idle.svg", "mark-recording.svg"):
            with self.subTest(filename):
                root, shapes = _shapes(filename)
                self.assertEqual(root.get("viewBox"), "0 0 100 100")
                ring = shapes["ring"]
                self.assertEqual(ring.tag, f"{SVG}circle")
                self.assertEqual(float(ring.get("cx")), CENTER)
                self.assertEqual(float(ring.get("cy")), CENTER)
                self.assertEqual(float(ring.get("r")), RING_RADIUS)
                self.assertEqual(float(ring.get("stroke-width")), RING_STROKE)
                self.assertEqual(ring.get("fill"), "none")
                self.assertEqual(ring.get("stroke"), "currentColor")

    def test_idle_pupil_is_a_slit_in_the_ring_color(self):
        _, shapes = _shapes("mark-idle.svg")
        pupil = shapes["pupil"]
        self.assertEqual(pupil.tag, f"{SVG}ellipse")
        self.assertEqual((float(pupil.get("rx")), float(pupil.get("ry"))), IDLE_PUPIL)
        self.assertEqual(pupil.get("fill"), "currentColor")

    def test_recording_pupil_is_round_and_the_recording_color(self):
        _, shapes = _shapes("mark-recording.svg")
        pupil = shapes["pupil"]
        self.assertEqual(pupil.tag, f"{SVG}circle")
        self.assertEqual((float(pupil.get("r")), float(pupil.get("r"))), RECORDING_PUPIL)
        # Swapping the theme's RECORDING_PUPIL means updating the SVG too.
        self.assertEqual(pupil.get("fill").upper(), theme.RECORDING_PUPIL.upper())


class TestPupilInterpolation(unittest.TestCase):
    def test_endpoints(self):
        rx, ry, fill = pupil_at(0.0, theme.OFF_WHITE)
        self.assertEqual((rx, ry), IDLE_PUPIL)
        self.assertEqual(fill.name().upper(), theme.OFF_WHITE.upper())

        rx, ry, fill = pupil_at(1.0, theme.OFF_WHITE)
        self.assertEqual((rx, ry), RECORDING_PUPIL)
        self.assertEqual(fill.name().upper(), theme.RECORDING_PUPIL.upper())

    def test_shape_and_color_move_together(self):
        rx, ry, fill = pupil_at(0.5, theme.OFF_WHITE)
        self.assertTrue(IDLE_PUPIL[0] < rx < RECORDING_PUPIL[0])
        self.assertTrue(IDLE_PUPIL[1] < ry < RECORDING_PUPIL[1])
        self.assertNotIn(fill.name().upper(), (theme.OFF_WHITE.upper(), theme.RECORDING_PUPIL.upper()))

    def test_progress_is_clamped(self):
        self.assertEqual(pupil_at(-1.0, theme.OFF_WHITE)[:2], IDLE_PUPIL)
        self.assertEqual(pupil_at(2.0, theme.OFF_WHITE)[:2], RECORDING_PUPIL)


class TestReflexMark(unittest.TestCase):
    def test_starts_idle(self):
        mark = ReflexMark()
        self.assertFalse(mark.recording)
        self.assertEqual(mark.progress, 0.0)

    def test_transitions_to_recording_and_back(self):
        mark = ReflexMark()
        mark.set_recording(True)
        self.assertEqual(mark._animation.duration(), TRANSITION_MS)
        _settle(mark)
        self.assertEqual(mark.progress, 1.0)

        # Coming back from a finished transition takes the full time too,
        # not a snap. (Regression: setting the animation's start/end values
        # re-emits valueChanged and used to clobber the progress read here.)
        mark.set_recording(False)
        self.assertEqual(mark._animation.duration(), TRANSITION_MS)
        _settle(mark)
        self.assertEqual(mark.progress, 0.0)

    def test_repeating_the_current_state_does_not_restart_the_transition(self):
        # app.py calls set_recording() on every 250ms poll tick.
        mark = ReflexMark()
        mark.set_recording(True)
        mark._animation.setCurrentTime(TRANSITION_MS // 2)
        mark.set_recording(True)
        self.assertEqual(mark._animation.currentTime(), TRANSITION_MS // 2)

    def test_reversing_mid_transition_starts_from_where_it_is(self):
        mark = ReflexMark()
        mark.set_recording(True)
        mark._animation.setCurrentTime(TRANSITION_MS // 2)
        midway = mark.progress
        self.assertTrue(0.0 < midway < 1.0)

        mark.set_recording(False)
        self.assertAlmostEqual(mark._animation.startValue(), midway)
        self.assertEqual(mark._animation.duration(), round(TRANSITION_MS * midway))
        _settle(mark)
        self.assertEqual(mark.progress, 0.0)

    def test_state_survives_grayscale(self):
        # Never color as the only signal: with the color taken away, the
        # two states must still differ over a real share of the mark.
        mark = ReflexMark(size=100)
        idle = _grayscale(mark)
        mark.set_recording(True)
        _settle(mark)
        recording = _grayscale(mark)
        changed = np.abs(idle - recording) > 24
        self.assertGreater(changed.mean(), 0.05)

    def test_is_not_interactive(self):
        # While recording nothing but Stop may be interactive.
        mark = ReflexMark()
        self.assertEqual(mark.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(mark.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_accessible_name_follows_state(self):
        mark = ReflexMark()
        self.assertEqual(mark.accessibleName(), "Not recording")
        mark.set_recording(True)
        self.assertEqual(mark.accessibleName(), "Recording")


if __name__ == "__main__":
    unittest.main()
