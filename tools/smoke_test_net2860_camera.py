r"""Hardware smoke test for Net2860Camera (the older Vantage Plus BIO's NET
GmbH KS722OUP camera): opens the camera through the real 32-bit helper
subprocess, captures a handful of frames through the real capture thread,
reports resolution and measured fps, and saves the last frame as a PNG for
a visual sanity check. See DECISIONS.md's "Net2860Camera: 32-bit helper
process for the older Vantage Plus BIO" entry.

    powershell .\setup_net2860_helper.ps1        (once, to create .venv32/)
    python tools/smoke_test_net2860_camera.py

Unlike tools/smoke_test_camera.py (IdsCamera), there's no serial to pass --
Net2860Camera doesn't identify a specific unit, since there's exactly one of
this camera in this setup (see DECISIONS.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# net2860_camera.py lives at the repo root, one level up from tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from net2860_camera import Net2860Camera, Net2860CameraError

FRAME_COUNT = 30
READ_TIMEOUT_S = 5.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="smoke_test_frame.png", help="where to save the last captured frame"
    )
    args = parser.parse_args()

    camera = Net2860Camera()
    try:
        camera.start()
    except Net2860CameraError as e:
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
