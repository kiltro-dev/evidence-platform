"""S4 synth-QA: review + benchmark + report for the 12-piece synth corpus.

Shared checkout, branch main. Piece registry only — the three stages
live in :mod:`live.synthqa.review`, :mod:`live.synthqa.bench` and
:mod:`live.synthqa.report`. Offline-safe: importing this module touches
no network, no ffmpeg, no secrets.
"""

from __future__ import annotations

import os

LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(LIVE_DIR)

# 12 pieces: 6 EN (SYNTH_EN) + 6 ES (SYNTH_ES). ``file`` is the on-disk
# stem under data/synth/<lang>/; ``truth`` the builder truth filename.
PIECES: list[dict] = [
    # -- EN (1080p30, voices A ljspeech / B norman) ---------------------
    {"id": "k1_econ", "lang": "en", "kind": "keynote",
     "speakers": 1, "file": "k1_econ",
     "truth": "live/material/truth_en/k1_econ.json",
     "wer": "data/synth/en/wer_reports/k1_econ.json"},
    {"id": "k2_clim", "lang": "en", "kind": "keynote",
     "speakers": 1, "file": "k2_clim",
     "truth": "live/material/truth_en/k2_clim.json",
     "wer": "data/synth/en/wer_reports/k2_clim.json"},
    {"id": "p1_panel", "lang": "en", "kind": "panel",
     "speakers": 2, "file": "p1_panel",
     "truth": "live/material/truth_en/p1_panel.json",
     "wer": "data/synth/en/wer_reports/p1_panel.json"},
    {"id": "s1_sci", "lang": "en", "kind": "class",
     "speakers": 1, "file": "s1_sci",
     "truth": "live/material/truth_en/s1_sci.json",
     "wer": "data/synth/en/wer_reports/s1_sci.json"},
    {"id": "s2_sci", "lang": "en", "kind": "class",
     "speakers": 1, "file": "s2_sci",
     "truth": "live/material/truth_en/s2_sci.json",
     "wer": "data/synth/en/wer_reports/s2_sci.json"},
    {"id": "e1_earn", "lang": "en", "kind": "earnings",
     "speakers": 1, "file": "e1_earn",
     "truth": "live/material/truth_en/e1_earn.json",
     "wer": "data/synth/en/wer_reports/e1_earn.json"},
    # -- ES (1080p25, voice A carlfm / B ffmpeg variant, panel only) ----
    {"id": "keynote_economia", "lang": "es", "kind": "keynote",
     "speakers": 1, "file": "keynote_economia",
     "truth": "live/material/truth_es/keynote_economia.json",
     "wer": "data/synth/es/keynote_economia_wer.json"},
    {"id": "keynote_ciencia", "lang": "es", "kind": "ciencia",
     "speakers": 1, "file": "keynote_ciencia",
     "truth": "live/material/truth_es/keynote_ciencia.json",
     "wer": "data/synth/es/keynote_ciencia_wer.json"},
    {"id": "panel_datos", "lang": "es", "kind": "panel",
     "speakers": 2, "file": "panel_datos",
     "truth": "live/material/truth_es/panel_datos.json",
     "wer": "data/synth/es/panel_datos_wer.json"},
    {"id": "clase_poblacion", "lang": "es", "kind": "class",
     "speakers": 1, "file": "clase_poblacion",
     "truth": "live/material/truth_es/clase_poblacion.json",
     "wer": "data/synth/es/clase_poblacion_wer.json"},
    {"id": "clase_economia", "lang": "es", "kind": "class",
     "speakers": 1, "file": "clase_economia",
     "truth": "live/material/truth_es/clase_economia.json",
     "wer": "data/synth/es/clase_economia_wer.json"},
    {"id": "earnings_luminara", "lang": "es", "kind": "earnings",
     "speakers": 1, "file": "earnings_luminara",
     "truth": "live/material/truth_es/earnings_luminara.json",
     "wer": "data/synth/es/earnings_luminara_wer.json"},
]

EARNINGS_IDS = {"e1_earn", "earnings_luminara"}

VOICES = {
    "en": {"A": "en_US-ljspeech-medium (public domain, from scratch)",
           "B": "en_US-norman-medium (public domain LibriVox, from "
                "scratch; panel guest only)"},
    "es": {"A": "es_ES-carlfm-x_low (public domain, from scratch)",
           "B": "ffmpeg pitch x0.85 variant of carlfm (documented "
                "variant, panel guest only)"},
}


def piece_by_id(piece_id: str) -> dict:
    """Registry row for ``piece_id`` (KeyError when unknown)."""
    for piece in PIECES:
        if piece["id"] == piece_id:
            return piece
    raise KeyError(f"unknown synth piece: {piece_id!r}")


def media_paths(piece: dict, repo_root: str = REPO_ROOT) -> dict[str, str]:
    """mp4/srt/truth/wer absolute paths for a registry row."""
    lang = piece["lang"]
    stem = piece["file"]
    synth = os.path.join(repo_root, "data", "synth", lang)
    return {
        "mp4": os.path.join(synth, f"{stem}.mp4"),
        "srt": os.path.join(synth, f"{stem}.srt"),
        "truth": os.path.join(repo_root, piece["truth"]),
        "wer": os.path.join(repo_root, piece["wer"]),
    }
