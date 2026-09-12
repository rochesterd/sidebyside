"""Answer IMAGING.md's "Not yet known" list against an attached IDS camera:
which bandwidth and image levers this camera actually offers, what their
ranges are, and which of them stay writable once acquisition has started.

    python tools/probe_camera_features.py <serial> [--seconds 10]

Get the serial from tools/check_ids.py. Read-only: every node is reported,
none is written, so this is safe to run on a calibrated clinic machine.

What it reports, with the camera streaming -- which is the harder of the
two questions, since GenICam freezes payload-affecting nodes at
TLParamsLocked, so a node writable when idle may be read-only mid-session:

  levers    region of interest, binning, the USB throughput cap, hardware
            mirroring, gamma and black level, exposure and gain -- each as
            present/absent, writable/read-only, and its range.
  formats   every pixel format this camera offers and what each would cost
            in MB/s at 30fps, so a bit-depth decision has a number on it.

Plus a throughput sample at the camera's configured format: measured fps,
frames dropped (counted from the device's own FrameID, not estimated),
bandwidth, and this process's CPU load while capturing.

Known gap: sustained frame rate at 10- and 12-bit is *not* measured here.
IdsCamera has no pixel-format parameter, and a probe is the wrong place to
reimplement its open sequence -- see IMAGING.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ids_camera.py lives at the repo root, one level up from tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ids_camera import IdsCamera, IdsCameraNotFoundError

# Grouped so the output reads as questions, not an alphabetical node dump.
GROUPS: list[tuple[str, list[str]]] = [
    ("Region of interest", ["Width", "Height", "WidthMax", "HeightMax", "OffsetX", "OffsetY"]),
    ("Binning / decimation", [
        "BinningHorizontal", "BinningVertical", "BinningSelector",
        "DecimationHorizontal", "DecimationVertical",
    ]),
    ("Link budget", [
        "DeviceLinkThroughputLimit", "DeviceLinkThroughputLimitMode",
        "DeviceClockFrequency", "AcquisitionFrameRate", "AcquisitionFrameRateEnable",
    ]),
    ("Orientation in hardware", ["ReverseX", "ReverseY"]),
    ("Tone", ["Gamma", "GammaEnable", "BlackLevel", "BlackLevelAuto", "LUTEnable"]),
    ("Exposure and gain", ["ExposureTime", "Gain", "ExposureAuto", "GainAuto", "BalanceWhiteAuto"]),
]

BITS_PER_PIXEL = {"8": 8, "10p": 10, "12p": 12, "10": 16, "12": 16}


def describe(node_map, name: str) -> str:
    node = node_map.TryFindNode(name)
    if node is None:
        return "absent"
    try:
        state = "writable" if node.IsWriteable() else "READ-ONLY"
    except Exception:
        state = "state unknown"
    try:
        return f"{state}  {node.CurrentEntry().SymbolicValue()}"
    except Exception:
        pass
    try:
        detail = f"{node.Value()}  [{node.Minimum()} .. {node.Maximum()}"
        try:
            detail += f", step {node.Increment()}"
        except Exception:
            pass
        return f"{state}  {detail}]"
    except Exception:
        return state


def pixel_format_costs(node_map, width: int, height: int, fps: float) -> list[str]:
    """Every format this camera offers, with what it would cost at `fps`."""
    node = node_map.TryFindNode("PixelFormat")
    if node is None:
        return ["  PixelFormat absent"]
    lines = []
    for entry in node.AvailableEntries():
        try:
            name = entry.SymbolicValue()
        except Exception:
            continue
        bits = next((b for k, b in BITS_PER_PIXEL.items() if name.endswith(k)), None)
        if name.startswith(("RGB", "BGR")) and bits == 8:
            bits = 32 if "a" in name else 24
        cost = f"{width * height * bits / 8 * fps / 1e6:8.1f} MB/s" if bits else "        ? MB/s"
        lines.append(f"  {name:<14} {cost}")
    return lines


def report(node_map, heading: str) -> None:
    print(f"\n===== {heading}")
    for group, names in GROUPS:
        print(f"  -- {group}")
        for name in names:
            print(f"     {name:<28} {describe(node_map, name)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serial", help="camera serial number, from tools/check_ids.py")
    parser.add_argument("--seconds", type=float, default=10.0, help="throughput sample length")
    args = parser.parse_args()

    camera = IdsCamera(serial=args.serial, target_fps=None, converge_auto=False)
    try:
        camera.start()
    except IdsCameraNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        node_map = camera._node_map  # a diagnostic reads what the app wraps
        width, height = camera.resolution
        print(f"serial {args.serial}  {width}x{height}")
        report(node_map, "while streaming (what we could change mid-session)")

        print("\n===== every pixel format, and what it would cost at 30fps")
        for line in pixel_format_costs(node_map, width, height, 30.0):
            print(line)

        print(f"\n===== throughput at the configured format, {args.seconds:.0f}s")
        while camera.read(timeout=0) is not None:
            pass
        first = camera.read(timeout=5.0)
        if first is None:
            print("  no frames arrived")
            return 1
        frames, last_index = 1, first.index
        gaps = 0
        cpu_start, wall_start = time.process_time(), time.monotonic()
        deadline = wall_start + args.seconds
        while time.monotonic() < deadline:
            frame = camera.read(timeout=2.0)
            if frame is None:
                continue
            gaps += max(0, frame.index - last_index - 1)
            last_index, frames = frame.index, frames + 1
        elapsed = time.monotonic() - wall_start
        cpu = time.process_time() - cpu_start
        pixels = width * height
        print(f"  {frames} frames in {elapsed:.1f}s = {frames / elapsed:.2f} fps")
        print(f"  {gaps} dropped (gaps in the camera's own FrameID)")
        print(f"  {pixels * 3 * frames / elapsed / 1e6:.1f} MB/s as delivered BGR8 to this process")
        print(f"  {100 * cpu / elapsed:.0f}% of one core, this process, capture only")
    finally:
        camera.stop()

    print("\nNote: nodes reported READ-ONLY above may still be writable before")
    print("acquisition starts -- that is the TLParamsLocked question. Re-run")
    print("against IMAGING.md's table and update it with whatever this says.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
