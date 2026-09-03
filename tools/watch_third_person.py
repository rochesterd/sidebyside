"""Watch the real third-person UVC camera and report every stall or
reconnect, with timestamps.

For diagnosing an intermittent "the camera disconnects" report: the kiosk
logs a reconnect when it happens, but that line scrolls past and a run
that is fine for ten minutes then blips is hard to catch. This prints one
line a second, stays quiet-but-visible while healthy, and shouts on any
gap -- so a run can be left going and the output pasted back.

Uses exactly the path app.py uses: config.json's third_person vid_pid,
resolved at start(), with the same recording.fps rate cap. Run it with
the kiosk closed (the camera can usually be shared, but a clean reading
is the point).

    python tools/watch_third_person.py [seconds]

Ctrl-C stops it early and still prints the summary.
"""

from __future__ import annotations

import logging
import sys
import time

import cv2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from uvc_camera import UvcCamera  # noqa: E402


class _EventCounter(logging.Handler):
    """Counts (and echoes) uvc_camera's own reconnect warnings, which are
    the authoritative signal that the device actually dropped."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.events: list[tuple[float, str]] = []
        self._t0 = time.monotonic()

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append((time.monotonic() - self._t0, record.getMessage()))


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    counter = _EventCounter()
    logging.getLogger("uvc_camera").addHandler(counter)

    cfg = load_config()
    vid_pid = cfg.third_person.vid_pid
    print(f"third-person camera: {cfg.third_person.friendly_name} ({vid_pid})")
    print(f"watching for {duration:.0f}s -- Ctrl-C to stop early\n")

    camera = UvcCamera(vid_pid=vid_pid, name="third-person", target_fps=cfg.recording.fps)
    camera.start()
    print(f"opened at {camera.resolution[0]}x{camera.resolution[1]}\n")

    last_index = None
    last_image = None
    stalled_seconds = 0
    frozen_seconds = 0
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < duration:
            time.sleep(1.0)
            elapsed = time.monotonic() - t0
            frame = camera.get_latest()
            index = frame.index if frame is not None else None
            image = frame.image if frame is not None else None

            if index is None:
                stalled_seconds += 1
                print(f"t={elapsed:6.1f}s  *** NO FRAME AT ALL ***  "
                      f"consecutive_read_failures={camera._consecutive_failures}", flush=True)
            elif last_index is None:
                print(f"t={elapsed:6.1f}s  first frame (index {index})", flush=True)
            else:
                delta = index - last_index
                # A frozen stream still advances Frame.index, because
                # UvcCamera counts frames itself (UVC exposes no source
                # counter -- see DECISIONS.md). So compare the pixels: a
                # live sensor is never identical twice, even in the dark.
                frozen = last_image is not None and image is not None and not cv2.absdiff(image, last_image).any()
                if delta <= 0:
                    stalled_seconds += 1
                    print(f"t={elapsed:6.1f}s  *** NO NEW FRAMES ***  "
                          f"consecutive_read_failures={camera._consecutive_failures}", flush=True)
                elif frozen:
                    frozen_seconds += 1
                    print(f"t={elapsed:6.1f}s  +{delta:3d} frames but *** IDENTICAL PIXELS *** "
                          f"-- camera is delivering a frozen image, not live video", flush=True)
                else:
                    print(f"t={elapsed:6.1f}s  +{delta:3d} frames  (~{delta}fps)  live", flush=True)

            if index is not None:
                last_index = index
                last_image = image
    except KeyboardInterrupt:
        print("\nstopped early")
    finally:
        camera.stop()

    print("\n--- summary ---")
    print(f"ran for {time.monotonic() - t0:.0f}s")
    print(f"seconds with no new frames : {stalled_seconds}")
    print(f"seconds frozen (same pixels): {frozen_seconds}")
    print(f"reconnect/failure events   : {len(counter.events)}")
    for when, message in counter.events:
        print(f"    t={when:6.1f}s  {message}")
    if frozen_seconds:
        print("")
        print("FROZEN: reads succeeded and the frame counter advanced, but the")
        print("pixels never changed. The camera is switched off or blocked at the")
        print("OS/driver level (privacy shutter, camera F-key, or vendor privacy")
        print("software) and the driver is re-delivering one still image.")
    if not stalled_seconds and not frozen_seconds and not counter.events:
        print("\nNo drop seen. If the camera visibly cut out during this run,")
        print("the problem is not the capture stream -- say what you saw instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
