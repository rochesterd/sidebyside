"""Technician tool: assigns which physical camera fills each role (slit
lamp, BIO, third-person) and writes config.json. See ROADMAP.md's "Device
compatibility & camera setup system" entry and CLAUDE.md's "Who uses it" --
this is deliberately a separate program from app.py, never launched from
the kiosk window, so nothing reachable from here needs a "could a student
stumble into this" review.

Deliberately lean, not a wizard: one row per role, a dropdown of currently-
detected candidate devices, a Preview button, and global Rescan/Save. Save
writes config.json but does not hot-reload a running app.py -- restart the
kiosk app to pick up changes (an explicit accepted scope line, not a gap).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from camera import BaseCamera
from config import ConfigError, DEFAULT_CONFIG_PATH, load_config, resolve_default_sessions_dir
from qt_image import bgr_to_pixmap
from uvc_camera import UvcCamera
from uvc_enumeration import UvcDeviceInfo, list_uvc_devices

logger = logging.getLogger(__name__)

ROLE_TITLES = {"slit_lamp": "Slit Lamp", "bio": "BIO"}
THIRD_PERSON_ROLE = "third_person"
THIRD_PERSON_TITLE = "Third-Person"

_UNSET = object()  # sentinel: "no pending preselection", distinct from a real None key


@dataclass
class RowCandidate:
    # UI-level selection identity (dropdown matching, is_valid()'s "something
    # is selected" check) -- None if this device can't be saved (see uvc_pid
    # note). NOT always literally what's written to config.json's "serial":
    # for kind="net2860" this is a fixed sentinel ("net2860"), since
    # _instrument_data() writes that kind's config shape from `kind` alone,
    # not from this key.
    key: str | None
    kind: str  # "ids" / "uvc" / "net2860" -- which BaseCamera subclass this becomes
    display_name: str
    preview_target: object  # e.g. serial (ids) / device index (uvc) -- what the preview factory pulls out of this candidate
    source: object  # the original IdsDeviceInfo/UvcDeviceInfo, for pulling extra fields (e.g. friendly_name) at Save time


def _default_list_ids_devices() -> list:
    # Lazy: ids_camera.py imports ids_peak at module level, which isn't
    # installed on every machine that runs settings.py (e.g. this dev
    # machine) -- see app.py's _make_camera for the same pattern.
    from ids_camera import list_ids_devices

    return list_ids_devices()


def _default_make_ids_camera(candidate: RowCandidate) -> BaseCamera:
    from ids_camera import IdsCamera

    return IdsCamera(serial=candidate.preview_target)


def _default_make_uvc_camera(candidate: RowCandidate) -> BaseCamera:
    return UvcCamera(device=candidate.preview_target, name="preview")


def _default_make_net2860_camera(_candidate: RowCandidate) -> BaseCamera:
    # Lazy import -- see app.py's _make_camera for why (net2860_camera.py's
    # default paths assume .venv32/ exists).
    from net2860_camera import Net2860Camera

    return Net2860Camera(label="preview")


def _default_make_instrument_camera(candidate: RowCandidate) -> BaseCamera:
    """Shared preview factory for both instrument rows (slit lamp, BIO) --
    branches per-candidate rather than per-row, since the BIO row can offer
    both "ids" and "net2860" candidates. See DECISIONS.md's "Net2860Camera"
    entry."""
    if candidate.kind == "net2860":
        return _default_make_net2860_camera(candidate)
    return _default_make_ids_camera(candidate)


def _ids_candidates(devices: list) -> list[RowCandidate]:
    return [
        RowCandidate(
            key=d.serial,
            kind="ids",
            display_name=f"{d.model_name}  (serial {d.serial})",
            preview_target=d.serial,
            source=d,
        )
        for d in devices
    ]


def _net2860_candidates() -> list[RowCandidate]:
    """Not device-scanned, unlike _ids_candidates()/_uvc_candidates() --
    this camera has no device-manager-visible category to enumerate without
    actually spinning up the 32-bit helper subprocess (see DECISIONS.md's
    "Net2860Camera" entry), so it's a single static candidate always offered
    on the BIO row instead. key="net2860" doubles as its own sentinel: there's
    exactly one of this camera, so no real identity to key off of."""
    return [
        RowCandidate(
            key="net2860",
            kind="net2860",
            display_name="Legacy BIO (NET GmbH KS722OUP)",
            preview_target=None,
            source=None,
        )
    ]


def _uvc_candidates(devices: list[UvcDeviceInfo]) -> list[RowCandidate]:
    return [
        RowCandidate(
            key=d.vid_pid,
            kind="uvc",
            display_name=f"{d.name}  ({d.vid_pid})" if d.vid_pid else f"{d.name}  (no VID/PID)",
            preview_target=d.index,
            source=d,
        )
        for d in devices
    ]


_GAIN_SLIDER_SCALE = 10  # QSlider is integer-only; gain is a small float (e.g. 1.0-24.0)
# BalanceRatio's real range is unverified (no hardware to check against --
# see ids_camera.py's note on BalanceRatioSelector/BalanceRatio); 100 is a
# starting guess for slider granularity, same footing as the gain scale above.
_BALANCE_RATIO_SLIDER_SCALE = 100


class PreviewDialog(QDialog):
    """Single-camera live preview for whichever device is highlighted in a
    DeviceRow's dropdown -- opened modally (.exec(), not .show()) so a
    concurrent Rescan can't mutate the row's candidate list out from under
    it, and so this camera's lifetime doesn't overlap another IDS open
    attempt while ids_peak.Library's Initialize/Close reentrancy across
    nested calls is unverified (not in vendor/ids_peak_api.txt's scope).

    For an instrument camera with no ExposureAuto/GainAuto (detected via
    `camera.needs_manual_calibration()`, duck-typed rather than an
    isinstance(IdsCamera) check so this module never needs to import
    ids_camera at all -- see CLAUDE.md's Environment section on why that
    import must stay lazy), also shows exposure/gain sliders and an
    Auto-Calibrate button. See ROADMAP.md's "In-app exposure/gain
    calibration" entry for the full design.

    Same treatment, independently, for a camera with no BalanceWhiteAuto
    (`camera.needs_manual_white_balance()`): red/blue balance-ratio sliders
    and an Auto White-Balance button. Kept as a separate control block with
    its own status label rather than merged with exposure/gain -- the slit
    lamp plausibly lacks both ExposureAuto/GainAuto *and* BalanceWhiteAuto
    at once, so both blocks can be visible simultaneously, and a shared
    status label would have one calibration's message clobber the other's.
    See ROADMAP.md's 2026-08-26 entry.
    """

    def __init__(
        self,
        camera: BaseCamera,
        title: str,
        parent=None,
        initial_exposure_time_us: float | None = None,
        initial_gain: float | None = None,
        initial_red_balance_ratio: float | None = None,
        initial_blue_balance_ratio: float | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Preview – {title}")
        self._camera = camera
        # Stop the camera on *any* dialog exit, not just closeEvent: the Esc
        # key routes through QDialog.reject() with no QCloseEvent, which
        # would otherwise leak the open IDS device. See DECISIONS.md's
        # 2026-09-01 entry.
        self.finished.connect(self._shutdown)
        self.calibration_supported = False
        self.white_balance_supported = False
        self.final_exposure_time_us = initial_exposure_time_us
        self.final_gain = initial_gain
        self.final_red_balance_ratio = initial_red_balance_ratio
        self.final_blue_balance_ratio = initial_blue_balance_ratio

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 360)
        self.video_label.setStyleSheet("background-color: black;")

        self.status_label = QLabel("Starting…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.calibration_status_label = QLabel()
        self.calibration_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.calibration_status_label.setWordWrap(True)

        self.white_balance_status_label = QLabel()
        self.white_balance_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.white_balance_status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self.video_label)
        layout.addWidget(self.status_label)

        try:
            self._camera.start()
            self.calibration_supported = bool(getattr(camera, "needs_manual_calibration", lambda: False)())
            self.white_balance_supported = bool(
                getattr(camera, "needs_manual_white_balance", lambda: False)()
            )
        except Exception as exc:
            self.status_label.setText(f"Failed to start: {exc}")

        # A raise partway through building the calibration controls would
        # propagate out of __init__ before the dialog is ever shown or
        # closed, leaking the just-started camera -- tear it down
        # explicitly, then let the error surface. See DECISIONS.md.
        try:
            if self.calibration_supported:
                self._build_exposure_gain_controls(layout, initial_exposure_time_us, initial_gain)
            layout.addWidget(self.calibration_status_label)

            if self.white_balance_supported:
                self._build_white_balance_controls(layout, initial_red_balance_ratio, initial_blue_balance_ratio)
            layout.addWidget(self.white_balance_status_label)
        except Exception:
            self._shutdown()
            raise

        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(33)

    def _add_slider_row(
        self, layout: QVBoxLayout, label_text: str, minimum: int, maximum: int, value: int
    ) -> tuple[QSlider, QLabel]:
        """Builds one labeled slider + value-readout row and appends it to
        layout. The caller wires valueChanged itself -- the handler differs
        per axis (exposure/gain/red/blue), so it isn't wired here.
        """
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        value_label = QLabel()

        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        row.addWidget(slider)
        row.addWidget(value_label)
        layout.addLayout(row)

        return slider, value_label

    def _build_exposure_gain_controls(
        self, layout: QVBoxLayout, initial_exposure_time_us: float | None, initial_gain: float | None
    ) -> None:
        exposure_min, exposure_max = self._camera.exposure_time_range_us()
        gain_min, gain_max = self._camera.gain_range()

        # Seed from a previously-saved calibration if there is one, rather
        # than whatever the sensor happened to power on with.
        if initial_exposure_time_us is not None:
            self._camera.set_exposure_time_us(min(exposure_max, max(exposure_min, initial_exposure_time_us)))
        if initial_gain is not None:
            self._camera.set_gain(min(gain_max, max(gain_min, initial_gain)))

        self.exposure_slider, self.exposure_value_label = self._add_slider_row(
            layout, "Exposure", int(exposure_min), int(exposure_max), int(self._camera.get_exposure_time_us())
        )
        self.exposure_slider.valueChanged.connect(self._on_exposure_changed)

        self.gain_slider, self.gain_value_label = self._add_slider_row(
            layout,
            "Gain",
            int(gain_min * _GAIN_SLIDER_SCALE),
            int(gain_max * _GAIN_SLIDER_SCALE),
            int(self._camera.get_gain() * _GAIN_SLIDER_SCALE),
        )
        self.gain_slider.valueChanged.connect(self._on_gain_changed)

        self.calibrate_button = QPushButton("Auto-Calibrate")
        self.calibrate_button.clicked.connect(self._on_calibrate_clicked)
        layout.addWidget(self.calibrate_button)

        self.final_exposure_time_us = self._camera.get_exposure_time_us()
        self.final_gain = self._camera.get_gain()
        self._refresh_exposure_gain_labels()

    def _build_white_balance_controls(
        self, layout: QVBoxLayout, initial_red: float | None, initial_blue: float | None
    ) -> None:
        red_min, red_max = self._camera.red_balance_ratio_range()
        blue_min, blue_max = self._camera.blue_balance_ratio_range()

        if initial_red is not None:
            self._camera.set_red_balance_ratio(min(red_max, max(red_min, initial_red)))
        if initial_blue is not None:
            self._camera.set_blue_balance_ratio(min(blue_max, max(blue_min, initial_blue)))

        self.red_balance_slider, self.red_balance_value_label = self._add_slider_row(
            layout,
            "Red",
            int(red_min * _BALANCE_RATIO_SLIDER_SCALE),
            int(red_max * _BALANCE_RATIO_SLIDER_SCALE),
            int(self._camera.get_red_balance_ratio() * _BALANCE_RATIO_SLIDER_SCALE),
        )
        self.red_balance_slider.valueChanged.connect(self._on_red_balance_changed)

        self.blue_balance_slider, self.blue_balance_value_label = self._add_slider_row(
            layout,
            "Blue",
            int(blue_min * _BALANCE_RATIO_SLIDER_SCALE),
            int(blue_max * _BALANCE_RATIO_SLIDER_SCALE),
            int(self._camera.get_blue_balance_ratio() * _BALANCE_RATIO_SLIDER_SCALE),
        )
        self.blue_balance_slider.valueChanged.connect(self._on_blue_balance_changed)

        self.white_balance_button = QPushButton("Auto White-Balance")
        self.white_balance_button.clicked.connect(self._on_white_balance_clicked)
        layout.addWidget(self.white_balance_button)

        self.final_red_balance_ratio = self._camera.get_red_balance_ratio()
        self.final_blue_balance_ratio = self._camera.get_blue_balance_ratio()
        self._refresh_white_balance_labels()

    def _on_exposure_changed(self, value: int) -> None:
        self._camera.set_exposure_time_us(float(value))
        self.final_exposure_time_us = float(value)
        self._refresh_exposure_gain_labels()

    def _on_gain_changed(self, value: int) -> None:
        gain = value / _GAIN_SLIDER_SCALE
        self._camera.set_gain(gain)
        self.final_gain = gain
        self._refresh_exposure_gain_labels()

    def _on_red_balance_changed(self, value: int) -> None:
        red = value / _BALANCE_RATIO_SLIDER_SCALE
        self._camera.set_red_balance_ratio(red)
        self.final_red_balance_ratio = red
        self._refresh_white_balance_labels()

    def _on_blue_balance_changed(self, value: int) -> None:
        blue = value / _BALANCE_RATIO_SLIDER_SCALE
        self._camera.set_blue_balance_ratio(blue)
        self.final_blue_balance_ratio = blue
        self._refresh_white_balance_labels()

    def _on_calibrate_clicked(self) -> None:
        self.calibrate_button.setEnabled(False)
        self.calibration_status_label.setText("Calibrating…")
        QApplication.processEvents()
        try:
            converged = self._camera.auto_calibrate()
        except Exception as exc:
            QMessageBox.warning(self, "Calibration failed", str(exc))
            converged = None
        self.calibrate_button.setEnabled(True)

        self.exposure_slider.blockSignals(True)
        self.gain_slider.blockSignals(True)
        self.exposure_slider.setValue(int(self._camera.get_exposure_time_us()))
        self.gain_slider.setValue(int(self._camera.get_gain() * _GAIN_SLIDER_SCALE))
        self.exposure_slider.blockSignals(False)
        self.gain_slider.blockSignals(False)
        self.final_exposure_time_us = self._camera.get_exposure_time_us()
        self.final_gain = self._camera.get_gain()
        self._refresh_exposure_gain_labels()

        if converged is True:
            self.calibration_status_label.setText("Calibrated.")
        elif converged is False:
            self.calibration_status_label.setText(
                "Couldn't reach target brightness automatically -- adjust the sliders by eye."
            )

    def _on_white_balance_clicked(self) -> None:
        self.white_balance_button.setEnabled(False)
        self.white_balance_status_label.setText("Calibrating…")
        QApplication.processEvents()
        try:
            converged = self._camera.auto_white_balance()
        except Exception as exc:
            QMessageBox.warning(self, "White balance calibration failed", str(exc))
            converged = None
        self.white_balance_button.setEnabled(True)

        self.red_balance_slider.blockSignals(True)
        self.blue_balance_slider.blockSignals(True)
        self.red_balance_slider.setValue(int(self._camera.get_red_balance_ratio() * _BALANCE_RATIO_SLIDER_SCALE))
        self.blue_balance_slider.setValue(int(self._camera.get_blue_balance_ratio() * _BALANCE_RATIO_SLIDER_SCALE))
        self.red_balance_slider.blockSignals(False)
        self.blue_balance_slider.blockSignals(False)
        self.final_red_balance_ratio = self._camera.get_red_balance_ratio()
        self.final_blue_balance_ratio = self._camera.get_blue_balance_ratio()
        self._refresh_white_balance_labels()

        if converged is True:
            self.white_balance_status_label.setText("Calibrated.")
        elif converged is False:
            self.white_balance_status_label.setText(
                "Couldn't reach a neutral balance automatically -- adjust the sliders by eye."
            )

    def _refresh_exposure_gain_labels(self) -> None:
        self.exposure_value_label.setText(f"{int(self._camera.get_exposure_time_us())} µs")
        self.gain_value_label.setText(f"{self._camera.get_gain():.1f}x")

    def _refresh_white_balance_labels(self) -> None:
        self.red_balance_value_label.setText(f"{self._camera.get_red_balance_ratio():.2f}x")
        self.blue_balance_value_label.setText(f"{self._camera.get_blue_balance_ratio():.2f}x")

    def _update(self) -> None:
        frame = self._camera.get_latest()
        if frame is None:
            return
        h, w = frame.image.shape[:2]
        self.status_label.setText(f"{w}x{h}")
        self.video_label.setPixmap(bgr_to_pixmap(frame.image))

    def _shutdown(self, *_args) -> None:
        """Stop the preview timer and the camera. Idempotent -- reached from
        both the finished signal and closeEvent, and tolerant of being
        called before self.timer exists (an __init__ failure path). The
        camera's own stop() no-ops if it never started."""
        timer = getattr(self, "timer", None)
        if timer is not None:
            timer.stop()
        self._camera.stop()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)


