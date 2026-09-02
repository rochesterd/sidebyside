"""Records from two real SyntheticCamera instances and checks the two
resulting per-camera MP4s -- decoded, not just present: frame counts,
strictly increasing millisecond PTS on the shared session clock, both
streams spanning the same interval, the rate limit, drop accounting, and
the verify-then-delete-MKV rule (never delete both).
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import av

from recorder import INSTRUMENT_STREAM, THIRD_PERSON_STREAM, Recorder, _mp4_verifies
from synthetic_camera import SyntheticCamera

FPS = 30


def _pts_seconds(mp4_path: Path) -> list[float]:
    """Every decoded frame's presentation time, in seconds."""
    with av.open(str(mp4_path)) as container:
        stream = container.streams.video[0]
        tb = stream.time_base
        return [float(frame.pts * tb) for frame in container.decode(stream)]


def _record(tmp_root: str, seconds: float, *, cam_a: SyntheticCamera, cam_b: SyntheticCamera, fps: int = FPS, **kwargs):
    cam_a.start()
    cam_b.start()
    try:
        recorder = Recorder(
            cam_a,
            cam_b,
            instrument_key="slit_lamp",
            instrument_label="BI900",
            third_person_label="third-person camera",
            output_root=tmp_root,
            fps=fps,
            preset="ultrafast",
            **kwargs,
        )
        recorder.start()
        time.sleep(seconds)
        info = recorder.stop()
    finally:
        cam_a.stop()
        cam_b.stop()
    return recorder, info


class TestRecorder(unittest.TestCase):
    def test_two_streams_with_shared_clock_pts(self):
        seconds = 6
        with tempfile.TemporaryDirectory() as tmp_root:
            recorder, info = _record(
                tmp_root,
                seconds,
                cam_a=SyntheticCamera(640, 480, name="cam-a", fps=FPS),
                cam_b=SyntheticCamera(320, 240, name="cam-b", fps=FPS),
            )
            d = recorder.session_dir

            # Manifest shape
            self.assertEqual(info["format_version"], 2)
            self.assertEqual(info["instrument"], "slit_lamp")
            self.assertEqual(info["fps"], FPS)
            self.assertEqual(set(info["streams"]), {INSTRUMENT_STREAM, THIRD_PERSON_STREAM})
            self.assertEqual(info["streams"][INSTRUMENT_STREAM]["label"], "BI900")
            self.assertEqual(info["streams"][INSTRUMENT_STREAM]["file"], "instrument.mp4")
            self.assertEqual(info["streams"][THIRD_PERSON_STREAM]["file"], "third_person.mp4")
            with open(d / "session.json", encoding="utf-8") as f:
                self.assertEqual(json.load(f), info)

            # Files: MP4s at native resolution, MKVs gone (verified)
            for role, (w, h) in ((INSTRUMENT_STREAM, (640, 480)), (THIRD_PERSON_STREAM, (320, 240))):
                s = info["streams"][role]
                self.assertTrue(s["verified"])
                self.assertNotIn("mkv", s)
                self.assertTrue((d / f"{role}.mp4").exists())
                self.assertFalse((d / f"{role}.mkv").exists())
                self.assertEqual((s["width"], s["height"]), (w, h))
                with av.open(str(d / f"{role}.mp4")) as c:
                    self.assertEqual((c.streams.video[0].width, c.streams.video[0].height), (w, h))

            # Frame counts near fps*seconds, and PTS strictly increasing
            pts = {role: _pts_seconds(d / f"{role}.mp4") for role in info["streams"]}
            for role, s in info["streams"].items():
                self.assertAlmostEqual(s["frame_count"], FPS * seconds, delta=FPS * seconds * 0.2)
                self.assertAlmostEqual(len(pts[role]), s["frame_count"], delta=2)
                self.assertTrue(all(b > a for a, b in zip(pts[role], pts[role][1:])), role)
                self.assertGreaterEqual(s["dropped_frames"], 0)
                self.assertEqual(s["offset_s"], 0.0)

            # Shared clock: both start near 0 and end near `seconds`, together.
            for role in pts:
                self.assertLess(pts[role][0], 0.5, role)
                self.assertAlmostEqual(pts[role][-1], seconds, delta=seconds * 0.2)
            self.assertLess(abs(pts[INSTRUMENT_STREAM][-1] - pts[THIRD_PERSON_STREAM][-1]), 0.5)

    def test_rate_limit_caps_a_fast_camera_at_recording_fps(self):
        seconds = 3
        with tempfile.TemporaryDirectory() as tmp_root:
            _, info = _record(
                tmp_root,
                seconds,
                cam_a=SyntheticCamera(160, 120, name="fast", fps=90),  # 3x the recording rate
                cam_b=SyntheticCamera(160, 120, name="normal", fps=FPS),
                fps=FPS,
            )
            fast = info["streams"][INSTRUMENT_STREAM]
            self.assertGreater(fast["rate_limited_frames"], 0)
            self.assertAlmostEqual(fast["frame_count"], FPS * seconds, delta=FPS * seconds * 0.25)
            self.assertLess(fast["frame_count"], 90 * seconds * 0.6)  # clearly not the full 90fps

    def test_dropped_frames_are_detected_from_the_camera_s_own_drops(self):
        """Frame.index must be the source's own sequence number, not a
        gapless counter (see camera.py / DECISIONS.md), or a camera's own
        drops are invisible. SyntheticCamera's drop_rate simulates exactly
        that."""
        with tempfile.TemporaryDirectory() as tmp_root:
            _, info = _record(
                tmp_root,
                3,
                cam_a=SyntheticCamera(160, 120, name="cam-a", fps=FPS, drop_rate=0.3),
                cam_b=SyntheticCamera(160, 120, name="cam-b", fps=FPS),
            )
            self.assertGreater(info["streams"][INSTRUMENT_STREAM]["dropped_frames"], 0)

    def test_mkv_is_kept_when_the_mp4_fails_verification(self):
        """Never delete both: a stream whose MP4 doesn't verify keeps its
        MKV as the recoverable copy and says so in the manifest."""
        with tempfile.TemporaryDirectory() as tmp_root:
            with patch("recorder._mp4_verifies", return_value=False):
                recorder, info = _record(
                    tmp_root,
                    2,
                    cam_a=SyntheticCamera(160, 120, name="cam-a", fps=FPS),
                    cam_b=SyntheticCamera(160, 120, name="cam-b", fps=FPS),
                )
            d = recorder.session_dir
            for role, s in info["streams"].items():
                self.assertFalse(s["verified"])
                self.assertEqual(s["mkv"], f"{role}.mkv")
                self.assertTrue((d / f"{role}.mkv").exists())
                self.assertTrue((d / f"{role}.mp4").exists())

    def test_mp4_verifies_rejects_a_truncated_file_and_empty_recordings(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            recorder, info = _record(
                tmp_root,
                3,
                cam_a=SyntheticCamera(160, 120, name="cam-a", fps=FPS),
                cam_b=SyntheticCamera(160, 120, name="cam-b", fps=FPS),
            )
            good = recorder.session_dir / "instrument.mp4"
            expected = info["streams"][INSTRUMENT_STREAM]["frame_count"]
            self.assertTrue(_mp4_verifies(good, expected))

            truncated = recorder.session_dir / "truncated.mp4"
            data = good.read_bytes()
            truncated.write_bytes(data[: len(data) // 4])
            self.assertFalse(_mp4_verifies(truncated, expected))

            self.assertFalse(_mp4_verifies(good, 0))  # nothing recorded -> never treat as sole copy


if __name__ == "__main__":
    unittest.main()
