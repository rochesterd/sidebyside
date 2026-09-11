"""The Reflex mark as a live widget: the kiosk's recording-state indicator.

Drawn with QPainter rather than rendered from branding/*.svg, because the
change between idle and recording interpolates the pupil's shape and fill
together, which a static SVG can't do. The SVGs stay the source for the
.ico files; test_reflex_mark.py holds both to the same numbers.

State is carried by shape (a slit becomes a round pupil) as well as color,
so it reads in grayscale and to red color deficiency.

Display only: the widget takes no focus and no clicks, so it can never be a
second interactive control while recording (CLAUDE.md "Who uses it").
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

import neco_reflex_theme as theme

# Geometry on the mark's 100 x 100 viewBox.
VIEWBOX = 100.0
CENTER = 50.0
RING_RADIUS = 34.0
RING_STROKE = 10.0
IDLE_PUPIL = (4.5, 19.0)  # (rx, ry): a vertical slit
RECORDING_PUPIL = (22.0, 22.0)  # a round pupil
TRANSITION_MS = 200


def pupil_at(progress: float, ring_color: str) -> tuple[float, float, QColor]:
    """The pupil's (rx, ry, fill) at `progress`, from idle (0.0) to
    recording (1.0). The idle pupil is the ring's color."""
    t = min(1.0, max(0.0, progress))
    rx = IDLE_PUPIL[0] + (RECORDING_PUPIL[0] - IDLE_PUPIL[0]) * t
    ry = IDLE_PUPIL[1] + (RECORDING_PUPIL[1] - IDLE_PUPIL[1]) * t
    start = QColor(ring_color)
    end = QColor(theme.RECORDING_PUPIL)
    fill = QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * t,
        start.greenF() + (end.greenF() - start.greenF()) * t,
        start.blueF() + (end.blueF() - start.blueF()) * t,
    )
    return rx, ry, fill


class ReflexMark(QWidget):
    """The cat-eye mark, animating between idle and recording.

    `ring_color` is the foreground of the surface the mark sits on --
    Off-White on the kiosk's Charcoal chrome.
    """

    def __init__(self, size: int = 40, ring_color: str = theme.OFF_WHITE, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Not recording")
        self._ring_color = ring_color
        self._recording = False
        self._progress = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._on_progress)

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def progress(self) -> float:
        """0.0 at idle, 1.0 at recording, in between mid-transition."""
        return self._progress

    def set_recording(self, recording: bool) -> None:
        """Animate toward the recording or the idle pupil.

        Idempotent: app.py calls this on every poll tick, and repeating the
        current state must not restart the transition.
        """
        if recording == self._recording:
            return
        self._recording = recording
        self.setAccessibleName("Recording" if recording else "Not recording")
        # Read before touching the animation: setting its start or end value
        # re-emits valueChanged, which would overwrite self._progress.
        start = self._progress
        target = 1.0 if recording else 0.0
        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(target)
        # Reversing mid-transition covers only the distance already
        # travelled, rather than taking a full transition to come back.
        self._animation.setDuration(max(1, round(TRANSITION_MS * abs(target - start))))
        self._animation.start()

    def _on_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
        painter.scale(side / VIEWBOX, side / VIEWBOX)

        painter.setPen(QPen(QColor(self._ring_color), RING_STROKE))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(CENTER, CENTER), RING_RADIUS, RING_RADIUS)

        rx, ry, fill = pupil_at(self._progress, self._ring_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(CENTER, CENTER), rx, ry)
        painter.end()
