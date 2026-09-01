"""Per-model camera quirks that can't be read from the device itself and
don't vary between installs -- so the program just knows them, rather than
a technician having to discover and configure them.

Today this is only image orientation. The Keeler Vantage Plus Digital BIO
delivers a vertically-flipped image (the instrument's optical path mirrors
it, and it's the same on every unit -- the older net2860 BIO camera needs
the identical flip, see net2860_helper.py). That's a property of the
product, not a per-clinic variation, so it belongs here and not in
config.json. A config.json `orientation` value still overrides this (see
config.py / app.py) as an escape hatch for a non-standard mounting. See
DECISIONS.md's "Device-model rotation presets" entry and its
orientation-correction follow-up.

Lightweight on purpose -- only imports camera.py (stdlib + numpy) for the
orientation constants, no Qt / IDS SDK, so it stays importable anywhere
ids_camera.py is.
"""

from __future__ import annotations

from camera import ORIENTATION_FLIP_VERTICAL, ORIENTATION_NONE

# Matched case-insensitively as a substring of ids_peak's
# descriptor.ModelName(). Confirmed strings on real hardware (2026-09-01):
# BIO camera reports "U3-327xCP-C", slit lamp reports "UI325xCP-C" -- the
# "U3-327" token hits the former and not the latter. Values are members of
# camera.VALID_ORIENTATIONS.
_ORIENTATION_BY_MODEL_TOKEN: dict[str, str] = {
    "U3-327": ORIENTATION_FLIP_VERTICAL,  # Keeler Vantage Plus Digital BIO -- optics deliver a vertically-flipped image
}


def orientation_for_model(model_name: str | None) -> str:
    """The orientation fix (a `camera.VALID_ORIENTATIONS` member) for a
    camera reporting `model_name`, or ORIENTATION_NONE if no preset
    matches."""
    normalized = (model_name or "").upper()
    for token, orientation in _ORIENTATION_BY_MODEL_TOKEN.items():
        if token.upper() in normalized:
            return orientation
    return ORIENTATION_NONE
