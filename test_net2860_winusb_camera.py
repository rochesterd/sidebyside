"""Tests for Net2860WinUsbCamera's field assembly, without hardware.

The interesting logic is the state machine that turns a stream of
isochronous packets into frames: header detection, field accumulation, the
even/odd pairing, and the unwrapping of the hardware's 7-bit field counter
into a Frame.index whose gaps mean real dropped frames. All of that is pure
and can be driven with synthetic packets -- no device, no ctypes, no
WinUSB. Constructing the camera does not touch hardware; only _open() does.

The ctypes layer in winusb.py is deliberately not mocked here. Mocking it
would only assert that we call the functions we already call; what actually
breaks a ctypes binding (a wrong restype truncating a handle) is invisible
to a mock and was caught against real hardware instead.
"""

from __future__ import annotations

import unittest

import numpy as np

import net2860_winusb_camera as m
from camera import ORIENTATION_NONE
from winusb import GUID


def field_packets(seq: int, luma: int, chroma: int = 0x80, total: int | None = None):
    """Packets for one field: a 22 5a header packet then one continuation.

    Packet sizes here are nothing like the real 2892-byte ones on purpose --
    the assembler must not care how the field is split up.
    """
    payload = bytes([luma, chroma]) * ((m.FIELD_BYTES if total is None else total) // 2)
    head = bytes([0x22, 0x5A, seq & 0x7F, 0x88]) + payload[:64]
    cont = m.FIELD_CONT + payload[64:]
    return [head, cont]


def feed(cam, packets):
    """Push packets through the assembler, collecting any frames emitted."""
    out = []
    for p in packets:
        got = cam._consume(p)
        if got is not None:
            out.append(got)
    return out


class FieldAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.cam = m.Net2860WinUsbCamera()

    def test_a_consecutive_even_odd_pair_makes_one_frame(self):
        packets = field_packets(0, 0x40) + field_packets(1, 0xC0) + field_packets(2, 0x40)
        frames = feed(self.cam, packets)
        self.assertEqual(len(frames), 1)
        image, timestamp, index = frames[0]
        self.assertEqual(image.shape, (m.HEIGHT, m.WIDTH, 3))
        self.assertEqual(image.dtype, np.uint8)
        self.assertEqual(index, 0)
        self.assertGreater(timestamp, 0)

    def test_even_field_lands_on_even_rows(self):
        packets = field_packets(4, 0x20) + field_packets(5, 0xE0) + field_packets(6, 0x20)
        image = feed(self.cam, packets)[0][0]
        # The even-sequence field is the top one, so it occupies rows 0,2,4...
        self.assertLess(image[0].mean(), image[1].mean())
        self.assertLess(image[2].mean(), image[3].mean())

    def test_frame_index_follows_the_hardware_counter(self):
        packets = (field_packets(10, 0x40) + field_packets(11, 0x40)
                   + field_packets(12, 0x40) + field_packets(13, 0x40)
                   + field_packets(14, 0x40))
        frames = feed(self.cam, packets)
        self.assertEqual([f[2] for f in frames], [5, 6])

    def test_counter_wrap_does_not_break_monotonicity(self):
        packets = []
        for seq in (124, 125, 126, 127, 0, 1, 2):
            packets += field_packets(seq, 0x40)
        indices = [f[2] for f in feed(self.cam, packets)]
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(indices, [62, 63, 64])

    def test_a_dropped_field_leaves_a_gap_rather_than_a_torn_frame(self):
        # Fields 2 and 3 never arrive. The pair either side must not be
        # stitched together, and the surviving indices must show the gap --
        # that is what makes recorder.py's drop count real for this camera.
        packets = (field_packets(0, 0x40) + field_packets(1, 0x40)
                   + field_packets(4, 0x40) + field_packets(5, 0x40)
                   + field_packets(6, 0x40))
        indices = [f[2] for f in feed(self.cam, packets)]
        self.assertEqual(indices, [0, 2])

    def test_odd_then_even_is_not_paired(self):
        # Joining the stream mid-frame must not produce a frame built from
        # the bottom half of one and the top half of the next.
        # A field is only finished by the *next* header arriving, so field 3
        # needs field 4 behind it before the (2,3) pair can be emitted.
        packets = (field_packets(1, 0x40) + field_packets(2, 0x40)
                   + field_packets(3, 0x40) + field_packets(4, 0x40))
        frames = feed(self.cam, packets)
        self.assertEqual([f[2] for f in frames], [1])

    def test_a_short_field_is_dropped_and_counted(self):
        packets = (field_packets(0, 0x40, total=m.FIELD_BYTES // 2)
                   + field_packets(1, 0x40) + field_packets(2, 0x40)
                   + field_packets(3, 0x40) + field_packets(4, 0x40))
        frames = feed(self.cam, packets)
        self.assertEqual(self.cam.short_fields, 1)
        # Field 0 is gone, so field 1 has no partner and the first frame
        # that can be built is the (2,3) pair.
        self.assertEqual([f[2] for f in frames], [1])

    def test_packets_before_any_header_are_ignored(self):
        # Whatever is mid-flight when we start streaming has no header, and
        # must not be treated as the beginning of a field.
        frames = feed(self.cam, [m.FIELD_CONT + b"\x00" * 512])
        self.assertEqual(frames, [])
        self.assertEqual(self.cam.short_fields, 0)


class ConfigurationTests(unittest.TestCase):
    def test_no_orientation_transform_by_default(self):
        # Verified against a scene with horizontal text: the raw sensor
        # output reads upright and correctly, and every one of the three
        # transforms mirrors or inverts it. This default was wrong twice
        # (flip_vertical, then rotate_180) before being checked that way --
        # see DECISIONS.md's 2026-09-10 orientation entries.
        self.assertEqual(m.Net2860WinUsbCamera()._orientation, ORIENTATION_NONE)

    def test_resolution_is_pal(self):
        self.assertEqual(m.Net2860WinUsbCamera().resolution, (720, 576))

    def test_field_size_matches_the_geometry(self):
        self.assertEqual(m.FIELD_BYTES, 720 * 288 * 2)

    def test_init_sequence_does_not_include_the_stop_bracket(self):
        # START_WRITES ending with the stop bracket would switch the bridge
        # straight back off after bringing it up -- see net2860_init.py.
        from net2860_init import START_WRITES, STOP_WRITES
        self.assertEqual(len(START_WRITES), 58)
        self.assertEqual([(r, v) for _, r, v in STOP_WRITES],
                         [(0x21, 0x08), (0x20, 0x10), (0x22, 0x0F), (0x25, 0x02)])
        self.assertNotEqual(START_WRITES[-4:], STOP_WRITES)


class GuidTests(unittest.TestCase):
    def test_round_trips_a_device_interface_guid(self):
        g = GUID.from_string("{CE873099-195F-4601-9800-F9748A92CB41}")
        self.assertEqual(g.d1, 0xCE873099)
        self.assertEqual(g.d2, 0x195F)
        self.assertEqual(g.d3, 0x4601)
        self.assertEqual(bytes(g.d4), bytes.fromhex("9800") + bytes.fromhex("F9748A92CB41"))

    def test_accepts_a_guid_without_braces(self):
        a = GUID.from_string("{CE873099-195F-4601-9800-F9748A92CB41}")
        b = GUID.from_string("CE873099-195F-4601-9800-F9748A92CB41")
        self.assertEqual(bytes(memoryview(a)), bytes(memoryview(b)))


if __name__ == "__main__":
    unittest.main()
