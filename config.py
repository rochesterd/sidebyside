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
    # None only for kind="net2860" -- that camera has no serial (there's
    # exactly one of it, no identification scheme; see DECISIONS.md's
    # "Net2860Camera" entry). Required (non-None) for kind="ids".
    serial: str | None
    label: str
    # Set only for a camera with no ExposureAuto/GainAuto (the slit lamp) --
    # see ids_camera.py's needs_manual_calibration() and ROADMAP.md's
    # "In-app exposure/gain calibration" entry. None means "let
    # _converge_auto_nodes() handle this axis," the same as before these
    # fields existed -- every config.json written before this is still valid.
    exposure_time_us: float | None = None
    gain: float | None = None
    # Set only for a camera with no BalanceWhiteAuto -- see ids_camera.py's
    # needs_manual_white_balance() and ROADMAP.md's 2026-08-26 entry. Unlike
    # exposure_time_us/gain (independent axes), these two are validated as a
    # pair: BalanceWhiteAuto=Once converges both together, so there's no
    # "auto blue, manual red" -- _parse_instrument() rejects exactly one
    # being present.
    red_balance_ratio: float | None = None
    blue_balance_ratio: float | None = None
    # Optional escape hatch overriding device_presets.py's per-model default
    # (e.g. the Keeler BIO camera delivers a vertically-flipped image). One
    # of camera.VALID_ORIENTATIONS ("none"/"rotate_180"/"flip_horizontal"/
    # "flip_vertical"); None means "use the model preset." There's no
    # settings.py UI for this yet; it's hand-set for a non-standard
    # mounting. See DECISIONS.md's "Device-model rotation presets" entry
    # and its orientation follow-up.
    orientation: str | None = None


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
class RetentionConfig:
    """Opt-in cleanup of old recording sessions -- see retention.py and
    DECISIONS.md's "Automatic cleanup of old recordings" entry. Absent from
    config.json entirely means disabled.

    `max_age_days` drives the unconditional age sweep. `min_free_gb` /
    `protect_days` (set together or not at all) add the low-disk capacity
    pass: when free space is below `min_free_gb`, delete the oldest sessions
    -- but never one younger than `protect_days` -- until it recovers.
    """
    max_age_days: int
    min_free_gb: float | None = None
    protect_days: int | None = None


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
    # None means "no automatic cleanup" -- see RetentionConfig.
    retention: RetentionConfig | None = None


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
    retention = _parse_retention(path, raw.get("retention"))

    return AppConfig(
        instruments=instruments,
        third_person=third_person,
        recording=recording,
        sessions_dir=sessions_dir,
        retention=retention,
    )


