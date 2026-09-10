"""WinUSB access via ctypes -- the low-level layer under
net2860_winusb_camera.py.

Talks only to inbox Windows DLLs (winusb.dll, setupapi.dll), so nothing
here needs a vendor SDK, a third-party runtime, or anything redistributed.
The device must already be bound to Microsoft's winusb.sys by an INF that
registers a DeviceInterfaceGUID -- see packaging/ for the one this project
ships.

Deliberately camera-agnostic: this module knows about USB devices, pipes
and transfers, not about the EM2860 or about frames. That keeps the same
boundary CLAUDE.md draws around the IDS SDK -- net2860_winusb_camera.py is
the only module that imports it.

Isochronous support here is the WinUSB isoch API (Windows 8.1+):
WinUsb_RegisterIsochBuffer once, then WinUsb_ReadIsochPipeAsap per
transfer, several kept in flight so the device never runs out of queued
buffers. Isochronous has no retry and no flow control -- a window that
isn't serviced in time is data lost, not data delayed.
"""

from __future__ import annotations

import ctypes as C
import logging
import winreg
from ctypes import wintypes as W

logger = logging.getLogger(__name__)

setupapi = C.WinDLL("setupapi", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
_winusb = C.WinDLL("winusb", use_last_error=True)

INVALID_HANDLE = C.c_void_p(-1).value
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_RW, OPEN_EXISTING = 0x03, 3
FILE_FLAG_OVERLAPPED, FILE_ATTRIBUTE_NORMAL = 0x40000000, 0x80
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0

PIPE_TYPE = {0: "Control", 1: "Isochronous", 2: "Bulk", 3: "Interrupt"}
ULONG_PTR = C.c_ulonglong


class WinUsbError(RuntimeError):
    """A WinUSB call failed, or no matching bound device was found."""


class GUID(C.Structure):
    _fields_ = [("d1", C.c_ulong), ("d2", C.c_ushort), ("d3", C.c_ushort), ("d4", C.c_ubyte * 8)]

    @classmethod
    def from_string(cls, s: str) -> "GUID":
        s = s.strip("{}")
        a, b, c, d, e = s.split("-")
        tail = bytes.fromhex(d) + bytes.fromhex(e)
        return cls(int(a, 16), int(b, 16), int(c, 16), (C.c_ubyte * 8)(*tail))


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", W.DWORD), ("Reserved", C.POINTER(C.c_ulonglong))]


class SP_DEVINFO_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("ClassGuid", GUID),
                ("DevInst", W.DWORD), ("Reserved", C.POINTER(C.c_ulonglong))]


class WINUSB_SETUP_PACKET(C.Structure):
    _pack_ = 1
    _fields_ = [("RequestType", C.c_ubyte), ("Request", C.c_ubyte),
                ("Value", C.c_ushort), ("Index", C.c_ushort), ("Length", C.c_ushort)]


class WINUSB_PIPE_INFORMATION_EX(C.Structure):
    _fields_ = [("PipeType", C.c_int), ("PipeId", C.c_ubyte),
                ("MaximumPacketSize", C.c_ushort), ("Interval", C.c_ubyte),
                ("MaximumBytesPerInterval", C.c_ulong)]


