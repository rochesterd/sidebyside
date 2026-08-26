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

from config import resolve_default_sessions_dir
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
        expected = {**VALID_CONFIG, "sessions_dir": str(resolve_default_sessions_dir())}
        self.assertEqual(written, expected)

    def test_calibration_round_trips_through_device_row_into_saved_config(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        row = window._instrument_rows["slit_lamp"]
        _select(row, "111")
        row.label_edit.setText("Slit Lamp")

        with patch("settings.PreviewDialog") as mock_dialog_cls:
            mock_dialog = mock_dialog_cls.return_value
            mock_dialog.calibration_supported = True
            mock_dialog.final_exposure_time_us = 12345.0
            mock_dialog.final_gain = 3.5
            mock_dialog.white_balance_supported = False
            row._on_preview_clicked()

        self.assertEqual(row.calibration(), (12345.0, 3.5))

        _select(window._instrument_rows["bio"], "222")
        window._instrument_rows["bio"].label_edit.setText("BIO")
        _select(window._third_person_row, "32E4:9310")
        window._on_save_clicked()

        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written["instruments"]["slit_lamp"]["exposure_time_us"], 12345.0)
        self.assertEqual(written["instruments"]["slit_lamp"]["gain"], 3.5)

    def test_calibration_not_persisted_when_preview_camera_lacks_it(self):
        window = self._make_window(ids_devices=[SLIT_LAMP_DEVICE])
        row = window._instrument_rows["slit_lamp"]
        _select(row, "111")

        with patch("settings.PreviewDialog") as mock_dialog_cls:
            mock_dialog_cls.return_value.calibration_supported = False
            mock_dialog_cls.return_value.white_balance_supported = False
            row._on_preview_clicked()

        self.assertEqual(row.calibration(), (None, None))

    def test_white_balance_round_trips_through_device_row_into_saved_config(self):
        window = self._make_window(
            ids_devices=[SLIT_LAMP_DEVICE, BIO_DEVICE], uvc_devices=[THIRD_PERSON_DEVICE]
        )
        row = window._instrument_rows["slit_lamp"]
        _select(row, "111")
        row.label_edit.setText("Slit Lamp")

        with patch("settings.PreviewDialog") as mock_dialog_cls:
            mock_dialog = mock_dialog_cls.return_value
            mock_dialog.calibration_supported = False
            mock_dialog.white_balance_supported = True
            mock_dialog.final_red_balance_ratio = 1.8
            mock_dialog.final_blue_balance_ratio = 2.1
            row._on_preview_clicked()

        self.assertEqual(row.white_balance(), (1.8, 2.1))

        _select(window._instrument_rows["bio"], "222")
        window._instrument_rows["bio"].label_edit.setText("BIO")
        _select(window._third_person_row, "32E4:9310")
        window._on_save_clicked()

        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written["instruments"]["slit_lamp"]["red_balance_ratio"], 1.8)
        self.assertEqual(written["instruments"]["slit_lamp"]["blue_balance_ratio"], 2.1)

    def test_white_balance_not_persisted_when_preview_camera_lacks_it(self):
        window = self._make_window(ids_devices=[SLIT_LAMP_DEVICE])
        row = window._instrument_rows["slit_lamp"]
        _select(row, "111")

        with patch("settings.PreviewDialog") as mock_dialog_cls:
            mock_dialog_cls.return_value.calibration_supported = False
            mock_dialog_cls.return_value.white_balance_supported = False
            row._on_preview_clicked()

        self.assertEqual(row.white_balance(), (None, None))

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

    def test_sessions_dir_defaults_to_resolved_default(self):
        window = self._make_window()

        self.assertEqual(window.sessions_dir_edit.text(), str(resolve_default_sessions_dir()))

    def test_existing_sessions_dir_is_loaded_from_config(self):
        data = {**VALID_CONFIG, "sessions_dir": "D:\\recordings"}
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

        window = self._make_window()

        self.assertEqual(window.sessions_dir_edit.text(), "D:\\recordings")

    def test_browse_button_updates_sessions_dir(self):
        window = self._make_window()

        with patch("settings.QFileDialog.getExistingDirectory", return_value="E:\\backup"):
            window._on_browse_sessions_dir()

        self.assertEqual(window.sessions_dir_edit.text(), "E:\\backup")

    def test_cancelled_browse_leaves_sessions_dir_unchanged(self):
        window = self._make_window()
        original = window.sessions_dir_edit.text()

        with patch("settings.QFileDialog.getExistingDirectory", return_value=""):
            window._on_browse_sessions_dir()

        self.assertEqual(window.sessions_dir_edit.text(), original)

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

    def test_camera_without_needs_manual_calibration_shows_no_calibration_controls(self):
        from settings import PreviewDialog

        camera = SyntheticCamera(160, 120, fps=30)  # no needs_manual_calibration() at all
        dialog = PreviewDialog(camera, "Test")
        try:
            self.assertFalse(dialog.calibration_supported)
            self.assertFalse(hasattr(dialog, "exposure_slider"))
        finally:
            dialog.close()


