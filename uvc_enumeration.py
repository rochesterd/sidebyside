"""Enumerates UVC/DirectShow video-input devices with their VID/PID, in the
same index order cv2.VideoCapture(i, cv2.CAP_DSHOW) opens by -- both walk
the same underlying DirectShow device enumerator, so index N here is
guaranteed to be openable as index N there. Written for settings.py's
device picker (ROADMAP.md's "Device compatibility & camera setup system"
entry); see DECISIONS.md for why this beats shelling out to Get-PnpDevice.

Uses pygrabber's semi-private internals (dshow_core.ICreateDevEnum,
dshow_ids.DeviceCategories/clsids), not its public FilterGraph API --
FilterGraph.get_input_devices() only exposes FriendlyName, not the
DevicePath property (which carries VID/PID) this needs. If a pygrabber
upgrade ever makes enumeration silently stop returning vid_pid, treat that
the same as this project already treats vendor/ids_peak_api.txt: verify
against the installed package rather than assuming the old behavior still
holds.

Also home to resolve_device(), the single-device-fallback/ambiguity logic
uvc_camera.UvcCamera uses to turn a configured vid_pid into an index at
start() time -- see DECISIONS.md's "UVC third-person identity moves to
VID/PID" entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from comtypes import GUID, client
from comtypes.persist import IPropertyBag
from pygrabber.dshow_core import ICreateDevEnum
from pygrabber.dshow_ids import DeviceCategories, clsids

_VID_PID_RE = re.compile(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", re.IGNORECASE)


@dataclass
class UvcDeviceInfo:
    index: int
    name: str
    vid_pid: str | None  # "XXXX:YYYY", uppercase hex; None if the device exposes no DevicePath


class UvcDeviceResolutionError(RuntimeError):
    """No attached UVC device could be confidently resolved from config."""


def list_uvc_devices() -> list[UvcDeviceInfo]:
    """Currently-attached UVC/DirectShow video-input devices. An empty list
    is the expected, correct result on a machine with none attached --
    same "0 found is fine" precedent as ids_peak's DeviceManager.Devices(),
    see SETUP.md.
    """
    system_device_enum = client.CreateObject(clsids.CLSID_SystemDeviceEnum, interface=ICreateDevEnum)
    enumerator = system_device_enum.CreateClassEnumerator(GUID(DeviceCategories.VideoInputDevice), dwFlags=0)

    devices: list[UvcDeviceInfo] = []
    try:
        moniker, count = enumerator.Next(1)
    except ValueError:
        # comtypes raises rather than returning count=0 when the category
        # enumerator itself comes back null (no devices at all) -- pygrabber's
        # own SystemDeviceEnum.get_available_filters() handles it the same way.
        return devices

    index = 0
    while count > 0:
        property_bag = moniker.BindToStorage(0, 0, IPropertyBag._iid_).QueryInterface(IPropertyBag)
        name = property_bag.Read("FriendlyName", pErrorLog=None)
        try:
            device_path = property_bag.Read("DevicePath", pErrorLog=None)
        except Exception:
            device_path = None
        devices.append(UvcDeviceInfo(index=index, name=name, vid_pid=_parse_vid_pid(device_path)))
        index += 1
        moniker, count = enumerator.Next(1)

    return devices


def _parse_vid_pid(device_path: str | None) -> str | None:
    if device_path is None:
        return None
    match = _VID_PID_RE.search(device_path)
    if match is None:
        return None
    return f"{match.group(1).upper()}:{match.group(2).upper()}"


def resolve_device(vid_pid: str | None, devices: list[UvcDeviceInfo] | None = None) -> UvcDeviceInfo:
    """Resolves which attached UVC device to use for the third-person role,
    per ROADMAP.md's "Third-person (UVC) role" strategy:

    - Exactly one UVC device attached -> use it, regardless of `vid_pid`.
      Covers the common case (one third-person camera, nothing else UVC on
      the machine) with zero configuration friction, and self-heals if
      that single camera is swapped for a different model.
    - More than one attached -> require a `vid_pid` match; refuse to guess
      if none is configured, or if the configured one isn't among them.
    - None attached -> nothing to resolve.

    `devices` is injectable for testing; defaults to a fresh
    list_uvc_devices() call.
    """
    if devices is None:
        devices = list_uvc_devices()

    if not devices:
        raise UvcDeviceResolutionError("no UVC device attached")

    if len(devices) == 1:
        return devices[0]

    if vid_pid is None:
        raise UvcDeviceResolutionError(
            f"{len(devices)} UVC devices attached and none is configured -- open Settings and pick one"
        )

    matches = [d for d in devices if d.vid_pid == vid_pid]
    if not matches:
        raise UvcDeviceResolutionError(
            f"configured UVC device {vid_pid} not found among {len(devices)} attached -- check Settings"
        )
    return matches[0]
