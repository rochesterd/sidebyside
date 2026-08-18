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

from config import ConfigError, load_config

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


if __name__ == "__main__":
    unittest.main()
