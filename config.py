"""Loads config.json: which physical camera fills each role (slit lamp,
BIO, third-person). See ROADMAP.md's "Device compatibility & camera setup
system" entry and DECISIONS.md for why this exists and what it deviates
from the eventual (Phase 3+) schema.

Deliberately has no import of camera.py/ids_camera.py/uvc_camera.py -- it
only parses JSON into dataclasses, so it stays usable on a dev machine with
no IDS SDK installed, the same assumption app.py's lazy IdsCamera import
already makes.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_VID_PID_RE = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}$")

_FIX_HINT = "Copy config.example.json to config.json and edit it for this machine."


def is_frozen() -> bool:
    """True under a PyInstaller-frozen app.exe/settings.exe, false for a
    normal `python app.py` dev/test run. `sys.frozen` is set by
    PyInstaller's bootloader before any of the app's own code runs, so
    this is reliable to check at any point, including at import time.
    """
    return bool(getattr(sys, "frozen", False))


def resolve_default_config_path() -> Path:
    """A frozen install has no repo checkout to be relative to, so
    config.json lives in %ProgramData% instead -- see ROADMAP.md's
    "Distribute a frozen-exe installer" entry. Dev/test behavior
    (relative to CWD) is unchanged.
    """
    if is_frozen():
        return Path(os.environ["ProgramData"]) / "sidebyside" / "config.json"
    return Path("config.json")


def resolve_default_sessions_dir() -> Path:
    """Public Documents, not ProgramData: unlike config.json, a session
    recording is the actual deliverable a technician goes and retrieves
    for a student (see CLAUDE.md), so it needs to be somewhere visible in
    Explorer by default, not a hidden system folder. Only used as the
    pre-filled default in settings.py's Browse field and as app.py's
    fallback when config.json doesn't set `sessions_dir` explicitly --
    once a technician saves a choice, that explicit value always wins.
    """
    if is_frozen():
        return Path(os.environ["PUBLIC"]) / "Documents" / "sidebyside" / "sessions"
    return Path("sessions")


DEFAULT_CONFIG_PATH = resolve_default_config_path()


class ConfigError(RuntimeError):
    """config.json is missing, malformed, or missing/wrong-typed required keys."""


@dataclass
class InstrumentConfig:
    kind: str
    serial: str
    label: str


@dataclass
class ThirdPersonConfig:
    kind: str
    vid_pid: str  # "XXXX:YYYY", uppercase hex -- see uvc_enumeration.py
    friendly_name: str


DEFAULT_RECORDING_FPS = 30


@dataclass
class RecordingConfig:
    fps: int


@dataclass
class AppConfig:
    instruments: dict[str, InstrumentConfig]
    third_person: ThirdPersonConfig
    recording: RecordingConfig
    # None means "caller decides the default" (resolve_default_sessions_dir()),
    # not an error -- same optional-with-fallback shape as `recording`,
    # and for the same reason: every config.json written before this field
    # existed is still valid.
    sessions_dir: Path | None = None


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found. {_FIX_HINT}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}. {_FIX_HINT}") from exc

    instruments_raw = raw.get("instruments")
    if not isinstance(instruments_raw, dict) or not instruments_raw:
        raise ConfigError(f"{path}: 'instruments' must be a non-empty object. {_FIX_HINT}")

    instruments: dict[str, InstrumentConfig] = {}
    for key, entry in instruments_raw.items():
        instruments[key] = _parse_instrument(path, key, entry)

    third_person_raw = raw.get("third_person")
    if not isinstance(third_person_raw, dict):
        raise ConfigError(f"{path}: 'third_person' must be an object. {_FIX_HINT}")
    third_person = _parse_third_person(path, third_person_raw)

    recording = _parse_recording(path, raw.get("recording"))
    sessions_dir = _parse_sessions_dir(path, raw.get("sessions_dir"))

    return AppConfig(
        instruments=instruments, third_person=third_person, recording=recording, sessions_dir=sessions_dir
    )


def _parse_instrument(path: Path, key: str, entry: object) -> InstrumentConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{path}: instruments.{key} must be an object. {_FIX_HINT}")

    kind = entry.get("kind")
    if kind != "ids":
        raise ConfigError(f"{path}: instruments.{key}.kind must be \"ids\", got {kind!r}. {_FIX_HINT}")

    serial = entry.get("serial")
    if not isinstance(serial, str) or not serial:
        raise ConfigError(f"{path}: instruments.{key}.serial must be a non-empty string. {_FIX_HINT}")

    label = entry.get("label")
    if not isinstance(label, str) or not label:
        raise ConfigError(f"{path}: instruments.{key}.label must be a non-empty string. {_FIX_HINT}")

    return InstrumentConfig(kind=kind, serial=serial, label=label)


def _parse_third_person(path: Path, entry: dict) -> ThirdPersonConfig:
    kind = entry.get("kind")
    if kind != "uvc":
        raise ConfigError(f"{path}: third_person.kind must be \"uvc\", got {kind!r}. {_FIX_HINT}")

    vid_pid = entry.get("vid_pid")
    if not isinstance(vid_pid, str) or not _VID_PID_RE.match(vid_pid):
        raise ConfigError(
            f"{path}: third_person.vid_pid must look like \"XXXX:YYYY\" (hex). {_FIX_HINT}"
        )
    # Normalized, not just validated: uvc_enumeration.py always uppercases,
    # and resolve_device() matches by direct string equality -- a
    # hand-typed lowercase value here would otherwise silently never match
    # an attached device.
    vid_pid = vid_pid.upper()

    friendly_name = entry.get("friendly_name")
    if not isinstance(friendly_name, str) or not friendly_name:
        raise ConfigError(f"{path}: third_person.friendly_name must be a non-empty string. {_FIX_HINT}")

    return ThirdPersonConfig(kind=kind, vid_pid=vid_pid, friendly_name=friendly_name)


def _parse_recording(path: Path, entry: object) -> RecordingConfig:
    # Optional section, unlike instruments/third_person: a missing
    # `recording` key means "use the measured default," not a broken
    # config -- see DECISIONS.md's "config-driven recording fps" entry for
    # why this is a measured value (a technician tunes it against real
    # observed throughput) rather than something read off the camera.
    if entry is None:
        return RecordingConfig(fps=DEFAULT_RECORDING_FPS)
    if not isinstance(entry, dict):
        raise ConfigError(f"{path}: 'recording' must be an object. {_FIX_HINT}")

    fps = entry.get("fps", DEFAULT_RECORDING_FPS)
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise ConfigError(f"{path}: recording.fps must be a positive number. {_FIX_HINT}")

    return RecordingConfig(fps=fps)


def _parse_sessions_dir(path: Path, entry: object) -> Path | None:
    # Optional, like `recording` above: absent means "caller decides the
    # default" via resolve_default_sessions_dir(), not a broken config.
    if entry is None:
        return None
    if not isinstance(entry, str) or not entry:
        raise ConfigError(f"{path}: 'sessions_dir' must be a non-empty string. {_FIX_HINT}")
    return Path(entry)
