"""Tests for device_presets.rotation_for_model -- pure lookup logic, no
hardware or SDK.
"""

from __future__ import annotations

import unittest

from device_presets import rotation_for_model


class RotationForModelTest(unittest.TestCase):
    def test_keeler_bio_camera_is_inverted(self):
        # Exact string this camera reports (confirmed on hardware 2026-09-01).
        self.assertEqual(rotation_for_model("U3-327xCP-C"), 180)

    def test_match_is_case_insensitive_and_substring(self):
        self.assertEqual(rotation_for_model("u3-327xcp-c rev.2"), 180)

    def test_slit_lamp_camera_has_no_preset(self):
        # Must NOT collide with the U3-327 token.
        self.assertEqual(rotation_for_model("UI325xCP-C"), 0)

    def test_unknown_model_defaults_to_zero(self):
        self.assertEqual(rotation_for_model("SomeOtherCamera"), 0)

    def test_none_and_empty_default_to_zero(self):
        self.assertEqual(rotation_for_model(None), 0)
        self.assertEqual(rotation_for_model(""), 0)


if __name__ == "__main__":
    unittest.main()