def _parse_instrument(path: Path, key: str, entry: object) -> InstrumentConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{path}: instruments.{key} must be an object. {_FIX_HINT}")

    kind = entry.get("kind")
    if kind not in ("ids", "net2860"):
        raise ConfigError(
            f"{path}: instruments.{key}.kind must be \"ids\" or \"net2860\", got {kind!r}. {_FIX_HINT}"
        )

    label = entry.get("label")
    if not isinstance(label, str) or not label:
        raise ConfigError(f"{path}: instruments.{key}.label must be a non-empty string. {_FIX_HINT}")

    if kind == "net2860":
        # No serial (there's exactly one of this camera, no identification
        # scheme -- see DECISIONS.md's "Net2860Camera" entry) and no
        # exposure/gain/white-balance calibration (not implemented for this
        # camera). Rejected loudly rather than silently ignored, so
        # copy-pasting an "ids" entry and only changing "kind" fails fast
        # instead of producing a config.json that looks configured but
        # isn't.
        unexpected = {
            "serial", "exposure_time_us", "gain", "red_balance_ratio", "blue_balance_ratio", "orientation"
        } & entry.keys()
        if unexpected:
            raise ConfigError(
                f"{path}: instruments.{key} is kind \"net2860\", which doesn't take "
                f"{', '.join(sorted(unexpected))}. {_FIX_HINT}"
            )
        return InstrumentConfig(kind=kind, serial=None, label=label)

    serial = entry.get("serial")
    if not isinstance(serial, str) or not serial:
        raise ConfigError(f"{path}: instruments.{key}.serial must be a non-empty string. {_FIX_HINT}")

    exposure_time_us = _parse_optional_positive_number(path, f"instruments.{key}.exposure_time_us", entry.get("exposure_time_us"))
    gain = _parse_optional_positive_number(path, f"instruments.{key}.gain", entry.get("gain"))

    red_balance_ratio = _parse_optional_positive_number(
        path, f"instruments.{key}.red_balance_ratio", entry.get("red_balance_ratio")
    )
    blue_balance_ratio = _parse_optional_positive_number(
        path, f"instruments.{key}.blue_balance_ratio", entry.get("blue_balance_ratio")
    )
    if (red_balance_ratio is None) != (blue_balance_ratio is None):
        raise ConfigError(
            f"{path}: instruments.{key} must set both red_balance_ratio and blue_balance_ratio, "
            f"or neither -- BalanceWhiteAuto converges them together, there's no partial manual "
            f"white balance. {_FIX_HINT}"
        )

    orientation = _parse_optional_orientation(path, f"instruments.{key}.orientation", entry.get("orientation"))

    return InstrumentConfig(
        kind=kind,
        serial=serial,
        label=label,
        exposure_time_us=exposure_time_us,
        gain=gain,
        red_balance_ratio=red_balance_ratio,
        blue_balance_ratio=blue_balance_ratio,
        orientation=orientation,
    )


def _parse_optional_positive_number(path: Path, field_name: str, value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path}: {field_name} must be a positive number. {_FIX_HINT}")
    return float(value)


_VALID_ORIENTATIONS = ("none", "rotate_180", "flip_horizontal", "flip_vertical")


def _parse_optional_orientation(path: Path, field_name: str, value: object) -> str | None:
    # Mirrors camera.VALID_ORIENTATIONS (kept as a literal here so config.py
    # stays import-light -- it deliberately doesn't import camera.py). All
    # four are dimension-preserving; 90/270 rotation is excluded because it
    # would desync frames from the camera's reported .resolution.
    if value is None:
        return None
    if value not in _VALID_ORIENTATIONS:
        raise ConfigError(
            f"{path}: {field_name} must be one of {', '.join(_VALID_ORIENTATIONS)}. {_FIX_HINT}"
        )
    return value


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


def _positive_int(path: Path, field_name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path}: {field_name} must be a positive integer. {_FIX_HINT}")
    return value


def _parse_retention(path: Path, entry: object) -> "RetentionConfig | None":
    # Optional and opt-in: absent means no automatic cleanup at all (the
    # default). Present means a technician has deliberately set a retention
    # policy -- see retention.py and DECISIONS.md's "Automatic cleanup of
    # old recordings" entry.
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ConfigError(f"{path}: 'retention' must be an object. {_FIX_HINT}")

    max_age_days = _positive_int(path, "retention.max_age_days", entry.get("max_age_days"))

    min_free_gb = entry.get("min_free_gb")
    protect_days = entry.get("protect_days")
    if (min_free_gb is None) != (protect_days is None):
        raise ConfigError(
            f"{path}: retention must set both min_free_gb and protect_days, or neither -- "
            f"protect_days bounds how far back the low-disk pass is allowed to delete. {_FIX_HINT}"
        )
    if min_free_gb is not None:
        if not isinstance(min_free_gb, (int, float)) or isinstance(min_free_gb, bool) or min_free_gb <= 0:
            raise ConfigError(f"{path}: retention.min_free_gb must be a positive number. {_FIX_HINT}")
        protect_days = _positive_int(path, "retention.protect_days", protect_days)
        if protect_days > max_age_days:
            raise ConfigError(
                f"{path}: retention.protect_days ({protect_days}) must not exceed "
                f"max_age_days ({max_age_days}). {_FIX_HINT}"
            )
        min_free_gb = float(min_free_gb)

    return RetentionConfig(max_age_days=max_age_days, min_free_gb=min_free_gb, protect_days=protect_days)
