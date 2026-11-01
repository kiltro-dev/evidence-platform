"""ffmpeg demux for Atlas Live.

One ffmpeg process (imageio_ffmpeg static binary), two outputs:

- stdout pipe: raw s16le mono 24 kHz PCM. Media clock derives from the
  byte offset: ``t_media = bytes_read / 48000``.
- PNG files: video frames passing ``select='gt(scene,T)'``, with t_media
  recovered from ``showinfo`` ``pts_time`` on stderr (showinfo sits after
  select in the filter chain, so it only sees surviving frames, in order).

Rate cap ("fps=1/N" semantics) is applied in Python as a minimum interval
between kept frames: an ``fps`` filter after ``select`` would duplicate
sparse frames instead of capping them.

Bus envelope (shared with M1b): ``{"type","t_wall","t_media","payload"}``.
Types emitted here: ``ingest.frame{t_media,path}``, ``ingest.eof{}``.
"""

from __future__ import annotations

import glob
import os
import queue
import re
import subprocess
import threading
import time

import imageio_ffmpeg

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH

_SHOWINFO_RE = re.compile(r"pts_time:([0-9.]+)")
_FRAME_GLOB = "frame-*.png"


def ffmpeg_exe() -> str:
    """Static ffmpeg binary path (imageio_ffmpeg)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def bus_event(etype: str, t_media: float | None, payload: dict) -> dict:
    """Shared bus envelope: {"type","t_wall","t_media","payload"}."""
    return {
        "type": etype,
        "t_wall": time.time(),
        "t_media": t_media,
        "payload": payload,
    }


def frame_event(t_media: float, path: str) -> dict:
    """ingest.frame{t_media,path} bus event."""
    return bus_event("ingest.frame", t_media, {"t_media": t_media, "path": path})


def eof_event() -> dict:
    """ingest.eof{} bus event."""
    return bus_event("ingest.eof", None, {})


def probe_streams(path: str) -> dict[str, bool]:
    """Detect audio/video streams via ``ffmpeg -i`` stderr (no ffprobe)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    err = proc.stderr or ""
    return {
        "audio": " Audio:" in err,
        "video": " Video:" in err,
    }


def _decimate(times: list[float], min_interval_s: float) -> list[int]:
    """Indices kept under a minimum-interval (max-rate) policy."""
    kept: list[int] = []
    last: float | None = None
    for i, t in enumerate(times):
        if last is None or t - last >= min_interval_s:
            kept.append(i)
            last = t
    return kept


def extract_pcm(
    path: str,
    rate: int = SAMPLE_RATE,
    timeout_s: float = 300.0,
) -> bytes:
    """One-shot demux of ``path`` to raw mono s16le PCM at ``rate``."""
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            path,
            "-map",
            "0:a?",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg pcm demux failed: {proc.stderr.decode()[:500]}")
    return proc.stdout