class DeviceRow(QWidget):
    """One role's dropdown + (optional) label field + Preview button.

    Generic over instrument roles (editable label, one or more candidate
    kinds -- IDS devices, or IDS devices plus the static net2860 candidate
    on the BIO row) and the third-person role (UVC devices, no label,
    key=vid_pid) -- the difference is entirely in the candidates/factory
    passed in, not in this class's behavior. A row's candidates can mix
    kinds (the BIO row does); each RowCandidate carries its own `kind` so
    the (shared) preview factory and Save's config-shape logic branch
    per-candidate. See DECISIONS.md's "Net2860Camera" entry.
    """

    changed = Signal()

    def __init__(
        self,
        role_key: str,
        title: str,
        has_label: bool,
        preview_camera_factory: Callable[[RowCandidate], BaseCamera],
        supports_calibration: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.role_key = role_key
        self.has_label = has_label
        self.supports_calibration = supports_calibration
        self._preview_camera_factory = preview_camera_factory
        self._candidates: list[RowCandidate] = []
        self._pending_selection = _UNSET
        # Populated from an existing config.json's exposure_time_us/gain
        # (instrument roles only) and updated after a Preview session that
        # used the calibration controls -- see PreviewDialog. None means
        # "no calibrated value yet," the same as a camera with working
        # auto-exposure never needing one.
        self._exposure_time_us: float | None = None
        self._gain: float | None = None
        # Same idea, independently, for white balance -- see
        # config.py's red_balance_ratio/blue_balance_ratio pairing rule.
        self._red_balance_ratio: float | None = None
        self._blue_balance_ratio: float | None = None

        self.title_label = QLabel(title)
        self.title_label.setMinimumWidth(90)

        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_selection_changed)

        self.label_edit = QLineEdit() if has_label else None
        if self.label_edit is not None:
            self.label_edit.setPlaceholderText("Label shown on the picker")
            self.label_edit.textChanged.connect(lambda _text: self.changed.emit())

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self._on_preview_clicked)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        grid = QGridLayout()
        grid.addWidget(self.title_label, 0, 0)
        grid.addWidget(self.combo, 0, 1)
        col = 2
        if self.label_edit is not None:
            grid.addWidget(self.label_edit, 0, col)
            col += 1
        grid.addWidget(self.preview_button, 0, col)
        grid.addWidget(self.status_label, 1, 1, 1, col)
        self.setLayout(grid)

        self._update_ui_state()

    def set_pending_selection(self, key: str | None) -> None:
        """Consumed once, by the next set_candidates() call -- used to
        preselect a device loaded from an existing config.json at startup.
        After that, set_candidates() preserves whatever's currently
        selected instead (so Rescan doesn't revert a technician's choice
        back to the originally-loaded config).
        """
        self._pending_selection = key

    def set_candidates(self, candidates: list[RowCandidate], status: str = "") -> None:
        if self._pending_selection is not _UNSET:
            target_key = self._pending_selection
            self._pending_selection = _UNSET
        else:
            current = self.selected_candidate()
            target_key = current.key if current is not None else None

        self._candidates = candidates
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("‹not connected›", -1)
        for i, candidate in enumerate(candidates):
            self.combo.addItem(candidate.display_name, i)

        new_index = 0
        if target_key is not None:
            for i, candidate in enumerate(candidates):
                if candidate.key == target_key:
                    new_index = i + 1
                    break
        self.combo.setCurrentIndex(new_index)
        self.combo.blockSignals(False)

        self.status_label.setText(status)
        self._update_ui_state()
        self.changed.emit()

    def selected_candidate(self) -> RowCandidate | None:
        idx = self.combo.currentData()
        if idx is None or idx < 0:
            return None
        return self._candidates[idx]

    def selected_key(self) -> str | None:
        candidate = self.selected_candidate()
        return candidate.key if candidate is not None else None

    def label_text(self) -> str:
        return self.label_edit.text().strip() if self.label_edit is not None else ""

    def set_label_text(self, text: str) -> None:
        if self.label_edit is not None:
            self.label_edit.setText(text)

    def calibration(self) -> tuple[float | None, float | None]:
        return self._exposure_time_us, self._gain

    def set_calibration(self, exposure_time_us: float | None, gain: float | None) -> None:
        self._exposure_time_us = exposure_time_us
        self._gain = gain

    def white_balance(self) -> tuple[float | None, float | None]:
        return self._red_balance_ratio, self._blue_balance_ratio

    def set_white_balance(self, red_balance_ratio: float | None, blue_balance_ratio: float | None) -> None:
        self._red_balance_ratio = red_balance_ratio
        self._blue_balance_ratio = blue_balance_ratio

    def is_valid(self) -> bool:
        if self.selected_key() is None:
            return False
        if self.has_label and not self.label_text():
            return False
        return True

    def _on_selection_changed(self, _index: int) -> None:
        self._update_ui_state()
        self.changed.emit()

    def _update_ui_state(self) -> None:
        candidate = self.selected_candidate()
        self.preview_button.setEnabled(candidate is not None)
        if candidate is not None and candidate.key is None:
            self.status_label.setText("This device has no discoverable VID/PID and can't be saved.")

    def _on_preview_clicked(self) -> None:
        candidate = self.selected_candidate()
        if candidate is None:
            return
        try:
            camera = self._preview_camera_factory(candidate)
        except Exception as exc:
            QMessageBox.warning(self, "Preview failed", str(exc))
            return
        dialog = PreviewDialog(
            camera,
            self.title_label.text(),
            parent=self,
            initial_exposure_time_us=self._exposure_time_us if self.supports_calibration else None,
            initial_gain=self._gain if self.supports_calibration else None,
            initial_red_balance_ratio=self._red_balance_ratio if self.supports_calibration else None,
            initial_blue_balance_ratio=self._blue_balance_ratio if self.supports_calibration else None,
        )
        dialog.exec()
        if self.supports_calibration and dialog.calibration_supported:
            self._exposure_time_us = dialog.final_exposure_time_us
            self._gain = dialog.final_gain
        if self.supports_calibration and dialog.white_balance_supported:
            self._red_balance_ratio = dialog.final_red_balance_ratio
            self._blue_balance_ratio = dialog.final_blue_balance_ratio


