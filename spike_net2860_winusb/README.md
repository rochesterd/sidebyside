# Legacy BIO over WinUSB -- spike

Proof that the older Vantage Plus BIO's camera (NET GmbH KS722OUP, eMPIA
EM2860 bridge) can be driven entirely from user-mode Python through
Microsoft's inbox `winusb.sys`, with **no vendor driver, no 32-bit helper
subprocess, no COM, and nothing of Keeler's or NET GmbH's redistributed**.

This is a spike, not a shipping implementation. See DECISIONS.md's
2026-09-09 "WinUSB spike" entry for results and what it changes.

## What was proven

| | |
|---|---|
| Register protocol | 53/53 registers read back what was written |
| Isochronous throughput | 20.74 MB/s sustained, 40,000 packets, **0 errors** |
| Framing | 50.2 fields/s at 20.0 ms -- exactly PAL |
| Decode | 720x576 BGR frame, correct colour, no shear |

20.74 MB/s is exactly 720x576 YUV422 at 25fps, sustained from ordinary
CPython with the GIL and GC running -- no C shim needed.

## Prerequisites

The camera must be bound to WinUSB, which **replaces** the Keeler vendor
driver. Use Zadig (Options -> List All Devices -> `KS722OUP Device (0)` ->
target **WinUSB** -> Replace Driver).

To go back to the working vendor path: Device Manager -> Update driver ->
`C:\Program Files (x86)\2860_Cam\driver`.

The two drivers are mutually exclusive -- only one can be bound at a time.

## Files

| File | Role |
|---|---|
| `winusb_dev.py` | WinUSB via `ctypes` against inbox `winusb.dll`/`setupapi.dll`. Device discovery, control transfers, the EM2860 register protocol, and `IsochReader` (keeps N transfers in flight against one registered isoch buffer). |
| `init_writes.py` | The 62-write init sequence, as `(delay, register, value)`. |
| `gen_init.py` | Regenerates `init_writes.py` from the USB capture, so it is derived rather than hand-transcribed. |
| `spike1.py` | Replays the init and reads every register back. No isochronous. |
| `spike2.py` | Streams isochronously and measures throughput and packet errors. |
| `spike3.py` | Counts `0x22 0x5a` field headers to check framing against PAL. |
| `spike4.py` | Assembles two fields into one 720x576 BGR frame, saves a PNG. |

`gen_init.py` reads `bio_all.pcap`, captured with USBPcap and archived at
`vendor/net2860_driver/capture/` (gitignored).

## Wire format notes

- **Register read**: `bmRequestType 0xC0`, `bRequest 0x00`, `wIndex` = register.
- **Register write**: `0x40` / `0x01`, `wIndex` = register, `wValue` = value,
  **and** the value repeated as a 1-byte data payload. Both are required --
  the captured SETUP stage is 9 bytes, not 8.
- Every isochronous packet carries a 4-byte header. `22 5a <seq> 88` starts
  a field; `88 88 88 88` continues one. Strip both before accumulating.
- A field is 414,720 bytes = 720 x 288 x 2 (YUYV). Two fields interleave
  into a 720x576 frame; header byte 2 bit 0 is the field parity.
- Header byte 2 is also a **device-provided sequence counter** (0-127,
  wrapping, one per field). A real implementation should use it for
  `Frame.index` -- that gives genuine dropped-frame detection, which
  neither `UvcCamera` nor today's `Net2860Camera` has.

## Known gaps before this is shippable

- No `BaseCamera` wrapper: no capture thread, queue, or latest-frame slot.
- No orientation transform. `net2860_helper.py` hardcodes `flip_vertical`
  for this camera; production should go through `BaseCamera`'s existing
  `orientation` mechanic and `device_presets.py`, not re-solve it here.
- No error recovery: stalls, surprise removal, resume-from-sleep and
  bandwidth renegotiation are all currently the vendor driver's job.
- No production INF or signing story -- Zadig's self-signed package stands
  in. See DECISIONS.md for the certificate options.
- `INSTANCE_ID` is hardcoded in each script; real code must resolve the
  device rather than assume a USB port.
