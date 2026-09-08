"""Pure math behind IdsCamera.auto_calibrate()/auto_white_balance() --
median-brightness/per-channel-color measurement and the exposure/gain/
white-balance correction steps -- split out from ids_camera.py so it's
unit-testable without the IDS peak SDK (this dev machine has no ids_peak
installed; see CLAUDE.md's Environment section).

See ROADMAP.md's "In-app exposure/gain calibration" entry, specifically the
2026-08-25 "Calibration UX" addendum, for the exposure/gain design: a
one-shot software auto-exposure for cameras with no ExposureAuto/GainAuto
(the slit lamp camera). Raises ExposureTime before Gain when more
brightness is needed -- Gain amplifies sensor noise, ExposureTime doesn't,
and this footage gets reviewed by students studying their own technique.

See ROADMAP.md's 2026-08-26 entry for center_crop() (vignette/center-weighted
metering) and the white-balance functions (channel_medians/
is_white_balanced/next_balance_ratios), added for the same reason as the
exposure/gain algorithm above: no ExposureAuto/GainAuto/BalanceWhiteAuto on
the slit lamp means no device-side auto-convergence to fall back on for any
of these axes.
"""

from __future__ import annotations

import numpy as np

DEFAULT_TARGET_MEDIAN = 128.0
DEFAULT_TOLERANCE = 10.0
DEFAULT_MAX_ITERATIONS = 8

# Slit-lamp/BIO video coupled through an eyepiece/beam-splitter commonly
# shows a circular illuminated field surrounded by true black -- unconfirmed
# against real footage from either camera, but if true, a whole-frame median
# is skewed dark by that surround and auto_calibrate()/auto_white_balance()
# would over-correct to compensate. A fixed centered crop is also just
# ordinary center-weighted metering practice regardless of whether a vignette
# is actually present, so it's a safe default either way. 0.5 is a starting
# guess, not a measurement -- revisit once real footage is available.
DEFAULT_METERING_FRACTION = 0.5

# A sensor cannot expose for the whole frame interval -- readout and
# transfer need part of it -- so the usable budget is a fraction of the
# nominal period rather than all of it.
DEFAULT_EXPOSURE_BUDGET_FRACTION = 0.9

DEFAULT_WB_TOLERANCE = 5.0
DEFAULT_WB_MAX_ITERATIONS = 8


def center_crop(image: np.ndarray, fraction: float = DEFAULT_METERING_FRACTION) -> np.ndarray:
    """Crop to a centered region covering `fraction` of both width and
    height. `fraction=1.0` returns the image unchanged (shape-wise) -- an
    explicit "no crop" case, not a special-cased no-op.
    """
    if not 0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction!r}")
    height, width = image.shape[:2]
    crop_h = max(1, round(height * fraction))
    crop_w = max(1, round(width * fraction))
    top = (height - crop_h) // 2
    left = (width - crop_w) // 2
    return image[top : top + crop_h, left : left + crop_w]


def median_brightness(image: np.ndarray) -> float:
    """Median pixel value across all channels, 0-255 -- robust against a
    bright reflection or dark surround skewing a plain mean, unlike a mean.
    """
    return float(np.median(image))


def channel_medians(image: np.ndarray) -> tuple[float, float, float]:
    """Per-channel median of a BGR image -- (blue, green, red)."""
    b = float(np.median(image[:, :, 0]))
    g = float(np.median(image[:, :, 1]))
    r = float(np.median(image[:, :, 2]))
    return b, g, r


def is_white_balanced(
    b_median: float, g_median: float, r_median: float, tolerance: float = DEFAULT_WB_TOLERANCE
) -> bool:
    """True iff both red and blue read within `tolerance` of green -- green
    is the fixed reference channel (matches GenICam's BalanceRatioSelector,
    which only has Red/Blue entries, not Green).
    """
    return abs(r_median - g_median) <= tolerance and abs(b_median - g_median) <= tolerance


