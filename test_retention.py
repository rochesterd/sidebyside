"""Tests for retention.apply_retention -- the age sweep and the low-disk
capacity pass. Real temp session folders; the disk-usage reading and the
per-session size are the only things stubbed, so the folder-walking and
deletion logic run for real.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import RetentionConfig
from retention import _GB, apply_retention

NOW = datetime(2026, 9, 1, 12, 0)


def _fake_disk(free_gb: float):
    return lambda _path: SimpleNamespace(free=int(free_gb * _GB), total=0, used=0)


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions = Path(self._tmp.name) / "sessions"
        self.sessions.mkdir()

    def _session(self, days_ago: float, *, complete: bool = True, name: str | None = None) -> Path:
        start = NOW - timedelta(days=days_ago)
        name = name or start.strftime("%Y-%m-%d_%H%M")
        d = self.sessions / name
        d.mkdir()
        (d / "composite.mp4").write_bytes(b"x" * 2048)
        if complete:
            (d / "session.json").write_text("{}", encoding="utf-8")
        return d

    def _run(self, policy: RetentionConfig, free_gb: float = 500.0):
        return apply_retention(self.sessions, policy, now=NOW, disk_usage_fn=_fake_disk(free_gb))

    # --- age sweep ----------------------------------------------------

    def test_age_sweep_deletes_only_sessions_older_than_max_age(self):
        old = self._session(40)
        mid = self._session(20)
        recent = self._session(2)

        result = self._run(RetentionConfig(max_age_days=30))

        self.assertFalse(old.exists())
        self.assertTrue(mid.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(result.deleted, [old.name])

    def test_age_sweep_never_deletes_the_newest_completed_session(self):
        older = self._session(100)
        old = self._session(90)
        newest = self._session(80)  # still older than max_age, but it's the newest

        result = self._run(RetentionConfig(max_age_days=30))

        self.assertFalse(older.exists())
        self.assertFalse(old.exists())
        self.assertTrue(newest.exists())
        self.assertEqual(sorted(result.deleted), sorted([older.name, old.name]))

    def test_incomplete_session_without_session_json_is_left_for_a_technician(self):
        failed = self._session(50, complete=False)
        self._session(1)  # a newer complete one, so `failed` isn't "newest"

        result = self._run(RetentionConfig(max_age_days=30))

        self.assertTrue(failed.exists())
        self.assertEqual(result.deleted, [])

    def test_a_folder_that_is_not_a_session_dir_is_ignored(self):
        stray = self.sessions / "technician-notes"
        stray.mkdir()
        (stray / "readme.txt").write_text("keep me", encoding="utf-8")
        self._session(1)

        self._run(RetentionConfig(max_age_days=30))

        self.assertTrue(stray.exists())

    def test_minute_collision_suffix_dirs_are_recognised(self):
        old = self._session(40, name="2026-07-01_1000_2")
        self._session(1)

        result = self._run(RetentionConfig(max_age_days=30))

        self.assertFalse(old.exists())
        self.assertEqual(result.deleted, [old.name])

    # --- capacity pass ---------------------------------------------------

    def test_capacity_pass_deletes_oldest_first_below_the_age_floor_when_disk_is_low(self):
        s20 = self._session(20)
        s15 = self._session(15)
        s10 = self._session(10)
        newest = self._session(2)

        # max_age_days=30 -> age sweep deletes nothing. Free space 5 GB is
        # below min_free_gb=10; each session "is" 2 GB.
        with patch("retention._dir_size", return_value=2 * _GB):
            result = self._run(
                RetentionConfig(max_age_days=30, min_free_gb=10, protect_days=7), free_gb=5.0
            )

        self.assertFalse(s20.exists())
        self.assertFalse(s15.exists())
        self.assertFalse(s10.exists())  # 5 + 2 + 2 + 2 = 11 GB >= 10 reached here
        self.assertTrue(newest.exists())
        self.assertEqual(result.deleted, [s20.name, s15.name, s10.name])
        self.assertTrue(result.capacity_target_met)

    def test_capacity_pass_stops_at_protect_days_and_reports_target_not_met(self):
        s10 = self._session(10)
        s8 = self._session(8)
        s5 = self._session(5)  # inside protect_days=7 -> untouchable
        self._session(1)

        with patch("retention._dir_size", return_value=2 * _GB):
            result = self._run(
                RetentionConfig(max_age_days=30, min_free_gb=20, protect_days=7), free_gb=5.0
            )

        self.assertFalse(s10.exists())
        self.assertFalse(s8.exists())
        self.assertTrue(s5.exists())
        self.assertEqual(result.deleted, [s10.name, s8.name])
        self.assertFalse(result.capacity_target_met)

    def test_capacity_pass_does_nothing_when_free_space_is_fine(self):
        s20 = self._session(20)
        self._session(1)

        with patch("retention._dir_size", return_value=2 * _GB):
            result = self._run(
                RetentionConfig(max_age_days=30, min_free_gb=10, protect_days=7), free_gb=50.0
            )

        self.assertTrue(s20.exists())
        self.assertEqual(result.deleted, [])
        self.assertTrue(result.capacity_target_met)

    def test_no_capacity_pass_when_only_age_is_configured(self):
        recent = self._session(10)
        self._session(1)

        # Disk critically low, but no min_free_gb/protect_days -> capacity
        # pass never runs.
        with patch("retention._dir_size", return_value=2 * _GB):
            result = self._run(RetentionConfig(max_age_days=30), free_gb=0.1)

        self.assertTrue(recent.exists())
        self.assertEqual(result.deleted, [])

    def test_missing_sessions_dir_is_a_no_op(self):
        result = apply_retention(
            self.sessions / "does-not-exist",
            RetentionConfig(max_age_days=30),
            now=NOW,
            disk_usage_fn=_fake_disk(1.0),
        )
        self.assertEqual(result.deleted, [])
        self.assertEqual(result.freed_bytes, 0)


if __name__ == "__main__":
    unittest.main()