class SettingsWindow(QMainWindow):
    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        list_ids_devices_fn: Callable[[], list] = _default_list_ids_devices,
        list_uvc_devices_fn: Callable[[], list[UvcDeviceInfo]] = list_uvc_devices,
        instrument_preview_camera_factory: Callable[[RowCandidate], BaseCamera] = _default_make_instrument_camera,
        uvc_preview_camera_factory: Callable[[RowCandidate], BaseCamera] = _default_make_uvc_camera,
    ):
        super().__init__()
        self.setWindowTitle("Camera Settings")
        self.config_path = Path(config_path)
        self._list_ids_devices_fn = list_ids_devices_fn
        self._list_uvc_devices_fn = list_uvc_devices_fn

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            "background-color: #b00020; color: white; font-weight: bold; padding: 8px;"
        )
        self.warning_label.hide()

        # Separate from warning_label: that one is about an existing
        # config.json this window couldn't read; this one is live
        # validation against the *current* on-screen selections (today,
        # just "same physical camera picked for two instrument roles" --
        # app.py would otherwise have both roles fighting to open one
        # device). Recomputed on every row change, not just at startup.
        self.conflict_label = QLabel()
        self.conflict_label.setWordWrap(True)
        self.conflict_label.setStyleSheet(
            "background-color: #b00020; color: white; font-weight: bold; padding: 8px;"
        )
        self.conflict_label.hide()

        self._instrument_rows: dict[str, DeviceRow] = {
            key: DeviceRow(
                key,
                title,
                has_label=True,
                preview_camera_factory=instrument_preview_camera_factory,
                supports_calibration=True,
            )
            for key, title in ROLE_TITLES.items()
        }
        self._third_person_row = DeviceRow(
            THIRD_PERSON_ROLE, THIRD_PERSON_TITLE, has_label=False, preview_camera_factory=uvc_preview_camera_factory
        )

        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.clicked.connect(self.rescan)
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._on_save_clicked)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        # Not a per-role DeviceRow: this is where recordings land, not a
        # camera. Pre-filled with the resolved default so it's always a
        # valid value even if the technician never touches it -- see
        # config.py's resolve_default_sessions_dir().
        self.sessions_dir_title = QLabel("Recordings folder")
        self.sessions_dir_title.setMinimumWidth(90)
        self.sessions_dir_edit = QLineEdit(str(resolve_default_sessions_dir()))
        self.sessions_dir_edit.setReadOnly(True)
        self.sessions_dir_browse_button = QPushButton("Browse...")
        self.sessions_dir_browse_button.clicked.connect(self._on_browse_sessions_dir)
        sessions_dir_row = QHBoxLayout()
        sessions_dir_row.addWidget(self.sessions_dir_title)
        sessions_dir_row.addWidget(self.sessions_dir_edit)
        sessions_dir_row.addWidget(self.sessions_dir_browse_button)

        layout = QVBoxLayout()
        layout.addWidget(self.warning_label)
        layout.addWidget(self.conflict_label)
        for row in self._all_rows():
            layout.addWidget(row)
            row.changed.connect(self._update_save_enabled)
        layout.addLayout(sessions_dir_row)

        buttons = QHBoxLayout()
        buttons.addWidget(self.rescan_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._load_existing_config()
        self.rescan()

    def _all_rows(self) -> list[DeviceRow]:
        return [*self._instrument_rows.values(), self._third_person_row]

    def _load_existing_config(self) -> None:
        if not self.config_path.exists():
            return  # normal first-run state -- no config yet, nothing to warn about
        try:
            cfg = load_config(self.config_path)
        except ConfigError as exc:
            self.warning_label.setText(f"Existing {self.config_path} could not be read: {exc}")
            self.warning_label.show()
            return

        for key, row in self._instrument_rows.items():
            inst = cfg.instruments.get(key)
            if inst is not None:
                row.set_label_text(inst.label)
                # inst.serial is None for kind="net2860" -- that candidate's
                # key is the sentinel "net2860" (== inst.kind), not a serial.
                pending_key = inst.serial if inst.kind == "ids" else inst.kind
                row.set_pending_selection(pending_key)
                row.set_calibration(inst.exposure_time_us, inst.gain)
                row.set_white_balance(inst.red_balance_ratio, inst.blue_balance_ratio)
        self._third_person_row.set_pending_selection(cfg.third_person.vid_pid)
        if cfg.sessions_dir is not None:
            self.sessions_dir_edit.setText(str(cfg.sessions_dir))

    def rescan(self) -> None:
        ids_devices, ids_status = self._safe_list_ids_devices()
        ids_candidates = _ids_candidates(ids_devices)
        for key, row in self._instrument_rows.items():
            # "bio" only: this camera is a BIO-specific alternative, not a
            # slit-lamp one -- see DECISIONS.md's "Net2860Camera" entry.
            candidates = ids_candidates + _net2860_candidates() if key == "bio" else ids_candidates
            row.set_candidates(candidates, status=ids_status)

        uvc_devices = self._list_uvc_devices_fn()
        self._third_person_row.set_candidates(_uvc_candidates(uvc_devices))

        self._update_save_enabled()

    def _safe_list_ids_devices(self) -> tuple[list, str]:
        try:
            return self._list_ids_devices_fn(), ""
        except Exception as exc:
            logger.warning("could not enumerate IDS devices: %s", exc)
            return [], f"Could not enumerate IDS devices: {exc}"

    def _on_browse_sessions_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose recordings folder", self.sessions_dir_edit.text())
        if chosen:
            self.sessions_dir_edit.setText(chosen)

    def _duplicate_serial_roles(self) -> dict[str, list[str]]:
        """Serials currently selected by more than one instrument row --
        app.py has no way to open the same physical IDS camera for two
        roles at once, so this must block Save, not just look odd.
        """
        by_serial: dict[str, list[str]] = {}
        for key, row in self._instrument_rows.items():
            serial = row.selected_key()
            if serial is not None:
                by_serial.setdefault(serial, []).append(key)
        return {serial: roles for serial, roles in by_serial.items() if len(roles) > 1}

    def _update_save_enabled(self) -> None:
        duplicates = self._duplicate_serial_roles()
        if duplicates:
            conflicts = []
            for serial, roles in duplicates.items():
                role_titles = " and ".join(ROLE_TITLES.get(r, r) for r in roles)
                conflicts.append(f"{role_titles} are both set to serial {serial}")
            self.conflict_label.setText(f"Can't save: {'; '.join(conflicts)}. Pick a different camera for each role.")
            self.conflict_label.show()
            self.save_button.setEnabled(False)
            return

        self.conflict_label.hide()
        self.save_button.setEnabled(all(row.is_valid() for row in self._all_rows()))

    def _instrument_data(self, row: DeviceRow) -> dict:
        candidate = row.selected_candidate()
        if candidate is not None and candidate.kind == "net2860":
            return {"kind": "net2860", "label": row.label_text()}

        data = {"kind": "ids", "serial": row.selected_key(), "label": row.label_text()}
        exposure_time_us, gain = row.calibration()
        if exposure_time_us is not None:
            data["exposure_time_us"] = exposure_time_us
        if gain is not None:
            data["gain"] = gain
        red_balance_ratio, blue_balance_ratio = row.white_balance()
        if red_balance_ratio is not None:
            data["red_balance_ratio"] = red_balance_ratio
        if blue_balance_ratio is not None:
            data["blue_balance_ratio"] = blue_balance_ratio
        return data

    def _on_save_clicked(self) -> None:
        if self._duplicate_serial_roles() or not all(row.is_valid() for row in self._all_rows()):
            return

        third_person_candidate = self._third_person_row.selected_candidate()
        data = {
            "instruments": {key: self._instrument_data(row) for key, row in self._instrument_rows.items()},
            "third_person": {
                "kind": "uvc",
                "vid_pid": third_person_candidate.key,
                "friendly_name": third_person_candidate.source.name,
            },
            "sessions_dir": self.sessions_dir_edit.text(),
        }
        self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.status_label.setText(f"Saved to {self.config_path}. Restart app.py to apply.")
        self.warning_label.hide()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    window = SettingsWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
