"""Real-microphone capture for Atlas Live: live mic -> PCM 24 kHz mono.

One ffmpeg process (no audio libraries, no new dependencies) reads a REAL
capture device (PulseAudio or ALSA, autodetected) and emits raw s16le mono
24 kHz PCM on stdout — the exact encoding ``live.oido`` expects. The media
clock derives from the byte offset (``t_media = bytes_read / 48000``), the
same contract as ``live.ingest.demux.Demuxer.iter_audio``.

Source labeling: this module emits ``"live-microphone"``, never
``"simulated-stream"`` (see ``live/ingest/simstream.py``). Test doubles
(lavfi sine, file replay) are separate, explicitly-labeled constructors —
autodetection only ever returns a REAL device and raises
:class:`NoMicrophoneError` when there is none (CI/headless), so a test
environment can never mistake synthetic audio for a microphone.

Wiring into ``live/run_live.py`` is documented (not implemented) in
``live/mic/README_MIC.md``: ``MicCapture.chunks()`` is a drop-in async
PCM source for ``live.oido.asr_ws.transcribe_stream``.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

from live.ingest.demux import (
    BYTES_PER_SEC,
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    ffmpeg_exe,
)

SOURCE_LABEL = "live-microphone"
FIRST_BYTE_TIMEOUT_S = 15.0
PROBE_SECONDS = 0.4  # live open+read probe per candidate input


class NoMicrophoneError(RuntimeError):
    """No usable real microphone found (honest degradation for CI/headless)."""


@dataclass(frozen=True)
class MicInput:
    """One ffmpeg input that yields live (or double) audio.

    ``kind`` is ``"pulse"``, ``"alsa"`` (real devices), ``"lavfi"`` or
    ``"file"`` (explicit test doubles, ``real=False``).
    """

    kind: str
    ffmpeg_input_args: tuple[str, ...]  # includes -f/-i, placed before outputs
    device: str
    binary: str
    label: str
    real: bool = True


def candidate_binaries() -> list[str]:
    """ffmpeg binaries to try: imageio static first, then system ffmpeg."""
    bins = [ffmpeg_exe()]
    sys_ff = shutil.which("ffmpeg")
    if sys_ff and os.path.abspath(sys_ff) != os.path.abspath(bins[0]):
        bins.append(sys_ff)
    return bins


def _has_demuxer(binary: str, name: str) -> bool:
    """True when ``binary`` can demux the ``name`` input device.

    Parsed from ``ffmpeg -devices`` (the device line must carry a ``D``
    flag). ``-h demuxer=<name>`` always exits 0 — even for unknown
    formats — so its exit code cannot be used (F2-8 fix: it wrongly
    reported ``pulse`` as present in the static build).
    """
    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-devices"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    for line in (proc.stdout or "").splitlines():
        m = re.match(r"\s*([.DE]{1,2})\s+([A-Za-z0-9_,]+)", line)
        if m and "D" in m.group(1) and name in m.group(2).split(","):
            return True
    return False


def _alsa_capture_nodes() -> list[str]:
    """ALSA PCM capture nodes (empty on headless/CI hosts)."""
    return sorted(glob.glob("/dev/snd/pcmC*D*c"))


def _pulse_server_hint() -> str | None:
    """Pulse server location hint, or None when no server is visible."""
    env = os.environ.get("PULSE_SERVER")
    if env:
        return f"PULSE_SERVER={env}"
    uid = os.getuid() if hasattr(os, "getuid") else None
    candidates = []
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        candidates.append(os.path.join(xdg, "pulse", "native"))
    if uid is not None:
        candidates.append(f"/run/user/{uid}/pulse/native")
    for sock in candidates:
        if os.path.exists(sock):
            return sock
    return None


def _live_probe(binary: str, input_args: list[str]) -> tuple[bool, str]:
    """Open the input and read a fraction of a second of audio.

    Returns ``(ok, stderr_tail)``. This is the only check that proves a
    device really captures; static hints (nodes, sockets) are not enough.
    """
    try:
        proc = subprocess.run(
            [
                binary,
                "-hide_banner",
                "-nostats",
                *input_args,
                "-t",
                str(PROBE_SECONDS),
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    err = proc.stderr or ""
    if proc.returncode != 0:
        return False, err.strip()[-300:]
    return True, ""


def describe_host() -> dict:
    """Diagnostics: binaries, demuxers, devices, pulse hints (no probing)."""
    binaries = []
    for b in candidate_binaries():
        binaries.append(
            {
                "path": b,
                "pulse_demuxer": _has_demuxer(b, "pulse"),
                "alsa_demuxer": _has_demuxer(b, "alsa"),
            }
        )
    return {
        "binaries": binaries,
        "alsa_capture_nodes": _alsa_capture_nodes(),
        "pulse_server_hint": _pulse_server_hint(),
    }


def detect_microphone(
    device: str | None = None,
    prefer: str = "pulse",
    binaries: list[str] | None = None,
) -> MicInput:
    """Autodetect a REAL microphone input (pulse/alsa only, never lavfi).

    ``prefer`` orders the kinds tried first (``"pulse"`` or ``"alsa"``).
    Every candidate is live-probed (open + short read); the first that
    really captures wins. Raises :class:`NoMicrophoneError` with the full
    attempt log when nothing captures — the honest CI/headless path.
    """
    kinds = ["pulse", "alsa"] if prefer == "pulse" else ["alsa", "pulse"]
    attempts: list[str] = []
    for binary in binaries or candidate_binaries():
        for kind in kinds:
            if kind == "pulse":
                if not _has_demuxer(binary, "pulse"):
                    attempts.append(f"{binary}: no pulse demuxer in this build")
                    continue
                hint = _pulse_server_hint()
                if hint is None:
                    attempts.append(f"{binary}: pulse demuxer ok, no server visible")
                    continue
                dev = device or "default"
                ok, err = _live_probe(binary, ["-f", "pulse", "-i", dev])
                if ok:
                    return MicInput(
                        kind="pulse",
                        ffmpeg_input_args=("-f", "pulse", "-i", dev),
                        device=dev,
                        binary=binary,
                        label=SOURCE_LABEL,
                    )
                attempts.append(f"{binary}: pulse {dev!r} probe failed: {err}")
            else:  # alsa
                if not _has_demuxer(binary, "alsa"):
                    attempts.append(f"{binary}: no alsa demuxer in this build")
                    continue
                nodes = _alsa_capture_nodes()
                if not nodes and device is None:
                    attempts.append(f"{binary}: alsa demuxer ok, no capture nodes")
                    continue
                dev = device or "default"
                ok, err = _live_probe(binary, ["-f", "alsa", "-i", dev])
                if ok:
                    return MicInput(
                        kind="alsa",
                        ffmpeg_input_args=("-f", "alsa", "-i", dev),
                        device=dev,
                        binary=binary,
                        label=SOURCE_LABEL,
                    )
                attempts.append(f"{binary}: alsa {dev!r} probe failed: {err}")
    raise NoMicrophoneError(
        "no usable microphone: every pulse/alsa candidate failed.\n"
        + "\n".join(f"  - {a}" for a in attempts)
        + "\nFix: plug in a mic and expose it via PulseAudio (Linux desktop / "
          "WSL2 PulseServer) or ALSA (arecord -l must list a capture device); "
          "on CI/headless this error is expected — skip live-mic tests."
    )


def lavfi_test_input(
    src: str = "sine=frequency=440",
    binary: str | None = None,
) -> MicInput:
    """Explicit synthetic double (labeled, never returned by autodetect)."""
    return MicInput(
        kind="lavfi",
        ffmpeg_input_args=("-f", "lavfi", "-i", src),
        device=f"lavfi:{src}",
        binary=binary or ffmpeg_exe(),
        label="test-double:lavfi",
        real=False,
    )


def file_test_input(path: str, binary: str | None = None) -> MicInput:
    """Explicit file-replay double (labeled, never returned by autodetect)."""
    return MicInput(
        kind="file",
        ffmpeg_input_args=("-i", path),
        device=path,
        binary=binary or ffmpeg_exe(),
        label="test-double:file",
        real=False,
    )


DRIVER_STUBBED_LABEL = "driver-stubbed-file-fed"


def custom_ffmpeg_input(
    ffmpeg_input_args: tuple[str, ...] | list[str],
    device: str,
    label: str,
    binary: str | None = None,
    kind: str = "custom",
) -> MicInput:
    """Explicit ARBITRARY ffmpeg input (labeled, never returned by autodetect).

    ``ffmpeg_input_args`` are placed verbatim before the PCM output flags
    in :meth:`MicCapture.argv`, so any source ffmpeg understands works:
    ``("-re", "-i", path)`` for paced file replay, ``("-f", "lavfi", "-i",
    src)``, stream URLs, etc. Always ``real=False``: callers must pick a
    label that says what was stubbed.
    """
    return MicInput(
        kind=kind,
        ffmpeg_input_args=tuple(ffmpeg_input_args),
        device=device,
        binary=binary or ffmpeg_exe(),
        label=label,
        real=False,
    )


def driver_stubbed_file_input(path: str, binary: str | None = None) -> MicInput:
    """Paced file feed for live E2E (``-re``): everything live but the driver.

    Labeled ``"driver-stubbed-file-fed"``: ffmpeg demux + PCM 24 kHz +
    ``iter_audio``/``chunks`` + realtime pacing are the real path; only the
    kernel capture driver (impossible on mic-less hosts, see live/F2_MIC2.md)
    is stubbed by a file. Never returned by autodetect.
    """
    return custom_ffmpeg_input(
        ("-re", "-i", path),
        device=path,
        label=DRIVER_STUBBED_LABEL,
        binary=binary,
        kind="file",
    )


class MicCapture:
    """Live microphone -> PCM chunks (async, t_media clock).

    Usage::

        cap = MicCapture().start()          # autodetects a real mic
        async for t_media, pcm in cap.iter_audio():
            ...  # t_media = media seconds since capture start
        cap.close()

    ``chunks()`` yields bare PCM bytes and plugs directly into
    ``live.oido.asr_ws.transcribe_stream`` / ``PCMFeed``.
    """

    def __init__(
        self,
        mic_input: MicInput | None = None,
        chunk_ms: int = 80,
        duration: float | None = None,
    ) -> None:
        self.mic_input = mic_input  # None -> autodetect in start()
        self.chunk_bytes = int(BYTES_PER_SEC * chunk_ms / 1000)
        self.duration = duration
        self.source_label = SOURCE_LABEL
        self.t0_wall: float | None = None
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._err: bytearray = bytearray()
        self._bytes_read = 0

    @classmethod
    def for_test_lavfi(
        cls, src: str = "sine=frequency=440", chunk_ms: int = 80,
        duration: float | None = None,
    ) -> "MicCapture":
        """Offline double: synthetic lavfi tone as the PCM source."""
        return cls(lavfi_test_input(src), chunk_ms=chunk_ms, duration=duration)

    @classmethod
    def for_test_file(
        cls, path: str, chunk_ms: int = 80,
        duration: float | None = None,
    ) -> "MicCapture":
        """Offline double: replay a media file as the PCM source."""
        return cls(file_test_input(path), chunk_ms=chunk_ms, duration=duration)

    @classmethod
    def for_custom_input(
        cls, mic_input: MicInput, chunk_ms: int = 80,
        duration: float | None = None,
    ) -> "MicCapture":
        """Capture from an explicit arbitrary ffmpeg input (never autodetect).

        Pair with :func:`custom_ffmpeg_input` /
        :func:`driver_stubbed_file_input`; the capture inherits the input's
        honest (non-real) label in :attr:`source_label`.
        """
        if mic_input.real:
            raise ValueError("for_custom_input needs an explicit double input")
        return cls(mic_input, chunk_ms=chunk_ms, duration=duration)

    @property
    def argv(self) -> list[str]:
        """Full ffmpeg command line (PCM s16le mono 24 kHz on stdout)."""
        mi = self.mic_input
        assert mi is not None, "call start() first (autodetect runs there)"
        args = [mi.binary, "-hide_banner", "-nostats", *mi.ffmpeg_input_args]
        args += ["-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE)]
        if self.duration is not None:
            args += ["-t", str(self.duration)]
        args += ["-f", "s16le", "-"]
        return args

    def start(self) -> "MicCapture":
        """Detect (if needed) and spawn the ffmpeg capture process."""
        if self._proc is not None:
            return self
        if self.mic_input is None:
            self.mic_input = detect_microphone()
        self.source_label = (
            SOURCE_LABEL if self.mic_input.real else self.mic_input.label
        )
        self._proc = subprocess.Popen(
            self.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._pump.start()
        return self

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for chunk in iter(lambda: self._proc.stderr.read(4096), b""):
                self._err.extend(chunk)
                del self._err[:-8192]  # keep the tail only
        except (ValueError, OSError):
            pass

    def _read_exact(self, n: int) -> bytes:
        """Blocking read of up to ``n`` bytes (loops over short pipe reads)."""
        assert self._proc is not None and self._proc.stdout is not None
        out = bytearray()
        while len(out) < n:
            piece = self._proc.stdout.read(n - len(out))
            if not piece:
                break
            out.extend(piece)
        return bytes(out)

    async def iter_audio(self):
        """Yield ``(t_media_start, pcm_bytes)`` until the capture ends."""
        if self._proc is None:
            self.start()
        while True:
            if self.t0_wall is None:
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(self._read_exact, self.chunk_bytes),
                        timeout=FIRST_BYTE_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        "no PCM from microphone in "
                        f"{FIRST_BYTE_TIMEOUT_S:.0f}s "
                        f"(rc={self._proc.poll() if self._proc else '?'} "
                        f"err={self.stderr_tail[-300:]!r})"
                    )
            else:
                data = await asyncio.to_thread(self._read_exact, self.chunk_bytes)
            if not data:
                return
            t_media = self._bytes_read / BYTES_PER_SEC
            if self.t0_wall is None:
                # First byte ~= media 0: back off this chunk's duration.
                self.t0_wall = time.time() - len(data) / BYTES_PER_SEC
            self._bytes_read += len(data)
            yield t_media, data

    async def chunks(self):
        """Yield bare PCM bytes (drop-in for ``transcribe_stream``)."""
        async for _, data in self.iter_audio():
            yield data

    @property
    def media_seconds(self) -> float:
        """PCM seconds emitted so far (media clock)."""
        return self._bytes_read / BYTES_PER_SEC

    @property
    def stderr_tail(self) -> str:
        """Last capture stderr bytes (for failure diagnosis)."""
        return bytes(self._err[-2000:]).decode("utf-8", "replace")

    def wait(self, timeout_s: float = 60.0) -> int | None:
        """Wait for ffmpeg to exit; join the stderr pump."""
        if self._proc is None:
            return None
        try:
            code = self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None
        finally:
            if self._pump is not None:
                self._pump.join(timeout=5)
        return code

    def close(self) -> None:
        """Terminate the capture if still running (idempotent)."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._pump is not None:
            self._pump.join(timeout=5)
        self._proc = None

    def __enter__(self) -> "MicCapture":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
