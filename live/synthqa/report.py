"""S4 report: manifest + benchmark informe (Atlas Live).

- ``data/synth/manifest.json``: pieces, sha256 of mp4/srt/truth, voices,
  licenses, durations, clean WER.
- ``data/synth/bench/report.json``: world accuracy/recall/falsos/Wilson
  GLOBAL + slices (category a-f, language, speaker count, piece), the
  E1 internal-coherence panels, the WER-by-noise table (builder
  wer.json aggregation — never a full re-transcription), and USD.

Offline except the optional 1-file ``--spot`` transcription.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import summarize as wilson_summarize  # noqa: E402
from live.synthqa import PIECES, REPO_ROOT, VOICES, media_paths  # noqa: E402

BENCH_DIR = os.path.join(REPO_ROOT, "data", "synth", "bench")
MANIFEST_PATH = os.path.join(REPO_ROOT, "data", "synth", "manifest.json")
QA_PATH = os.path.join(REPO_ROOT, "data", "synth", "qa", "review.json")
RESULTS_PATH = os.path.join(BENCH_DIR, "results.json")
REPORT_PATH = os.path.join(BENCH_DIR, "report.json")
SPOT_PATH = os.path.join(BENCH_DIR, "spot_wer.json")

ASR_USD_PER_HOUR = 0.18

LICENSES = {
    "en": ["en_US-ljspeech-medium: public domain, from scratch",
           "en_US-norman-medium: public domain LibriVox data, from "
           "scratch (panel guest)"],
    "es": ["es_ES-carlfm-x_low: public domain, from scratch",
           "panel guest: ffmpeg pitch-x0.85 variant of carlfm "
           "(documented variant, not a second person)"],
}

# SNR-variant wav layout per language (builders' convention).
WAVS = {
    "en": {"clean": "{stem}_clean_16k.wav", "dir": "wavs",
           "snr": {20: "{stem}_snr20.wav", 10: "{stem}_snr10.wav",
                   0: "{stem}_snr00.wav"}},
    "es": {"clean": "{stem}_clean.wav", "dir": ".",
           "snr": {20: "{stem}_snr20db.wav", 10: "{stem}_snr10db.wav",
                   5: "{stem}_snr5db.wav"}},
}


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str | None:
    """Hex sha256, or None when the file is missing/unreadable."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def load_json(path: str, default):
    """JSON load with a default when missing/invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def wav_paths(piece: dict, repo_root: str = REPO_ROOT) -> dict[str, str]:
    """clean + per-SNR wav paths for a registry row."""
    spec = WAVS[piece["lang"]]
    base = os.path.join(repo_root, "data", "synth", piece["lang"])
    if spec["dir"] != ".":
        base = os.path.join(base, spec["dir"])
    out = {"clean": os.path.join(
        base, spec["clean"].format(stem=piece["file"]))}
    for snr, template in spec["snr"].items():
        out[f"snr{snr}"] = os.path.join(
            base, template.format(stem=piece["file"]))
    return out


def build_manifest(repo_root: str = REPO_ROOT,
                   qa_path: str = QA_PATH) -> dict:
    """Manifest rows: files, hashes, voices, licenses, gates, WER."""
    qa = load_json(qa_path, {})
    qa_by_piece = {r.get("piece"): r for r in qa.get("results", [])}
    pieces = []
    for piece in PIECES:
        paths = media_paths(piece, repo_root)
        wavs = wav_paths(piece, repo_root)
        wer = load_json(paths["wer"], {})
        qa_row = qa_by_piece.get(piece["id"], {})
        gates = qa_row.get("gates", {})
        duration = (gates.get("duration") or {}).get("value")
        lang = piece["lang"]
        voices = {"A": VOICES[lang]["A"]}
        if piece["speakers"] == 2:
            voices["B"] = VOICES[lang]["B"]
        pieces.append({
            "id": piece["id"], "lang": lang, "kind": piece["kind"],
            "speakers": piece["speakers"],
            "files": {"mp4": os.path.relpath(paths["mp4"], repo_root),
                      "srt": os.path.relpath(paths["srt"], repo_root),
                      "truth": os.path.relpath(paths["truth"], repo_root),
                      "wer": os.path.relpath(paths["wer"], repo_root),
                      "wavs": {k: os.path.relpath(v, repo_root)
                               for k, v in wavs.items()}},
            "sha256": {"mp4": sha256_file(paths["mp4"]),
                       "srt": sha256_file(paths["srt"]),
                       "truth": sha256_file(paths["truth"])},
            "voices": voices, "licenses": LICENSES[lang],
            "duration_s": duration,
            "qa_pass": qa_row.get("pass"),
            "wer_clean": wer.get("wer"), "recall_clean": wer.get("recall"),
            "wer_usd": wer.get("usd"),
        })
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "n_pieces": len(pieces), "pieces": pieces}


def write_manifest(path: str = MANIFEST_PATH,
                   repo_root: str = REPO_ROOT) -> dict:
    """Build + write the manifest; return it."""
    manifest = build_manifest(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest


# ---------------------------------------------------------------------------
# World metrics (global + slices)
# ---------------------------------------------------------------------------

def collect_world(bench_results: dict,
                  repo_root: str = REPO_ROOT) -> list[dict]:
    """Join bench per_claim rows with truth category/lang/speakers."""
    joined = []
    for piece in PIECES:
        entry = (bench_results.get("pieces") or {}).get(piece["id"]) or {}
        run = entry.get("world_run") or {}
        if not run.get("per_claim"):
            continue
        truth = load_json(media_paths(piece, repo_root)["truth"], [])
        meta = {r.get("id"): r for r in truth}
        for row in run["per_claim"]:
            truth_row = meta.get(row.get("claim_id"), {})
            joined.append({
                "claim_id": row.get("claim_id"),
                "piece": piece["id"], "lang": piece["lang"],
                "kind": piece["kind"], "speakers": piece["speakers"],
                "category": truth_row.get("category", "?"),
                "expected": row.get("expected"),
                "verdict": row.get("verdict"),
                "latency_s": row.get("latency_s"),
            })
    return joined


def slice_panel(rows: list[dict]) -> dict:
    """Accuracy panel + recall over joined world rows.

    Accuracy/Wilson/abstention come from the M6 auditor (decided-only
    accuracy); recall is exact over ALL scored rows (abstentions are
    misses), plus per-side recall (true->supported, false->contradicted).
    """
    expected = {r["claim_id"]: r["expected"] for r in rows}
    verdicts = {r["claim_id"]: r["verdict"] for r in rows}
    panel = wilson_summarize(expected, verdicts)
    n_true = sum(1 for r in rows if r["expected"] == "true")
    n_false = sum(1 for r in rows if r["expected"] == "false")
    hit_true = sum(1 for r in rows if r["expected"] == "true"
                   and r["verdict"] == "supported")
    hit_false = sum(1 for r in rows if r["expected"] == "false"
                    and r["verdict"] == "contradicted")
    panel["recall"] = round(panel["correct"] / panel["n"], 4) if panel["n"] \
        else None
    panel["recall_true"] = round(hit_true / n_true, 4) if n_true else None
    panel["recall_false"] = round(hit_false / n_false, 4) if n_false else None
    panel.pop("per_canary", None)
    return panel


def summarize_world(bench_results: dict,
                    repo_root: str = REPO_ROOT) -> dict:
    """Global panel + slices by category, language, speakers, piece."""
    rows = collect_world(bench_results, repo_root)
    by_category = {}
    for cat in sorted({r["category"] for r in rows}):
        by_category[cat] = slice_panel([r for r in rows
                                        if r["category"] == cat])
    by_lang = {lang: slice_panel([r for r in rows if r["lang"] == lang])
               for lang in sorted({r["lang"] for r in rows})}
    by_speakers = {}
    for key in sorted({r["speakers"] for r in rows}):
        by_speakers[str(key)] = slice_panel(
            [r for r in rows if r["speakers"] == key])
    by_piece = {}
    for piece in PIECES:
        prows = [r for r in rows if r["piece"] == piece["id"]]
        if prows:
            by_piece[piece["id"]] = slice_panel(prows)
    excluded: dict = {}
    for pid, entry in (bench_results.get("pieces") or {}).items():
        for key, val in (entry.get("excluded") or {}).items():
            excluded[key] = excluded.get(key, 0) + (val or 0)
    e1 = {pid: (entry.get("e1_internal") or {})
          for pid, entry in (bench_results.get("pieces") or {}).items()
          if entry.get("e1_internal")}
    e1_coherence = {pid: {"n_scored": v.get("n_scored"),
                          "n_pass": v.get("n_pass"),
                          "coherence": v.get("coherence")}
                    for pid, v in e1.items()}
    usd = round(sum(((entry.get("world_run") or {}).get("total_cost_usd")
                       or 0.0)
                    for entry in (bench_results.get("pieces") or {}).values()
                    ), 4)
    return {"n_world": len(rows), "global": slice_panel(rows),
            "by_category": by_category, "by_lang": by_lang,
            "by_speakers": by_speakers, "by_piece": by_piece,
            "excluded": excluded, "e1_internal": e1_coherence, "usd": usd}


# ---------------------------------------------------------------------------
# WER by noise level (aggregation, no full re-transcription)
# ---------------------------------------------------------------------------

def wer_table(repo_root: str = REPO_ROOT,
              spot_path: str = SPOT_PATH) -> dict:
    """Clean WER from builder wer.json + any spot-check SNR cells.

    Builders measured clean audio only; SNR-variant wavs exist but were
    never transcribed. SNR cells stay ``"no medido"`` unless a spot
    check (see :func:`spot_check`) filled them in ``spot_wer.json``.
    """
    spot = load_json(spot_path, {}) or {}
    spot_cells = {(c.get("piece"), c.get("snr_db")): c
                  for c in spot.get("cells", [])}
    rows = []
    for piece in PIECES:
        paths = media_paths(piece, repo_root)
        wer = load_json(paths["wer"], {}) or {}
        wavs = wav_paths(piece, repo_root)
        snr_levels = sorted(int(k[3:]) for k in wavs if k != "clean")
        cells = {}
        for snr in snr_levels:
            hit = spot_cells.get((piece["id"], snr))
            cells[f"snr{snr}"] = (round(hit["wer"], 4) if hit else
                                  "no medido")
        rows.append({"piece": piece["id"], "lang": piece["lang"],
                     "wer_clean": wer.get("wer"),
                     "recall_clean": wer.get("recall"),
                     "wer_usd": wer.get("usd"), "snr_wer": cells})
    return {"note": "clean = builder measurement; SNR cells need a live "
            "spot-check (builders never transcribed noisy variants)",
            "rows": rows}


def spot_check(piece_id: str, snr_db: int, api_key: str,
               repo_root: str = REPO_ROOT,
               spot_path: str = SPOT_PATH, post=None) -> dict:
    """Transcribe ONE noisy wav (1 SNR) and score WER vs its SRT.

    Language bias only, no keywords (labeled as such — not directly
    comparable to the builders' keyword-biased clean numbers).
    """
    piece = next(p for p in PIECES if p["id"] == piece_id)
    sys.path.insert(0, os.path.join(REPO_ROOT, "live", "material"))
    # Per-language scoring helpers (the builders' own: EN expands numbers
    # to English words, ES to Spanish). SRT concatenation is
    # language-agnostic.
    from measure_wer import srt_plain_text as _srt_text  # noqa

    if piece["lang"] == "en":
        from build_corpus_en import claim_recall as _recall  # noqa
        from build_corpus_en import wer_details as _wer  # noqa
    else:
        from measure_wer import asr_recall as _recall  # noqa
        from measure_wer import wer_details as _wer  # noqa

    from live.oido.asr_file import transcribe_file  # noqa: E402

    wavs = wav_paths(piece, repo_root)
    key = f"snr{snr_db}"
    if key not in wavs:
        raise SystemExit(f"{piece_id} has no {key} wav "
                         f"(has: {sorted(wavs)})")
    paths = media_paths(piece, repo_root)
    kwargs = {} if post is None else {"post": post}
    t0 = time.time()
    resp = transcribe_file(
        wavs[key], api_key,
        language_bias=("English",) if piece["lang"] == "en"
        else ("Spanish",),
        timeout_s=300.0, **kwargs)
    # Same transcript extraction as the builders (top-level field,
    # per-turn fallback).
    hyp = (resp.get("transcript") or "").strip()
    if not hyp:
        hyp = " ".join((t.get("transcript") or "")
                       for t in resp.get("turns", [])).strip()
    ref = _srt_text(paths["srt"])
    details = _wer(ref, hyp)
    truth = load_json(paths["truth"], []) or []
    recall = _recall([str(r.get("text", "")) for r in truth], hyp)
    audio_ms = resp.get("audioDurationMs") or 0
    usd = round((audio_ms / 3600000.0) * ASR_USD_PER_HOUR, 5)
    cell = {"piece": piece_id, "snr_db": snr_db,
            "wer": round(details["wer"], 4),
            "sub": details.get("sub"), "ins": details.get("ins"),
            "del": details.get("del"),
            "recall": round(recall["recall"], 4),
            "usd": usd, "keywords": False, "language_bias": piece["lang"],
            "elapsed_s": round(time.time() - t0, 1)}
    spot = load_json(spot_path, {}) or {}
    cells = [c for c in spot.get("cells", [])
             if (c.get("piece"), c.get("snr_db")) != (piece_id, snr_db)]
    cells.append(cell)
    spot = {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "cells": cells}
    os.makedirs(os.path.dirname(spot_path), exist_ok=True)
    with open(spot_path, "w", encoding="utf-8") as fh:
        json.dump(spot, fh, ensure_ascii=False, indent=2)
    return cell


# ---------------------------------------------------------------------------
# Report assembly + CLI
# ---------------------------------------------------------------------------

def build_report(repo_root: str = REPO_ROOT,
                 results_path: str = RESULTS_PATH,
                 qa_path: str = QA_PATH) -> dict:
    """Assemble manifest + world summary + WER table + USD."""
    manifest = build_manifest(repo_root, qa_path)
    bench_results = load_json(results_path, {"pieces": {}})
    world = summarize_world(bench_results, repo_root)
    qa = load_json(qa_path, {})
    qa_usd = round(sum(
        (r.get("vision") or {}).get("cost_usd", 0.0)
        for r in qa.get("results", [])), 4)
    spot = load_json(SPOT_PATH, {}) or {}
    spot_usd = round(sum(c.get("usd", 0.0) for c in spot.get("cells", [])),
                     4)
    total_usd = round(world["usd"] + qa_usd + spot_usd, 4)
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest, "world": world,
            "wer_by_noise": wer_table(repo_root),
            "qa": {"n_pass": sum(1 for r in qa.get("results", [])
                                if r.get("pass")),
                   "n": len(qa.get("results", [])), "usd": qa_usd},
            "spot_usd": spot_usd, "total_usd": total_usd}


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the S4 report."""
    parser = argparse.ArgumentParser(
        description="S4 report: manifest + benchmark informe.")
    parser.add_argument("--manifest-out", default=MANIFEST_PATH,
                        help=f"manifest path (default: {MANIFEST_PATH})")
    parser.add_argument("--report-out", default=REPORT_PATH,
                        help=f"report path (default: {REPORT_PATH})")
    parser.add_argument("--spot", default="",
                        help="optional live spot-check PIECE@SNR "
                        "(e.g. k1_econ@10)")
    parser.add_argument("--api-key", default="",
                        help="Meta API key for --spot "
                        "(default: MODEL_API_KEY env)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write manifest + report; optionally run 1 spot-check first."""
    args = build_parser().parse_args(argv)
    if args.spot:
        try:
            from dotenv import load_dotenv  # noqa: E402

            load_dotenv()
        except ImportError:
            pass
        api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
        if not api_key:
            raise SystemExit("MODEL_API_KEY not configured; source .env.")
        try:
            pid, snr = args.spot.split("@")
            cell = spot_check(pid.strip(), int(snr.strip()), api_key)
        except ValueError:
            raise SystemExit("--spot format: PIECE@SNR (e.g. k1_econ@10)")
        print(f"[report] spot {cell['piece']}@{cell['snr_db']}dB: "
              f"wer={cell['wer']} recall={cell['recall']} "
              f"usd={cell['usd']}", flush=True)
    manifest = write_manifest(args.manifest_out)
    report = build_report()
    os.makedirs(os.path.dirname(args.report_out), exist_ok=True)
    with open(args.report_out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    world = report["world"]
    print(f"[report] manifest pieces={manifest['n_pieces']} "
          f"world n={world['n_world']} "
          f"acc={world['global']['accuracy']} "
          f"wilson=[{world['global']['wilson_lo']},"
          f"{world['global']['wilson_hi']}] "
          f"usd={report['total_usd']} out={args.report_out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
