"""Shared BGR-ndarray-to-QPixmap conversion, used by every PySide6 window
in this repo that shows a live camera feed (app.py, preview.py,
settings.py).
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtGui import QImage, QPixmap


def bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
    # .copy() so the QImage owns its buffer independent of `rgb`'s lifetime.
    return QPixmap.fromImage(qimage.copy())
