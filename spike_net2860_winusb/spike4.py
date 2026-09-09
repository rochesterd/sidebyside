"""Stage 3: assemble two fields into a real 720x576 BGR frame.

Every isochronous packet carries a 4-byte header: 22 5a <seq> 88 starts a
field, 88 88 88 88 continues one. Strip both, accumulate 414,720 bytes per
field, then interleave an even/odd pair into a full interlaced frame.
"""
import sys, time
import numpy as np, cv2
from winusb_dev import WinUsbDevice, IsochReader
from init_writes import INIT_WRITES

INSTANCE_ID = r"USB\VID_20F1&PID_0004\5&2A77A265&0&4"
ALT, W, H = 6, 720, 576
FIELD_BYTES = W * (H // 2) * 2
CONT = b"\x88\x88\x88\x88"

with WinUsbDevice(INSTANCE_ID) as dev:
    w = INIT_WRITES[:-4]
    prev = w[0][0]
    for t, reg, val in w:
        if t - prev > 0.005:
            time.sleep(t - prev)
        prev = t
        dev.write_reg(reg, val)
    dev.set_alt(ALT)

    rd = IsochReader(dev, 0x82, ALT, 64, 8)
    fields, cur, cur_seq, short = [], None, None, 0
    rd.start()
    t0 = time.monotonic()
    slot = 0
    while time.monotonic() - t0 < 3.0 and len(fields) < 6:
        r = rd.collect(slot, 2000)
        if r is None:
            break
        got, ok, bad, data, lengths = r
        for off, ln, st in lengths:
            if st != 0 or ln == 0:
                continue
            pkt = data[off:off + ln]
            if pkt[0] == 0x22 and pkt[1] == 0x5a:
                if cur is not None:
                    if len(cur) >= FIELD_BYTES:
                        fields.append((cur_seq, bytes(cur[:FIELD_BYTES])))
                    else:
                        short += 1
                cur, cur_seq = bytearray(pkt[4:]), pkt[2]
            elif cur is not None:
                cur += pkt[4:] if pkt[:4] == CONT else pkt
        rd.submit(slot, True)
        slot = (slot + 1) % 8
    rd.close()

print(f"captured {len(fields)} fields (seqs {[s for s, _ in fields]}), {short} short")
if len(fields) < 2:
    sys.exit("not enough fields")

pair = next((((s1, f1), (s2, f2)) for (s1, f1), (s2, f2) in zip(fields, fields[1:])
             if (s1 & 1) != (s2 & 1)), None)
if pair is None:
    sys.exit("no even/odd pair")

(s1, f1), (s2, f2) = pair
top, bot = (f1, f2) if (s1 & 1) == 0 else (f2, f1)
frame = np.empty((H, W, 2), np.uint8)
frame[0::2] = np.frombuffer(top, np.uint8).reshape(H // 2, W, 2)
frame[1::2] = np.frombuffer(bot, np.uint8).reshape(H // 2, W, 2)
bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUY2)
cv2.imwrite("winusb_frame.png", bgr)
print(f"paired seq {s1}/{s2} -> {bgr.shape} mean={bgr.mean():.1f} -> winusb_frame.png")
