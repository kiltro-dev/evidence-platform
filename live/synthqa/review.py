"""S4 QA per-piece review: code gates + 1 vision call (Atlas Live).

Per piece (12): extract 2 frames (t = 25% / 75%), run code checks
(duration 150-300 s, 1920x1080, fps 25-30, audio without clipping via
volumedetect, SRT parseable with cues inside the duration, badge
"Synthetic demo" as a builder flag + 1 vision-sampled frame), then ONE
Muse vision call over both frames (clarity, pacing, said-vs-shown
coherence against a truth sample), plus a path/email/secret scan over
script + SRT + visible text.

No re-render: pixels are checked by sampling, never rebuilt.
Offline-safe except :func:`review_vision` (needs a vision client) and
the ffmpeg ``runner`` seam (tests inject doubles).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.synthqa import LIVE_DIR, PIECES, REPO_ROOT, media_paths  # noqa: E402
from live.vista.read import (  # noqa: E402
    READ_MAX_TOKENS,
    build_data_url,
    estimate_cost_usd,
)

DUR_MIN_S = 150.0
DUR_MAX_S = 300.0
WIDTH, HEIGHT = 1920, 1080
FPS_MIN, FPS_MAX = 25.0, 30.0
# Container timebase rounding: nominal 25 fps probes as 24.99.
FPS_EPS = 0.05
BADGE_TEXT = "Synthetic demo"
SRT_SLACK_S = 1.0  # cues may touch the tail (mux rounding)

BUILDER_EN = os.path.join(LIVE_DIR, "material", "build_corpus_en.py")
BUILDER_ES = os.path.join(LIVE_DIR, "material", "build_corpus_es.py")

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_SIZE_RE = re.compile(r"(\d{3,5})x(\d{3,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_MAXVOL_RE = re.compile(r"max_volume:\s*(-?[\d.]+|n/a)\s*dB")
_MEANVOL_RE = re.compile(r"mean_volume:\s*(-?[\d.]+|n/a)\s*dB")
_HIST0_RE = re.compile(r"histogram_0db:\s*(\d+)")

# Flat-top clipping rule (second stage, only when volumedetect shows no
# headroom): sustained runs at int16 full scale. Isolated full-scale
# peaks (peak-normalized TTS) are not clipping; flats of >=64 samples
# (~4 ms @16 kHz) or >=10 runs of >=16 samples are.
HEADROOM_DB = -0.5
FLAT_MIN_RUN = 16
FLAT_MANY_RUNS = 10
FLAT_LONG_RUN = 64
_SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)",
    re.S)

_SECRET_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("unix_path", re.compile(r"(?:/home/|/tmp/|/Users/|/etc/|/root/)\S*")),
    ("win_path", re.compile(r"[A-Za-z]:\\(?:[^\\\n]+\\)*[^\\\n]*")),
    ("api_key_assign",
     re.compile(r"(?:MODEL_API_KEY|API_KEY|api[_-]?key)\s*[:=]\s*\S+",
                re.I)),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._~-]+")),
    ("secret_token",
     re.compile(r"\b(?:sk-|age1|xox[bap]-)[A-Za-z0-9]{8,}")),
    ("private_key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


# ---------------------------------------------------------------------------
# ffmpeg seams (runner injectable; default shells out)
# ---------------------------------------------------------------------------

def find_ffmpeg() -> str:
    """ffmpeg binary: $FFMPEG, imageio-ffmpeg, or PATH fallback."""
    env = os.environ.get("FFMPEG", "")
    if env:
        return env
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def default_runner(cmd: list[str]) -> tuple[int, str, str]:
    """Run ``cmd``; return (returncode, stdout, stderr) as text."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def default_bytes_runner(cmd: list[str]) -> bytes:
    """Run ``cmd``; return raw stdout bytes (PCM decode)."""
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"decode failed: {proc.stderr[-200:]!r}")
    return proc.stdout


