"""PySide6 preview window: two synthetic cameras, composited side by side
or picture-in-picture, refreshed at 30fps.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from compositor import draw_timer, picture_in_picture, side_by_side
from qt_image import bgr_to_pixmap
from synthetic_camera import SyntheticCamera

FPS = 30
CAMERA_A_RESOLUTION = (1600, 1200)
CAMERA_B_RESOLUTION = (2056, 1542)
CANVAS_SIZE = (1280, 720)


class PreviewWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Side by Side Preview")

        self.camera_a = SyntheticCamera(*CAMERA_A_RESOLUTION, name="cam-a", fps=30)
        self.camera_b = SyntheticCamera(*CAMERA_B_RESOLUTION, name="cam-b", fps=30)

        self.layout_box = QComboBox()
        self.layout_box.addItem("Side by side", "side_by_side")
        self.layout_box.addItem("Picture in picture", "picture_in_picture")

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(*CANVAS_SIZE)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Layout:"))
        controls.addWidget(self.layout_box)
        controls.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.video_label)
        layout.addWidget(self.status_label)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.camera_a.start()
        self.camera_b.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(int(1000 / FPS))

    def update_frame(self) -> None:
        frame_a = self.camera_a.get_latest()
        frame_b = self.camera_b.get_latest()
        if frame_a is None or frame_b is None:
            return

        layout_mode = self.layout_box.currentData()
        if layout_mode == "picture_in_picture":
            canvas = picture_in_picture(frame_a.image, frame_b.image, out_size=CANVAS_SIZE)
        else:
            canvas = side_by_side(frame_a.image, frame_b.image, out_size=CANVAS_SIZE)

        skew_ms = abs(frame_a.timestamp - frame_b.timestamp) * 1000.0
        status = (
            f"cam-a frame {frame_a.index:6d}   "
            f"cam-b frame {frame_b.index:6d}   "
            f"skew {skew_ms:6.1f} ms"
        )
        draw_timer(canvas, status, position=(10, 10))

        self.video_label.setPixmap(bgr_to_pixmap(canvas))
        self.status_label.setText(status)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.camera_a.stop()
        self.camera_b.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = PreviewWindow()
    window.resize(*CANVAS_SIZE)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
