"""Simulated stream source for Atlas Live.

Replays a media file in real time (``ffmpeg -re``) so the pipeline can be
exercised without a live feed. Everything produced here is labeled
``"simulated-stream"`` — never mistake it for a genuine live source.
"""

from __future__ import annotations

import subprocess

from .demux import ffmpeg_exe

SOURCE_LABEL = "simulated-stream"


class SimulatedStream:
    """Real-time file replay piped as MPEG-TS on stdout.

    Usage::

        with SimulatedStream("clip.mp4") as sim:
            assert sim.label == "simulated-stream"
            for chunk in sim.iter_chunks():
                demux_stdin.write(chunk)
    """

    def __init__(
        self,
        path: str,
        chunk_bytes: int = 188 * 100,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.path = path
        self.chunk_bytes = chunk_bytes
        self.extra_args = tuple(extra_args)
        self.label = SOURCE_LABEL
        self._proc: subprocess.Popen | None = None

    @property
    def argv(self) -> list[str]:
        """Full ffmpeg command line (always contains ``-re``)."""
        return [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-re",
            "-i",
            self.path,
            *self.extra_args,
            "-f",
            "mpegts",
            "-",
        ]

    def open(self) -> "SimulatedStream":
        """Spawn the paced replay process."""
        if self._proc is None:
            self._proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        return self

    @property
    def stdout(self):
        """Raw stdout pipe (bytes). Open first."""
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("SimulatedStream not open; call open() first")
        return self._proc.stdout

    def read(self, n: int = -1) -> bytes:
        """Read up to ``n`` bytes (paced, real-time)."""
        if n is None or n < 0:
            return self.stdout.read()
        return self.stdout.read(n)

    def iter_chunks(self):
        """Yield paced byte chunks until the replay ends."""
        self.open()
        while True:
            data = self.stdout.read(self.chunk_bytes)
            if not data:
                return
            yield data

    def wait(self, timeout_s: float = 600.0) -> int:
        """Wait for the replay to finish."""
        if self._proc is None:
            return 0
        return self._proc.wait(timeout=timeout_s)

    def close(self) -> None:
        """Terminate the replay if still running."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def __enter__(self) -> "SimulatedStream":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()


def restream_to(
    src: str,
    dst: str,
    extra_args: tuple[str, ...] = ("-c", "copy"),
) -> tuple[subprocess.Popen, str]:
    """Replay ``src`` in real time to ``dst`` (file, pipe, or URL).

    Returns ``(proc, SOURCE_LABEL)`` so the caller always carries the
    simulated-source label alongside the process handle.
    """
    argv = [
        ffmpeg_exe(),
        "-hide_banner",
        "-nostats",
        "-re",
        "-i",
        src,
        *extra_args,
        dst,
    ]
    proc = subprocess.Popen(argv, stderr=subprocess.DEVNULL)
    return proc, SOURCE_LABEL
