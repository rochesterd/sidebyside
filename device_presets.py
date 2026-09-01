"""Per-model camera quirks that can't be read from the device itself and
don't vary between installs -- so the program just knows them, rather than
a technician having to discover and configure them.

Today this is only physical mounting orientation. The Keeler Vantage Plus
Digital ships its IDS U3-327x camera mounted inverted inside the BIO
headset: that's true of every unit of that product, not a per-clinic
variation, so it belongs here and not in config.json. A config.json
`rotation` value still overrides this (see config.py / app.py) as an escape
hatch for a non-standard mounting. See DECISIONS.md's "Device-model
rotation presets" entry.

Pure stdlib on purpose -- imported by ids_camera.py, which must stay
importable reasoning-wise the same way config.py does.
"""

from __future__ import annotations

# Matched case-insensitively as a substring of ids_peak's
# descriptor.ModelName(). Confirmed strings on real hardware (2026-09-01):
# BIO camera reports "U3-327xCP-C", slit lamp reports "UI325xCP-C" -- the
# "U3-327" token hits the former and not the latter. Only 0 and 180 are
# supported (dimension-preserving -- see camera.py's rotation handling).
_ROTATION_BY_MODEL_TOKEN: dict[str, int] = {
    "U3-327": 180,  # Keeler Vantage Plus Digital BIO -- camera mounts upside down
}


def rotation_for_model(model_name: str | None) -> int:
    """Default rotation in degrees (0 or 180) for a camera reporting
    `model_name`, or 0 if no preset matches."""
    normalized = (model_name or "").upper()
    for token, rotation in _ROTATION_BY_MODEL_TOKEN.items():
        if token.upper() in normalized:
            return rotation
    return 0
