"""Unit tests for Net2860Camera, mocking at the subprocess.Popen boundary --
same pattern test_uvc_camera.py uses for cv2.VideoCapture. No real 32-bit
process, .venv32/, or hardware involved; net2860_protocol.py's real
pack_*/read_message functions are used to build/parse the fake process's
stdout, so these tests exercise the actual wire parsing, not a stand-in
for it."""

import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import net2860_protocol as proto
from net2860_camera import Net2860Camera, Net2860CameraError

_REAL_PYTHON_EXE = Path(__file__)  # any file that exists, so the .exists() checks pass
_REAL_HELPER_SCRIPT = Path(__file__)


class _FakeProcess:
    """Stands in for subprocess.Popen's return value. stdout is a real
    BytesIO fed with real net2860_protocol-encoded bytes."""

    def __init__(self, stdout_bytes: bytes = b"", poll_result=None):
        self.stdout = io.BytesIO(stdout_bytes)
        self.stderr = io.BytesIO(b"")
        self.terminate_called = False
        self.kill_called = False
        self.wait_calls = 0
        self._poll_result = poll_result
        self.first_wait_times_out = False

    def poll(self):
        return self._poll_result

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.first_wait_times_out and self.wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0


def _make_camera(**kwargs) -> Net2860Camera:
    return Net2860Camera(python_exe=_REAL_PYTHON_EXE, helper_script=_REAL_HELPER_SCRIPT, **kwargs)


class TestOpen(unittest.TestCase):
    def test_open_parses_ready_and_sets_resolution(self):
        fake = _FakeProcess(proto.pack_ready(720, 576))
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            camera._open()
        self.assertEqual(camera.resolution, (720, 576))

    def test_open_raises_on_error_message(self):
        fake = _FakeProcess(proto.pack_error("KS722OUP filter not found -- camera unplugged?"))
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            with self.assertRaisesRegex(Net2860CameraError, "camera unplugged"):
                camera._open()
        self.assertTrue(fake.terminate_called)

    def test_open_raises_on_eof_before_handshake(self):
        fake = _FakeProcess(b"")  # process wrote nothing before stdout closed
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            with self.assertRaises(Net2860CameraError):
                camera._open()

    def test_open_raises_on_expired_startup_timeout(self):
        fake = _FakeProcess(proto.pack_ready(720, 576))
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera(startup_timeout=-1.0)  # already expired
            with self.assertRaisesRegex(Net2860CameraError, "timeout"):
                camera._open()

    def test_open_raises_if_python_exe_missing(self):
        camera = Net2860Camera(python_exe=Path("Z:/does/not/exist/python.exe"), helper_script=_REAL_HELPER_SCRIPT)
        with self.assertRaisesRegex(Net2860CameraError, "not found"):
            camera._open()

    def test_open_raises_if_helper_script_missing(self):
        camera = Net2860Camera(python_exe=_REAL_PYTHON_EXE, helper_script=Path("Z:/does/not/exist/helper.py"))
        with self.assertRaisesRegex(Net2860CameraError, "not found"):
            camera._open()


class TestGrab(unittest.TestCase):
    def _opened_camera(self, extra_stream: bytes = b"") -> tuple[Net2860Camera, _FakeProcess]:
        fake = _FakeProcess(proto.pack_ready(4, 2) + extra_stream)
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            camera._open()
        return camera, fake

    def test_grab_returns_frame_from_helper(self):
        payload = bytes(range(4 * 2 * 3))  # 4x2 image, 3 channels
        camera, _fake = self._opened_camera(proto.pack_frame(42.5, 7, payload))

        result = camera._grab()

        self.assertIsNotNone(result)
        image, timestamp, index = result
        self.assertEqual(image.shape, (2, 4, 3))
        self.assertEqual(timestamp, 42.5)
        self.assertEqual(index, 7)
        self.assertTrue(image.flags.writeable)

    def test_grab_returns_none_and_logs_once_on_eof(self):
        camera, _fake = self._opened_camera(b"")  # nothing after the READY handshake

        with self.assertLogs("net2860_camera", level="ERROR"):
            first = camera._grab()
        self.assertIsNone(first)
        self.assertTrue(camera._dead)

        # Second call must not error or attempt another read -- just stays quiet.
        second = camera._grab()
        self.assertIsNone(second)

    def test_grab_returns_none_on_mid_stream_error_message(self):
        camera, _fake = self._opened_camera(proto.pack_error("device disconnected"))

        with self.assertLogs("net2860_camera", level="ERROR"):
            result = camera._grab()

        self.assertIsNone(result)
        self.assertTrue(camera._dead)


class TestClose(unittest.TestCase):
    def test_close_terminates_process(self):
        fake = _FakeProcess(proto.pack_ready(720, 576))
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            camera._open()
            camera._close()

        self.assertTrue(fake.terminate_called)
        self.assertFalse(fake.kill_called)

    def test_close_kills_if_terminate_does_not_exit_in_time(self):
        fake = _FakeProcess(proto.pack_ready(720, 576))
        fake.first_wait_times_out = True
        with patch("net2860_camera.subprocess.Popen", return_value=fake):
            camera = _make_camera()
            camera._open()
            camera._close()

        self.assertTrue(fake.terminate_called)
        self.assertTrue(fake.kill_called)

    def test_close_is_a_no_op_if_never_opened(self):
        camera = _make_camera()
        camera._close()  # must not raise


if __name__ == "__main__":
    unittest.main()
