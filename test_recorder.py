"""Records 10 seconds from two synthetic cameras and checks the resulting
MP4 is a real, roughly-correctly-timed file.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest

import av

from recorder import Recorder
from synthetic_camera import SyntheticCamera

RECORD_SECONDS = 10
FPS = 30
WIDTH, HEIGHT = 640, 480


class TestRecorder(unittest.TestCase):
    def test_records_expected_duration_and_frame_count(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            camera_a = SyntheticCamera(640, 480, name="cam-a", fps=FPS)
            camera_b = SyntheticCamera(800, 600, name="cam-b", fps=FPS)
            camera_a.start()
            camera_b.start()
            try:
                recorder = Recorder(
                    camera_a,
                    camera_b,
                    name_a="cam-a",
                    name_b="cam-b",
                    output_root=tmp_root,
                    width=WIDTH,
                    height=HEIGHT,
                    fps=FPS,
                    preset="ultrafast",
                )
                recorder.start()
                time.sleep(RECORD_SECONDS)
                session_info = recorder.stop()
            finally:
                camera_a.stop()
                camera_b.stop()

            mp4_path = recorder.session_dir / "composite.mp4"
            self.assertTrue(mp4_path.exists())
            self.assertGreater(mp4_path.stat().st_size, 0)

            session_json_path = recorder.session_dir / "session.json"
            self.assertTrue(session_json_path.exists())
            with open(session_json_path) as f:
                on_disk_info = json.load(f)
            self.assertEqual(on_disk_info, session_info)

            expected_frames = RECORD_SECONDS * FPS
            frame_count = session_info["composite"]["frame_count"]
            self.assertAlmostEqual(frame_count, expected_frames, delta=expected_frames * 0.2)

            container = av.open(str(mp4_path))
            stream = container.streams.video[0]
            decoded = sum(1 for _ in container.decode(stream))
            container.close()
            self.assertAlmostEqual(decoded, frame_count, delta=2)

            duration_s = decoded / FPS
            self.assertAlmostEqual(duration_s, RECORD_SECONDS, delta=RECORD_SECONDS * 0.2)

            for key in ("camera_a", "camera_b"):
                cam_info = session_info["cameras"][key]
                self.assertGreaterEqual(cam_info["frame_count"], 1)
                self.assertGreaterEqual(cam_info["dropped_frames"], 0)


if __name__ == "__main__":
    unittest.main()
