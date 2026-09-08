"""Tests for exposure_calibration.py's pure median-brightness/exposure-gain
math -- split out from ids_camera.py specifically so this logic is
testable without the IDS peak SDK. See ROADMAP.md's calibration-UX entry.
"""

from __future__ import annotations

import unittest

import numpy as np

from exposure_calibration import (
    exposure_budget_us,
    center_crop,
    channel_medians,
    is_converged,
    is_white_balanced,
    median_brightness,
    next_balance_ratios,
    next_exposure_gain,
)


class MedianBrightnessTest(unittest.TestCase):
    def test_uniform_image(self):
        image = np.full((10, 10, 3), 64, dtype=np.uint8)
        self.assertEqual(median_brightness(image), 64.0)

    def test_robust_to_a_bright_outlier_patch(self):
        image = np.full((10, 10, 3), 50, dtype=np.uint8)
        image[0:1, 0:1, :] = 255  # a small hot reflection shouldn't move the median
        self.assertEqual(median_brightness(image), 50.0)


class IsConvergedTest(unittest.TestCase):
    def test_within_tolerance_is_converged(self):
        self.assertTrue(is_converged(120.0, target=128.0, tolerance=10.0))
        self.assertTrue(is_converged(138.0, target=128.0, tolerance=10.0))

    def test_outside_tolerance_is_not_converged(self):
        self.assertFalse(is_converged(100.0, target=128.0, tolerance=10.0))


class NextExposureGainTest(unittest.TestCase):
    def test_exposure_alone_absorbs_a_reachable_correction(self):
        new_exposure, new_gain = next_exposure_gain(
            median=64.0,  # half the target -- needs 2x brightness
            exposure_time_us=1000.0,
            exposure_range_us=(100.0, 10_000.0),
            gain=2.0,
            gain_range=(1.0, 8.0),
            target=128.0,
        )
        self.assertAlmostEqual(new_exposure, 2000.0)
        self.assertEqual(new_gain, 2.0)  # untouched -- exposure alone covered it

    def test_gain_makes_up_the_shortfall_once_exposure_maxes_out(self):
        new_exposure, new_gain = next_exposure_gain(
            median=16.0,  # needs 8x brightness, exposure can only give 2x
            exposure_time_us=5000.0,
            exposure_range_us=(100.0, 10_000.0),
            gain=2.0,
            gain_range=(1.0, 8.0),
            target=128.0,
        )
        self.assertAlmostEqual(new_exposure, 10_000.0)  # clamped at max
        self.assertAlmostEqual(new_gain, 8.0)  # 2.0 * (8x / 2x reachable via exposure) = 8.0, clamped at max

    def test_exposure_alone_absorbs_a_reachable_reduction(self):
        new_exposure, new_gain = next_exposure_gain(
            median=256.0,  # twice the target -- needs half the brightness
            exposure_time_us=1000.0,
            exposure_range_us=(100.0, 10_000.0),
            gain=2.0,
            gain_range=(1.0, 8.0),
            target=128.0,
        )
        self.assertAlmostEqual(new_exposure, 500.0)
        self.assertEqual(new_gain, 2.0)

    def test_gain_reduced_once_exposure_hits_its_minimum(self):
        new_exposure, new_gain = next_exposure_gain(
            median=2048.0,  # 16x the target -- needs to cut brightness far more than exposure alone can
            exposure_time_us=800.0,
            exposure_range_us=(100.0, 10_000.0),
            gain=4.0,
            gain_range=(1.0, 8.0),
            target=128.0,
        )
        self.assertAlmostEqual(new_exposure, 100.0)  # clamped at min
        self.assertAlmostEqual(new_gain, 2.0)  # gain pulled down too, to make up the rest