class USBD_ISO_PACKET_DESCRIPTOR(C.Structure):
    _fields_ = [("Offset", C.c_ulong), ("Length", C.c_ulong), ("Status", C.c_long)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", ULONG_PTR), ("InternalHigh", ULONG_PTR),
                ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", C.c_void_p)]


# ctypes defaults every return to c_int, which truncates the 64-bit handles
# these return -- and the resulting failure is a silent "not found" rather
# than an error, which is genuinely hard to diagnose. Declare them.
setupapi.SetupDiGetClassDevsW.restype = C.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p,
                                                      W.DWORD, C.c_void_p, C.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [C.c_void_p, C.c_void_p, C.c_wchar_p,
                                                 W.DWORD, C.c_void_p]
kernel32.CreateFileW.restype = C.c_void_p
kernel32.CreateEventW.restype = C.c_void_p
kernel32.CloseHandle.argtypes = [C.c_void_p]
kernel32.WaitForSingleObject.argtypes = [C.c_void_p, W.DWORD]
kernel32.ResetEvent.argtypes = [C.c_void_p]
_winusb.WinUsb_Initialize.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
_winusb.WinUsb_Free.argtypes = [C.c_void_p]
_winusb.WinUsb_ControlTransfer.argtypes = [C.c_void_p, WINUSB_SETUP_PACKET, C.c_void_p,
                                           W.ULONG, C.POINTER(W.ULONG), C.c_void_p]
_winusb.WinUsb_SetCurrentAlternateSetting.argtypes = [C.c_void_p, C.c_ubyte]
_winusb.WinUsb_GetCurrentAlternateSetting.argtypes = [C.c_void_p, C.POINTER(C.c_ubyte)]
_winusb.WinUsb_QueryPipeEx.argtypes = [C.c_void_p, C.c_ubyte, C.c_ubyte,
                                       C.POINTER(WINUSB_PIPE_INFORMATION_EX)]
_winusb.WinUsb_RegisterIsochBuffer.argtypes = [C.c_void_p, C.c_ubyte, C.c_void_p,
                                               W.ULONG, C.POINTER(C.c_void_p)]
_winusb.WinUsb_UnregisterIsochBuffer.argtypes = [C.c_void_p]
_winusb.WinUsb_ReadIsochPipeAsap.argtypes = [C.c_void_p, W.ULONG, W.ULONG, W.BOOL, W.ULONG,
                                             C.POINTER(USBD_ISO_PACKET_DESCRIPTOR), C.c_void_p]
_winusb.WinUsb_GetOverlappedResult.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(W.DWORD), W.BOOL]
_winusb.WinUsb_ResetPipe.argtypes = [C.c_void_p, C.c_ubyte]
_winusb.WinUsb_AbortPipe.argtypes = [C.c_void_p, C.c_ubyte]


def _interface_guids(instance_id: str) -> list[str]:
    key = "SYSTEM\\CurrentControlSet\\Enum\\" + instance_id + "\\Device Parameters"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            for name in ("DeviceInterfaceGUIDs", "DeviceInterfaceGUID"):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    return [v] if isinstance(v, str) else list(v)
                except FileNotFoundError:
                    continue
    except OSError:
        pass
    return []


def _service(instance_id: str) -> str:
    key = "SYSTEM\\CurrentControlSet\\Enum\\" + instance_id
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return winreg.QueryValueEx(k, "Service")[0]
    except OSError:
        return ""


def _interfaces_for_guid(guid_str: str) -> list[tuple[str, str]]:
    """Every present device interface for a GUID, as (instance_id, path).

    Enumerates *all* of them and reads each one's owning device instance
    from the enumeration rather than assuming one interface per GUID. Our
    INF declares a single fixed DeviceInterfaceGUID, so every unit of this
    camera model shares it -- looking only at index 0 would return the same
    device once per instance that happened to be registered, which is
    exactly the duplicate that made find_by_vid_pid() report "2 devices"
    with one camera attached.
    """
    guid = GUID.from_string(guid_str)
    h = setupapi.SetupDiGetClassDevsW(C.byref(guid), None, None,
                                      DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not h or h == INVALID_HANDLE:
        return []
    out: list[tuple[str, str]] = []
    try:
        index = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = C.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(h, None, C.byref(guid), index, C.byref(did)):
                break
            index += 1
            need = W.DWORD()
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = C.sizeof(devinfo)
            setupapi.SetupDiGetDeviceInterfaceDetailW(h, C.byref(did), None, 0, C.byref(need), None)
            buf = C.create_string_buffer(need.value)
            # cbSize of the fixed part of SP_DEVICE_INTERFACE_DETAIL_DATA_W,
            # which is 8 on 64-bit (DWORD + WCHAR[ANYSIZE], 8-byte aligned).
            C.cast(buf, C.POINTER(W.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    h, C.byref(did), C.cast(buf, C.c_void_p), need, C.byref(need),
                    C.byref(devinfo)):
                continue
            path = C.wstring_at(C.addressof(buf) + 4)
            idbuf = C.create_unicode_buffer(512)
            if not setupapi.SetupDiGetDeviceInstanceIdW(h, C.byref(devinfo), idbuf, 512, None):
                continue
            out.append((idbuf.value, path))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h)
    return out


def find_by_vid_pid(vid: int, pid: int) -> list[tuple[str, str]]:
    """Every present device matching vid/pid that is bound to WinUSB.

    Returns (instance_id, device_path) pairs. Identifying by VID/PID rather
    than by a USB port path follows the same rule CLAUDE.md applies to the
    IDS cameras' serial numbers: a port path changes across reboots and
    re-plugs, and is the classic way a setup like this breaks silently.
    """
    enum_key = "SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_%04X&PID_%04X" % (vid, pid)
    prefix = "USB\\VID_%04X&PID_%04X\\" % (vid, pid)

    # Registry pass: which instances of this VID/PID are bound to WinUSB, and
    # which interface GUID(s) their INF registered. This says nothing about
    # whether the device is actually plugged in -- stale instances from other
    # USB ports persist here indefinitely.
    guids: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_key) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                instance = prefix + sub
                if _service(instance).lower() == "winusb":
                    guids.update(_interface_guids(instance))
    except OSError:
        pass

    # Interface pass: which of them are present *now*. This is what decides
    # the answer -- SetupAPI enumerates live interfaces, so a stale registry
    # instance contributes nothing here.
    found: dict[str, tuple[str, str]] = {}
    for guid in guids:
        for instance, path in _interfaces_for_guid(guid):
            if instance.upper().startswith(prefix.upper()):
                found.setdefault(path, (instance, path))
    return sorted(found.values())


