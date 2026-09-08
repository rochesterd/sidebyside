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


# Pixel clock, for camera families where it is settable and where the
# power-on default is not what this app needs. The legacy uEye slit lamp
# camera comes up at 24MHz of a 10-128MHz range *on every open* (it does
# not persist), and at 24MHz a 1600x1200 frame takes ~87ms -- which is
# simultaneously an 11.5fps ceiling and the reason its ExposureTime
# maximum reads 87208us. Neither is a sensor limit; both are this one
# unset value. 80MHz is the lowest clock that reliably delivers the full
# 30fps recording target (60MHz tops out at 28.6fps, so the frame-rate cap
# can never reach 30), measured with zero device-side drops over 150s with
# the third-person camera streaming too. Raising it shortens the maximum
# exposure in proportion, so this is a trade of available light for frame
# rate, not a free win. See DECISIONS.md.
#
# The BIO's USB3 Vision camera reports a fixed, unwritable 197MHz -- there
# is nothing to set, which is why it has no entry here.
_PIXEL_CLOCK_HZ_BY_MODEL_TOKEN: dict[str, int] = {
    "UI325": 80_000_000,  # Haag-Streit BI 900 slit lamp, legacy uEye
}


def pixel_clock_hz_for_model(model_name: str | None) -> int | None:
    """Pixel clock this model should run at, or None to leave it alone."""
    normalized = (model_name or "").upper()
    for token, clock_hz in _PIXEL_CLOCK_HZ_BY_MODEL_TOKEN.items():
        if token.upper() in normalized:
            return clock_hz
    return None


def orientation_for_model(model_name: str | None) -> str:
    """The orientation fix (a `camera.VALID_ORIENTATIONS` member) for a
    camera reporting `model_name`, or ORIENTATION_NONE if no preset
    matches."""
    normalized = (model_name or "").upper()
    for token, orientation in _ORIENTATION_BY_MODEL_TOKEN.items():
        if token.upper() in normalized:
            return orientation
    return ORIENTATION_NONE
