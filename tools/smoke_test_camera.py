"""Hardware smoke test for a single IdsCamera: opens by serial, captures a
handful of frames through the real capture thread, reports resolution and
measured fps, and saves the last frame as a PNG for a visual sanity check
(wrong Bayer pattern or stride tends to show up immediately as color noise
or tearing). See SETUP.md and DECISIONS.md's "One IdsCamera class" entry.

    python tools/smoke_test_camera.py <serial>

Get the serial from tools/check_ids.py first.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ids_camera.py lives at the repo root, one level up from tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from ids_camera import IdsCamera, IdsCameraNotFoundError

FRAME_COUNT = 30
READ_TIMEOUT_S = 5.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serial", help="camera serial number, from tools/check_ids.py")
    parser.add_argument(
        "--out", default="smoke_test_frame.png", help="where to save the last captured frame"
    )
    args = parser.parse_args()

    camera = IdsCamera(serial=args.serial)
    try:
        camera.start()
    except IdsCameraNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        print(f"resolution: {camera.resolution}")

        frame = None
        start = time.monotonic()
        for i in range(FRAME_COUNT):
            frame = camera.read(timeout=READ_TIMEOUT_S)
            if frame is None:
                print(f"error: timed out waiting for frame {i}", file=sys.stderr)
                return 1
        elapsed = time.monotonic() - start

        print(f"captured {FRAME_COUNT} frames in {elapsed:.2f}s ({FRAME_COUNT / elapsed:.1f} fps)")
        print(f"last frame: index={frame.index} shape={frame.image.shape} dtype={frame.image.dtype}")

        cv2.imwrite(args.out, frame.image)
        print(f"saved last frame to {args.out} -- open it and confirm it looks like a real image")
    finally:
        camera.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