class WinUsbDevice:
    """One open handle on a WinUSB-bound device."""

    def __init__(self, path: str):
        self.path = path
        self._file = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW,
                                          None, OPEN_EXISTING,
                                          FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None)
        if self._file in (None, INVALID_HANDLE):
            raise WinUsbError("CreateFile(%s) failed: %d" % (path, C.get_last_error()))
        self._h = C.c_void_p()
        if not _winusb.WinUsb_Initialize(self._file, C.byref(self._h)):
            err = C.get_last_error()
            kernel32.CloseHandle(C.c_void_p(self._file))
            self._file = None
            raise WinUsbError("WinUsb_Initialize failed: %d" % err)

    @classmethod
    def open_one(cls, vid: int, pid: int) -> "WinUsbDevice":
        matches = find_by_vid_pid(vid, pid)
        if not matches:
            raise WinUsbError(
                "no WinUSB-bound device matching VID_%04X&PID_%04X -- is the driver "
                "installed and the device plugged in?" % (vid, pid))
        if len(matches) > 1:
            logger.warning("%d devices match VID_%04X&PID_%04X; using the first",
                           len(matches), vid, pid)
        return cls(matches[0][1])

    @property
    def handle(self) -> C.c_void_p:
        return self._h

    def control(self, bmRequestType: int, bRequest: int, wValue: int, wIndex: int,
                data: bytes | None = None, length: int = 0) -> bytes:
        if data is not None:
            buf = C.create_string_buffer(bytes(data), len(data))
            length = len(data)
        else:
            buf = C.create_string_buffer(length) if length else None
        pkt = WINUSB_SETUP_PACKET(bmRequestType, bRequest, wValue, wIndex, length)
        got = W.ULONG()
        ok = _winusb.WinUsb_ControlTransfer(self._h, pkt,
                                            C.cast(buf, C.c_void_p) if buf else None,
                                            length, C.byref(got), None)
        if not ok:
            raise WinUsbError(
                "control transfer failed (err %d): bmRT=0x%02x bReq=0x%02x wIndex=0x%04x"
                % (C.get_last_error(), bmRequestType, bRequest, wIndex))
        return bytes(buf.raw[:got.value]) if buf else b""

    def pipes(self, alt: int) -> list[WINUSB_PIPE_INFORMATION_EX]:
        out = []
        for idx in range(16):
            info = WINUSB_PIPE_INFORMATION_EX()
            if not _winusb.WinUsb_QueryPipeEx(self._h, alt, idx, C.byref(info)):
                break
            out.append(info)
        return out

    def set_alt(self, alt: int) -> None:
        if not _winusb.WinUsb_SetCurrentAlternateSetting(self._h, alt):
            raise WinUsbError("SetCurrentAlternateSetting(%d) failed: %d"
                              % (alt, C.get_last_error()))

    def get_alt(self) -> int:
        a = C.c_ubyte()
        _winusb.WinUsb_GetCurrentAlternateSetting(self._h, C.byref(a))
        return a.value

    def abort_pipe(self, pipe_id: int) -> None:
        _winusb.WinUsb_AbortPipe(self._h, pipe_id)

    def close(self) -> None:
        if getattr(self, "_h", None):
            _winusb.WinUsb_Free(self._h)
            self._h = None
        if getattr(self, "_file", None):
            kernel32.CloseHandle(C.c_void_p(self._file))
            self._file = None

    def __enter__(self) -> "WinUsbDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class IsochReader:
    """Keeps `depth` isochronous transfers in flight against one buffer.

    One registered buffer is carved into `depth` slots; each in-flight
    transfer reads into its own slot, so a completed slot can be parsed
    while the others are still being filled by the host controller.
    """

    def __init__(self, dev: WinUsbDevice, pipe_id: int, alt: int,
                 packets_per_xfer: int = 64, depth: int = 8):
        self._dev, self._pipe_id, self._depth = dev, pipe_id, depth
        info = next((p for p in dev.pipes(alt) if p.PipeId == pipe_id), None)
        if info is None:
            raise WinUsbError("pipe 0x%02x not present in alt setting %d" % (pipe_id, alt))
        if info.MaximumBytesPerInterval == 0:
            raise WinUsbError(
                "pipe 0x%02x has zero bandwidth in alt %d -- pick an alt setting that "
                "actually allocates isochronous bandwidth" % (pipe_id, alt))
        self.bytes_per_interval = info.MaximumBytesPerInterval
        self.packets = packets_per_xfer
        self.xfer_bytes = self.bytes_per_interval * self.packets
        self._buf = C.create_string_buffer(self.xfer_bytes * depth)
        self._handle = C.c_void_p()
        if not _winusb.WinUsb_RegisterIsochBuffer(dev.handle, pipe_id,
                                                  C.cast(self._buf, C.c_void_p),
                                                  len(self._buf), C.byref(self._handle)):
            raise WinUsbError("RegisterIsochBuffer failed: %d" % C.get_last_error())
        self._ovl = [OVERLAPPED() for _ in range(depth)]
        self._desc = [(USBD_ISO_PACKET_DESCRIPTOR * self.packets)() for _ in range(depth)]
        for o in self._ovl:
            o.hEvent = kernel32.CreateEventW(None, True, False, None)
        self._next = 0
        self._streaming = False

    def start(self) -> None:
        for i in range(self._depth):
            self._submit(i, self._streaming)
            self._streaming = True
        self._next = 0

    def _submit(self, slot: int, continue_stream: bool) -> None:
        o = self._ovl[slot]
        kernel32.ResetEvent(C.c_void_p(o.hEvent))
        ok = _winusb.WinUsb_ReadIsochPipeAsap(self._handle, slot * self.xfer_bytes,
                                              self.xfer_bytes, continue_stream,
                                              self.packets, self._desc[slot], C.byref(o))
        if not ok and C.get_last_error() != ERROR_IO_PENDING:
            raise WinUsbError("ReadIsochPipeAsap(slot=%d) failed: %d"
                              % (slot, C.get_last_error()))

    def next_packets(self, timeout_ms: int = 1000) -> list[bytes] | None:
        """Wait for the oldest in-flight transfer, resubmit it, and return
        its packets. None on timeout -- the caller decides whether that is a
        recoverable hiccup or a dead device."""
        slot = self._next
        o = self._ovl[slot]
        if kernel32.WaitForSingleObject(C.c_void_p(o.hEvent), timeout_ms) != WAIT_OBJECT_0:
            return None
        got = W.DWORD()
        _winusb.WinUsb_GetOverlappedResult(self._dev.handle, C.byref(o), C.byref(got), False)
        base = slot * self.xfer_bytes
        raw = self._buf.raw
        packets = [raw[base + d.Offset: base + d.Offset + d.Length]
                   for d in self._desc[slot] if d.Status == 0 and d.Length]
        self._submit(slot, True)
        self._next = (slot + 1) % self._depth
        return packets

    def close(self) -> None:
        if getattr(self, "_handle", None):
            _winusb.WinUsb_UnregisterIsochBuffer(self._handle)
            self._handle = None
        for o in getattr(self, "_ovl", []):
            if o.hEvent:
                kernel32.CloseHandle(C.c_void_p(o.hEvent))
                o.hEvent = None