def next_balance_ratios(
    b_median: float,
    g_median: float,
    r_median: float,
    red_ratio: float,
    red_ratio_range: tuple[float, float],
    blue_ratio: float,
    blue_ratio_range: tuple[float, float],
) -> tuple[float, float]:
    """One correction step toward R == G == B, green held fixed as the
    reference channel. Red and blue are independent per-channel analog
    gains -- unlike exposure/gain there's no priority ordering or
    clamp-then-spill-remainder step, each channel's ratio is scaled and
    clamped to its own range independently.
    """
    red_min, red_max = red_ratio_range
    new_red = min(red_max, max(red_min, red_ratio * (g_median / max(r_median, 1.0))))

    blue_min, blue_max = blue_ratio_range
    new_blue = min(blue_max, max(blue_min, blue_ratio * (g_median / max(b_median, 1.0))))

    return new_red, new_blue


def is_converged(
    median: float, target: float = DEFAULT_TARGET_MEDIAN, tolerance: float = DEFAULT_TOLERANCE
) -> bool:
    return abs(median - target) <= tolerance


def exposure_budget_us(
    target_fps: float, fraction: float = DEFAULT_EXPOSURE_BUDGET_FRACTION
) -> float:
    """The longest exposure that still leaves room for `target_fps`.

    Exposure time is a frame-rate budget, not a free parameter: a sensor
    exposing for E microseconds cannot deliver frames faster than 1/E.
    Anything above this ceiling costs frame rate *and* smears motion --
    the exact motion this app exists to record. That makes it a
    constraint rather than a preference, which is why it lives here and
    not in a settings dialog. See CLAUDE.md's "Camera configuration: who
    decides what".
    """
    if target_fps <= 0:
        raise ValueError(f"target_fps must be positive, got {target_fps!r}")
    return (1_000_000.0 / target_fps) * fraction


def next_exposure_gain(
    median: float,
    exposure_time_us: float,
    exposure_range_us: tuple[float, float],
    gain: float,
    gain_range: tuple[float, float],
    target: float = DEFAULT_TARGET_MEDIAN,
    max_exposure_us: float | None = None,
) -> tuple[float, float]:
    """One correction step toward `target` median brightness.

    Scales ExposureTime by the brightness ratio first, spilling the
    remainder onto Gain once ExposureTime is clamped -- so a target
    reachable by ExposureTime alone never touches Gain.

    `max_exposure_us` (from exposure_budget_us()) tightens that clamp to
    the frame-rate budget rather than the sensor's own maximum. Without
    it this function will happily spend the whole frame interval to avoid
    a little gain, which is correct for stills and wrong for video: it
    produced an 87ms exposure on the slit lamp, capping it at ~11fps
    against a 30fps target and putting 87ms of motion blur on every
    frame, while 4x of gain headroom sat unused. See DECISIONS.md's
    "Camera configuration: which layer owns what" entry.
    """
    ratio = target / max(median, 1.0)
    exposure_min, exposure_max = exposure_range_us
    gain_min, gain_max = gain_range

    if max_exposure_us is not None:
        # Never below what the sensor can physically do: a frame-rate
        # target this camera cannot meet is a reason to warn (see
        # config.check_exposure_fits_fps), not a reason to ask it for an
        # impossible exposure.
        exposure_max = max(exposure_min, min(exposure_max, max_exposure_us))

    desired_exposure = exposure_time_us * ratio
    new_exposure = min(exposure_max, max(exposure_min, desired_exposure))

    new_gain = gain
    if new_exposure != desired_exposure:
        # ExposureTime alone couldn't absorb the full correction (it hit a
        # range limit) -- apply exactly the shortfall to Gain, not the full
        # ratio again, so Gain only ever makes up what Exposure couldn't.
        residual_ratio = desired_exposure / new_exposure
        desired_gain = gain * residual_ratio
        new_gain = min(gain_max, max(gain_min, desired_gain))

    return new_exposure, new_gain
