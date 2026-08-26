"""Tests for uvc_camera.UvcCamera's construction contract and the
vid_pid identification mode. The vid_pid path's actual device-opening
behavior is exercised end to end (against real hardware) by
test_uvc_enumeration.py; these tests are headless and hardware-free,
covering the two properties this module's docstring calls out as load-
bearing: construction rejects an ambiguous device/vid_pid combination, and
resolution failure surfaces from start() (so BaseCamera's retry loop can
catch it) rather than from __init__.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import cv2

from uvc_camera import _AUTOEXPOSURE_WARMUP_FRAMES, UvcCamera
from uvc_enumeration import UvcDeviceResolutionError


class UvcCameraConstructionTest(unittest.TestCase):
    def test_neither_device_nor_vid_pid_raises(self):
        with self.assertRaises(ValueError):
            UvcCamera()

    def test_both_device_and_vid_pid_raises(self):
        with self.assertRaises(ValueError):
            UvcCamera(device=0, vid_pid="1111:2222")

    def test_device_only_is_accepted(self):
        UvcCamera(device=0)

    def test_vid_pid_only_is_accepted(self):
        UvcCamera(vid_pid="1111:2222")


class UvcCameraVidPidResolutionTest(unittest.TestCase):
    def test_start_raises_resolution_error_without_opening_cv2_when_no_device_attached(self):
        camera = UvcCamera(vid_pid="1111:2222")

        with patch("uvc_camera.uvc_enumeration.resolve_device", side_effect=UvcDeviceResolutionError("none attached")):
            with patch("uvc_camera.cv2.VideoCapture") as mock_video_capture:
                with self.assertRaises(UvcDeviceResolutionError):
                    camera.start()
                mock_video_capture.assert_not_called()

        # BaseCamera.start() only creates the capture thread after _open()
        # succeeds -- confirm the failed resolution left it retryable.
        self.assertIsNone(camera._thread)


class UvcCameraAutofocusExposureLockTest(unittest.TestCase):
    def test_reads_warmup_frames_then_locks_both_controls(self):
        camera = UvcCamera(device=0)
        cap = MagicMock()

        camera._configure_capture(cap)

        self.assertEqual(cap.read.call_count, _AUTOEXPOSURE_WARMUP_FRAMES)
        cap.set.assert_has_calls(
            [call(cv2.CAP_PROP_AUTOFOCUS, 0), call(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)]
        )

    def test_backlight_compensation_is_set_before_the_warmup_reads(self):
        camera = UvcCamera(device=0)
        cap = MagicMock()
        calls = []
        cap.set.side_effect = lambda *args: calls.append(("set", *args))
        cap.read.side_effect = lambda: calls.append(("read",))

        camera._configure_capture(cap)

        self.assertEqual(calls[0], ("set", cv2.CAP_PROP_BACKLIGHT, 1))
        first_read_index = calls.index(("read",))
        self.assertGreater(first_read_index, 0)

    def test_does_not_raise_when_device_does_not_support_the_controls(self):
        camera = UvcCamera(device=0)
        cap = MagicMock()
        cap.set.return_value = False  # cv2's own signal for "unsupported control"

        camera._configure_capture(cap)  # must not raise


class UvcCameraFrameRateCapTest(unittest.TestCase):
    def test_sets_cap_prop_fps_to_the_target(self):
        camera = UvcCamera(device=0, target_fps=30.0)
        cap = MagicMock()
        cap.get.return_value = 30.0

        camera._apply_frame_rate_cap(cap)

        cap.set.assert_called_once_with(cv2.CAP_PROP_FPS, 30.0)

    def test_mismatch_beyond_tolerance_logs_a_warning(self):
        camera = UvcCamera(device=0, target_fps=30.0)
        cap = MagicMock()
        cap.get.return_value = 15.0  # device only supports half the request

        with self.assertLogs("uvc_camera", level="WARNING"):
            camera._apply_frame_rate_cap(cap)

    def test_no_target_fps_means_open_never_calls_the_cap(self):
        camera = UvcCamera(device=0)  # target_fps defaults to None

        with patch("uvc_camera.cv2.VideoCapture") as mock_video_capture:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.get.return_value = 640  # so int(cap.get(...)) for width/height doesn't choke on a MagicMock
            mock_video_capture.return_value = mock_cap

            camera._open()

            mock_cap.set.assert_any_call(cv2.CAP_PROP_BACKLIGHT, 1)  # _configure_capture still ran
            fps_calls = [c for c in mock_cap.set.call_args_list if c.args[0] == cv2.CAP_PROP_FPS]
            self.assertEqual(fps_calls, [])


if __name__ == "__main__":
    unittest.main()
