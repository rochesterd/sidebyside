"""Stage 3 probe: do em28xx frame headers segment the stream cleanly?

0x22 0x5a marks a frame/field start in Linux's em28xx driver; byte 2 bit 0
carries field parity. Count them and see whether the implied field rate
matches PAL (50 fields/s -> 25 frames/s) and the payload per field matches
720x576 YUV422.
"""
import sys, time, collections
from winusb_dev import WinUsbDevice, IsochReader
from init_writes import INIT_WRITES

INSTANCE_ID = r"USB\VID_20F1&PID_0004\5&2A77A265&0&4"
ALT, SECONDS = 6, 4.0

with WinUsbDevice(INSTANCE_ID) as dev:
    w = INIT_WRITES[:-4]; prev = w[0][0]
    for t, reg, val in w:
        if t - prev > 0.005: time.sleep(t - prev)
        prev = t; dev.write_reg(reg, val)
    dev.set_alt(ALT)

    rd = IsochReader(dev, 0x82, ALT, 64, 8)
    headers, field_bytes, since_header = [], [], 0
    hdr_flags = collections.Counter()
    rd.start(); t0 = time.monotonic(); slot = 0
    while time.monotonic() - t0 < SECONDS:
        r = rd.collect(slot, 2000)
        if r is None: break
        got, ok, bad, data, lengths = r
        for off, ln, st in lengths:
            if st != 0 or ln == 0: continue
            pkt = data[off:off+ln]
            if len(pkt) >= 4 and pkt[0] == 0x22 and pkt[1] == 0x5a:
                headers.append(time.monotonic() - t0)
                hdr_flags[pkt[2]] += 1
                if since_header: field_bytes.append(since_header)
                since_header = len(pkt) - 4
            else:
                since_header += len(pkt)
        rd.submit(slot, True); slot = (slot + 1) % 8
    elapsed = time.monotonic() - t0
    rd.close()

print(f"--- {elapsed:.2f}s ---")
print(f"  0x22 0x5a headers seen: {len(headers)}  -> {len(headers)/elapsed:.1f} /s")
print(f"  header byte[2] values:  {dict(hdr_flags)}   (bit0 = field parity)")
if field_bytes:
    body = field_bytes[1:-1] or field_bytes
    avg = sum(body)/len(body)
    print(f"  payload between headers: avg {avg:,.0f} B  (min {min(body):,} max {max(body):,})")
    print(f"  720x576 YUV422 full frame = {720*576*2:,} B;  one field = {720*288*2:,} B")
if len(headers) > 2:
    gaps = [b-a for a, b in zip(headers, headers[1:])]
    print(f"  header interval: avg {sum(gaps)/len(gaps)*1000:.1f} ms")
