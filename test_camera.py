"""BaseCamera behavior that isn't tied to a specific subclass.

The rotation mechanic is exercised through a tiny fixed-frame fake (so the
input pixels are known exactly) and through a real SyntheticCamera capture
thread for the end-to-end path.
"""

from __future__ import annotations

import time
import unittest

import numpy as np

from camera import ALLOWED_ROTATIONS, BaseCamera
from synthetic_camera import SyntheticCamera


class _FixedFrameCamera(BaseCamera):
    """Emits one known frame repeatedly -- lets a test assert exactly what
    the rotation transform did to specific pixels."""

    def __init__(self, image: np.ndarray, rotation: int | None = 0):
        super().__init__(queue_size=2, label="fixed", rotation=rotation)
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


class RotationTest(unittest.TestCase):
    def _marked_frame(self) -> np.ndarray:
        img = np.zeros((3, 4, 3), dtype=np.uint8)
        img[0, 0] = (255, 0, 0)  # top-left only
        return img

    def test_no_rotation_leaves_the_frame_untouched(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), rotation=0))
        self.assertEqual(tuple(frame.image[0, 0]), (255, 0, 0))
        self.assertEqual(frame.image.shape, (3, 4, 3))

    def test_180_moves_top_left_to_bottom_right_and_keeps_dimensions(self):
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), rotation=180))
        self.assertEqual(frame.image.shape, (3, 4, 3))
        self.assertEqual(tuple(frame.image[2, 3]), (255, 0, 0))  # was [0,0]
        self.assertEqual(tuple(frame.image[0, 0]), (0, 0, 0))
        self.assertTrue(frame.image.flags["C_CONTIGUOUS"])  # cv2/PyAV-safe

    def test_none_rotation_behaves_as_no_rotation(self):
        # None means "a subclass may resolve it in _open()"; a subclass that
        # never does must behave as 0.
        frame = _first_frame(_FixedFrameCamera(self._marked_frame(), rotation=None))
        self.assertEqual(tuple(frame.image[0, 0]), (255, 0, 0))

    def test_invalid_rotation_rejected_at_construction(self):
        for bad in (90, 270, 45, -180):
            with self.assertRaises(ValueError):
                SyntheticCamera(120, 90, rotation=bad)

    def test_synthetic_camera_end_to_end_with_180(self):
        frame = _first_frame(SyntheticCamera(120, 90, fps=60, rotation=180))
        self.assertEqual(frame.image.shape, (90, 120, 3))

    def test_allowed_rotations_constant(self):
        self.assertEqual(ALLOWED_ROTATIONS, (0, 180))


if __name__ == "__main__":
    unittest.main()
