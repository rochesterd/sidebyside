"""BaseCamera behavior that isn't tied to a specific subclass.

The orientation mechanic is exercised through a tiny fixed-frame fake (so
the input pixels are known exactly) and through a real SyntheticCamera
capture thread for the end-to-end path.
"""

from __future__ import annotations

import time
import unittest

import numpy as np

from camera import (
    BaseCamera,
    ORIENTATION_FLIP_HORIZONTAL,
    ORIENTATION_FLIP_VERTICAL,
    ORIENTATION_NONE,
    ORIENTATION_ROTATE_180,
    VALID_ORIENTATIONS,
    apply_orientation,
)
from synthetic_camera import SyntheticCamera


class _FixedFrameCamera(BaseCamera):
    """Emits one known frame repeatedly -- lets a test assert exactly what
    the orientation transform did to specific pixels."""

    def __init__(self, image: np.ndarray, orientation: str | None = ORIENTATION_NONE):
        super().__init__(queue_size=2, label="fixed", orientation=orientation)
        self._source = image

    @property
    def resolution(self) -> tuple[int, int]:
        h, w = self._source.shape[:2]
        return (w, h)

    def _open(self) -> None: ...
    def _close(self) -> None: ...

    def _grab(self):
        return self._source.copy(), time.monotonic(), 0


def _first_frame(camera: BaseCamera, timeout: float = 2.0):
    camera.start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = camera.get_latest()
            if frame is not None:
                return frame
            time.sleep(0.01)
        raise AssertionError("no frame produced within timeout")
    finally:
        camera.stop()


class OrientationTest(unittest.TestCase):
    def _marked_frame(self) -> np.ndarray:
        # 3 rows x 4 cols, marker only at top-left.
        img = np.zeros((3, 4, 3), dtype=np.uint8)
        img[0, 0] = (255, 0, 0)
        return img

    def test_none_leaves_the_frame_untouched(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), ORIENTATION_NONE))
        self.assertEqual(tuple(frame.image[0, 0]), (255, 0, 0))
        self.assertEqual(frame.image.shape, (3, 4, 3))

    def test_rotate_180_moves_top_left_to_bottom_right(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), ORIENTATION_ROTATE_180))
        self.assertEqual(frame.image.shape, (3, 4, 3))
        self.assertEqual(tuple(frame.image[2, 3]), (255, 0, 0))
        self.assertEqual(tuple(frame.image[0, 0]), (0, 0, 0))
        self.assertTrue(frame.image.flags["C_CONTIGUOUS"])

    def test_flip_vertical_moves_top_left_to_bottom_left(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), ORIENTATION_FLIP_VERTICAL))
        self.assertEqual(frame.image.shape, (3, 4, 3))
        self.assertEqual(tuple(frame.image[2, 0]), (255, 0, 0))
        self.assertEqual(tuple(frame.image[0, 0]), (0, 0, 0))
        self.assertTrue(frame.image.flags["C_CONTIGUOUS"])

    def test_flip_horizontal_moves_top_left_to_top_right(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), ORIENTATION_FLIP_HORIZONTAL))
        self.assertEqual(frame.image.shape, (3, 4, 3))
        self.assertEqual(tuple(frame.image[0, 3]), (255, 0, 0))
        self.assertEqual(tuple(frame.image[0, 0]), (0, 0, 0))

    def test_none_and_missing_arg_behave_identically(self):
        default = _first_frame(_FixedFrameCamera(self._marked_frame()))
        self.assertEqual(tuple(default.image[0, 0]), (255, 0, 0))

    def test_none_orientation_arg_is_allowed_and_acts_as_no_transform(self):
        # None means "a subclass may resolve it in _open()"; a subclass that
        # never does must behave as ORIENTATION_NONE.
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), None))
        self.assertEqual(tuple(frame.image[0, 0]), (255, 0, 0))

    def test_invalid_orientation_rejected_at_construction(self):
        for bad in ("upside_down", "rotate_90", 180, ""):
            with self.assertRaises(ValueError):
                SyntheticCamera(120, 90, orientation=bad)

    def test_synthetic_camera_end_to_end_with_flip_vertical(self):
        frame = _first_frame(SyntheticCamera(120, 90, fps=60, orientation=ORIENTATION_FLIP_VERTICAL))
        self.assertEqual(frame.image.shape, (90, 120, 3))

    def test_apply_orientation_composition_matches_the_group(self):
        # rotate_180 == flip_vertical then flip_horizontal.
        img = np.arange(3 * 4 * 3, dtype=np.uint8).reshape(3, 4, 3)
        rot = apply_orientation(img, ORIENTATION_ROTATE_180)
        both = apply_orientation(apply_orientation(img, ORIENTATION_FLIP_VERTICAL), ORIENTATION_FLIP_HORIZONTAL)
        np.testing.assert_array_equal(rot, both)

    def test_valid_orientations_constant(self):
        self.assertEqual(
            VALID_ORIENTATIONS,
            (ORIENTATION_NONE, ORIENTATION_ROTATE_180, ORIENTATION_FLIP_HORIZONTAL, ORIENTATION_FLIP_VERTICAL),
        )


if __name__ == "__main__":
    unittest.main()
