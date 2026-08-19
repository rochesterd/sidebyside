"""Headless tests for settings.py's SettingsWindow/DeviceRow. Enumeration
and camera-construction functions are injected with fakes -- no real
hardware or the IDS SDK is needed (this dev machine has neither), matching
test_app.py's approach of exercising real failure/success paths against
fake BaseCamera-shaped objects rather than mocking internals.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from settings import DeviceRow, SettingsWindow
from synthetic_camera import SyntheticCamera
from uvc_enumeration import UvcDeviceInfo

_qt_app = QApplication.instance() or QApplication([])


@dataclass
class _FakeIdsDevice:
    serial: str
    model_name: str


SLIT_LAMP_DEVICE = _FakeIdsDevice(serial="111", model_name="UI-3250CP-C-HQ")
BIO_DEVICE = _FakeIdsDevice(serial="222", model_name="U3-327xCP-C")
THIRD_PERSON_DEVICE = UvcDeviceInfo(index=0, name="HD USB Camera", vid_pid="32E4:9310")

VALID_CONFIG = {
    "instruments": {
        "slit_lamp": {"kind": "ids", "serial": "111", "label": "Slit Lamp"},
        "bio": {"kind": "ids", "serial": "222", "label": "BIO"},
    },
    "third_person": {"kind": "uvc", "vid_pid": "32E4:9310", "friendly_name": "HD USB Camera"},
}


def _select(row: DeviceRow, key: str) -> None:
    for i in range(row.combo.count()):
        if row.combo.itemData(i) not in (None, -1) and row._candidates[row.combo.itemData(i)].key == key:
            row.combo.setCurrentIndex(i)
            return
    raise AssertionError(f"no candidate with key {key!r} in row {row.role_key!r}")


class SettingsWindowTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config_path = Path(self._tmpdir.name) / "config.json"

    def _make_window(
        self,
        ids_devices: list = (),
        uvc_devices: list = (),
        ids_preview_factory=lambda serial: SyntheticCamera(160, 120, fps=30),
        uvc_preview_factory=lambda index: SyntheticCamera(160, 120, fps=30),
    ) -> SettingsWindow:
        return SettingsWindow(
            config_path=self.config_path,
            list_ids_devices_fn=lambda: list(ids_devices),
            list_uvc_devices_fn=lambda: list(uvc_devices),
            ids_preview_camera_factory=ids_preview_factory,
            uvc_preview_camera_factory=uvc_preview_factory,
        )

    def test_fresh_start_shows_not_connected_and_save_disabled(self):
        window = self._make_window()

        for row in window._all_rows():
            self.assertIsNone(row.selected_key())
        self.assertFalse(window.save_button.isEnabled())
        self.assertTrue(window.warning_label.isHidden())

    def test_valid_existing_config_prepopulates_selection_and_labels(self):
        self.config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )

        self.assertEqual(window._instrument_rows["slit_lamp"].selected_key(), "111")
        self.assertEqual(window._instrument_rows["slit_lamp"].label_text(), "Slit Lamp")
        self.assertEqual(window._instrument_rows["bio"].selected_key(), "222")
        self.assertEqual(window._third_person_row.selected_key(), "32E4:9310")
        self.assertTrue(window.save_button.isEnabled())

    def test_configured_device_no_longer_present_shows_not_connected(self):
        self.config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
        # Only bio is currently enumerated -- slit_lamp's configured serial is absent.
        window = self._make_window(ids_devices=[BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE])

        self.assertIsNone(window._instrument_rows["slit_lamp"].selected_key())
        self.assertEqual(window._instrument_rows["slit_lamp"].label_text(), "Slit Lamp")  # label still loaded
        self.assertEqual(window._instrument_rows["bio"].selected_key(), "222")
        self.assertFalse(window.save_button.isEnabled())

    def test_malformed_existing_config_shows_warning_without_crashing(self):
        self.config_path.write_text("{not valid json", encoding="utf-8")

        window = self._make_window()

        self.assertFalse(window.warning_label.isHidden())
        for row in window._all_rows():
            self.assertIsNone(row.selected_key())

    def test_save_gating_requires_all_three_rows_valid(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        self.assertFalse(window.save_button.isEnabled())

        _select(window._instrument_rows["slit_lamp"], "111")
        window._instrument_rows["slit_lamp"].label_edit.setText("Slit Lamp")
        self.assertFalse(window.save_button.isEnabled())

        _select(window._instrument_rows["bio"], "222")
        window._instrument_rows["bio"].label_edit.setText("BIO")
        self.assertFalse(window.save_button.isEnabled())  # third-person still unset

        _select(window._third_person_row, "32E4:9310")
        self.assertTrue(window.save_button.isEnabled())

    def test_same_camera_picked_for_two_roles_blocks_save(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        _select(window._instrument_rows["slit_lamp"], "111")
        window._instrument_rows["slit_lamp"].label_edit.setText("Slit Lamp")
        _select(window._instrument_rows["bio"], "111")  # same serial as slit_lamp
        window._instrument_rows["bio"].label_edit.setText("BIO")
        _select(window._third_person_row, "32E4:9310")

        self.assertFalse(window.save_button.isEnabled())
        self.assertFalse(window.conflict_label.isHidden())
        self.assertIn("111", window.conflict_label.text())

        window._on_save_clicked()  # defense in depth: must no-op even if called directly
        self.assertFalse(self.config_path.exists())

        _select(window._instrument_rows["bio"], "222")  # resolve the conflict

        self.assertTrue(window.conflict_label.isHidden())
        self.assertTrue(window.save_button.isEnabled())

    def test_instrument_row_with_empty_label_is_invalid(self):
        window = self._make_window(ids_devices=[SLIT_LAMP_DEVICE])
        row = window._instrument_rows["slit_lamp"]
        _select(row, "111")

        self.assertFalse(row.is_valid())  # label still empty

        row.label_edit.setText("Slit Lamp")
        self.assertTrue(row.is_valid())

    def test_save_writes_expected_json_shape(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        _select(window._instrument_rows["slit_lamp"], "111")
        window._instrument_rows["slit_lamp"].label_edit.setText("Slit Lamp")
        _select(window._instrument_rows["bio"], "222")
        window._instrument_rows["bio"].label_edit.setText("BIO")
        _select(window._third_person_row, "32E4:9310")

        window._on_save_clicked()

        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written, VALID_CONFIG)

    def test_rescan_preserves_a_still_present_selection(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        _select(window._instrument_rows["slit_lamp"], "111")

        window.rescan()

        self.assertEqual(window._instrument_rows["slit_lamp"].selected_key(), "111")

    def test_rescan_drops_a_selection_that_disappeared(self):
        window = self._make_window(ids_devices=[SLIT_LAMP_DEVICE])
        _select(window._instrument_rows["slit_lamp"], "111")

        window._list_ids_devices_fn = lambda: []
        window.rescan()

        self.assertIsNone(window._instrument_rows["slit_lamp"].selected_key())

    def test_ids_enumeration_failure_is_surfaced_not_crashed(self):
        def _raise():
            raise RuntimeError("ids_peak not installed")

        window = self._make_window()
        window._list_ids_devices_fn = _raise
        window.rescan()

        self.assertIn("ids_peak not installed", window._instrument_rows["slit_lamp"].status_label.text())

    def test_preview_button_uses_the_injected_factory(self):
        calls = []

        def factory(serial):
            calls.append(serial)
            return SyntheticCamera(160, 120, fps=30)

        window = self._make_window(ids_devices=[SLIT_LAMP_DEVICE], ids_preview_factory=factory)
        _select(window._instrument_rows["slit_lamp"], "111")

        with patch("settings.PreviewDialog.exec", return_value=None):
            window._instrument_rows["slit_lamp"]._on_preview_clicked()

        self.assertEqual(calls, ["111"])


class PreviewDialogTest(unittest.TestCase):
    def test_starts_and_stops_a_real_synthetic_camera(self):
        from settings import PreviewDialog

        camera = SyntheticCamera(160, 120, fps=30)
        dialog = PreviewDialog(camera, "Test")
        try:
            self.assertIsNotNone(camera._thread)  # started
        finally:
            dialog.close()
        self.assertIsNone(camera._thread)  # stopped by closeEvent


if __name__ == "__main__":
    unittest.main()
