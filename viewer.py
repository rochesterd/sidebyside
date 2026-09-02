"""Session viewer: plays back a recorded session, laying its two streams
out on demand rather than however they were composited at record time
(they aren't -- see recorder.py).

A thin PySide6 shell over session_reader.SessionPlayer, the same split
app.py has over kiosk.py. Runs two ways from one class:

- inside the kiosk, opened modally by app.py's Watch button, and
- as a standalone viewer.exe for reviewing a session later.

Student-facing, same audience as recording (CLAUDE.md's "Who uses it"):
play/pause, scrub, and a layout picker. Nothing here can modify or delete
a recording.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from compositor import fit_into_canvas, picture_in_picture, side_by_side
from config import ConfigError, load_config, resolve_default_sessions_dir
from qt_image import bgr_to_pixmap
from recorder import INSTRUMENT_STREAM, THIRD_PERSON_STREAM
from session_reader import Session, SessionError, SessionPlayer, list_sessions

logger = logging.getLogger(__name__)

TICK_MS = 33  # ~30Hz, matching the recording rate ceiling
SLIDER_STEPS = 1000
DEFAULT_CANVAS = (1280, 540)

# A (0, 0, 3) placeholder, not a real image -- compositor._fit_into_pane
# treats a zero-sized source as "fill this pane with the background",
# which is what a stream with no frame at this instant should look like.
_EMPTY_IMAGE = np.zeros((0, 0, 3), dtype=np.uint8)


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:d}:{seconds % 60:02d}"


class ViewerDialog(QDialog):
    """Playback window for one session.

    A QDialog rather than a QMainWindow so app.py can open it modally with
    .exec() (the kiosk's own controls are then unreachable while it's up)
    while main() can still show it as a standalone top-level window.

    Teardown hangs off the `finished` signal, not closeEvent: Esc routes
    through QDialog.reject(), which never delivers a QCloseEvent, and this
    dialog holds open PyAV decoders. That's the same leak DECISIONS.md's
    "settings.py Preview leaked the IDS device" entry is about.
    """

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"Recording - {session.directory.name}")
        self.setSizeGripEnabled(True)

        self.player = SessionPlayer(session)
        self._playing = False
        self._play_started_wall = 0.0
        self._play_started_media = 0.0
        self._scrubbing = False
        self._resume_after_scrub = False
        self._dirty = True
        self.finished.connect(self._shutdown)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(640, 300)
        self.video_label.setStyleSheet("background-color: black;")

        self.play_button = QPushButton("Play")
        self.play_button.setMinimumHeight(40)
        self.play_button.clicked.connect(self._toggle_play)

        self.layout_box = QComboBox()
        self.layout_box.addItem("Side by side", "side_by_side")
        self.layout_box.addItem("Picture in picture", "picture_in_picture")
        instrument = session.instrument
        third = session.third_person
        self.layout_box.addItem(f"{instrument.label if instrument else 'Instrument'} only", "instrument")
        self.layout_box.addItem(f"{third.label if third else 'Third-person'} only", "third_person")
        self.layout_box.currentIndexChanged.connect(self._on_layout_changed)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.sliderPressed.connect(self._on_scrub_start)
        self.slider.sliderMoved.connect(self._on_scrub_move)
        self.slider.sliderReleased.connect(self._on_scrub_end)

        self.time_label = QLabel()
        self.time_label.setMinimumWidth(90)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_label = QLabel(self._describe_session())
        self.status_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.slider, stretch=1)
        controls.addWidget(self.time_label)
        controls.addWidget(QLabel("View:"))
        controls.addWidget(self.layout_box)

        layout = QVBoxLayout()
        layout.addWidget(self.video_label, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        self._sync_position_ui()
        self._render()

    # --- session description ------------------------------------------------

    def _describe_session(self) -> str:
        parts = [f"{_mmss(self.player.duration)} recording"]
        for stream in self.session.streams.values():
            note = "" if stream.verified else "  (unverified - see the .mkv beside it)"
            parts.append(f"{stream.label}: {stream.width}x{stream.height}, {stream.frame_count} frames{note}")
        return "   |   ".join(parts)

    # --- playback -------------------------------------------------------------

    def _toggle_play(self) -> None:
        if not self._playing and self.player.position >= self.player.duration:
            self.player.seek(0.0)  # replay from the top rather than sitting at the end
        self._set_playing(not self._playing)

    def _set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.play_button.setText("Pause" if playing else "Play")
        if playing:
            self._play_started_wall = time.monotonic()
            self._play_started_media = self.player.position
        self._dirty = True

    def _tick(self) -> None:
        if self._playing and not self._scrubbing:
            elapsed = time.monotonic() - self._play_started_wall
            target = self._play_started_media + elapsed
            if target >= self.player.duration:
                target = self.player.duration
                self._set_playing(False)
            # advance_to only moves forward and decodes what it must -- if
            # the UI falls behind, frames are skipped for *presentation*,
            # so media time never drifts from wall-clock.
            if self.player.advance_to(target):
                self._dirty = True
            self._sync_position_ui()
        if self._dirty:
            self._render()
            self._dirty = False

    def _sync_position_ui(self) -> None:
        duration = self.player.duration
        if not self._scrubbing:
            fraction = (self.player.position / duration) if duration > 0 else 0.0
            self.slider.blockSignals(True)
            self.slider.setValue(int(fraction * SLIDER_STEPS))
            self.slider.blockSignals(False)
        self.time_label.setText(f"{_mmss(self.player.position)} / {_mmss(duration)}")

    # --- scrubbing -----------------------------------------------------------

    def _on_scrub_start(self) -> None:
        self._scrubbing = True
        self._resume_after_scrub = self._playing
        self._set_playing(False)

    def _on_scrub_move(self, value: int) -> None:
        self.player.seek(self.player.duration * (value / SLIDER_STEPS))
        self.time_label.setText(f"{_mmss(self.player.position)} / {_mmss(self.player.duration)}")
        self._dirty = True

    def _on_scrub_end(self) -> None:
        self._scrubbing = False
        if self._resume_after_scrub:
            self._set_playing(True)
        self._sync_position_ui()

    def _on_layout_changed(self, _index: int) -> None:
        self._dirty = True

    # --- rendering ------------------------------------------------------------

    def _canvas_size(self) -> tuple[int, int]:
        size = self.video_label.size()
        return (max(160, size.width()), max(120, size.height()))

    def _render(self) -> None:
        images = self.player.images()
        instrument = images.get(INSTRUMENT_STREAM)
        third_person = images.get(THIRD_PERSON_STREAM)
        if instrument is None:
            instrument = _EMPTY_IMAGE
        if third_person is None:
            third_person = _EMPTY_IMAGE

        out_size = self._canvas_size()
        mode = self.layout_box.currentData()
        if mode == "instrument":
            canvas = fit_into_canvas(instrument, out_size)
        elif mode == "third_person":
            canvas = fit_into_canvas(third_person, out_size)
        elif mode == "picture_in_picture":
            canvas = picture_in_picture(instrument, third_person, out_size=out_size)
        else:
            canvas = side_by_side(instrument, third_person, out_size=out_size)

        self.video_label.setPixmap(bgr_to_pixmap(canvas))

    def resizeEvent(self, event) -> None:
        self._dirty = True
        super().resizeEvent(event)

    # --- teardown --------------------------------------------------------------

    def _shutdown(self, *_args) -> None:
        """Stop the timer and release the decoders. Idempotent -- reached
        from both `finished` and closeEvent."""
        timer = getattr(self, "timer", None)
        if timer is not None:
            timer.stop()
        player = getattr(self, "player", None)
        if player is not None:
            player.close()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)


def open_session(session_dir: Path | str, parent=None) -> ViewerDialog | None:
    """Load and show a session modally, reporting a bad session with a
    dialog rather than a traceback. Returns None if it couldn't be opened.
    """
    try:
        session = Session.load(session_dir)
    except SessionError as exc:
        QMessageBox.warning(parent, "Can't open this recording", str(exc))
        logger.warning("could not open session %s: %s", session_dir, exc)
        return None
    dialog = ViewerDialog(session, parent=parent)
    dialog.resize(*DEFAULT_CANVAS)
    dialog.exec()
    return dialog


def _default_sessions_dir() -> Path:
    """Where recordings live for this install -- config.json's choice if
    there is one, else the same default app.py falls back to."""
    try:
        cfg = load_config()
        if cfg.sessions_dir is not None:
            return Path(cfg.sessions_dir)
    except ConfigError as exc:
        logger.info("no usable config.json (%s); using the default recordings folder", exc)
    return resolve_default_sessions_dir()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication.instance() or QApplication(sys.argv)

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        # No argument: open the most recent recording. The full
        # Past-recordings picker is the next phase (see ROADMAP.md).
        sessions = list_sessions(_default_sessions_dir())
        if not sessions:
            QMessageBox.information(
                None, "No recordings", f"No recordings found in {_default_sessions_dir()}."
            )
            return 1
        target = sessions[0].directory

    return 0 if open_session(target) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
