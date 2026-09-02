"""Records two BaseCamera feeds to a composited MKV (then remuxed to MP4),
alongside a session.json manifest.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

import av

from camera import BaseCamera, Frame
from compositor import draw_timer, side_by_side

logger = logging.getLogger(__name__)

# Frames to actually decode from the head of the remuxed MP4 when verifying
# it before deleting the interim MKV -- enough to prove the leading keyframe
# survived the remux (the failure mode DECISIONS.md's packet-filter entry
# describes: valid headers, decodes zero frames). ~0.5s at 30fps.
_MP4_VERIFY_DECODE_FRAMES = 15
# How many video packets the remuxed MP4 may be short of what was encoded
# before verification fails it -- a couple of frames of muxer edge-effect
# slack, same tolerance test_recorder.py uses decoding the output.
_MP4_VERIFY_PACKET_SLACK = 2


@dataclass
class _CameraTrack:
    name: str
    camera: BaseCamera
    last_frame: Frame | None = None
    last_index: int | None = None
    received: int = 0
    dropped: int = 0
    first_timestamp: float | None = None

    def absorb(self, frame: Frame | None) -> None:
        if frame is None:
            return
        if self.first_timestamp is None:
            self.first_timestamp = frame.timestamp
        if self.last_index is not None and frame.index > self.last_index + 1:
            self.dropped += frame.index - self.last_index - 1
        self.last_index = frame.index
        self.last_frame = frame
        self.received += 1

    @property
    def frame_count(self) -> int:
        # Deliberately `received`, not `last_index + 1`: Frame.index is now
        # the source's own frame sequence number (see BaseCamera._grab) and
        # can legitimately have gaps or not start at 0, so it no longer
        # equals a count of frames actually received. See DECISIONS.md.
        return self.received


class Recorder:
    """Pulls frames from two cameras' queues, composites them side by side,
    and encodes the result to MKV, remuxing to MP4 on stop().
    """

    def __init__(
        self,
        camera_a: BaseCamera,
        camera_b: BaseCamera,
        name_a: str = "camera_a",
        name_b: str = "camera_b",
        output_root: str | Path = "sessions",
        # None means "derive from the two cameras' own resolution at
        # start()" -- side by side, not a 1080p split, whatever that adds
        # up to. See DECISIONS.md's "config-driven recording fps" entry
        # for why this is auto-derived rather than configured, unlike fps.
        width: int | None = None,
        height: int | None = None,
        fps: int = 30,
        codec: str = "libx264",
        crf: int = 23,
        preset: str = "ultrafast",
    ):
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec
        self.crf = crf
        self.preset = preset
        self.output_root = Path(output_root)

        self._track_a = _CameraTrack(name=name_a, camera=camera_a)
        self._track_b = _CameraTrack(name=name_b, camera=camera_b)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._frame_count = 0

        self.session_dir: Path | None = None
        self.mkv_path: Path | None = None
        self.mp4_path: Path | None = None
        # Set in stop(): True once composite.mp4 has been decoded-checked and
        # the interim composite.mkv deleted; False means the MKV was kept
        # because the MP4 didn't verify (see _finalize_outputs()).
        self.mp4_verified = False
        self._container = None
        self._stream = None
        self._start_wall: datetime | None = None
        self._start_monotonic: float | None = None

    def start(self) -> None:
        if self.width is None or self.height is None:
            # Cameras are already live by the time a real caller reaches
            # here (kiosk.py only calls start_recording() from READY,
            # which requires both cameras to have delivered a frame), so
            # .resolution reports the real thing, not a placeholder.
            a_width, a_height = self._track_a.camera.resolution
            b_width, b_height = self._track_b.camera.resolution
            if self.width is None:
                self.width = a_width + b_width
            if self.height is None:
                self.height = max(a_height, b_height)

        self.session_dir = self._make_session_dir()
        self.mkv_path = self.session_dir / "composite.mkv"

        self._container = av.open(str(self.mkv_path), mode="w")
        self._stream = self._container.add_stream(self.codec, rate=self.fps)
        self._stream.width = self.width
        self._stream.height = self.height
        self._stream.pix_fmt = "yuv420p"
        self._stream.codec_context.options = {"crf": str(self.crf), "preset": self.preset}

        self._start_wall = datetime.now(timezone.utc)
        self._start_monotonic = time.monotonic()
        self._frame_count = 0

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                # Loud and early (see CLAUDE.md) rather than proceeding to
                # flush the encoder from this thread while the capture
                # thread might still be mid-encode - that race is what
                # produces a corrupt/short composite.mkv with no warning.
                # Logged in addition to the raise: a caller catching this
                # broadly (kiosk.py's _fail() does, to still record a
                # session summary) would otherwise leave no trace of why.
                logger.error(
                    "session_dir=%s: capture thread did not stop within 10s; "
                    "refusing to finalize the encoder",
                    self.session_dir,
                )
                raise RuntimeError(
                    "Recorder's capture thread did not stop within 10s; "
                    "refusing to finalize the encoder while it may still "
                    "be writing, to avoid a silently corrupt recording."
                )
            self._thread = None

        packets = self._stream.encode(None)
        self._container.mux(packets)
        self._container.close()

        self.mp4_path = self.session_dir / "composite.mp4"
        self._remux_to_mp4(self.mkv_path, self.mp4_path)
        self._finalize_outputs()

        session_info = self._build_session_info()
        with open(self.session_dir / "session.json", "w") as f:
            json.dump(session_info, f, indent=2)
        return session_info

    def _make_session_dir(self) -> Path:
        base = datetime.now().strftime("%Y-%m-%d_%H%M")
        candidate = self.output_root / base
        suffix = 1
        while candidate.exists():
            suffix += 1
            candidate = self.output_root / f"{base}_{suffix}"
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    def _run(self) -> None:
        period = 1.0 / self.fps if self.fps > 0 else 0.0
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if next_tick > now:
                time.sleep(min(next_tick - now, period) if period else 0.001)
                continue
            self._tick()
            next_tick = max(next_tick + period, time.monotonic()) if period else time.monotonic()
        self._drain_remaining()

    def _tick(self) -> None:
        self._track_a.absorb(self._drain_latest(self._track_a.camera))
        self._track_b.absorb(self._drain_latest(self._track_b.camera))
        if self._track_a.last_frame is None or self._track_b.last_frame is None:
            return
        self._encode_pair(self._track_a.last_frame, self._track_b.last_frame)

    @staticmethod
    def _drain_latest(camera: BaseCamera) -> Frame | None:
        """Pop every frame currently queued and return only the newest one."""
        latest = None
        while True:
            frame = camera.read(timeout=0)
            if frame is None:
                break
            latest = frame
        return latest

    def _drain_remaining(self) -> None:
        """After a stop request, flush whatever was already sitting in each
        camera's queue at that moment, so it isn't silently discarded.

        Deliberately a single bounded pass, not a loop until both queues go
        empty: cameras may keep running past this session's end (e.g. for a
        live preview between recordings), continuously refilling their
        queues. If encoding ever falls behind the camera's frame rate even
        slightly, "both queues empty" is a moving target that's never
        actually reached, and this method would never return - which then
        causes the caller's thread.join() to time out and proceed to flush
        the encoder from another thread while this one is still encoding.
        See DECISIONS.md.
        """
        frame_a = self._drain_latest(self._track_a.camera)
        frame_b = self._drain_latest(self._track_b.camera)
        self._track_a.absorb(frame_a)
        self._track_b.absorb(frame_b)
        if self._track_a.last_frame is not None and self._track_b.last_frame is not None:
            self._encode_pair(self._track_a.last_frame, self._track_b.last_frame)

    def _encode_pair(self, frame_a: Frame, frame_b: Frame) -> None:
        composite = side_by_side(frame_a.image, frame_b.image, out_size=(self.width, self.height))
        elapsed = time.monotonic() - self._start_monotonic
        draw_timer(composite, f"{elapsed:7.2f}s", position=(10, 10))

        video_frame = av.VideoFrame.from_ndarray(composite, format="bgr24").reformat(format="yuv420p")
        video_frame.pts = self._frame_count
        packets = self._stream.encode(video_frame)
        self._container.mux(packets)
        self._frame_count += 1

    def _finalize_outputs(self) -> None:
        """The MKV exists only as the interruption-safe copy during capture
        (see CLAUDE.md: an interrupted MKV is still playable, an interrupted
        MP4 is lost). Once stop() has produced composite.mp4 and it checks
        out, the MKV is redundant -- the remux is a stream copy, so the MP4
        holds byte-identical video -- and keeping it just doubles what every
        session costs on disk. Delete it, but only after verifying the MP4,
        and never delete both: if verification fails, the MKV stays as the
        recoverable copy and session.json records mp4_verified=false.
        """
        if self._mp4_verifies(self.mp4_path):
            self.mp4_verified = True
            self.mkv_path.unlink()
            logger.info(
                "session_dir=%s: composite.mp4 verified (%d frames); removed interim composite.mkv",
                self.session_dir,
                self._frame_count,
            )
        else:
            self.mp4_verified = False
            logger.error(
                "session_dir=%s: composite.mp4 did not verify; keeping composite.mkv as the recoverable copy",
                self.session_dir,
            )

    def _mp4_verifies(self, mp4_path: Path) -> bool:
        """True if the remuxed MP4 is safe to treat as the sole copy: its
        first frames actually decode (catches the dropped-leading-keyframe
        failure in DECISIONS.md's packet-filter entry -- valid headers, zero
        decodable frames) and it carries essentially all the video packets
        that were encoded (catches gross truncation). Both checks are cheap
        -- a partial decode and a demux-only packet count, not a full
        decode pass a waiting student would feel.
        """
        if self._frame_count == 0:
            return False  # nothing was recorded -- no basis to verify, so don't drop the MKV
        try:
            with av.open(str(mp4_path)) as container:
                stream = container.streams.video[0]
                packets = sum(1 for packet in container.demux(stream) if packet.size)
            with av.open(str(mp4_path)) as container:
                stream = container.streams.video[0]
                decoded = sum(1 for _ in islice(container.decode(stream), _MP4_VERIFY_DECODE_FRAMES))
        except Exception as exc:
            logger.error("session_dir=%s: composite.mp4 verification errored: %s", self.session_dir, exc)
            return False

        if decoded == 0:
            logger.error(
                "session_dir=%s: composite.mp4 decoded 0 frames from its first %d packets",
                self.session_dir,
                _MP4_VERIFY_DECODE_FRAMES,
            )
            return False
        if packets < self._frame_count - _MP4_VERIFY_PACKET_SLACK:
            logger.error(
                "session_dir=%s: composite.mp4 has %d video packets, expected ~%d",
                self.session_dir,
                packets,
                self._frame_count,
            )
            return False
        return True

    @staticmethod
    def _remux_to_mp4(mkv_path: Path, mp4_path: Path) -> None:
        input_ = av.open(str(mkv_path))
        output = av.open(str(mp4_path), mode="w")
        try:
            in_stream = input_.streams.video[0]
            out_stream = output.add_stream_from_template(in_stream)
            for packet in input_.demux(in_stream):
                # Skip only empty flush packets. Filtering on `packet.dts is
                # None` instead drops the leading keyframe here and produces
                # an MP4 that decodes zero frames.
                if packet.size == 0:
                    continue
                packet.stream = out_stream
                output.mux(packet)
        finally:
            output.close()
            input_.close()

    def _build_session_info(self) -> dict:
        return {
            "session_start_utc": self._start_wall.isoformat(),
            # composite.mkv is absent once the MP4 verified and it was
            # deleted; present alongside the MP4 when verification failed.
            "output_files": (
                {"mp4": self.mp4_path.name}
                if self.mp4_verified
                else {"mkv": self.mkv_path.name, "mp4": self.mp4_path.name}
            ),
            "mp4_verified": self.mp4_verified,
            "composite": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "frame_count": self._frame_count,
            },
            "cameras": {
                key: {
                    "name": track.name,
                    "resolution": list(track.camera.resolution),
                    "start_timestamp": track.first_timestamp,
                    "frame_count": track.frame_count,
                    "dropped_frames": track.dropped,
                }
                for key, track in (("camera_a", self._track_a), ("camera_b", self._track_b))
            },
        }
