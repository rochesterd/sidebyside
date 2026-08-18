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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from camera import BaseCamera
from config import ConfigError, DEFAULT_CONFIG_PATH, load_config
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
    key: str | None  # value written to config.json; None if this device can't be saved (see uvc_pid note)
    display_name: str
    preview_target: object  # what the row's preview_camera_factory is called with
    source: object  # the original IdsDeviceInfo/UvcDeviceInfo, for pulling extra fields (e.g. friendly_name) at Save time


def _default_list_ids_devices() -> list:
    # Lazy: ids_camera.py imports ids_peak at module level, which isn't
    # installed on every machine that runs settings.py (e.g. this dev
    # machine) -- see app.py's _make_camera for the same pattern.
    from ids_camera import list_ids_devices

    return list_ids_devices()


def _default_make_ids_camera(serial: str) -> BaseCamera:
    from ids_camera import IdsCamera

    return IdsCamera(serial=serial)


def _default_make_uvc_camera(index: int) -> BaseCamera:
    return UvcCamera(device=index, name="preview")


def _ids_candidates(devices: list) -> list[RowCandidate]:
    return [
        RowCandidate(key=d.serial, display_name=f"{d.model_name}  (serial {d.serial})", preview_target=d.serial, source=d)
        for d in devices
    ]


def _uvc_candidates(devices: list[UvcDeviceInfo]) -> list[RowCandidate]:
    return [
        RowCandidate(
            key=d.vid_pid,
            display_name=f"{d.name}  ({d.vid_pid})" if d.vid_pid else f"{d.name}  (no VID/PID)",
            preview_target=d.index,
            source=d,
        )
        for d in devices
    ]


class PreviewDialog(QDialog):
    """Single-camera live preview for whichever device is highlighted in a
    DeviceRow's dropdown -- opened modally (.exec(), not .show()) so a
    concurrent Rescan can't mutate the row's candidate list out from under
    it, and so this camera's lifetime doesn't overlap another IDS open
    attempt while ids_peak.Library's Initialize/Close reentrancy across
    nested calls is unverified (not in vendor/ids_peak_api.txt's scope).
    """

    def __init__(self, camera: BaseCamera, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Preview – {title}")
        self._camera = camera

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 360)
        self.video_label.setStyleSheet("background-color: black;")

        self.status_label = QLabel("Starting…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addWidget(self.video_label)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        try:
            self._camera.start()
        except Exception as exc:
            self.status_label.setText(f"Failed to start: {exc}")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(33)

    def _update(self) -> None:
        frame = self._camera.get_latest()
        if frame is None:
            return
        h, w = frame.image.shape[:2]
        self.status_label.setText(f"{w}x{h}")
        self.video_label.setPixmap(bgr_to_pixmap(frame.image))

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self._camera.stop()
        super().closeEvent(event)


class DeviceRow(QWidget):
    """One role's dropdown + (optional) label field + Preview button.

    Generic over instrument roles (IDS devices, editable label, key=serial)
    and the third-person role (UVC devices, no label, key=vid_pid) -- the
    difference is entirely in the candidates/factory passed in, not in this
    class's behavior. See DECISIONS.md.
    """

    changed = Signal()

    def __init__(
        self,
        role_key: str,
        title: str,
        has_label: bool,
        preview_camera_factory: Callable[[object], BaseCamera],
        parent=None,
    ):
        super().__init__(parent)
        self.role_key = role_key
        self.has_label = has_label
        self._preview_camera_factory = preview_camera_factory
        self._candidates: list[RowCandidate] = []
        self._pending_selection = _UNSET

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
            camera = self._preview_camera_factory(candidate.preview_target)
        except Exception as exc:
            QMessageBox.warning(self, "Preview failed", str(exc))
            return
        PreviewDialog(camera, self.title_label.text(), parent=self).exec()


class SettingsWindow(QMainWindow):
    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        list_ids_devices_fn: Callable[[], list] = _default_list_ids_devices,
        list_uvc_devices_fn: Callable[[], list[UvcDeviceInfo]] = list_uvc_devices,
        ids_preview_camera_factory: Callable[[str], BaseCamera] = _default_make_ids_camera,
        uvc_preview_camera_factory: Callable[[int], BaseCamera] = _default_make_uvc_camera,
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

        self._instrument_rows: dict[str, DeviceRow] = {
            key: DeviceRow(key, title, has_label=True, preview_camera_factory=ids_preview_camera_factory)
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

        layout = QVBoxLayout()
        layout.addWidget(self.warning_label)
        for row in self._all_rows():
            layout.addWidget(row)
            row.changed.connect(self._update_save_enabled)

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
                row.set_pending_selection(inst.serial)
        self._third_person_row.set_pending_selection(cfg.third_person.vid_pid)

    def rescan(self) -> None:
        ids_devices, ids_status = self._safe_list_ids_devices()
        for row in self._instrument_rows.values():
            row.set_candidates(_ids_candidates(ids_devices), status=ids_status)

        uvc_devices = self._list_uvc_devices_fn()
        self._third_person_row.set_candidates(_uvc_candidates(uvc_devices))

        self._update_save_enabled()

    def _safe_list_ids_devices(self) -> tuple[list, str]:
        try:
            return self._list_ids_devices_fn(), ""
        except Exception as exc:
            logger.warning("could not enumerate IDS devices: %s", exc)
            return [], f"Could not enumerate IDS devices: {exc}"

    def _update_save_enabled(self) -> None:
        self.save_button.setEnabled(all(row.is_valid() for row in self._all_rows()))

    def _on_save_clicked(self) -> None:
        if not all(row.is_valid() for row in self._all_rows()):
            return

        third_person_candidate = self._third_person_row.selected_candidate()
        data = {
            "instruments": {
                key: {"kind": "ids", "serial": row.selected_key(), "label": row.label_text()}
                for key, row in self._instrument_rows.items()
            },
            "third_person": {
                "kind": "uvc",
                "vid_pid": third_person_candidate.key,
                "friendly_name": third_person_candidate.source.name,
            },
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
