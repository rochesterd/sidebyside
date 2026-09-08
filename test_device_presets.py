"""Tests for device_presets.orientation_for_model -- pure lookup logic, no
hardware or SDK.
"""

from __future__ import annotations

import unittest

from camera import ORIENTATION_FLIP_VERTICAL, ORIENTATION_NONE
from device_presets import orientation_for_model, pixel_clock_hz_for_model


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


class PixelClockForModelTest(unittest.TestCase):
    """The legacy uEye slit lamp camera powers up at 24MHz of a 10-128MHz
    range on every open, which is an ~87ms frame period at 1600x1200 --
    the whole of its long-assumed "11fps sensor limit"."""

    def test_slit_lamp_gets_the_measured_clock(self):
        self.assertEqual(pixel_clock_hz_for_model("UI325xCP-C"), 80_000_000)

    def test_match_is_case_insensitive(self):
        self.assertEqual(pixel_clock_hz_for_model("ui325xcp-c rev.2"), 80_000_000)

    def test_bio_has_no_entry_because_its_clock_is_fixed(self):
        # The USB3 Vision camera reports a fixed, unwritable 197MHz.
        self.assertIsNone(pixel_clock_hz_for_model("U3-327xCP-C"))

    def test_unknown_model_is_left_alone(self):
        self.assertIsNone(pixel_clock_hz_for_model("SomeOtherCamera"))
        self.assertIsNone(pixel_clock_hz_for_model(None))
        self.assertIsNone(pixel_clock_hz_for_model(""))


if __name__ == "__main__":
    unittest.main()
