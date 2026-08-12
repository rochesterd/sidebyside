"""Runs both real cameras at once and reports measured throughput -- frames
received per camera per second, and dropped-frame counts from gaps in
Frame.index (the same method recorder.py uses for session.json).

Exercises CLAUDE.md's #1 flagged hardware risk directly: the two cameras
together need ~300MB/s against a realistic 350-400MB/s single-host-
controller ceiling, and USB3 Vision degrades by silently dropping frames
rather than raising an error -- so "it didn't crash" is not evidence of
"it's fine." Compare the measured fps here against each camera's solo
smoke-test fps (tools/smoke_test_camera.py) -- a drop under load is the
signal to look for, not just a nonzero dropped-frame count (see caveat
below).

    python tools/dual_camera_smoke_test.py <serial_a> <serial_b> [--seconds N]

Caveat: Frame.index is assigned locally by BaseCamera only to frames that
actually reach _grab() -- it does not see the camera's own on-the-wire
FrameID, so a device that silently skips frames without ever fully
stalling (no WaitForFinishedBuffer timeout) would show 0 "dropped" here
while still measurably under target fps. Treat measured fps as the
primary signal; dropped-frame count as a lower bound, not the whole
picture.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ids_camera import IdsCamera, IdsCameraNotFoundError

DEFAULT_SECONDS = 20.0
READ_TIMEOUT_S = 2.0


def _drain_and_count(camera: IdsCamera, seconds: float, results: dict, key: str) -> None:
    received = 0
    dropped = 0
    last_index = None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        frame = camera.read(timeout=READ_TIMEOUT_S)
        if frame is None:
            continue
        received += 1
        if last_index is not None and frame.index != last_index + 1:
            dropped += frame.index - last_index - 1
        last_index = frame.index
    results[key] = {"received": received, "dropped": dropped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serial_a")
    parser.add_argument("serial_b")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    args = parser.parse_args()

    camera_a = IdsCamera(serial=args.serial_a)
    camera_b = IdsCamera(serial=args.serial_b)

    for camera, label in ((camera_a, "camera_a"), (camera_b, "camera_b")):
        try:
            camera.start()
        except IdsCameraNotFoundError as e:
            print(f"error starting {label}: {e}", file=sys.stderr)
            return 1

    print(f"camera_a ({args.serial_a}): resolution {camera_a.resolution}")
    print(f"camera_b ({args.serial_b}): resolution {camera_b.resolution}")
    print(f"running both simultaneously for {args.seconds:.0f}s...")

    results: dict = {}
    threads = [
        threading.Thread(target=_drain_and_count, args=(camera_a, args.seconds, results, "camera_a")),
        threading.Thread(target=_drain_and_count, args=(camera_b, args.seconds, results, "camera_b")),
    ]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    for key in ("camera_a", "camera_b"):
        r = results[key]
        fps = r["received"] / elapsed
        print(
            f"{key}: {r['received']} frames received, {r['dropped']} index-gap drops, "
            f"~{fps:.1f} fps measured over {elapsed:.1f}s"
        )

    camera_a.stop()
    camera_b.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
