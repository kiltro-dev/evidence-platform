"""Video intake: sampled FRAMES as images -> timestamped claims.

A video is ingested as K still frames, each described by the image
pipeline; every surviving claim carries ``{t_second + region +
resolution + span}``. :func:`timeline_json` renders the per-frame
roll-up (machine-readable timeline of what was claimed when).

Hard NO-GOs, enforced in code:
- NO audio: there is no transcription path; :func:`transcribe_audio`
  raises :class:`AudioNoGoError` unconditionally. Spoken content is
  invisible to this intake and that is disclosed, not silent.
- NO container decoding in stdlib: callers supply decoded frames
  (PNG/JPEG bytes + timestamps). :func:`extract_frames_ffmpeg` shells
  to an external ffmpeg when present and raises
  :class:`ToolUnavailableError` otherwise — never a half-decoded guess.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import (
    MAX_FRAMES_PER_VIDEO,
    AudioNoGoError,
    IngestedClaim,
    IngestResult,
    ToolUnavailableError,
    validate_anchors,
)
from .image import IMAGE_SCHEMA, IMAGE_SYSTEM, MIN_VISION_TOKENS, resolution_note

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoFrame:
    """One decoded frame: timestamp in seconds + image bytes."""

    t_second: float
    image_bytes: bytes
    name: str = ""  # e.g. "frame_0002.png"; defaults to t-based label


def transcribe_audio(video_bytes: bytes) -> IngestResult:  # noqa: ARG001
    """Audio path: unconditionally refused (frames-only intake by design)."""
    raise AudioNoGoError(
        "video intake is frames-as-images ONLY: no audio track is read, "
        "transcribed, or summarized. Spoken claims are out of scope."
    )


def extract_frames_ffmpeg(
    video_path: str | Path,
    fps: int = 1,
    max_frames: int = MAX_FRAMES_PER_VIDEO,
    timeout_s: float = 120.0,
) -> list[VideoFrame]:
    """Sample up to `max_frames` PNG frames via external ffmpeg.

    Raises :class:`ToolUnavailableError` when ffmpeg is missing (the
    stdlib cannot decode containers — documented limit, not a fallback).
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ToolUnavailableError(
            "ffmpeg not installed: cannot sample frames from video "
            "containers; supply decoded VideoFrame list instead."
        )
    if fps < 1:
        raise ValueError("fps must be >= 1")
    with tempfile.TemporaryDirectory(prefix="atlas-frames-") as tmp:
        pattern = str(Path(tmp) / "frame_%04d.png")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(video_path), "-vf", f"fps={fps}",
               "-frames:v", str(max_frames), pattern]
        try:
            subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout_s, check=True)
        except subprocess.CalledProcessError as exc:
            log.warning("ffmpeg sampling failed: %s", exc.stderr[-500:])
            raise ToolUnavailableError(
                f"ffmpeg could not sample {video_path}: "
                f"{exc.stderr[-200:].strip()}"
            ) from exc
        frames: list[VideoFrame] = []
        for i, path in enumerate(sorted(Path(tmp).glob("frame_*.png"))):
            frames.append(VideoFrame(
                t_second=i / fps,
                image_bytes=path.read_bytes(),
                name=path.name,
            ))
    if not frames:
        raise ToolUnavailableError(f"ffmpeg produced 0 frames for {video_path}")
    return frames


def ingest_frames(
    frames: list[VideoFrame],
    source: str,
    vision=None,
    max_tokens: int = MIN_VISION_TOKENS,
) -> IngestResult:
    """Describe each frame via vision; anchor claims with {t_second}.

    One vision call per frame (callers bound cost with frame count and
    fps). Frames beyond MAX_FRAMES_PER_VIDEO are skipped, audit-visibly.
    Without a vision client: 0 claims + explicit uncertainty (as image).
    """
    uncertainties: list[str] = []
    if len(frames) > MAX_FRAMES_PER_VIDEO:
        uncertainties.append(
            f"frame cap: kept first {MAX_FRAMES_PER_VIDEO} of {len(frames)}"
        )
        frames = frames[:MAX_FRAMES_PER_VIDEO]
    if vision is None or not getattr(vision, "available", False):
        uncertainties.append("no vision client: frames not read (0 claims, "
                             "no guessing)")
        return IngestResult(uncertainties=uncertainties)
    if max_tokens < MIN_VISION_TOKENS:
        uncertainties.append(
            f"max_tokens raised {max_tokens}->{MIN_VISION_TOKENS} "
            "(vision floor)"
        )
        max_tokens = MIN_VISION_TOKENS
    from .image import build_data_url

    draft: list[IngestedClaim] = []
    for frame in frames:
        label = frame.name or f"t={frame.t_second:.1f}s"
        try:
            note = resolution_note(frame.image_bytes)
            data_url = build_data_url(frame.image_bytes)
        except Exception as exc:
            uncertainties.append(f"frame {label}: unreadable ({exc})")
            continue
        try:
            data = vision.complete_vision(
                IMAGE_SYSTEM,
                f"Video {source}, frame at t={frame.t_second:.1f}s "
                f"({label}, {note}). List every checkable factual claim "
                "it shows.",
                [data_url],
                max_tokens=max_tokens,
                json_schema=IMAGE_SCHEMA,
            )
        except Exception as exc:
            log.warning("Vision drafting failed for %s %s: %r",
                        source, label, exc)
            uncertainties.append(f"frame {label}: vision call failed "
                                 f"({type(exc).__name__})")
            continue
        items = data.get("claims", []) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            text = " ".join(text.split())
            span = item.get("span")
            span = span.strip() if isinstance(span, str) else ""
            if span not in text:
                span = ""
            draft.append(IngestedClaim(
                text=text,
                anchor={"t_second": frame.t_second,
                        "region": item.get("region"),
                        "resolution": note, "span": span},
                source=f"{source}@t={frame.t_second:.1f}s",
                actor=f"model:{getattr(vision, 'name', 'vision')}",
            ))
    return validate_anchors(draft, uncertainties)


def timeline_json(result: IngestResult, source: str,
                 duration_s: float | None = None) -> str:
    """Render an ingest result as a per-timestamp timeline (JSON string).

    Groups surviving claims by their ``t_second`` anchor; discards and
    uncertainties ride along so the timeline never overstates coverage.
    """
    buckets: dict[float, list[dict]] = {}
    for claim in result.claims:
        t = claim.anchor.get("t_second", 0.0)
        buckets.setdefault(float(t), []).append({
            "text": claim.text,
            "span": claim.anchor.get("span", ""),
            "region": claim.anchor.get("region", ""),
            "resolution": claim.anchor.get("resolution", ""),
            "actor": claim.actor,
        })
    timeline = {
        "source": source,
        "duration_s": duration_s,
        "audio": "NO-GO: frames only, no audio track read",
        "frames": [
            {"t_second": t, "claims": buckets[t]}
            for t in sorted(buckets)
        ],
        "claim_count": len(result.claims),
        "discarded": [
            {"text": d.text, "code": d.code, "detail": d.detail}
            for d in result.discarded
        ],
        "uncertainties": list(result.uncertainties),
    }
    return json.dumps(timeline, indent=2, ensure_ascii=False)