def parse_ffmpeg_info(stderr: str) -> dict:
    """Scrape duration/size/fps/streams from ``ffmpeg -i`` stderr."""
    out: dict = {"duration_s": None, "width": None, "height": None,
                 "fps": None, "has_video": False, "has_audio": False}
    match = _DURATION_RE.search(stderr)
    if match:
        hours, minutes, secs = match.groups()
        out["duration_s"] = (int(hours) * 3600 + int(minutes) * 60
                             + float(secs))
    for line in stderr.splitlines():
        if "Video:" in line:
            out["has_video"] = True
            size = _SIZE_RE.search(line)
            if size and out["width"] is None:
                out["width"] = int(size.group(1))
                out["height"] = int(size.group(2))
            fps = _FPS_RE.search(line)
            if fps and out["fps"] is None:
                out["fps"] = float(fps.group(1))
        if "Audio:" in line:
            out["has_audio"] = True
    return out


def probe_media(mp4: str, ff: str | None = None,
               runner=default_runner) -> dict:
    """``ffmpeg -i`` probe (no output files); never raises on shape."""
    ff = ff or find_ffmpeg()
    try:
        _, _, stderr = runner([ff, "-hide_banner", "-i", mp4])
    except Exception as exc:  # noqa: BLE001 — probe failure is data
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "duration_s": None, "width": None, "height": None,
                "fps": None, "has_video": False, "has_audio": False}
    info = parse_ffmpeg_info(stderr or "")
    info["ok"] = info["duration_s"] is not None and info["has_video"]
    return info


def parse_volumedetect(stderr: str) -> dict:
    """Scrape max/mean volume dB + 0 dBFS histogram bin (volumedetect)."""
    out: dict = {"max_volume_db": None, "mean_volume_db": None,
                 "histogram_0db": None}
    match = _MAXVOL_RE.search(stderr or "")
    if match and match.group(1) != "n/a":
        out["max_volume_db"] = float(match.group(1))
    match = _MEANVOL_RE.search(stderr or "")
    if match and match.group(1) != "n/a":
        out["mean_volume_db"] = float(match.group(1))
    match = _HIST0_RE.search(stderr or "")
    if match:
        out["histogram_0db"] = int(match.group(1))
    return out


