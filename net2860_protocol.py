"""Wire protocol between net2860_camera.py (64-bit main process) and
net2860_helper.py (32-bit helper subprocess) -- see DECISIONS.md's
"Net2860Camera: 32-bit helper process for the older Vantage Plus BIO" entry
for why this exists instead of running the capture code in-process.

Pure stdlib, no comtypes import: this module is imported by both sides, and
the whole point of the split is that only net2860_helper.py ever touches
comtypes/pygrabber (CLAUDE.md's "nothing outside a camera module may
reference the vendor SDK" rule, extended to the process boundary here).

Three fixed-magic message types, little-endian:

- READY (sent once, after the helper's DirectShow graph connects):
  width:u32, height:u32
- FRAME (sent once per captured frame):
  timestamp:f64, index:u32, payload_len:u32, payload:bytes
- ERROR (sent once instead of READY if the graph fails to build/connect):
  message_len:u32, message:utf-8 bytes
"""

from __future__ import annotations

import struct
from typing import Callable

MAGIC_READY = b"RDY1"
MAGIC_FRAME = b"FRM1"
MAGIC_ERROR = b"ERR1"

_READY_BODY = struct.Struct("<II")  # width, height
_FRAME_HEADER = struct.Struct("<dII")  # timestamp, index, payload_len
_ERROR_HEADER = struct.Struct("<I")  # message_len


class ProtocolError(RuntimeError):
    """A message was truncated, malformed, or carried an unrecognized magic."""


def pack_ready(width: int, height: int) -> bytes:
    return MAGIC_READY + _READY_BODY.pack(width, height)


def pack_frame(timestamp: float, index: int, payload: bytes) -> bytes:
    return MAGIC_FRAME + _FRAME_HEADER.pack(timestamp, index, len(payload)) + payload


def pack_error(message: str) -> bytes:
    encoded = message.encode("utf-8")
    return MAGIC_ERROR + _ERROR_HEADER.pack(len(encoded)) + encoded


def _read_exact(read: Callable[[int], bytes], size: int) -> bytes:
    data = read(size)
    if len(data) != size:
        raise ProtocolError(f"short read: expected {size} bytes, got {len(data)}")
    return data


def read_message(read: Callable[[int], bytes]) -> tuple[str, tuple]:
    """Reads exactly one message via `read(n) -> bytes` (matches both a real
    Popen.stdout.read and a BytesIO.read in tests). Returns (kind, payload):

    - ("ready", (width, height))
    - ("frame", (timestamp, index, payload_bytes))
    - ("error", (message,))

    Raises ProtocolError on EOF, a short read, or an unrecognized magic --
    callers distinguish "helper never said anything" (0 bytes on the first
    read) from "helper said something we couldn't parse" by checking the
    exception message if needed; both are equally fatal to net2860_camera.py's
    _open()/_grab().
    """
    magic = read(4)
    if len(magic) == 0:
        raise ProtocolError("EOF: helper process produced no more data")
    if len(magic) != 4:
        raise ProtocolError(f"short read: expected 4-byte magic, got {len(magic)} bytes")

    if magic == MAGIC_READY:
        width, height = _READY_BODY.unpack(_read_exact(read, _READY_BODY.size))
        return "ready", (width, height)

    if magic == MAGIC_FRAME:
        timestamp, index, payload_len = _FRAME_HEADER.unpack(_read_exact(read, _FRAME_HEADER.size))
        payload = _read_exact(read, payload_len)
        return "frame", (timestamp, index, payload)

    if magic == MAGIC_ERROR:
        (message_len,) = _ERROR_HEADER.unpack(_read_exact(read, _ERROR_HEADER.size))
        message = _read_exact(read, message_len).decode("utf-8", errors="replace")
        return "error", (message,)

    raise ProtocolError(f"unrecognized magic {magic!r}")
