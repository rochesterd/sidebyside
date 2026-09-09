"""Minimal WinUSB access via ctypes -- no libusb, no third-party runtime.

Opens a device that has been bound to Microsoft's inbox winusb.sys and
exposes control transfers plus alternate-setting selection. Everything
here talks to winusb.dll / setupapi.dll, both inbox.
"""
from __future__ import annotations

import ctypes as C
import winreg
from ctypes import wintypes as W

setupapi = C.WinDLL("setupapi", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
winusb   = C.WinDLL("winusb",   use_last_error=True)

INVALID_HANDLE = C.c_void_p(-1).value
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_RW, OPEN_EXISTING = 0x03, 3
FILE_FLAG_OVERLAPPED, FILE_ATTRIBUTE_NORMAL = 0x40000000, 0x80


class GUID(C.Structure):
    _fields_ = [("d1", C.c_ulong), ("d2", C.c_ushort), ("d3", C.c_ushort), ("d4", C.c_ubyte * 8)]

    @classmethod
    def from_string(cls, s):
        s = s.strip("{}")
        a, b, c, d, e = s.split("-")
        d4 = bytes.fromhex(d) + bytes.fromhex(e)
        return cls(int(a, 16), int(b, 16), int(c, 16), (C.c_ubyte * 8)(*d4))


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", W.DWORD), ("Reserved", C.POINTER(C.c_ulonglong))]


class WINUSB_SETUP_PACKET(C.Structure):
    _pack_ = 1
    _fields_ = [("RequestType", C.c_ubyte), ("Request", C.c_ubyte),
                ("Value", C.c_ushort), ("Index", C.c_ushort), ("Length", C.c_ushort)]


setupapi.SetupDiGetClassDevsW.restype = C.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p, C.c_void_p]
kernel32.CreateFileW.restype = C.c_void_p
winusb.WinUsb_Initialize.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
winusb.WinUsb_Free.argtypes = [C.c_void_p]
winusb.WinUsb_ControlTransfer.argtypes = [C.c_void_p, WINUSB_SETUP_PACKET, C.c_void_p,
                                          W.ULONG, C.POINTER(W.ULONG), C.c_void_p]
winusb.WinUsb_SetCurrentAlternateSetting.argtypes = [C.c_void_p, C.c_ubyte]
winusb.WinUsb_GetCurrentAlternateSetting.argtypes = [C.c_void_p, C.POINTER(C.c_ubyte)]


def interface_guids(instance_id: str) -> list[str]:
    """Read the DeviceInterfaceGUID(s) the WinUSB INF registered for a device."""
    key = rf"SYSTEM\CurrentControlSet\Enum\{instance_id}\Device Parameters"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        for name in ("DeviceInterfaceGUIDs", "DeviceInterfaceGUID"):
            try:
                v, _ = winreg.QueryValueEx(k, name)
                return [v] if isinstance(v, str) else list(v)
            except FileNotFoundError:
                continue
    return []


