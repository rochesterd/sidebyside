"""Helpers for laying out one or two BGR images into a single canvas."""

from __future__ import annotations

import cv2
import numpy as np


def _fast_fill(canvas: np.ndarray, color: tuple[int, int, int]) -> None:
    """Fill `canvas` (uint8, HxWx3) with a solid color in place.

    `np.full(shape, color)` / `canvas[...] = color` broadcast a (3,)-shaped
    fill value across every pixel through numpy's slow element-wise path -
    about 10x slower than a real memset at the canvas sizes used here
    (measured: ~11.5ms vs ~1ms+ for a 2560x1080 frame), and this used to
    run three times per composited frame. cv2.rectangle's solid fill goes
    through OpenCV's fill path instead. See DECISIONS.md.
    """
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1] - 1, canvas.shape[0] - 1), color, thickness=-1)


def _fit_into_pane(
    image: np.ndarray, dst: np.ndarray, background: tuple[int, int, int] = (0, 0, 0)
) -> None:
    """Resize `image` to fit within `dst`'s shape preserving aspect ratio,
    writing the result directly into `dst` (a view into the caller's
    canvas) rather than building and copying a separate array. Only pays
    for a background fill when there's actually letterbox padding to
    cover - most calls in this module resize to exactly fill their target
    area and need no fill at all.
    """
    pane_h, pane_w = dst.shape[:2]
    h, w = image.shape[:2]
    if w == 0 or h == 0:
        _fast_fill(dst, background)
        return

    scale = min(pane_w / w, pane_h / h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR

    x_off = (pane_w - new_w) // 2
    y_off = (pane_h - new_h) // 2
    if x_off > 0 or y_off > 0:
        _fast_fill(dst, background)

    cv2.resize(image, (new_w, new_h), dst=dst[y_off : y_off + new_h, x_off : x_off + new_w], interpolation=interp)


def side_by_side(
    left: np.ndarray,
    right: np.ndarray,
    out_size: tuple[int, int] | None = None,
    background: tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """Compose two images into one canvas, left and right halves, each
    letterboxed to preserve its own aspect ratio.

    out_size: (width, height) of the resulting canvas. Defaults to the sum
    of the two images' widths and the max of their heights.
    """
    if out_size is None:
        lh, lw = left.shape[:2]
        rh, rw = right.shape[:2]
        out_size = (lw + rw, max(lh, rh))

    out_w, out_h = out_size
    pane_w = max(1, out_w // 2)
    # Uninitialized on purpose: the two _fit_into_pane calls below always
    # write every pixel between them (each pane is either fully covered by
    # the resized image or has its own letterbox fill), so pre-filling the
    # whole canvas here would just be wasted work immediately overwritten.
    canvas = np.empty((out_h, out_w, 3), dtype=np.uint8)
    _fit_into_pane(left, canvas[:, :pane_w], background)
    _fit_into_pane(right, canvas[:, pane_w:], background)
    return canvas


def picture_in_picture(
    main: np.ndarray,
    pip: np.ndarray,
    out_size: tuple[int, int] | None = None,
    pip_scale: float = 0.3,
    margin: int = 16,
    corner: str = "bottom-right",
    border_color: tuple[int, int, int] = (255, 255, 255),
    border_thickness: int = 2,
    background: tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """Compose `main` full-frame (letterboxed to preserve aspect ratio) with
    `pip` overlaid in a corner, scaled to pip_scale of the canvas's smaller
    dimension.
    """
    if out_size is None:
        h, w = main.shape[:2]
        out_size = (w, h)

    out_w, out_h = out_size
    canvas = np.empty((out_h, out_w, 3), dtype=np.uint8)
    _fit_into_pane(main, canvas, background)

    pip_w = max(1, round(out_w * pip_scale))
    pip_h = max(1, round(out_h * pip_scale))

    if corner == "top-left":
        x0, y0 = margin, margin
    elif corner == "top-right":
        x0, y0 = out_w - pip_w - margin, margin
    elif corner == "bottom-left":
        x0, y0 = margin, out_h - pip_h - margin
    else:  # bottom-right
        x0, y0 = out_w - pip_w - margin, out_h - pip_h - margin

    x0 = max(0, min(x0, out_w - pip_w))
    y0 = max(0, min(y0, out_h - pip_h))

    _fit_into_pane(pip, canvas[y0 : y0 + pip_h, x0 : x0 + pip_w], background)
    if border_thickness > 0:
        cv2.rectangle(
            canvas,
            (x0, y0),
            (x0 + pip_w - 1, y0 + pip_h - 1),
            border_color,
            thickness=border_thickness,
        )
    return canvas


def draw_timer(
    image: np.ndarray,
    text: str,
    position: tuple[int, int] = (10, 10),
    font_scale: float = 0.6,
    color: tuple[int, int, int] = (255, 255, 255),
    background: tuple[int, int, int] | None = (0, 0, 0),
    thickness: int = 1,
    padding: int = 6,
) -> np.ndarray:
    """Draw a text overlay (e.g. a timestamp/status line) at the top-left
    of `position`, with an optional filled background for readability.
    Modifies and returns `image`.
    """
    font = cv2.FONT_HERSHEY_SIMPLEX
    x, y = position
    (text_w, text_h), baseline = cv2.getTextSize(text, font, font_scale, thickness)

    if background is not None:
        cv2.rectangle(
            image,
            (x - padding, y - padding),
            (x + text_w + padding, y + text_h + baseline + padding),
            background,
            thickness=-1,
        )

    cv2.putText(
        image,
        text,
        (x, y + text_h),
        font,
        font_scale,
        color,
        thickness,
        cv2.LINE_AA,
    )
    return image
