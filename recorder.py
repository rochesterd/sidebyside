"""Records two BaseCamera feeds to a composited MKV (then remuxed to MP4),
alongside a session.json manifest.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import av

from camera import BaseCamera, Frame
from compositor import draw_timer, side_by_side


@dataclass
class _CameraTrack:
    name: str
    camera: BaseCamera
    last_frame: Frame | None = None
    last_index: int | None = None
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

    @property
    def frame_count(self) -> int:
        return 0 if self.last_index is None else self.last_index + 1


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
        width: int = 2560,  # see DECISIONS.md: wide canvas, not a 1080p split
        height: int = 1080,
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
        self._container = None
        self._stream = None
        self._start_wall: datetime | None = None
        self._start_monotonic: float | None = None

    def start(self) -> None:
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
            "output_files": {
                "mkv": self.mkv_path.name,
                "mp4": self.mp4_path.name,
            },
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
