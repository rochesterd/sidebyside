"""Kiosk recording app: one Start button, one Stop button, nothing else
clickable during a session. See CLAUDE.md 'Who uses it'.

All actual decisions (when Start may be pressed, when a stall counts as a
failure, what the post-session summary says) live in kiosk.KioskController.
This module is a thin PySide6 shell that polls it on a timer and reflects
what it reports.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from camera import BaseCamera
from compositor import side_by_side
from kiosk import KioskController, PreflightStatus, State
from synthetic_camera import SyntheticCamera

PREVIEW_FPS = 30
POLL_MS = 250
CAMERA_A_RESOLUTION = (1600, 1200)
CAMERA_B_RESOLUTION = (2056, 1542)
PREVIEW_CANVAS_SIZE = (1280, 540)  # downscaled preview of the 2560x1080 recording canvas


def bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
    # .copy() so the QImage owns its buffer independent of `rgb`'s lifetime.
    return QPixmap.fromImage(qimage.copy())


class KioskWindow(QMainWindow):
    def __init__(
        self,
        camera_a: BaseCamera,
        camera_b: BaseCamera,
        name_a: str = "camera_a",
        name_b: str = "camera_b",
    ):
        super().__init__()
        self.setWindowTitle("Side by Side Recorder")

        self.camera_a = camera_a
        self.camera_b = camera_b
        self.controller = KioskController(camera_a, camera_b, name_a=name_a, name_b=name_b)

        self.error_banner = QLabel()
        self.error_banner.setWordWrap(True)
        self.error_banner.setStyleSheet(
            "background-color: #b00020; color: white; font-weight: bold; padding: 12px; font-size: 14pt;"
        )
        self.error_banner.hide()

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(*PREVIEW_CANVAS_SIZE)
        self.video_label.setStyleSheet("background-color: black;")

        self.start_button = QPushButton("Start")
        self.start_button.setEnabled(False)
        self.start_button.setMinimumHeight(64)
        self.start_button.clicked.connect(self._on_start_clicked)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setMinimumHeight(64)
        self.stop_button.clicked.connect(self._on_stop_clicked)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        # Separate from status_label on purpose: status_label reflects live
        # state and gets overwritten every ~250ms by _sync_ui(), which would
        # blow away a "here's what just got recorded" message within one
        # poll tick. summary_label is only ever touched right after a
        # session ends and stays put until the next one.
        self.summary_label = QLabel()
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)

        layout = QVBoxLayout()
        layout.addWidget(self.error_banner)
        layout.addWidget(self.video_label)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.camera_a.start()
        self.camera_b.start()

        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start(int(1000 / PREVIEW_FPS))

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_tick)
        self.poll_timer.start(POLL_MS)

        self._sync_ui(self.controller.poll_preflight())

    # --- Qt event handlers -------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self.controller.state != State.READY:
            return
        self.controller.start_recording()
        self.error_banner.hide()
        self.summary_label.clear()
        self._sync_ui()

    def _on_stop_clicked(self) -> None:
        if self.controller.state != State.RECORDING:
            return
        session_info = self.controller.stop_recording()
        self._sync_ui()
        self.summary_label.setText(self._format_summary("Session complete", session_info))

    def _update_preview(self) -> None:
        frame_a = self.camera_a.get_latest()
        frame_b = self.camera_b.get_latest()
        if frame_a is None or frame_b is None:
            return
        canvas = side_by_side(frame_a.image, frame_b.image, out_size=PREVIEW_CANVAS_SIZE)
        self.video_label.setPixmap(bgr_to_pixmap(canvas))

    def _poll_tick(self) -> None:
        preflight = None
        if self.controller.state == State.RECORDING:
            self.controller.poll_recording()
            if self.controller.state == State.ERROR:
                self._show_error()
        else:
            preflight = self.controller.poll_preflight()
        self._sync_ui(preflight)

    # --- UI reflection --------------------------------------------------

    def _sync_ui(self, preflight: PreflightStatus | None = None) -> None:
        state = self.controller.state
        self.start_button.setEnabled(state == State.READY)
        self.stop_button.setEnabled(state == State.RECORDING)

        if state == State.RECORDING:
            self.status_label.setText("Recording...")
        elif state == State.READY:
            self.status_label.setText("Ready. Press Start.")
        elif state == State.IDLE:
            self.status_label.setText(self._idle_reason(preflight))
        # ERROR: leave whatever _show_error() just wrote in place.

    def _idle_reason(self, preflight) -> str:
        if preflight is None:
            return "Waiting for cameras..."
        missing = []
        if not preflight.cameras_ready:
            missing.append("both cameras live")
        if not preflight.disk_ok:
            free_mb = preflight.free_bytes / (1024 * 1024)
            required_mb = preflight.required_bytes / (1024 * 1024)
            missing.append(f"free disk space ({free_mb:.0f} MB free, {required_mb:.0f} MB required)")
        if not missing:
            return "Waiting for cameras..."
        return "Waiting for: " + "; ".join(missing)

    def _show_error(self) -> None:
        message = self.controller.error_message or "Recording stopped unexpectedly."
        self.error_banner.setText(f"RECORDING STOPPED - {message}")
        self.error_banner.show()
        if self.controller.last_session_info is not None:
            self.summary_label.setText(
                self._format_summary("Partial session saved", self.controller.last_session_info)
            )

    def _format_summary(self, headline: str, session_info: dict) -> str:
        if "error" in session_info and "composite" not in session_info:
            return f"{headline}, but the output could not be finalized: {session_info['error']}"

        comp = session_info["composite"]
        parts = [f"{headline}: {comp['frame_count']} frames"]
        for cam in session_info["cameras"].values():
            parts.append(f"{cam['name']}: {cam['frame_count']} frames, {cam['dropped_frames']} dropped")
        if self.controller.last_session_dir is not None:
            parts.append(f"saved to {self.controller.last_session_dir}")
        return "  |  ".join(parts)

    def closeEvent(self, event) -> None:
        self.preview_timer.stop()
        self.poll_timer.stop()
        if self.controller.state == State.RECORDING:
            try:
                self.controller.stop_recording()
            except Exception:
                pass
        self.camera_a.stop()
        self.camera_b.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    camera_a = SyntheticCamera(*CAMERA_A_RESOLUTION, name="cam-a", fps=30)
    camera_b = SyntheticCamera(*CAMERA_B_RESOLUTION, name="cam-b", fps=30)
    window = KioskWindow(camera_a, camera_b, name_a="cam-a", name_b="cam-b")
    window.resize(*PREVIEW_CANVAS_SIZE)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
