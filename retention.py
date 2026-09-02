"""Optional cleanup of old recording sessions, so an unattended kiosk
doesn't fill its disk over a semester of use.

Opt-in: driven entirely by config.json's `retention` section (see
config.RetentionConfig). Absent -> this module is never called. app.py runs
one pass at startup, before any recording -- never on the recording timer,
and never against a session that's still being written.

Two passes, in order:

1. Age sweep (always): delete every *completed* session older than
   `max_age_days`, regardless of how much disk is free.
2. Capacity pass (only if `min_free_gb`/`protect_days` are set, and only if
   free space is below `min_free_gb`): delete the oldest completed sessions
   -- oldest first, but never one younger than `protect_days` -- until free
   space recovers. If it can't recover without crossing `protect_days`, it
   stops and leaves the rest to kiosk.py's disk preflight (Start disabled,
   loud status). That's a human's call, not this module's.

Never deleted, in either pass:
- a folder that isn't a well-formed `YYYY-MM-DD_HHMM[_N]` session dir
  (something a technician put there by hand),
- a session with no `session.json` (in progress, or a failed session a
  technician should look at),
- the single newest completed session, whatever its age.

Every deletion is logged. See DECISIONS.md's "Automatic cleanup of old
recordings" entry.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from config import RetentionConfig

logger = logging.getLogger(__name__)

_GB = 1024**3

# YYYY-MM-DD_HHMM, with the optional _2/_3/... minute-collision suffix
# Recorder._make_session_dir() adds.
_SESSION_DIR_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(?:_\d+)?$")


@dataclass
class RetentionResult:
    deleted: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    free_bytes_after: int = 0
    # False only when the capacity pass ran and still couldn't get free
    # space up to min_free_gb without deleting a protected (too-recent)
    # session -- the caller warns, and the disk preflight takes it from there.
    capacity_target_met: bool = True

    @property
    def freed_gb(self) -> float:
        return self.freed_bytes / _GB


def _session_start(dir_name: str) -> datetime | None:
    m = _SESSION_DIR_RE.match(dir_name)
    if m is None:
        return None
    year, month, day, hour, minute = (int(part) for part in m.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _completed_sessions(sessions_dir: Path) -> list[tuple[Path, datetime]]:
    """(dir, recording-start time) for every well-formed, completed session
    under `sessions_dir`, sorted oldest first."""
    found: list[tuple[Path, datetime]] = []
    for child in sorted(sessions_dir.iterdir()):
        if not child.is_dir():
            continue
        start = _session_start(child.name)
        if start is None:
            continue  # not one of ours
        if not (child / "session.json").is_file():
            continue  # in progress, or a failed session -- leave it for a technician
        found.append((child, start))
    found.sort(key=lambda pair: pair[1])
    return found


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _delete(session_dir: Path, result: RetentionResult) -> None:
    size = _dir_size(session_dir)
    shutil.rmtree(session_dir)
    result.deleted.append(session_dir.name)
    result.freed_bytes += size
    logger.info("retention: deleted %s (%.2f GB)", session_dir.name, size / _GB)


def apply_retention(
    sessions_dir: Path,
    policy: RetentionConfig,
    *,
    now: datetime | None = None,
    disk_usage_fn: Callable[[str], object] = shutil.disk_usage,
) -> RetentionResult:
    """Run the age sweep, then (if configured and needed) the capacity
    pass. Safe to call when `sessions_dir` doesn't exist yet. Never raises
    on an individual session it can't size or delete cleanly -- logs and
    moves on -- so a cleanup problem can't stop the kiosk from starting.
    """
    now = now or datetime.now()
    result = RetentionResult()

    if not sessions_dir.is_dir():
        return result

    initial_free = disk_usage_fn(str(sessions_dir)).free
    sessions = _completed_sessions(sessions_dir)

    if sessions:
        newest = sessions[-1][0]
        eligible = [(d, t) for d, t in sessions if d != newest]

        age_cutoff = now - timedelta(days=policy.max_age_days)
        remaining: list[tuple[Path, datetime]] = []
        for session_dir, start in eligible:
            if start < age_cutoff:
                _safe_delete(session_dir, result)
            else:
                remaining.append((session_dir, start))

        if policy.min_free_gb is not None:
            need = policy.min_free_gb * _GB
            protect_cutoff = now - timedelta(days=policy.protect_days)
            for session_dir, start in remaining:  # oldest first
                if initial_free + result.freed_bytes >= need:
                    break
                if start >= protect_cutoff:
                    break  # everything left is too recent to touch
                _safe_delete(session_dir, result)
            result.capacity_target_met = initial_free + result.freed_bytes >= need

    result.free_bytes_after = initial_free + result.freed_bytes
    return result


def _safe_delete(session_dir: Path, result: RetentionResult) -> None:
    try:
        _delete(session_dir, result)
    except OSError as exc:
        logger.warning("retention: could not delete %s: %s", session_dir.name, exc)
