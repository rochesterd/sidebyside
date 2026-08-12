"""Standalone check that the ids_peak Python bindings are installed and
match the IDS peak runtime on this machine. Not wired into the app; does
not import any project camera modules. See SETUP.md Section 4.

Exits 0 if DeviceManager.Update() completes without raising, regardless of
how many devices are found. 0 devices is a valid, expected result on a
machine with no cameras attached — it means the bindings loaded and found
the GenTL producers, not that anything is broken.
"""

from __future__ import annotations

import sys

from ids_peak import ids_peak


def main() -> int:
    ids_peak.Library.Initialize()
    try:
        device_manager = ids_peak.DeviceManager.Instance()
        device_manager.Update()

        devices = device_manager.Devices()
        print(f"{len(devices)} device(s) found:")
        for descriptor in devices:
            print(f"  {descriptor.ModelName()}  serial={descriptor.SerialNumber()}")
    finally:
        ids_peak.Library.Close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
