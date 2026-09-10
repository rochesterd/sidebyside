r"""Dump everything the host can learn about a legacy BIO camera, so two
physical units can be compared.

Why this exists: two Keeler Vantage Plus BIOs in service at NECO present
images 180 degrees apart from each other, through the same driver and the
same VID/PID. If the difference is visible to the host, orientation can be
a per-revision preset (the way device_presets.py keys off IDS model
names). If it is not, orientation is a per-unit fact that only a
technician can know, and it has to become a config.json setting.

Run it with one unit attached, save the output, swap units, run it again,
and diff:

    python tools/net2860_identity.py > unit-a.txt
    python tools/net2860_identity.py > unit-b.txt

The fields that could plausibly differ, in order of likelihood:

  bcdDevice   a hardware/firmware revision, and the only identity field
              this device populates -- it also lands in the Windows
              hardware ID as REV_xxxx, so a difference here is something
              an INF could even match on separately
  registers   the EM2860's readback after init; mostly echoes what we
              wrote, but a board strap or chip revision could show
  string descriptors  all absent (index 0) on the unit checked
              2026-09-10, but a later board might populate them

If both units are identical here, the host cannot tell them apart, and
neither can Keeler's Kapture -- which would mean Kapture is not
compensating at all, just displaying whatever the sensor gives.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import winusb
from net2860_init import START_WRITES
from net2860_winusb_camera import PID, VID


# The EM2860 register protocol, spelled out here rather than reached for on
# WinUsbDevice: winusb.py is deliberately camera-agnostic, so it knows about
# control transfers and not about registers. Same two requests
# net2860_winusb_camera.py uses.
def read_reg(dev, reg: int) -> int:
    return dev.control(0xC0, 0x00, 0x0000, reg, length=1)[0]


def write_reg(dev, reg: int, val: int) -> None:
    dev.control(0x40, 0x01, val, reg, data=bytes([val]))


def main() -> int:
    found = winusb.find_by_vid_pid(VID, PID)
    print("=== discovery ===")
    for instance, path in found:
        print(f"  instance {instance}")
        print(f"  path     {path}")
    if not found:
        print("  no WinUSB-bound camera found", file=sys.stderr)
        return 1

    dev = winusb.WinUsbDevice.open_one(VID, PID)
    try:
        raw = dev.control(0x80, 0x06, (1 << 8) | 0, 0, length=18)
        (_len, _type, bcdUSB, cls, sub, proto, maxpkt, vid, pid,
         bcdDevice, i_man, i_prod, i_serial, n_cfg) = struct.unpack("<BBHBBBBHHHBBBB", raw)

        print("\n=== device descriptor ===")
        print(f"  bcdUSB           0x{bcdUSB:04x}")
        print(f"  idVendor:Product 0x{vid:04x}:0x{pid:04x}")
        print(f"  bcdDevice        0x{bcdDevice:04x}   <-- THE FIELD TO COMPARE")
        print(f"  class/sub/proto  0x{cls:02x}/0x{sub:02x}/0x{proto:02x}")
        print(f"  maxPacketSize0   {maxpkt}")
        print(f"  numConfigurations {n_cfg}")

        print("\n=== string descriptors ===")
        for name, idx in (("manufacturer", i_man), ("product", i_prod), ("serial", i_serial)):
            if not idx:
                print(f"  {name:13s} index 0 (not present)")
                continue
            try:
                s = dev.control(0x80, 0x06, (3 << 8) | idx, 0x0409, length=255)
                print(f"  {name:13s} {s[2:s[0]].decode('utf-16-le', 'replace')!r}")
            except Exception as e:  # noqa: BLE001 -- diagnostic, report and continue
                print(f"  {name:13s} read failed: {e}")

        # Registers are read *before* init as well as after: a value that
        # only differs pre-init would be a board-level default rather than
        # an echo of what we just wrote.
        print("\n=== EM2860 registers, before init ===")
        _dump_registers(dev)

        for delay, reg, val in START_WRITES:
            write_reg(dev, reg, val)
        print("\n=== EM2860 registers, after init ===")
        _dump_registers(dev)

        print("\n=== isochronous pipe bandwidth per alt setting ===")
        for alt in range(8):
            pipes = dev.pipes(alt)
            iso = [p for p in pipes if p.PipeType == 1 and p.PipeId == 0x82]
            if iso:
                print(f"  alt {alt}: {iso[0].MaximumBytesPerInterval} bytes/interval")
    finally:
        dev.close()

    print("\nCompare two units by diffing this output. If nothing differs, the")
    print("host cannot distinguish them and orientation must become a setting.")
    return 0


def _dump_registers(dev) -> None:
    """Rows of 16. '--' means that register refused a read, which is
    normal for some of the EM2860's address space."""
    for base in range(0x00, 0x40, 16):
        row = []
        for reg in range(base, base + 16):
            try:
                row.append("%02x" % read_reg(dev, reg))
            except Exception:  # noqa: BLE001 -- some registers refuse a read
                row.append("--")
        print(f"  0x{base:02x}: {' '.join(row)}")


if __name__ == "__main__":
    sys.exit(main())
