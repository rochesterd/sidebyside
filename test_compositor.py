"""Correctness tests for compositor.py.

Written after a perf rewrite (see DECISIONS.md - "compositor.py canvas
fills were the frame-drop bottleneck") that made _fit_into_pane write
directly into a view of the caller's canvas and skip filling areas it's
about to fully overwrite. That's less obviously correct than the original
build-a-fresh-array-and-copy version, and this file had no coverage at
all beforehand - these tests exist so a future change to that logic (or a
reintroduction of the slow np.full(shape, tuple) fill) gets caught.
"""

from __future__ import annotations

import unittest

import numpy as np

from compositor import picture_in_picture, side_by_side


def solid(height: int, width: int, color: tuple[int, int, int]) -> np.ndarray:
    """A solid-color BGR image, so resize/letterbox results are easy to
    assert on: the color at any pixel not touched by letterboxing should
    be exactly `color`.
    """
    return np.full((height, width, 3), color, dtype=np.uint8)


class TestSideBySide(unittest.TestCase):
    def test_output_shape_and_dtype(self):
        left = solid(1200, 1600, (10, 20, 30))
        right = solid(1542, 2056, (200, 100, 50))
        out = side_by_side(left, right, out_size=(2560, 1080))
        self.assertEqual(out.shape, (1080, 2560, 3))
        self.assertEqual(out.dtype, np.uint8)

    def test_panes_show_the_right_source_image(self):
        # Same aspect ratio as the canvas halves, so each pane is filled
        # edge-to-edge with no letterboxing - checks placement, not padding.
        left = solid(200, 200, (255, 0, 0))
        right = solid(200, 200, (0, 255, 0))
        out = side_by_side(left, right, out_size=(400, 200))
        self.assertTrue(np.all(out[:, :200] == (255, 0, 0)))
        self.assertTrue(np.all(out[:, 200:] == (0, 255, 0)))

    def test_letterbox_padding_uses_background_color(self):
        # A very wide image in a square pane must letterbox top/bottom.
        left = solid(50, 500, (255, 255, 255))
        right = solid(200, 200, (255, 255, 255))
        background = (40, 41, 42)
        out = side_by_side(left, right, out_size=(400, 200), background=background)

        left_pane = out[:, :200]
        # Some rows must be pure background (the letterbox bars); the
        # image itself is pure white, so background and content can't be
        # confused with each other.
        is_background_row = np.all(left_pane == background, axis=2).all(axis=1)
        self.assertTrue(is_background_row.any(), "expected at least one letterbox row")
        self.assertTrue((~is_background_row).any(), "expected at least one image row")

        # No pixel anywhere should be anything other than the image color
        # or the background color - i.e. no uninitialized memory leaked
        # through from the np.empty() canvas.
        valid = np.all(left_pane == (255, 255, 255), axis=2) | np.all(left_pane == background, axis=2)
        self.assertTrue(valid.all(), "found a pixel that's neither image nor background color")

    def test_default_out_size_matches_source_dimensions(self):
        left = solid(100, 150, (1, 2, 3))
        right = solid(100, 90, (4, 5, 6))
        out = side_by_side(left, right)
        self.assertEqual(out.shape, (100, 240, 3))

    def test_degenerate_zero_size_image_fills_background_without_crashing(self):
        left = np.zeros((0, 0, 3), dtype=np.uint8)
        right = solid(100, 100, (10, 10, 10))
        background = (7, 8, 9)
        out = side_by_side(left, right, out_size=(200, 100), background=background)
        self.assertTrue(np.all(out[:, :100] == background))


class TestPictureInPicture(unittest.TestCase):
    def test_output_shape(self):
        main = solid(1200, 1600, (10, 20, 30))
        pip = solid(1542, 2056, (200, 100, 50))
        out = picture_in_picture(main, pip, out_size=(1920, 1080))
        self.assertEqual(out.shape, (1080, 1920, 3))

    def test_pip_inset_shows_pip_color_in_requested_corner(self):
        main = solid(400, 400, (0, 0, 0))
        pip = solid(100, 100, (255, 255, 255))
        out = picture_in_picture(
            main, pip, out_size=(400, 400), pip_scale=0.25, margin=10, corner="top-left", border_thickness=0
        )
        # Center of the expected inset region should be pip-colored...
        self.assertTrue(np.all(out[20, 20] == (255, 255, 255)))
        # ...while a point far from any corner should still be main-colored.
        self.assertTrue(np.all(out[200, 200] == (0, 0, 0)))

    def test_pip_border_is_drawn_when_requested(self):
        main = solid(400, 400, (0, 0, 0))
        pip = solid(100, 100, (255, 255, 255))
        out = picture_in_picture(
            main,
            pip,
            out_size=(400, 400),
            pip_scale=0.25,
            margin=10,
            corner="bottom-right",
            border_color=(0, 255, 0),
            border_thickness=3,
        )
        # top-left corner of the pip inset should be the border color.
        pip_w = pip_h = round(400 * 0.25)
        x0 = 400 - pip_w - 10
        y0 = 400 - pip_h - 10
        self.assertTrue(np.all(out[y0, x0] == (0, 255, 0)))


if __name__ == "__main__":
    unittest.main()