def find_device_path(guid_str: str) -> str | None:
    guid = GUID.from_string(guid_str)
    h = setupapi.SetupDiGetClassDevsW(C.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    try:
        did = SP_DEVICE_INTERFACE_DATA(); did.cbSize = C.sizeof(did)
        if not setupapi.SetupDiEnumDeviceInterfaces(h, None, C.byref(guid), 0, C.byref(did)):
            return None
        need = W.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(h, C.byref(did), None, 0, C.byref(need), None)
        buf = C.create_string_buffer(need.value)
        C.cast(buf, C.POINTER(W.DWORD))[0] = 8
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(h, C.byref(did), C.cast(buf, C.c_void_p),
                                                         need, C.byref(need), None):
            return None
        return C.wstring_at(C.addressof(buf) + 4)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(C.c_void_p(h))


class WinUsbDevice:
    def __init__(self, instance_id: str):
        guids = interface_guids(instance_id)
        if not guids:
            raise RuntimeError(
                f"no DeviceInterfaceGUID for {instance_id} -- is it bound to WinUSB yet?")
        self.path = next((p for g in guids if (p := find_device_path(g))), None)
        if not self.path:
            raise RuntimeError(f"device interface not present for GUIDs {guids}")
        self._file = kernel32.CreateFileW(self.path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW,
                                          None, OPEN_EXISTING,
                                          FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None)
        if self._file in (None, INVALID_HANDLE):
            raise OSError(f"CreateFile failed: {C.get_last_error()}")
        self._h = C.c_void_p()
        if not winusb.WinUsb_Initialize(self._file, C.byref(self._h)):
            raise OSError(f"WinUsb_Initialize failed: {C.get_last_error()}")

    @property
    def handle(self):
        return self._h

    def control(self, bmRequestType, bRequest, wValue, wIndex, data=None, length=0):
        if data is not None:
            buf = C.create_string_buffer(bytes(data), len(data)); length = len(data)
        else:
            buf = C.create_string_buffer(length) if length else None
        pkt = WINUSB_SETUP_PACKET(bmRequestType, bRequest, wValue, wIndex, length)
        got = W.ULONG()
        ok = winusb.WinUsb_ControlTransfer(self._h, pkt,
                                           C.cast(buf, C.c_void_p) if buf else None,
                                           length, C.byref(got), None)
        if not ok:
            raise OSError(f"control transfer failed (err {C.get_last_error()}) "
                          f"bmRT=0x{bmRequestType:02x} bReq=0x{bRequest:02x} idx=0x{wIndex:04x}")
        return bytes(buf.raw[:got.value]) if buf else b""

    # --- the EM2860 register protocol, exactly as captured from the vendor driver
    def read_reg(self, reg: int) -> int:
        return self.control(0xC0, 0x00, 0x0000, reg, length=1)[0]

    def write_reg(self, reg: int, val: int) -> None:
        self.control(0x40, 0x01, val, reg, data=bytes([val]))

    def set_alt(self, alt: int) -> None:
        if not winusb.WinUsb_SetCurrentAlternateSetting(self._h, alt):
            raise OSError(f"SetCurrentAlternateSetting({alt}) failed: {C.get_last_error()}")

    def get_alt(self) -> int:
        a = C.c_ubyte()
        winusb.WinUsb_GetCurrentAlternateSetting(self._h, C.byref(a))
        return a.value

    def close(self):
        if getattr(self, "_h", None): winusb.WinUsb_Free(self._h); self._h = None
        if getattr(self, "_file", None): kernel32.CloseHandle(C.c_void_p(self._file)); self._file = None

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


# --------------------------------------------------------------------------
# Isochronous support (WinUSB, Windows 8.1+)
# --------------------------------------------------------------------------

ULONG_PTR = C.c_ulonglong
PIPE_TYPE = {0: "Control", 1: "Isochronous", 2: "Bulk", 3: "Interrupt"}


class WINUSB_PIPE_INFORMATION_EX(C.Structure):
    _fields_ = [("PipeType", C.c_int), ("PipeId", C.c_ubyte),
                ("MaximumPacketSize", C.c_ushort), ("Interval", C.c_ubyte),
                ("MaximumBytesPerInterval", C.c_ulong)]


class USBD_ISO_PACKET_DESCRIPTOR(C.Structure):
    _fields_ = [("Offset", C.c_ulong), ("Length", C.c_ulong), ("Status", C.c_long)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", ULONG_PTR), ("InternalHigh", ULONG_PTR),
                ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", C.c_void_p)]


winusb.WinUsb_QueryPipeEx.argtypes = [C.c_void_p, C.c_ubyte, C.c_ubyte,
                                      C.POINTER(WINUSB_PIPE_INFORMATION_EX)]
winusb.WinUsb_RegisterIsochBuffer.argtypes = [C.c_void_p, C.c_ubyte, C.c_void_p,
                                              W.ULONG, C.POINTER(C.c_void_p)]
winusb.WinUsb_UnregisterIsochBuffer.argtypes = [C.c_void_p]
winusb.WinUsb_ReadIsochPipeAsap.argtypes = [C.c_void_p, W.ULONG, W.ULONG, W.BOOL, W.ULONG,
                                            C.POINTER(USBD_ISO_PACKET_DESCRIPTOR), C.c_void_p]
winusb.WinUsb_GetOverlappedResult.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(W.DWORD), W.BOOL]
winusb.WinUsb_ResetPipe.argtypes = [C.c_void_p, C.c_ubyte]
kernel32.CreateEventW.restype = C.c_void_p
kernel32.WaitForSingleObject.argtypes = [C.c_void_p, W.DWORD]


def _pipes(dev, alt):
    out = []
    for idx in range(8):
        info = WINUSB_PIPE_INFORMATION_EX()
        if not winusb.WinUsb_QueryPipeEx(dev.handle, alt, idx, C.byref(info)):
            break
        out.append(info)
    return out


class IsochReader:
    """Keeps `depth` isochronous transfers in flight against one registered buffer."""

    def __init__(self, dev, pipe_id, alt, packets_per_xfer=64, depth=8):
        self.dev, self.pipe_id = dev, pipe_id
        info = next((p for p in _pipes(dev, alt) if p.PipeId == pipe_id), None)
        if info is None:
            raise RuntimeError(f"pipe 0x{pipe_id:02x} not found in alt {alt}")
        self.bytes_per_interval = info.MaximumBytesPerInterval
        self.packets = packets_per_xfer
        self.depth = depth
        self.xfer_bytes = self.bytes_per_interval * self.packets
        self.buf = C.create_string_buffer(self.xfer_bytes * depth)
        self.handle = C.c_void_p()
        if not winusb.WinUsb_RegisterIsochBuffer(dev.handle, pipe_id, C.cast(self.buf, C.c_void_p),
                                                 len(self.buf), C.byref(self.handle)):
            raise OSError(f"RegisterIsochBuffer failed: {C.get_last_error()}")
        self.ovl = [OVERLAPPED() for _ in range(depth)]
        self.desc = [(USBD_ISO_PACKET_DESCRIPTOR * self.packets)() for _ in range(depth)]
        for o in self.ovl:
            o.hEvent = kernel32.CreateEventW(None, True, False, None)
        self._started = False

    def submit(self, slot, continue_stream):
        o = self.ovl[slot]
        kernel32.ResetEvent(C.c_void_p(o.hEvent))
        ok = winusb.WinUsb_ReadIsochPipeAsap(self.handle, slot * self.xfer_bytes, self.xfer_bytes,
                                             continue_stream, self.packets, self.desc[slot],
                                             C.byref(o))
        err = C.get_last_error()
        if not ok and err != 997:  # ERROR_IO_PENDING
            raise OSError(f"ReadIsochPipeAsap(slot={slot}) failed: {err}")

    def start(self):
        for i in range(self.depth):
            self.submit(i, self._started)
            self._started = True

    def collect(self, slot, timeout_ms=1000):
        o = self.ovl[slot]
        if kernel32.WaitForSingleObject(C.c_void_p(o.hEvent), timeout_ms) != 0:
            return None
        got = W.DWORD()
        winusb.WinUsb_GetOverlappedResult(self.dev.handle, C.byref(o), C.byref(got), False)
        base = slot * self.xfer_bytes
        ok = sum(1 for d in self.desc[slot] if d.Status == 0)
        bad = self.packets - ok
        data = self.buf.raw[base:base + self.xfer_bytes]
        lengths = [(d.Offset, d.Length, d.Status) for d in self.desc[slot]]
        return got.value, ok, bad, data, lengths

    def close(self):
        if self.handle:
            winusb.WinUsb_UnregisterIsochBuffer(self.handle); self.handle = None
        for o in self.ovl:
            if o.hEvent: kernel32.CloseHandle(C.c_void_p(o.hEvent))
