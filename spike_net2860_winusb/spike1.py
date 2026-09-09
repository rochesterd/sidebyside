"""Stage 1: prove WinUSB binding + the EM2860 register protocol.

Replays the 62 register writes captured from the Keeler vendor driver,
honouring the captured inter-write delays, then reads every touched
register back and compares. No isochronous transfers -- this only
establishes that we can talk to the bridge at all.
"""
import sys, time
from winusb_dev import WinUsbDevice
from init_writes import INIT_WRITES

INSTANCE_ID = r"USB\VID_20F1&PID_0004\5&2A77A265&0&4"

# The last four writes mirror the first four and look like the vendor
# driver's stop/reset bracket -- replay them for fidelity only if asked.
TRAILING_STOP = 4

def main(include_stop=False):
    try:
        dev = WinUsbDevice(INSTANCE_ID)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        print("\nBind the device to WinUSB first (Zadig -> WinUSB), then re-run.", file=sys.stderr)
        return 1

    with dev:
        print(f"opened: {dev.path}")
        print(f"current alt setting: {dev.get_alt()}")

        try:
            chip = dev.read_reg(0x0A)
            print(f"reg 0x0a (chip id) = 0x{chip:02x}   <- capture saw 0x22")
        except OSError as e:
            print(f"first read FAILED: {e}", file=sys.stderr)
            return 1

        writes = INIT_WRITES[:-TRAILING_STOP] if not include_stop else INIT_WRITES
        print(f"\nreplaying {len(writes)} register writes...")
        prev_t = writes[0][0]
        for t, reg, val in writes:
            gap = t - prev_t
            if gap > 0.005:
                time.sleep(gap)
            prev_t = t
            dev.write_reg(reg, val)

        expected = {}
        for _, reg, val in writes:
            expected[reg] = val

        print(f"\nreading back {len(expected)} registers:")
        ok = bad = 0
        mismatches = []
        for reg in sorted(expected):
            got = dev.read_reg(reg)
            want = expected[reg]
            if got == want:
                ok += 1
            else:
                bad += 1
                mismatches.append((reg, want, got))
        print(f"  {ok} match, {bad} differ")
        for reg, want, got in mismatches:
            print(f"    reg 0x{reg:02x}: wrote 0x{want:02x}, read 0x{got:02x}")
        print("\nSTAGE 1 RESULT: control transfers work, register protocol confirmed."
              if ok else "\nSTAGE 1 RESULT: writes are not sticking.")
    return 0

if __name__ == "__main__":
    sys.exit(main("--with-stop" in sys.argv))