def extract_scene_frames(
    path: str,
    out_dir: str,
    scene_threshold: float = 0.3,
    min_interval_s: float = 1.0,
    timeout_s: float = 300.0,
) -> list[dict]:
    """One-shot scene-frame extraction.

    Returns ``[{"t_media": float, "path": str}, ...]`` sorted by t_media,
    decimated to at most one frame per ``min_interval_s``. All detected
    scene frames stay on disk; only kept ones are returned.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not probe_streams(path)["video"]:
        return []
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-i",
            path,
            "-map",
            "0:v?",
            "-vf",
            f"select='gt(scene,{scene_threshold})',showinfo",
            "-vsync",
            "vfr",
            os.path.join(out_dir, "frame-%06d.png"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg scene extract failed: {proc.stderr[-500:]}")
    pts = [float(m.group(1)) for m in _SHOWINFO_RE.finditer(proc.stderr or "")]
    files = sorted(glob.glob(os.path.join(out_dir, _FRAME_GLOB)))
    n = min(len(pts), len(files))
    frames = [{"t_media": pts[i], "path": files[i]} for i in range(n)]
    kept = set(_decimate([f["t_media"] for f in frames], min_interval_s))
    return [f for i, f in enumerate(frames) if i in kept]


class Demuxer:
    """Streaming demux: PCM chunks from stdout + scene frames to disk.

    Usage::

        with Demuxer("clip.mp4", "frames/") as dx:
            for t_media, chunk in dx.iter_audio():
                feed_asr(chunk)
            for ev in dx.poll_frames():  # + final poll after the loop
                bus.publish(ev)
    """

    def __init__(
        self,
        path: str,
        frames_dir: str,
        scene_threshold: float = 0.3,
        min_interval_s: float = 1.0,
        chunk_ms: int = 80,
    ) -> None:
        self.path = path
        self.frames_dir = frames_dir
        self.scene_threshold = scene_threshold
        self.min_interval_s = min_interval_s
        self.chunk_bytes = int(BYTES_PER_SEC * chunk_ms / 1000)
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._pts: list[float] = []  # showinfo pts_time, in frame order
        self._pts_lock = threading.Lock()
        self._emitted = 0  # frame files already mapped (kept or dropped)
        self._last_kept: float | None = None
        self._bytes_read = 0
        self.has_audio = False
        self.has_video = False

    def start(self) -> "Demuxer":
        """Spawn the ffmpeg process (audio pipe + frame files)."""
        if self._proc is not None:
            return self
        streams = probe_streams(self.path)
        self.has_audio = streams["audio"]
        self.has_video = streams["video"]
        os.makedirs(self.frames_dir, exist_ok=True)
        args = [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", self.path]
        if self.has_audio:
            args += [
                "-map",
                "0:a?",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ]
        if self.has_video:
            args += [
                "-map",
                "0:v?",
                "-vf",
                f"select='gt(scene,{self.scene_threshold})',showinfo",
                "-vsync",
                "vfr",
                os.path.join(self.frames_dir, "frame-%06d.png"),
            ]
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE if self.has_audio else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._pump.start()
        return self

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                m = _SHOWINFO_RE.search(line.decode("utf-8", "replace"))
                if m:
                    with self._pts_lock:
                        self._pts.append(float(m.group(1)))
        except (ValueError, OSError):
            pass  # stderr closed under us during shutdown

    def iter_audio(self):
        """Yield ``(t_media_start, bytes)`` until the audio stream ends."""
        if self._proc is None:
            self.start()
        assert self._proc is not None
        stdout = self._proc.stdout
        if stdout is None:  # no audio stream
            return
        while True:
            data = stdout.read(self.chunk_bytes)
            if not data:
                return
            t_media = self._bytes_read / BYTES_PER_SEC
            self._bytes_read += len(data)
            yield t_media, data

    def poll_frames(self) -> list[dict]:
        """Return new ``ingest.frame`` bus events for completed frames."""
        files = sorted(glob.glob(os.path.join(self.frames_dir, _FRAME_GLOB)))
        with self._pts_lock:
            n_pts = len(self._pts)
        mappable = min(len(files), n_pts)
        events: list[dict] = []
        with self._pts_lock:
            pts = list(self._pts)
        while self._emitted < mappable:
            i = self._emitted
            t = pts[i]
            if self._last_kept is None or t - self._last_kept >= self.min_interval_s:
                self._last_kept = t
                events.append(frame_event(t, files[i]))
            self._emitted += 1
        return events

    def wait(self, timeout_s: float = 300.0) -> int:
        """Wait for ffmpeg to exit; join the stderr pump."""
        if self._proc is None:
            return 0
        try:
            code = self._proc.wait(timeout=timeout_s)
        finally:
            if self._pump is not None:
                self._pump.join(timeout=10)
        return code

    def close(self) -> None:
        """Terminate the ffmpeg process if still running."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._pump is not None:
            self._pump.join(timeout=10)
        self._proc = None

    @property
    def audio_seconds(self) -> float:
        """PCM seconds consumed so far (media clock)."""
        return self._bytes_read / BYTES_PER_SEC

    def __enter__(self) -> "Demuxer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
