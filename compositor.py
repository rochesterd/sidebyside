"""Helpers for laying out one or two BGR images into a single canvas."""

from __future__ import annotations

import cv2
import numpy as np


def _fit_into_pane(
    image: np.ndarray, pane_w: int, pane_h: int, background: tuple[int, int, int] = (0, 0, 0)
) -> np.ndarray:
    """Resize `image` to fit within pane_w x pane_h preserving aspect ratio,
    letterboxing with `background` to fill the rest of the pane exactly.
    """
    pane_w = max(1, pane_w)
    pane_h = max(1, pane_h)
    h, w = image.shape[:2]
    if w == 0 or h == 0:
        return np.full((pane_h, pane_w, 3), background, dtype=np.uint8)

    scale = min(pane_w / w, pane_h / h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (new_w, new_h), interpolation=interp)

    canvas = np.full((pane_h, pane_w, 3), background, dtype=np.uint8)
    x_off = (pane_w - new_w) // 2
    y_off = (pane_h - new_h) // 2
    canvas[y_off : y_off + new_h, x_off : x_off + new_w] = resized
    return canvas


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
    pane_w = out_w // 2
    canvas = np.full((out_h, out_w, 3), background, dtype=np.uint8)
    canvas[:, :pane_w] = _fit_into_pane(left, pane_w, out_h, background)
    canvas[:, pane_w:] = _fit_into_pane(right, out_w - pane_w, out_h, background)
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
    canvas = _fit_into_pane(main, out_w, out_h, background)

    pip_w = max(1, round(out_w * pip_scale))
    pip_h = max(1, round(out_h * pip_scale))
    pip_resized = _fit_into_pane(pip, pip_w, pip_h, background)

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

    canvas[y0 : y0 + pip_h, x0 : x0 + pip_w] = pip_resized
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