class _FakeCalibratableCamera(SyntheticCamera):
    """Stands in for an IdsCamera lacking ExposureAuto/GainAuto/
    BalanceWhiteAuto (the slit lamp plausibly lacks all three) -- exercises
    PreviewDialog's exposure/gain *and* white-balance branches headlessly,
    without the IDS peak SDK. Extended in place (rather than a second fake)
    specifically so the "both blocks visible at once" case is directly
    testable against one camera.
    """

    def __init__(self):
        super().__init__(160, 120, fps=30)
        self._exposure_time_us = 1000.0
        self._gain = 2.0
        self._red_balance_ratio = 1.5
        self._blue_balance_ratio = 1.2

    def needs_manual_calibration(self) -> bool:
        return True

    def get_exposure_time_us(self) -> float:
        return self._exposure_time_us

    def set_exposure_time_us(self, value: float) -> None:
        self._exposure_time_us = value

    def exposure_time_range_us(self) -> tuple[float, float]:
        return (100.0, 10_000.0)

    def get_gain(self) -> float:
        return self._gain

    def set_gain(self, value: float) -> None:
        self._gain = value

    def gain_range(self) -> tuple[float, float]:
        return (1.0, 8.0)

    def auto_calibrate(self, **_kwargs) -> bool:
        self._exposure_time_us = 4000.0
        self._gain = 3.0
        return True

    def needs_manual_white_balance(self) -> bool:
        return True

    def get_red_balance_ratio(self) -> float:
        return self._red_balance_ratio

    def set_red_balance_ratio(self, value: float) -> None:
        self._red_balance_ratio = value

    def red_balance_ratio_range(self) -> tuple[float, float]:
        return (0.5, 4.0)

    def get_blue_balance_ratio(self) -> float:
        return self._blue_balance_ratio

    def set_blue_balance_ratio(self, value: float) -> None:
        self._blue_balance_ratio = value

    def blue_balance_ratio_range(self) -> tuple[float, float]:
        return (0.5, 4.0)

    def auto_white_balance(self, **_kwargs) -> bool:
        self._red_balance_ratio = 1.9
        self._blue_balance_ratio = 2.2
        return True


class PreviewDialogCalibrationTest(unittest.TestCase):
    def test_calibratable_camera_shows_sliders_seeded_from_its_current_values(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp")
        try:
            self.assertTrue(dialog.calibration_supported)
            self.assertEqual(dialog.exposure_slider.value(), 1000)
            self.assertEqual(dialog.final_exposure_time_us, 1000.0)
            self.assertEqual(dialog.final_gain, 2.0)
        finally:
            dialog.close()

    def test_initial_calibration_is_applied_before_reading_slider_seed(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp", initial_exposure_time_us=5000.0, initial_gain=4.0)
        try:
            self.assertEqual(camera.get_exposure_time_us(), 5000.0)
            self.assertEqual(dialog.final_gain, 4.0)
        finally:
            dialog.close()

    def test_auto_calibrate_button_updates_sliders_and_final_values(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp")
        try:
            dialog._on_calibrate_clicked()
            self.assertEqual(dialog.final_exposure_time_us, 4000.0)
            self.assertEqual(dialog.final_gain, 3.0)
            self.assertEqual(dialog.exposure_slider.value(), 4000)
            self.assertEqual(dialog.calibration_status_label.text(), "Calibrated.")
        finally:
            dialog.close()

    def test_white_balance_sliders_seeded_from_current_values(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp")
        try:
            self.assertTrue(dialog.white_balance_supported)
            self.assertEqual(dialog.red_balance_slider.value(), 150)  # 1.5 * scale(100)
            self.assertEqual(dialog.final_red_balance_ratio, 1.5)
            self.assertEqual(dialog.final_blue_balance_ratio, 1.2)
        finally:
            dialog.close()

    def test_initial_white_balance_is_applied_before_reading_slider_seed(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(
            camera, "Slit Lamp", initial_red_balance_ratio=2.5, initial_blue_balance_ratio=3.0
        )
        try:
            self.assertEqual(camera.get_red_balance_ratio(), 2.5)
            self.assertEqual(dialog.final_blue_balance_ratio, 3.0)
        finally:
            dialog.close()

    def test_auto_white_balance_button_updates_sliders_and_final_values(self):
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp")
        try:
            dialog._on_white_balance_clicked()
            self.assertEqual(dialog.final_red_balance_ratio, 1.9)
            self.assertEqual(dialog.final_blue_balance_ratio, 2.2)
            self.assertEqual(dialog.red_balance_slider.value(), 190)
            self.assertEqual(dialog.white_balance_status_label.text(), "Calibrated.")
        finally:
            dialog.close()

    def test_exposure_gain_and_white_balance_status_labels_stay_independent(self):
        """A camera lacking both ExposureAuto/GainAuto and BalanceWhiteAuto
        shows both control blocks at once -- calibrating one must not
        clobber the other's status message. This is why the two blocks
        use separate status labels rather than a shared one.
        """
        from settings import PreviewDialog

        camera = _FakeCalibratableCamera()
        dialog = PreviewDialog(camera, "Slit Lamp")
        try:
            dialog._on_calibrate_clicked()
            self.assertEqual(dialog.calibration_status_label.text(), "Calibrated.")
            self.assertEqual(dialog.white_balance_status_label.text(), "")

            dialog._on_white_balance_clicked()
            self.assertEqual(dialog.white_balance_status_label.text(), "Calibrated.")
            self.assertEqual(dialog.calibration_status_label.text(), "Calibrated.")
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
