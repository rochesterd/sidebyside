"""Kiosk recording app: an instrument picker (Slit Lamp / BIO), one Start
button, one Stop button. See CLAUDE.md 'Who uses it'. The picker and Start
are the only controls live before a session; the instant Start is pressed,
the picker disables along with everything else, same as before.

All actual decisions (which instrument may be picked, when Start may be
pressed, when a stall counts as a failure, what the post-session summary
says) live in kiosk.KioskController. This module is a thin PySide6 shell
that polls it on a timer and reflects what it reports.
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from camera import BaseCamera
from compositor import side_by_side
from config import (
    DEFAULT_RECORDING_FPS,
    ConfigError,
    InstrumentConfig,
    is_frozen,
    load_config,
    resolve_default_sessions_dir,
)
from kiosk import KioskController, PreflightStatus, State
from qt_image import bgr_to_pixmap
from synthetic_camera import SyntheticCamera
from uvc_camera import UvcCamera

logger = logging.getLogger(__name__)

PREVIEW_FPS = 30
POLL_MS = 250
CAMERA_RETRY_MS = 2000
# Stand-in shapes for --instrument-synthetic, keyed by role -- not
# identity, so unlike serials/device index these stay out of config.json.
# Falls back to DEFAULT_SYNTHETIC_RESOLUTION for any role config.json
# defines that isn't one of today's two (see config.py/ROADMAP.md).
INSTRUMENT_SYNTHETIC_RESOLUTIONS = {"slit_lamp": (1600, 1200), "bio": (2056, 1542)}
DEFAULT_SYNTHETIC_RESOLUTION = (1600, 1200)
# The real ELP-USB100W03M-L21's resolution is queried at runtime (see
# uvc_camera.py) rather than hardcoded here -- this is only the stand-in
# used when --third-person-synthetic substitutes a SyntheticCamera for it.
THIRD_PERSON_SYNTHETIC_RESOLUTION = (640, 480)
PREVIEW_CANVAS_SIZE = (1280, 540)  # downscaled preview of the 2560x1080 recording canvas

# A (0, 0, 3) placeholder, not a real image -- compositor._fit_into_pane
# treats a zero-width/height source as "just fill this pane with the
# background color," used when there's no instrument frame yet to show
# next to the third-person preview.
_EMPTY_IMAGE = np.zeros((0, 0, 3), dtype=np.uint8)

# See config.py's resolve_default_config_path()/resolve_default_sessions_dir()
# for why this needs the same frozen/dev split -- a frozen install has no
# repo checkout for "logs" to be relative to.
LOG_DIR = Path(os.environ["ProgramData"]) / "sidebyside" / "logs" if is_frozen() else Path("logs")
LOG_FILE = LOG_DIR / "app.log"

THIRD_PERSON_LABEL = "third-person camera"


def _configure_logging() -> None:
    """Durable file logging plus an excepthook, so a failure on a student
    machine with no one around leaves a trace beyond the in-memory error
    banner and session.json -- see CLAUDE.md 'Who uses it'.

    RotatingFileHandler caps this at 4 * 2MB = 8MB total rather than
    growing without bound across a semester of unattended kiosk runs.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
        # Without this, an exception outside a caught path (e.g. during Qt
        # event handling) can leave the app frozen or gone with nothing in
        # the log explaining why.
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught


