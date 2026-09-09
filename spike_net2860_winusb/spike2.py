"""Stage 2: can we actually sustain the isochronous video stream?

Replays the captured init, selects an alternate setting with bandwidth,
and streams isochronously through WinUSB, measuring throughput and
packet-level errors. This is the go/no-go for a WinUSB replacement.
"""
import argparse, sys, time, collections
from winusb_dev import WinUsbDevice, IsochReader, _pipes, PIPE_TYPE
from init_writes import INIT_WRITES

INSTANCE_ID = r"USB\VID_20F1&PID_0004\5&2A77A265&0&4"


def replay_init(dev):
    writes = INIT_WRITES[:-4]
    prev = writes[0][0]
    for t, reg, val in writes:
        if t - prev > 0.005:
            time.sleep(t - prev)
        prev = t
        dev.write_reg(reg, val)
    return len(writes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alt", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--packets", type=int, default=64)
    ap.add_argument("--depth", type=int, default=8)
    ap.add_argument("--alt-first", action="store_true")
    args = ap.parse_args()

    with WinUsbDevice(INSTANCE_ID) as dev:
        print(f"pipes in alt {args.alt}:")
        for p in _pipes(dev, args.alt):
            print(f"  pipe 0x{p.PipeId:02x}  {PIPE_TYPE.get(p.PipeType,p.PipeType):12s} "
                  f"maxpkt={p.MaximumPacketSize:5d}  bytes/interval={p.MaximumBytesPerInterval}")

        if args.alt_first:
            dev.set_alt(args.alt); print(f"\nalt set to {dev.get_alt()} (before init)")
            print(f"replayed {replay_init(dev)} init writes")
        else:
            print(f"\nreplayed {replay_init(dev)} init writes")
            dev.set_alt(args.alt); print(f"alt set to {dev.get_alt()} (after init)")

        rd = IsochReader(dev, 0x82, args.alt, args.packets, args.depth)
        print(f"buffer: {args.depth} x {rd.xfer_bytes} B "
              f"({args.packets} packets x {rd.bytes_per_interval} B)")

        total = good = bad = 0
        nonzero = 0
        status_hist = collections.Counter()
        first_data = None
        rd.start()
        t0 = time.monotonic()
        slot = 0
        try:
            while time.monotonic() - t0 < args.seconds:
                r = rd.collect(slot, timeout_ms=2000)
                if r is None:
                    print(f"  TIMEOUT on slot {slot}", file=sys.stderr)
                    break
                got, ok, nbad, data, lengths = r
                total += sum(l for _, l, s in lengths if s == 0)
                good += ok; bad += nbad
                for _, _, s in lengths:
                    status_hist[s] += 1
                payload = b"".join(data[o:o+l] for o, l, s in lengths if s == 0 and l)
                if payload:
                    nonzero += sum(1 for b in payload[:4096] if b)
                    if first_data is None and any(payload[:64]):
                        first_data = payload[:64]
                rd.submit(slot, True)
                slot = (slot + 1) % args.depth
        finally:
            elapsed = time.monotonic() - t0
            rd.close()

        print(f"\n--- {elapsed:.2f}s ---")
        print(f"  bytes:   {total:,}  ({total/elapsed/1e6:.2f} MB/s)")
        print(f"  packets: {good} ok, {bad} error")
        print(f"  status codes: {dict(status_hist)}")
        if first_data:
            print(f"  first payload bytes: {first_data[:32].hex()}")
        target = 720*576*2*25/1e6
        print(f"\n  need ~{target:.2f} MB/s for 720x576 YUV422 @25fps")
        print("  RESULT:", "STREAMING WORKS" if total/elapsed/1e6 > target*0.8 else "under target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
