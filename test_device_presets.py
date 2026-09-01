"""Tests for device_presets.orientation_for_model -- pure lookup logic, no
hardware or SDK.
"""

from __future__ import annotations

import unittest

from camera import ORIENTATION_FLIP_VERTICAL, ORIENTATION_NONE
from device_presets import orientation_for_model


class OrientationForModelTest(unittest.TestCase):
    def test_keeler_bio_camera_image_is_vertically_flipped(self):
        # Exact string this camera reports (confirmed on hardware 2026-09-01).
        self.assertEqual(orientation_for_model("U3-327xCP-C"), ORIENTATION_FLIP_VERTICAL)

    def test_match_is_case_insensitive_and_substring(self):
        self.assertEqual(orientation_for_model("u3-327xcp-c rev.2"), ORIENTATION_FLIP_VERTICAL)

    def test_slit_lamp_camera_has_no_preset(self):
        # Must NOT collide with the U3-327 token.
        self.assertEqual(orientation_for_model("UI325xCP-C"), ORIENTATION_NONE)

    def test_unknown_model_defaults_to_none(self):
        self.assertEqual(orientation_for_model("SomeOtherCamera"), ORIENTATION_NONE)

    def test_none_and_empty_default_to_none(self):
        self.assertEqual(orientation_for_model(None), ORIENTATION_NONE)
        self.assertEqual(orientation_for_model(""), ORIENTATION_NONE)


if __name__ == "__main__":
    unittest.main()
