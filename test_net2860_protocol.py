"""Unit tests for net2860_protocol.py's wire format -- pure logic, no
subprocess/hardware needed. See test_net2860_camera.py for the
subprocess-boundary-mocked tests that build on top of this."""

import io
import unittest

import net2860_protocol as proto


class TestRoundTrip(unittest.TestCase):
    def test_ready_round_trip(self):
        stream = io.BytesIO(proto.pack_ready(720, 576))
        kind, payload = proto.read_message(stream.read)
        self.assertEqual(kind, "ready")
        self.assertEqual(payload, (720, 576))

    def test_frame_round_trip(self):
        payload_bytes = bytes(range(12)) * 3  # arbitrary non-trivial payload
        stream = io.BytesIO(proto.pack_frame(123.456, 7, payload_bytes))
        kind, payload = proto.read_message(stream.read)
        self.assertEqual(kind, "frame")
        timestamp, index, raw = payload
        self.assertAlmostEqual(timestamp, 123.456)
        self.assertEqual(index, 7)
        self.assertEqual(raw, payload_bytes)

    def test_frame_round_trip_empty_payload(self):
        stream = io.BytesIO(proto.pack_frame(0.0, 0, b""))
        kind, payload = proto.read_message(stream.read)
        self.assertEqual(kind, "frame")
        _, _, raw = payload
        self.assertEqual(raw, b"")

    def test_error_round_trip(self):
        stream = io.BytesIO(proto.pack_error("camera not found"))
        kind, payload = proto.read_message(stream.read)
        self.assertEqual(kind, "error")
        self.assertEqual(payload, ("camera not found",))

    def test_error_round_trip_non_ascii(self):
        stream = io.BytesIO(proto.pack_error("café — could not connect"))
        kind, payload = proto.read_message(stream.read)
        self.assertEqual(kind, "error")
        self.assertEqual(payload, ("café — could not connect",))

    def test_two_messages_back_to_back(self):
        data = proto.pack_ready(720, 576) + proto.pack_frame(1.0, 0, b"abc")
        stream = io.BytesIO(data)
        kind1, payload1 = proto.read_message(stream.read)
        kind2, payload2 = proto.read_message(stream.read)
        self.assertEqual(kind1, "ready")
        self.assertEqual(payload1, (720, 576))
        self.assertEqual(kind2, "frame")
        self.assertEqual(payload2, (1.0, 0, b"abc"))


class TestErrors(unittest.TestCase):
    def test_eof_before_any_data(self):
        stream = io.BytesIO(b"")
        with self.assertRaisesRegex(proto.ProtocolError, "EOF"):
            proto.read_message(stream.read)

    def test_short_magic(self):
        stream = io.BytesIO(b"RD")
        with self.assertRaisesRegex(proto.ProtocolError, "short read"):
            proto.read_message(stream.read)

    def test_unrecognized_magic(self):
        stream = io.BytesIO(b"XXXX" + b"\x00" * 8)
        with self.assertRaisesRegex(proto.ProtocolError, "unrecognized magic"):
            proto.read_message(stream.read)

    def test_truncated_ready_body(self):
        stream = io.BytesIO(proto.MAGIC_READY + b"\x00\x00")  # only 2 of 8 body bytes
        with self.assertRaisesRegex(proto.ProtocolError, "short read"):
            proto.read_message(stream.read)

    def test_truncated_frame_payload(self):
        full = proto.pack_frame(1.0, 0, b"abcdef")
        truncated = full[:-3]  # header claims 6 payload bytes, only 3 present
        stream = io.BytesIO(truncated)
        with self.assertRaisesRegex(proto.ProtocolError, "short read"):
            proto.read_message(stream.read)

    def test_truncated_error_message(self):
        full = proto.pack_error("hello world")
        truncated = full[:-3]
        stream = io.BytesIO(truncated)
        with self.assertRaisesRegex(proto.ProtocolError, "short read"):
            proto.read_message(stream.read)


if __name__ == "__main__":
    unittest.main()