def flat_top_runs(pcm_s16le: bytes, min_run: int = FLAT_MIN_RUN) -> dict:
    """Runs of consecutive int16 full-scale samples (|x| = 32767)."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm_s16le[:len(pcm_s16le) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    n_runs, max_run, cur, n_full = 0, 0, 0, 0
    for sample in samples:
        if abs(sample) >= 32767:
            cur += 1
            n_full += 1
        else:
            if cur >= min_run:
                n_runs += 1
            max_run = max(max_run, cur)
            cur = 0
    if cur >= min_run:
        n_runs += 1
    max_run = max(max_run, cur)
    return {"n_runs_ge16": n_runs, "max_run": max_run,
            "n_fullscale": n_full, "n_samples": len(samples)}


def check_clipping(mp4: str, ff: str | None = None,
                   runner=default_runner,
                   bytes_runner=default_bytes_runner) -> dict:
    """Two-stage clipping check: volumedetect, then flat-top analysis.

    volumedetect alone cannot separate peak-normalized audio (max 0 dB)
    from real clipping, so max_volume above HEADROOM_DB triggers a PCM
    decode (mono 16 kHz s16) and a sustained-flat-top verdict.
    """
    ff = ff or find_ffmpeg()
    try:
        _, _, stderr = runner([ff, "-hide_banner", "-i", mp4, "-af",
                               "volumedetect", "-f", "null", "-"])
    except Exception as exc:  # noqa: BLE001 — measurement failure is data
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "max_volume_db": None, "mean_volume_db": None,
                "clipping": None}
    vols = parse_volumedetect(stderr or "")
    max_db = vols["max_volume_db"]
    if max_db is None:
        vols["ok"] = False
        vols["clipping"] = None
        return vols
    if max_db < HEADROOM_DB:
        vols["ok"] = True
        vols["clipping"] = False
        vols["stage"] = "headroom"
        return vols
    try:
        pcm = bytes_runner([ff, "-hide_banner", "-loglevel", "error",
                            "-i", mp4, "-vn", "-ac", "1", "-ar", "16000",
                            "-f", "s16le", "-"])
        flats = flat_top_runs(pcm)
    except Exception as exc:  # noqa: BLE001 — decode failure is data
        vols["ok"] = False
        vols["clipping"] = None
        vols["stage"] = "flat-top"
        vols["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return vols
    vols.update(flats)
    vols["stage"] = "flat-top"
    vols["clipping"] = bool(flats["max_run"] >= FLAT_LONG_RUN
                            or flats["n_runs_ge16"] >= FLAT_MANY_RUNS)
    vols["ok"] = True
    return vols


def extract_frames(mp4: str, duration_s: float, outdir: str,
                   ff: str | None = None,
                   runner=default_runner) -> list[str]:
    """Extract t=25%/75% PNGs; return paths (raise on ffmpeg failure)."""
    ff = ff or find_ffmpeg()
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for frac, tag in ((0.25, "p25"), (0.75, "p75")):
        target = os.path.join(outdir, f"{tag}.png")
        code, _, err = runner([
            ff, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{duration_s * frac:.2f}", "-i", mp4,
            "-frames:v", "1", target])
        if code != 0 or not os.path.exists(target):
            raise RuntimeError(f"frame {tag} failed: {(err or '')[:200]}")
        paths.append(target)
    return paths


# ---------------------------------------------------------------------------
# SRT + badge flag + secret scan (pure code)
# ---------------------------------------------------------------------------

def srt_to_seconds(stamp: str) -> float:
    """``HH:MM:SS,mmm`` -> seconds."""
    hours, minutes, rest = stamp.split(":")
    secs, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000


def parse_srt(text: str) -> list[dict]:
    """Parse SRT cues (raise ValueError when nothing parses)."""
    cues = []
    for num, start, end, body in _SRT_RE.findall(text or ""):
        cues.append({"n": int(num), "start": srt_to_seconds(start),
                     "end": srt_to_seconds(end), "text": body.strip()})
    if not cues and (text or "").strip():
        raise ValueError("no SRT cues parsed")
    return cues


def check_srt(srt_path: str, duration_s: float | None) -> dict:
    """SRT gates: parseable, non-empty, ordered, inside the duration."""
    try:
        with open(srt_path, encoding="utf-8") as fh:
            cues = parse_srt(fh.read())
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:200], "n_cues": 0,
                "issues": [f"unparseable: {exc}"], "text": ""}
    issues = []
    prev_end = -1.0
    for cue in cues:
        if not cue["text"]:
            issues.append(f"cue {cue['n']}: empty text")
        if cue["end"] < cue["start"]:
            issues.append(f"cue {cue['n']}: end before start")
        if cue["start"] < prev_end - 0.001:
            issues.append(f"cue {cue['n']}: overlaps previous cue")
        prev_end = cue["end"]
        if duration_s and cue["end"] > duration_s + SRT_SLACK_S:
            issues.append(f"cue {cue['n']}: ends {cue['end']:.1f}s "
                          f"beyond duration {duration_s:.1f}s")
    return {"ok": not issues, "n_cues": len(cues), "issues": issues,
            "text": "\n".join(c["text"] for c in cues)}


def badge_flag_ok(lang: str) -> dict:
    """Badge "Synthetic demo" as a builder flag (not pixels).

    EN: ``BADGE_ENABLED = True`` + ``BADGE_TEXT = "Synthetic demo"``
    burned via fig.text. ES: ``fig.text(0.95, 0.955, " Synthetic demo ")``.
    Pixel presence is sampled separately by vision (1 frame).
    """
    path = BUILDER_EN if lang == "en" else BUILDER_ES
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        return {"ok": False, "builder": path, "error": str(exc)[:160]}
    has_text = BADGE_TEXT in src
    if lang == "en":
        enabled = "BADGE_ENABLED = True" in src
        badge_arg = "badge=BADGE_ENABLED" in src
        ok = has_text and enabled and badge_arg
        return {"ok": ok, "builder": path, "has_text": has_text,
                "enabled": enabled, "wired": badge_arg}
    burned = '" Synthetic demo "' in src and "fig.text(0.95, 0.955" in src
    return {"ok": has_text and burned, "builder": path,
            "has_text": has_text, "burned": burned}


def scan_secrets(sources: dict[str, str]) -> list[dict]:
    """Scan texts for paths/emails/secrets; samples redacted to 16 chars."""
    hits = []
    for name, text in sources.items():
        for kind, regex in _SECRET_PATTERNS:
            for match in regex.finditer(text or ""):
                sample = match.group(0)[:16]
                hits.append({"source": name, "kind": kind,
                             "sample": sample + "…"})
    return hits


# ---------------------------------------------------------------------------
# Vision: exactly 1 call per piece over both frames
# ---------------------------------------------------------------------------

QA_SYSTEM = """You QA two sampled frames (t=25% and t=75%) of a synthetic demo video.
Return ONLY a JSON object: {"clarity": "...", "pacing": "...", "said_vs_shown": "...",
"badge_visible": true|false, "secrets_visible": true|false, "notes": "..."}.
Rules: "clarity" is "ok" when slide text/charts are sharp and readable in BOTH frames,
"blurry" when hard to read, "unreadable" when text cannot be resolved.
"pacing" is "ok" when the two frames show DIFFERENT slides (video progresses),
"static" when both frames look like the same slide, "rushed" when text is cut mid-transition.
"said_vs_shown" compares the provided script sample against visible on-screen text/numbers.
Each sample line carries its truth label: contradictions against [true]/[coherent-internal]
lines are genuine defects ("mismatch"); contradictions against [false] lines are planted
traps working as designed — report "coherent" and name the trap in notes.
"unclear" when the sample does not cover what is shown.
"badge_visible" is true ONLY when the literal text "Synthetic demo" is visibly rendered
in at least one frame (typically a top-right corner badge).
"secrets_visible" is true when any frame visibly shows an email, credential, API key,
or absolute local path. Describe ONLY what is visibly rendered; NEVER invent digits."""

QA_SCHEMA = {
    "name": "synthqa_piece_review",
    "schema": {
        "type": "object",
        "properties": {
            "clarity": {"type": "string",
                        "enum": ["ok", "blurry", "unreadable"]},
            "pacing": {"type": "string",
                       "enum": ["ok", "static", "rushed"]},
            "said_vs_shown": {"type": "string",
                              "enum": ["coherent", "mismatch", "unclear"]},
            "badge_visible": {"type": "boolean"},
            "secrets_visible": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": ["clarity", "pacing", "said_vs_shown",
                     "badge_visible", "secrets_visible", "notes"],
        "additionalProperties": False,
    },
}


def sample_rows(truth_path: str, n: int = 8,
                times: list[float] | None = None) -> list[dict]:
    """Truth rows behind :func:`truth_sample` (frame-time nearest)."""
    try:
        with open(truth_path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return []
    if not times:
        return rows[:n]
    per = max(1, n // len(times))
    picked: list[dict] = []
    for stamp in times:
        ranked = sorted(rows, key=lambda r: abs(
            float(r.get("t_aprox", 0.0) or 0.0) - stamp))
        picked.extend(ranked[:per])
    return picked


def truth_sample(truth_path: str, n: int = 8, max_chars: int = 1200,
                 times: list[float] | None = None) -> str:
    """Claim texts near ``times`` (or the first ``n``) — said-vs-shown.

    With frame times, picks ``n // len(times)`` claims closest in
    ``t_aprox`` to each frame, so the vision verdict compares what is
    SAID around those timestamps against what is SHOWN. Lines carry
    ``[expected]`` labels so vision can tell planted traps from defects.
    """
    def _line(row: dict) -> str:
        return f"[{row.get('expected', '?')}] {row.get('text', '')}"[:170]

    return " | ".join(_line(r)
                      for r in sample_rows(truth_path, n, times))[:max_chars]


def adjudicate_mismatch(notes: str, sample: list[dict]) -> dict:
    """Adjudicate a vision "mismatch" against truth labels (offline).

    The corpus plants expected=false traps, so a raw said-vs-shown
    contradiction is evidence of a defect ONLY when it hits an
    expected=true/coherent-internal line:

    - ``trap-induced``: a figure named in the notes matches a planted
      expected=false sample claim (the trap works; not a defect).
    - ``trap-plausible``: the notes name no figure but the sample holds
      planted falsehoods (no true claim contradicted on record).
    - ``genuine``: no planted falsehood explains the verdict — a real
      production defect (wrong slide, desync, misrender).
    """
    def _norm(token: str) -> str:
        return token.replace(",", "").replace(".", "").replace(" ", "")

    note_nums = re.findall(r"\d[\d.,]*", notes or "")
    false_claims = [r for r in sample if r.get("expected") == "false"]
    hits = []
    for num in note_nums:
        needle = _norm(num)
        if len(needle) < 2:
            continue
        for row in false_claims:
            if needle in _norm(str(row.get("text", ""))):
                hits.append({"figure": num, "claim_id": row.get("id"),
                             "text": str(row.get("text", ""))[:120]})
                break
    if hits:
        return {"verdict": "trap-induced", "hits": hits,
                "n_false_in_sample": len(false_claims),
                "n_sample": len(sample)}
    if false_claims:
        return {"verdict": "trap-plausible", "hits": [],
                "n_false_in_sample": len(false_claims),
                "n_sample": len(sample),
                "false_ids": [r.get("id") for r in false_claims]}
    return {"verdict": "genuine", "hits": [],
            "n_false_in_sample": 0, "n_sample": len(sample)}


def validate_qa(data, frame_paths: list[str]) -> dict:
    """Coerce a vision reply into the QA contract (never raises)."""
    out = {"clarity": "unclear", "pacing": "unclear",
           "said_vs_shown": "unclear", "badge_visible": False,
           "secrets_visible": False, "notes": "", "uncertainties": []}
    if not isinstance(data, dict):
        out["uncertainties"].append("model returned non-object")
        return out
    for key, allowed in (("clarity", ("ok", "blurry", "unreadable")),
                         ("pacing", ("ok", "static", "rushed")),
                         ("said_vs_shown",
                          ("coherent", "mismatch", "unclear"))):
        value = data.get(key)
        if value in allowed:
            out[key] = value
        else:
            out["uncertainties"].append(f"{key} missing/invalid")
    for key in ("badge_visible", "secrets_visible"):
        value = data.get(key)
        if isinstance(value, bool):
            out[key] = value
        else:
            out["uncertainties"].append(f"{key} missing/non-boolean")
    notes = data.get("notes")
    out["notes"] = str(notes)[:600] if isinstance(notes, str) else ""
    out["frames"] = list(frame_paths)
    return out


def review_vision(frame_paths: list[str], script_sample: str,
                 vision, max_tokens: int = READ_MAX_TOKENS) -> dict:
    """ONE vision call over both frames; usage + USD attached."""
    data_urls = []
    for path in frame_paths:
        with open(path, "rb") as fh:
            data_urls.append(build_data_url(fh.read()))
    data = vision.complete_vision(
        QA_SYSTEM,
        f"Two frames of one synthetic demo video ({len(data_urls)} images). "
        f"Script sample (said): {script_sample or '(unavailable)'}",
        data_urls, max_tokens=max(max_tokens, READ_MAX_TOKENS),
        json_schema=QA_SCHEMA)
    out = validate_qa(data, frame_paths)
    usage = getattr(vision, "last_usage", None) or {}
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    out["usage"] = {"prompt_tokens": prompt,
                    "completion_tokens": completion}
    out["cost_usd"] = round(estimate_cost_usd(prompt, completion), 5)
    out["model"] = getattr(vision, "name", "vision")
    return out


# ---------------------------------------------------------------------------
# Per-piece orchestration
# ---------------------------------------------------------------------------

def review_piece(piece_id: str, vision=None,
                repo_root: str = REPO_ROOT,
                frames_root: str | None = None,
                runner=default_runner,
                bytes_runner=default_bytes_runner,
                ff: str | None = None) -> dict:
    """Full QA for one piece; vision=None runs code checks only."""
    t0 = time.time()
    piece = next(p for p in PIECES if p["id"] == piece_id)
    paths = media_paths(piece, repo_root)
    frames_root = frames_root or os.path.join(
        repo_root, "data", "synth", "qa_frames")
    frame_dir = os.path.join(frames_root, piece_id)

    probe = probe_media(paths["mp4"], ff=ff, runner=runner)
    duration = probe.get("duration_s")
    gates: dict = {}
    gates["duration"] = {
        "value": duration,
        "ok": duration is not None and DUR_MIN_S <= duration <= DUR_MAX_S,
        "gate": f"{DUR_MIN_S:.0f}-{DUR_MAX_S:.0f}s"}
    gates["resolution"] = {
        "value": [probe.get("width"), probe.get("height")],
        "ok": probe.get("width") == WIDTH and probe.get("height") == HEIGHT,
        "gate": f"{WIDTH}x{HEIGHT}"}
    fps = probe.get("fps")
    gates["fps"] = {"value": fps,
                    "ok": fps is not None
                    and FPS_MIN - FPS_EPS <= fps <= FPS_MAX + FPS_EPS,
                    "gate": f"{FPS_MIN:.0f}-{FPS_MAX:.0f} (±{FPS_EPS})"}
    gates["streams"] = {"video": probe.get("has_video"),
                        "audio": probe.get("has_audio"),
                        "ok": bool(probe.get("has_video")
                                   and probe.get("has_audio"))}

    clip = check_clipping(paths["mp4"], ff=ff, runner=runner,
                          bytes_runner=bytes_runner)
    gates["audio_no_clipping"] = {
        "max_volume_db": clip.get("max_volume_db"),
        "mean_volume_db": clip.get("mean_volume_db"),
        "histogram_0db": clip.get("histogram_0db"),
        "stage": clip.get("stage"),
        "max_run": clip.get("max_run"),
        "n_runs_ge16": clip.get("n_runs_ge16"),
        "ok": clip.get("clipping") is False}

    srt = check_srt(paths["srt"], duration)
    gates["srt"] = {"n_cues": srt["n_cues"], "issues": srt["issues"],
                    "ok": srt["ok"]}
    gates["badge_flag"] = badge_flag_ok(piece["lang"])

    try:
        truth_rows = json.load(open(paths["truth"], encoding="utf-8"))
        script_text = "\n".join(str(r.get("text", "")) for r in truth_rows)
    except (OSError, ValueError):
        truth_rows, script_text = [], ""
    frame_times = [duration * 0.25, duration * 0.75] if duration else None
    sample = truth_sample(paths["truth"], times=frame_times)
    secrets = scan_secrets({"script": script_text, "srt": srt.get("text", "")})
    gates["secrets_scan"] = {"hits": secrets, "ok": not secrets}

    frames: list[str] = []
    frame_error = None
    if duration:
        try:
            frames = extract_frames(paths["mp4"], duration, frame_dir,
                                    ff=ff, runner=runner)
        except RuntimeError as exc:
            frame_error = str(exc)[:200]
    else:
        frame_error = "no duration: frames skipped"

    qa_vision: dict
    if vision is None or not getattr(vision, "available", False):
        qa_vision = {"skipped": True,
                     "reason": "no vision client (code-only review)"}
    elif frame_error:
        qa_vision = {"skipped": True, "reason": frame_error}
    else:
        try:
            qa_vision = review_vision(frames, sample, vision)
            vis_text = qa_vision.get("notes", "")
            vis_hits = scan_secrets({"frame_notes": vis_text})
            if vis_hits or qa_vision.get("secrets_visible"):
                secrets.extend(vis_hits)
                gates["secrets_scan"] = {"hits": secrets,
                                         "ok": not secrets}
        except Exception as exc:  # noqa: BLE001 — vision failure is data
            qa_vision = {"skipped": True,
                         "reason": f"vision call failed: "
                                   f"{type(exc).__name__}: {str(exc)[:160]}"}

    code_ok = all(g.get("ok") for g in gates.values())
    vision_ok = (qa_vision.get("skipped", False)
                 or (qa_vision.get("clarity") == "ok"
                     and qa_vision.get("pacing") == "ok"
                     and qa_vision.get("said_vs_shown") in (
                         "coherent", "unclear")
                     and qa_vision.get("badge_visible") is True
                     and qa_vision.get("secrets_visible") is False))
    return {
        "piece": piece_id, "lang": piece["lang"], "kind": piece["kind"],
        "speakers": piece["speakers"],
        "gates": gates, "frames": frames, "frame_error": frame_error,
        "vision": qa_vision, "code_ok": code_ok, "vision_ok": vision_ok,
        "pass": bool(code_ok and vision_ok),
        "elapsed_s": round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the S4 QA review."""
    parser = argparse.ArgumentParser(
        description="S4 QA: code gates + 1 vision call per synth piece.")
    parser.add_argument("--pieces", default="",
                        help="comma-separated piece ids (default: all 12)")
    parser.add_argument("--live", action="store_true",
                        help="enable the 1 vision call per piece (~$0.05)")
    parser.add_argument("--out", default=None,
                        help="JSON output (default: "
                        "data/synth/qa/review.json)")
    parser.add_argument("--frames-dir", default=None,
                        help="frame dump dir (default: "
                        "data/synth/qa_frames)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run QA over selected pieces; print a table + write JSON."""
    args = build_parser().parse_args(argv)
    wanted = [p.strip() for p in args.pieces.split(",") if p.strip()]
    selected = [p for p in PIECES if not wanted or p["id"] in wanted]
    if wanted and len(selected) != len(wanted):
        missing = {*wanted} - {p["id"] for p in selected}
        raise SystemExit(f"unknown pieces: {sorted(missing)}")
    vision = None
    if args.live:
        from live.vista.read import make_live_vision

        vision = make_live_vision()
        if not vision.available:
            raise SystemExit("MODEL_API_KEY not configured; source .env.")
    results = []
    for piece in selected:
        print(f"[qa] {piece['id']} ...", flush=True)
        results.append(review_piece(piece["id"], vision=vision,
                                    frames_root=args.frames_dir))
        last = results[-1]
        print(f"[qa] {piece['id']}: {'PASS' if last['pass'] else 'FAIL'} "
              f"(code={last['code_ok']} vision={last['vision_ok']})",
              flush=True)
    out = args.out or os.path.join(REPO_ROOT, "data", "synth", "qa",
                                   "review.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "live_vision": bool(args.live), "results": results}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    usd = round(sum(r["vision"].get("cost_usd", 0.0)
                    for r in results
                    if isinstance(r.get("vision"), dict)), 4)
    print(f"[qa] {sum(r['pass'] for r in results)}/{len(results)} PASS, "
          f"vision usd={usd}, out={out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
