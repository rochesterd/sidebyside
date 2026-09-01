"""Tests for config.load_config() -- the config.json schema and its error
messages, in isolation from app.py (which only wires the result into
camera construction; see ROADMAP.md's "Device compatibility & camera setup
system" entry and DECISIONS.md for why this file exists).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigError, load_config, resolve_default_config_path, resolve_default_sessions_dir

VALID = {
    "instruments": {
        "slit_lamp": {"kind": "ids", "serial": "111", "label": "Slit Lamp"},
        "bio": {"kind": "ids", "serial": "222", "label": "BIO"},
    },
    "third_person": {"kind": "uvc", "vid_pid": "32E4:9310", "friendly_name": "HD USB Camera"},
}


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "config.json"

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_config_loads(self):
        self._write(VALID)
        cfg = load_config(self.path)

        self.assertEqual(set(cfg.instruments), {"slit_lamp", "bio"})
        self.assertEqual(cfg.instruments["slit_lamp"].serial, "111")
        self.assertEqual(cfg.instruments["slit_lamp"].label, "Slit Lamp")
        self.assertEqual(cfg.third_person.vid_pid, "32E4:9310")
        self.assertEqual(cfg.third_person.friendly_name, "HD USB Camera")

    def test_more_than_two_instruments_is_not_a_fixed_pair(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["indirect_scope"] = {"kind": "ids", "serial": "333", "label": "Indirect Scope"}
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(set(cfg.instruments), {"slit_lamp", "bio", "indirect_scope"})

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.path)
        self.assertIn("config.example.json", str(ctx.exception))

    def test_malformed_json_raises(self):
        self.path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_empty_instruments_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"] = {}
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_missing_instrument_key_raises(self):
        data = json.loads(json.dumps(VALID))
        del data["instruments"]["slit_lamp"]["serial"]
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_wrong_typed_instrument_key_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["serial"] = 111
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_unsupported_instrument_kind_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["kind"] = "uvc"
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_net2860_instrument_loads_with_no_serial(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"] = {"kind": "net2860", "label": "BIO"}
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["bio"].kind, "net2860")
        self.assertIsNone(cfg.instruments["bio"].serial)
        self.assertEqual(cfg.instruments["bio"].label, "BIO")

    def test_net2860_instrument_rejects_serial(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"] = {"kind": "net2860", "label": "BIO", "serial": "222"}
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_net2860_instrument_rejects_calibration_fields(self):
        for field, value in [
            ("exposure_time_us", 1000.0),
            ("gain", 2.0),
            ("red_balance_ratio", 1.5),
            ("blue_balance_ratio", 1.5),
        ]:
            with self.subTest(field=field):
                data = json.loads(json.dumps(VALID))
                data["instruments"]["bio"] = {"kind": "net2860", "label": "BIO", field: value}
                self._write(data)

                with self.assertRaises(ConfigError):
                    load_config(self.path)

    def test_net2860_instrument_still_requires_label(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"] = {"kind": "net2860"}
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_missing_third_person_raises(self):
        data = json.loads(json.dumps(VALID))
        del data["third_person"]
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_wrong_typed_third_person_vid_pid_raises(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["vid_pid"] = 12345
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_empty_third_person_vid_pid_raises(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["vid_pid"] = ""
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_badly_shaped_third_person_vid_pid_raises(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["vid_pid"] = "not-a-vid-pid"
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_lowercase_third_person_vid_pid_is_normalized_uppercase(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["vid_pid"] = "32e4:9310"
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.third_person.vid_pid, "32E4:9310")

    def test_missing_third_person_friendly_name_raises(self):
        data = json.loads(json.dumps(VALID))
        del data["third_person"]["friendly_name"]
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_wrong_typed_third_person_friendly_name_raises(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["friendly_name"] = 123
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_unsupported_third_person_kind_raises(self):
        data = json.loads(json.dumps(VALID))
        data["third_person"]["kind"] = "ids"
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_unknown_extra_keys_are_ignored(self):
        data = json.loads(json.dumps(VALID))
        data["notes"] = "hand-added field, not part of the schema"
        data["instruments"]["slit_lamp"]["vid_pid"] = "32E4:9310"
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["slit_lamp"].serial, "111")

    def test_missing_exposure_gain_default_to_none(self):
        self._write(VALID)
        cfg = load_config(self.path)

        self.assertIsNone(cfg.instruments["slit_lamp"].exposure_time_us)
        self.assertIsNone(cfg.instruments["slit_lamp"].gain)

    def test_exposure_gain_are_parsed_when_present(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["exposure_time_us"] = 15000
        data["instruments"]["slit_lamp"]["gain"] = 2.5
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["slit_lamp"].exposure_time_us, 15000.0)
        self.assertEqual(cfg.instruments["slit_lamp"].gain, 2.5)

    def test_zero_exposure_time_us_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["exposure_time_us"] = 0
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_negative_gain_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["gain"] = -1.0
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_wrong_typed_exposure_time_us_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["exposure_time_us"] = "fast"
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_missing_red_blue_balance_ratio_default_to_none(self):
        self._write(VALID)
        cfg = load_config(self.path)

        self.assertIsNone(cfg.instruments["slit_lamp"].red_balance_ratio)
        self.assertIsNone(cfg.instruments["slit_lamp"].blue_balance_ratio)

    def test_red_blue_balance_ratio_are_parsed_when_both_present(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["red_balance_ratio"] = 1.8
        data["instruments"]["slit_lamp"]["blue_balance_ratio"] = 2.1
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["slit_lamp"].red_balance_ratio, 1.8)
        self.assertEqual(cfg.instruments["slit_lamp"].blue_balance_ratio, 2.1)

    def test_only_red_balance_ratio_present_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["red_balance_ratio"] = 1.8
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_only_blue_balance_ratio_present_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["blue_balance_ratio"] = 2.1
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_negative_red_balance_ratio_raises(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["slit_lamp"]["red_balance_ratio"] = -1.0
        data["instruments"]["slit_lamp"]["blue_balance_ratio"] = 2.1
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_missing_orientation_defaults_to_none(self):
        self._write(VALID)
        cfg = load_config(self.path)

        self.assertIsNone(cfg.instruments["bio"].orientation)

    def test_orientation_is_parsed_when_present(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"]["orientation"] = "flip_vertical"
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["bio"].orientation, "flip_vertical")

    def test_orientation_none_string_is_allowed_and_kept(self):
        # "none" is meaningful: it overrides a device-model preset that
        # would otherwise transform (device_presets.py), so it must survive
        # as "none", not collapse to Python None.
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"]["orientation"] = "none"
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.instruments["bio"].orientation, "none")

    def test_unsupported_orientation_raises(self):
        for bad in ("upside_down", "rotate_90", 180, "", True):
            with self.subTest(bad=bad):
                data = json.loads(json.dumps(VALID))
                data["instruments"]["bio"]["orientation"] = bad
                self._write(data)

                with self.assertRaises(ConfigError):
                    load_config(self.path)

    def test_net2860_instrument_rejects_orientation(self):
        data = json.loads(json.dumps(VALID))
        data["instruments"]["bio"] = {"kind": "net2860", "label": "BIO", "orientation": "flip_vertical"}
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_missing_sessions_dir_defaults_to_none(self):
        self._write(VALID)
        cfg = load_config(self.path)

        self.assertIsNone(cfg.sessions_dir)

    def test_sessions_dir_is_parsed_when_present(self):
        data = json.loads(json.dumps(VALID))
        data["sessions_dir"] = "D:\\recordings"
        self._write(data)

        cfg = load_config(self.path)

        self.assertEqual(cfg.sessions_dir, Path("D:\\recordings"))

    def test_empty_sessions_dir_raises(self):
        data = json.loads(json.dumps(VALID))
        data["sessions_dir"] = ""
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_wrong_typed_sessions_dir_raises(self):
        data = json.loads(json.dumps(VALID))
        data["sessions_dir"] = 123
        self._write(data)

        with self.assertRaises(ConfigError):
            load_config(self.path)


class DefaultPathsTest(unittest.TestCase):
    """resolve_default_config_path()/resolve_default_sessions_dir() split
    on sys.frozen -- a frozen install (see ROADMAP.md's "Distribute a
    frozen-exe installer" entry) has no repo checkout to be relative to.
    """

    def test_dev_mode_config_path_is_relative_to_cwd(self):
        with patch("sys.frozen", False, create=True):
            self.assertEqual(resolve_default_config_path(), Path("config.json"))

    def test_frozen_config_path_is_under_programdata(self):
        with patch("sys.frozen", True, create=True), patch.dict(
            "os.environ", {"ProgramData": "C:\\ProgramData"}
        ):
            self.assertEqual(
                resolve_default_config_path(), Path("C:\\ProgramData") / "sidebyside" / "config.json"
            )

    def test_dev_mode_sessions_dir_is_relative_to_cwd(self):
        with patch("sys.frozen", False, create=True):
            self.assertEqual(resolve_default_sessions_dir(), Path("sessions"))

    def test_frozen_sessions_dir_is_under_public_documents(self):
        with patch("sys.frozen", True, create=True), patch.dict("os.environ", {"PUBLIC": "C:\\Users\\Public"}):
            self.assertEqual(
                resolve_default_sessions_dir(),
                Path("C:\\Users\\Public") / "Documents" / "sidebyside" / "sessions",
            )


if __name__ == "__main__":
    unittest.main()