class CenterCropTest(unittest.TestCase):
    def test_half_fraction_crops_both_dimensions(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        cropped = center_crop(image, fraction=0.5)
        self.assertEqual(cropped.shape, (50, 100, 3))

    def test_full_fraction_is_a_no_op(self):
        image = np.zeros((60, 80, 3), dtype=np.uint8)
        cropped = center_crop(image, fraction=1.0)
        self.assertEqual(cropped.shape, image.shape)

    def test_zero_fraction_raises(self):
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            center_crop(image, fraction=0.0)

    def test_fraction_above_one_raises(self):
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            center_crop(image, fraction=1.5)

    def test_demonstrates_the_vignette_skew_scenario(self):
        # A bright center patch surrounded by true black -- the whole-frame
        # median is skewed dark by the surround; a centered crop that stays
        # inside the bright patch reads the real content instead.
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[25:75, 25:75, :] = 200
        whole_frame_median = median_brightness(image)
        cropped_median = median_brightness(center_crop(image, fraction=0.4))
        self.assertEqual(whole_frame_median, 0.0)
        self.assertEqual(cropped_median, 200.0)


class ChannelMediansTest(unittest.TestCase):
    def test_uniform_per_channel_values_map_to_correct_order(self):
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        image[:, :, 0] = 10  # blue
        image[:, :, 1] = 20  # green
        image[:, :, 2] = 30  # red
        b, g, r = channel_medians(image)
        self.assertEqual((b, g, r), (10.0, 20.0, 30.0))


class IsWhiteBalancedTest(unittest.TestCase):
    def test_all_channels_equal_is_balanced(self):
        self.assertTrue(is_white_balanced(128.0, 128.0, 128.0))

    def test_red_outside_tolerance_is_not_balanced(self):
        self.assertFalse(is_white_balanced(b_median=128.0, g_median=128.0, r_median=140.0, tolerance=5.0))

    def test_blue_outside_tolerance_is_not_balanced(self):
        self.assertFalse(is_white_balanced(b_median=140.0, g_median=128.0, r_median=128.0, tolerance=5.0))

    def test_within_tolerance_is_balanced(self):
        self.assertTrue(is_white_balanced(b_median=130.0, g_median=128.0, r_median=126.0, tolerance=5.0))


class NextBalanceRatiosTest(unittest.TestCase):
    def test_channel_darker_than_green_is_raised(self):
        new_red, new_blue = next_balance_ratios(
            b_median=128.0,
            g_median=128.0,
            r_median=64.0,  # half of green -- needs 2x correction
            red_ratio=1.0,
            red_ratio_range=(0.5, 4.0),
            blue_ratio=1.0,
            blue_ratio_range=(0.5, 4.0),
        )
        self.assertAlmostEqual(new_red, 2.0)
        self.assertAlmostEqual(new_blue, 1.0)  # blue already equal to green -- untouched

    def test_channel_brighter_than_green_is_lowered(self):
        new_red, new_blue = next_balance_ratios(
            b_median=256.0,  # double green -- needs half correction
            g_median=128.0,
            r_median=128.0,
            red_ratio=1.0,
            red_ratio_range=(0.5, 4.0),
            blue_ratio=1.0,
            blue_ratio_range=(0.5, 4.0),
        )
        self.assertAlmostEqual(new_red, 1.0)
        self.assertAlmostEqual(new_blue, 0.5)

    def test_correction_clamps_at_range_limits(self):
        new_red, new_blue = next_balance_ratios(
            b_median=128.0,
            g_median=128.0,
            r_median=1.0,  # would need an enormous correction
            red_ratio=1.0,
            red_ratio_range=(0.5, 4.0),
            blue_ratio=1.0,
            blue_ratio_range=(0.5, 4.0),
        )
        self.assertEqual(new_red, 4.0)  # clamped at max
        self.assertEqual(new_blue, 1.0)


class ExposureBudgetTest(unittest.TestCase):
    """Exposure time is a frame-rate budget: a sensor exposing for E
    microseconds cannot deliver faster than 1/E."""

    def test_budget_is_the_frame_interval_less_readout_headroom(self):
        # 30fps -> 33.33ms interval, times the 0.9 headroom fraction.
        self.assertAlmostEqual(exposure_budget_us(30), 30_000.0, delta=1.0)
        self.assertAlmostEqual(exposure_budget_us(60), 15_000.0, delta=1.0)

    def test_budget_rejects_a_non_positive_rate(self):
        for bad in (0, -30):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    exposure_budget_us(bad)


class NextExposureGainBudgetTest(unittest.TestCase):
    def test_without_a_budget_exposure_absorbs_everything(self):
        """The pre-existing behaviour, kept as the contrast for the test
        below -- this is what produced 87ms on the slit lamp."""
        exposure, gain = next_exposure_gain(
            median=48.0,
            exposure_time_us=15_000.0,
            exposure_range_us=(10.0, 200_000.0),
            gain=1.0,
            gain_range=(1.0, 4.0),
            target=128.0,
        )
        self.assertAlmostEqual(exposure, 40_000.0, delta=1.0)
        self.assertEqual(gain, 1.0)

    def test_a_budget_caps_exposure_and_spills_the_remainder_onto_gain(self):
        exposure, gain = next_exposure_gain(
            median=48.0,
            exposure_time_us=15_000.0,
            exposure_range_us=(10.0, 200_000.0),
            gain=1.0,
            gain_range=(1.0, 4.0),
            target=128.0,
            max_exposure_us=exposure_budget_us(30),
        )
        self.assertAlmostEqual(exposure, 30_000.0, delta=1.0)
        self.assertAlmostEqual(gain, 40_000.0 / 30_000.0, places=3)

    def test_the_budget_never_asks_for_less_than_the_sensor_can_do(self):
        exposure, _gain = next_exposure_gain(
            median=48.0,
            exposure_time_us=15_000.0,
            exposure_range_us=(20_000.0, 200_000.0),
            gain=1.0,
            gain_range=(1.0, 4.0),
            target=128.0,
            max_exposure_us=5_000.0,
        )
        self.assertEqual(exposure, 20_000.0)

    def test_a_budget_that_is_not_binding_changes_nothing(self):
        args = dict(
            median=100.0, exposure_time_us=10_000.0, exposure_range_us=(10.0, 200_000.0),
            gain=1.0, gain_range=(1.0, 4.0), target=128.0,
        )
        self.assertEqual(
            next_exposure_gain(**args),
            next_exposure_gain(**args, max_exposure_us=exposure_budget_us(30)),
        )

    def test_the_slit_lamp_regression_converges_inside_the_frame_budget(self):
        """Run the loop the way auto_calibrate() does, from a near-black
        frame, and confirm it lands inside a 30fps budget by using gain
        rather than spending the whole frame interval."""
        exposure, gain = 15_000.0, 1.0
        budget = exposure_budget_us(30)
        median = 20.0
        for _ in range(8):
            if is_converged(median, 128.0, 10.0):
                break
            exposure, gain = next_exposure_gain(
                median, exposure, (10.0, 200_000.0), gain, (1.0, 4.0),
                target=128.0, max_exposure_us=budget,
            )
            median = min(255.0, 20.0 * (exposure / 15_000.0) * gain)

        self.assertLessEqual(exposure, budget)
        self.assertGreater(gain, 1.0)
        self.assertGreaterEqual(1_000_000.0 / exposure, 30.0)


if __name__ == "__main__":
    unittest.main()
