r"""Hardware smoke test for Net2860WinUsbCamera (the older Vantage Plus
BIO's NET GmbH KS722OUP camera, over Microsoft's inbox winusb.sys): opens
the camera, captures a few seconds through the real capture thread, reports
resolution, measured fps and whether the hardware frame counter shows any
gaps, and saves the last frame as a PNG for a visual check.

    python tools/smoke_test_net2860_winusb_camera.py

Needs the WinUSB driver package installed first -- see
packaging/net2860_winusb/. If discovery finds nothing, that is the usual
reason (the other being that the camera isn't plugged in).

No serial to pass: there's exactly one of this camera, so it is identified
by VID/PID.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# net2860_winusb_camera.py lives at the repo root, one level up from tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

import winusb
from net2860_winusb_camera import PID, VID, Net2860WinUsbCamera, Net2860WinUsbError

SECONDS = 5.0
READ_TIMEOUT_S = 5.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="smoke_test_frame_winusb.png",
                        help="where to save the last captured frame")
    parser.add_argument("--seconds", type=float, default=SECONDS)
    args = parser.parse_args()

    found = winusb.find_by_vid_pid(VID, PID)
    print(f"discovery: {len(found)} device(s)")
    for instance, path in found:
        print(f"  {instance}\n    {path}")
    if not found:
        print("error: no WinUSB-bound camera found -- is the driver package installed?",
              file=sys.stderr)
        return 1

    camera = Net2860WinUsbCamera()
    try:
        camera.start()
    except Net2860WinUsbError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        print(f"resolution: {camera.resolution}")
        frames = []
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            frame = camera.read(timeout=READ_TIMEOUT_S)
            if frame is None:
                print("error: timed out waiting for a frame", file=sys.stderr)
                return 1
            frames.append(frame)
        elapsed = time.monotonic() - start

        print(f"captured {len(frames)} frames in {elapsed:.2f}s ({len(frames) / elapsed:.1f} fps)")

        # Frame.index is the camera's own field counter, so a delta of
        # anything but 1 is a real source-side drop rather than a guess.
        deltas = sorted({b.index - a.index for a, b in zip(frames, frames[1:])})
        print(f"frame index deltas seen: {deltas}"
              + ("  <- gaps mean dropped frames" if deltas != [1] else "  (no drops)"))
        print(f"short fields discarded: {camera.short_fields}")

        last = frames[-1]
        print(f"last frame: index={last.index} shape={last.image.shape} mean={last.image.mean():.1f}")
        cv2.imwrite(args.out, last.image)
        print(f"saved last frame to {args.out} -- open it and confirm it looks like a real image")
    finally:
        camera.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