class KioskWindow(QMainWindow):
    def __init__(
        self,
        third_person_camera: BaseCamera,
        instruments: dict[str, BaseCamera],
        instrument_labels: dict[str, str] | None = None,
        fps: int = DEFAULT_RECORDING_FPS,
        output_root: str | Path = "sessions",
    ):
        super().__init__()
        self.setWindowTitle("Side by Side Recorder")

        self.third_person_camera = third_person_camera
        self.instruments = instruments
        self.instrument_labels = instrument_labels or {key: key for key in instruments}
        self._display_names = {**self.instrument_labels, "third_person": THIRD_PERSON_LABEL}
        self.controller = KioskController(third_person_camera, instruments, fps=fps, output_root=output_root)
        self._camera_start_errors: dict[str, str] = {}
        # The instrument the student last picked -- distinct from
        # controller.selected_instrument, which only updates once that
        # camera actually confirms live. Kept so a failed start (device
        # not plugged in yet) can keep retrying the same choice on the
        # camera-retry timer instead of silently reverting to "nothing
        # selected."
        self._desired_instrument: str | None = None

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

        self._instrument_buttons: dict[str, QPushButton] = {}
        picker = QHBoxLayout()
        for key in self.instruments:
            button = QPushButton(self.instrument_labels.get(key, key))
            button.setCheckable(True)
            button.setMinimumHeight(48)
            button.clicked.connect(functools.partial(self._on_instrument_clicked, key))
            picker.addWidget(button)
            self._instrument_buttons[key] = button

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
        layout.addLayout(picker)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._try_start_cameras()

        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start(int(1000 / PREVIEW_FPS))

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_tick)
        self.poll_timer.start(POLL_MS)

        # A real camera's start() can fail for reasons that resolve on
        # their own (not plugged in yet) as well as ones that don't (wrong
        # serial, already open elsewhere) -- both look the same from here,
        # so this just keeps retrying rather than requiring an app restart.
        # Also drives retrying the student's last-picked instrument until
        # it actually comes up. SyntheticCamera's start() never fails, so
        # this is a no-op churn of already-running checks on the default
        # synthetic path.
        self.camera_retry_timer = QTimer(self)
        self.camera_retry_timer.timeout.connect(self._try_start_cameras)
        self.camera_retry_timer.start(CAMERA_RETRY_MS)

        self._sync_ui(self.controller.poll_preflight())

    def _try_start_cameras(self) -> None:
        try:
            self.third_person_camera.start()
        except Exception as exc:
            if "third_person" not in self._camera_start_errors:
                logger.warning("third-person camera failed to start: %s", exc)
            self._camera_start_errors["third_person"] = str(exc)
        else:
            if "third_person" in self._camera_start_errors:
                logger.info("third-person camera recovered and started")
            self._camera_start_errors.pop("third_person", None)

        self._try_select_instrument()

    def _try_select_instrument(self) -> None:
        if self._desired_instrument is None or self.controller.state == State.RECORDING:
            return
        if self.controller.selected_instrument == self._desired_instrument:
            return
        key = self._desired_instrument
        try:
            self.controller.select_instrument(key)
        except Exception as exc:
            if key not in self._camera_start_errors:
                logger.warning("%s failed to start: %s", key, exc)
            self._camera_start_errors[key] = str(exc)
        else:
            if key in self._camera_start_errors:
                logger.info("%s recovered and started", key)
            self._camera_start_errors.pop(key, None)

    # --- Qt event handlers -------------------------------------------------

    def _on_instrument_clicked(self, key: str) -> None:
        if self.controller.state == State.RECORDING:
            return
        self._desired_instrument = key
        self._try_select_instrument()
        self._sync_ui()

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
        frame_tp = self.third_person_camera.get_latest()
        if frame_tp is None:
            return
        frame_instrument = None
        if self.controller.selected_instrument is not None:
            frame_instrument = self.instruments[self.controller.selected_instrument].get_latest()
        instrument_image = frame_instrument.image if frame_instrument is not None else _EMPTY_IMAGE
        canvas = side_by_side(instrument_image, frame_tp.image, out_size=PREVIEW_CANVAS_SIZE)
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
        for key, button in self._instrument_buttons.items():
            button.setEnabled(state != State.RECORDING)
            button.setChecked(key == self._desired_instrument)

        if state == State.RECORDING:
            self.status_label.setText("Recording...")
        elif state == State.READY:
            self.status_label.setText("Ready. Press Start.")
        elif state == State.IDLE:
            self.status_label.setText(self._idle_reason(preflight))
        # ERROR: leave whatever _show_error() just wrote in place.

    def _idle_reason(self, preflight) -> str:
        if self._desired_instrument is None:
            return "Select an instrument to begin."
        if preflight is None:
            return "Waiting for cameras..."
        missing = []
        if not preflight.cameras_ready:
            if self._camera_start_errors:
                errors = "; ".join(
                    f"{self._display_names.get(key, key)}: {msg}"
                    for key, msg in self._camera_start_errors.items()
                )
                missing.append(f"cameras live ({errors})")
            else:
                missing.append("cameras live")
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
            display_name = self._display_names.get(cam["name"], cam["name"])
            parts.append(f"{display_name}: {cam['frame_count']} frames, {cam['dropped_frames']} dropped")
        if self.controller.last_session_dir is not None:
            parts.append(f"saved to {self.controller.last_session_dir}")
        return "  |  ".join(parts)

    def _confirm_stop_and_exit(self) -> bool:
        # Split out from closeEvent() so tests can monkeypatch this instead
        # of dealing with a real modal dialog headlessly.
        reply = QMessageBox.question(
            self,
            "Recording in progress",
            "A recording is in progress. Stop it and exit?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        # A stray Alt+F4 or X-click shouldn't be able to silently end a
        # session, but a deliberate force-quit should still be possible --
        # defaults to No so an accidental click doesn't exit either way.
        if self.controller.state == State.RECORDING:
            if not self._confirm_stop_and_exit():
                logger.info("close-during-recording declined; recording continues")
                event.ignore()
                return
            logger.info("close-during-recording confirmed; stopping and exiting")
            try:
                self.controller.stop_recording()
            except Exception:
                logger.exception("stop_recording() failed during confirmed close")

        self.preview_timer.stop()
        self.poll_timer.stop()
        self.camera_retry_timer.stop()
        self.third_person_camera.stop()
        if self.controller.selected_instrument is not None:
            self.instruments[self.controller.selected_instrument].stop()
        super().closeEvent(event)


def _make_camera(
    inst: InstrumentConfig,
    synthetic: bool,
    resolution: tuple[int, int],
    name: str,
    target_fps: float | None = None,
) -> BaseCamera:
    if synthetic:
        return SyntheticCamera(*resolution, name=name, fps=30)

    if inst.kind == "net2860":
        # Imported lazily, not at module level: net2860_camera.py's default
        # paths assume .venv32/ exists, which it won't on a dev machine
        # that never set it up -- same reasoning as ids_camera.py's lazy
        # import just below. Not actually needed until a "net2860" instrument
        # is selected, same as ids_camera.py isn't needed until an "ids" one
        # is. See DECISIONS.md's "Net2860Camera" entry -- no target_fps or
        # calibration args, neither is implemented for this camera.
        from net2860_camera import Net2860Camera

        return Net2860Camera(label=name)

    # kind == "ids" -- config.py's _parse_instrument only allows "ids" or
    # "net2860", and "net2860" was handled above.
    # Imported lazily, not at module level: ids_camera.py pulls in
    # ids_peak/ids_peak_ipl, which aren't installed (or needed) on a dev
    # machine running only with --synthetic -- see CLAUDE.md's Environment
    # section. Importing it unconditionally at the top of this file would
    # break `python app.py --synthetic` on such a machine.
    from ids_camera import IdsCamera

    return IdsCamera(
        serial=inst.serial,
        exposure_time_us=inst.exposure_time_us,
        gain=inst.gain,
        red_balance_ratio=inst.red_balance_ratio,
        blue_balance_ratio=inst.blue_balance_ratio,
        target_fps=target_fps,
    )


def _make_third_person_camera(
    vid_pid: str | None, synthetic: bool, name: str, target_fps: float | None = None
) -> BaseCamera:
    if synthetic:
        return SyntheticCamera(*THIRD_PERSON_SYNTHETIC_RESOLUTION, name=name, fps=30)
    return UvcCamera(vid_pid=vid_pid, name=name, target_fps=target_fps)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use SyntheticCamera for all three cameras instead of real hardware "
        "(for development on a machine with no cameras attached)",
    )
    parser.add_argument(
        "--instrument-synthetic",
        action="store_true",
        help="Use SyntheticCamera for both instrument cameras (slit lamp, BIO), "
        "keeping the third-person camera real",
    )
    parser.add_argument(
        "--third-person-synthetic",
        action="store_true",
        help="Use SyntheticCamera for the third-person camera, keeping the "
        "instrument cameras real",
    )
    args = parser.parse_args()

    instrument_synthetic = args.synthetic or args.instrument_synthetic
    third_person_synthetic = args.synthetic or args.third_person_synthetic

    _configure_logging()

    # QApplication created before the config check (not after, as a plain
    # log-and-exit would allow) specifically so a missing/malformed
    # config.json can show a QMessageBox. app.exe is built with
    # console=False (see packaging/app.spec) and launched from a Desktop
    # shortcut with no console attached, so logger's StreamHandler->stderr
    # reaches nobody -- a technician on a freshly installed machine (no
    # config.json yet, the exact case this guards) would double-click the
    # icon and see literally nothing happen. The RotatingFileHandler still
    # captures it durably in LOG_FILE either way; this is about the
    # in-the-moment signal, not the record.
    # QApplication.instance() or ...: guards against a second instantiation
    # when main() runs inside a test process that already created one at
    # module import time (see test_app.py) -- in real usage there's never
    # an existing instance before main() runs, so this is a no-op there.
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error(str(exc))
        QMessageBox.critical(None, "sidebyside - Setup required", str(exc))
        return 1

    logger.info(
        "app starting: instruments=%s third_person_vid_pid=%s instrument_synthetic=%s third_person_synthetic=%s",
        {
            key: "synthetic" if instrument_synthetic else f"{inst.kind}:{inst.serial}"
            for key, inst in cfg.instruments.items()
        },
        cfg.third_person.vid_pid,
        instrument_synthetic,
        third_person_synthetic,
    )

    instruments = {
        key: _make_camera(
            inst,
            instrument_synthetic,
            INSTRUMENT_SYNTHETIC_RESOLUTIONS.get(key, DEFAULT_SYNTHETIC_RESOLUTION),
            key,
            target_fps=cfg.recording.fps,
        )
        for key, inst in cfg.instruments.items()
    }
    third_person = _make_third_person_camera(
        cfg.third_person.vid_pid, third_person_synthetic, "third-person", target_fps=cfg.recording.fps
    )
    instrument_labels = {key: inst.label for key, inst in cfg.instruments.items()}
    output_root = cfg.sessions_dir if cfg.sessions_dir is not None else resolve_default_sessions_dir()
    window = KioskWindow(
        third_person,
        instruments,
        instrument_labels=instrument_labels,
        fps=cfg.recording.fps,
        output_root=output_root,
    )
    window.resize(*PREVIEW_CANVAS_SIZE)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
