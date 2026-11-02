"""S5 noise grid: full ASR WER/recall over clean + SNR variants (Atlas Live).

12 pieces x 4 levels (EN: clean/20/10/0 dB; ES: clean/20/10/5 dB — the
exact builders' layouts, see ``live/synthqa/report.WAVS``) = 48 cells.
Each cell is transcribed via POST asr with the SAME language bias +
keywords as the builder's FINAL pass for that piece (EN attempt from
its ``wer.json``: 0 = all keywords, 1 = numbers-only; ES always full
keywords), then scored with the builders' own WER/recall helpers.
Grid cells are therefore directly comparable to the builder clean
numbers — unlike the S4 spot-check, which ran without keywords.

E2E propagation (offline, $0): the heuristic :class:`Detector`
(``live.detector.claims``, no model) runs over each noisy transcript
of 1 EN + 1 ES piece x 4 levels; detected-claim counts vs truth size
show how detection degrades with SNR.

Artifacts (all under data/, gitignored):
``data/synth/qa/noise_grid.json`` (48 cells + e2e rows) and
``data/synth/qa/noise_transcripts/<piece>_<level>.txt``.

Offline-safe except :func:`run_grid` with a live ``post`` (needs
MODEL_API_KEY). Aggregation, E2E over saved transcripts, and the CLI
summary need no network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import wilson_interval  # noqa: E402
from live.detector.claims import Detector  # noqa: E402
from live.synthqa import PIECES, REPO_ROOT, media_paths, piece_by_id  # noqa: E402
from live.synthqa.report import (  # noqa: E402
    ASR_USD_PER_HOUR,
    WAVS,
    load_json,
    sha256_file,
    wav_paths,
)

NOISE_GRID_PATH = os.path.join(REPO_ROOT, "data", "synth", "qa",
                               "noise_grid.json")
NOISE_TRANSCRIPTS_DIR = os.path.join(REPO_ROOT, "data", "synth", "qa",
                                     "noise_transcripts")

# E2E propagation pieces: 1 EN + 1 ES, 4 levels each (task scope).
E2E_PIECES = ("k1_econ", "keynote_economia")

LEVEL_ORDER = ["clean", "snr20", "snr10", "snr5", "snr0"]


def levels_for_lang(lang: str) -> list[str]:
    """Level keys for a language (clean + its SNR variants, easy first)."""
    spec = WAVS[lang]["snr"]
    levels = ["clean"] + [f"snr{snr}" for snr in sorted(spec, reverse=True)]
    order = {name: rank for rank, name in enumerate(LEVEL_ORDER)}
    return sorted(levels, key=order.__getitem__)


def snr_db(level: str) -> int | None:
    """SNR in dB for a level key (None for clean)."""
    return None if level == "clean" else int(level[3:])


def transcript_path(piece_id: str, level: str,
                    transcripts_dir: str = NOISE_TRANSCRIPTS_DIR) -> str:
    """Saved-transcript path for a grid cell."""
    return os.path.join(transcripts_dir, f"{piece_id}_{level}.txt")


# ---------------------------------------------------------------------------
# Builder parity: keywords + scorers (the builders' own code)
# ---------------------------------------------------------------------------

def _material_path() -> str:
    return os.path.join(REPO_ROOT, "live", "material")


def claim_texts(piece: dict, repo_root: str = REPO_ROOT) -> list[str]:
    """Truth claim texts for a registry row."""
    truth = load_json(media_paths(piece, repo_root)["truth"], []) or []
    return [str(r.get("text", "")) for r in truth]


def builder_attempt(piece: dict, repo_root: str = REPO_ROOT) -> int:
    """Final builder attempt for EN (0/1/2); ES builders never retried."""
    if piece["lang"] != "en":
        return 0
    wer = load_json(media_paths(piece, repo_root)["wer"], {}) or {}
    try:
        return int(wer.get("attempt", 0))
    except (TypeError, ValueError):
        return 0


def keywords_for_piece(piece: dict, repo_root: str = REPO_ROOT) -> tuple:
    """Exact builder-final keywords for a piece (bias-matched, comparable).

    EN: ``extract_keywords_en`` over truth texts, filtered by the final
    attempt recorded in the piece's ``wer.json`` (0 = all, 1 =
    numbers-only, else none) — the same rule as
    ``build_corpus_en.measure_piece``. ES: ``extract_keywords`` over
    truth texts (builders never retried).
    """
    sys.path.insert(0, _material_path())
    texts = claim_texts(piece, repo_root)
    if piece["lang"] == "en":
        from build_corpus_en import NUM_RE, extract_keywords_en  # noqa

        kws_all = extract_keywords_en(texts)
        attempt = builder_attempt(piece, repo_root)
        if attempt == 0:
            return tuple(kws_all)
        if attempt == 1:
            return tuple(k for k in kws_all if NUM_RE.search(k))
        return ()
    from measure_wer import extract_keywords  # noqa

    return tuple(extract_keywords(texts))


def scorers_for_lang(lang: str):
    """(wer_details, recall_fn, srt_text_fn) — the builders' own."""
    sys.path.insert(0, _material_path())
    from measure_wer import srt_plain_text  # noqa

    if lang == "en":
        from build_corpus_en import claim_recall, wer_details  # noqa

        return wer_details, claim_recall, srt_plain_text
    from measure_wer import asr_recall, wer_details  # noqa

    return wer_details, asr_recall, srt_plain_text


def language_bias(lang: str) -> tuple[str, ...]:
    """Builder language bias (same strings the builders passed)."""
    return ("English",) if lang == "en" else ("Spanish",)


def extract_transcript(resp: dict) -> str:
    """Builder transcript extraction: top-level field, per-turn fallback."""
    hyp = (resp.get("transcript") or "").strip()
    if not hyp:
        hyp = " ".join((t.get("transcript") or "")
                       for t in resp.get("turns", [])).strip()
    return hyp


# ---------------------------------------------------------------------------
# Grid: transcribe + score (live unless ``post`` is stubbed)
# ---------------------------------------------------------------------------

def transcribe_cell(piece: dict, level: str, api_key: str,
                    repo_root: str = REPO_ROOT, post=None,
                    timeout_s: float = 300.0,
                    transcripts_dir: str = NOISE_TRANSCRIPTS_DIR) -> dict:
    """Transcribe ONE level wav with builder bias+keywords; score it.

    ``post`` is the injectable ``transcribe_file`` seam (tests stub it;
    production hits POST asr). The transcript is saved to
    ``noise_transcripts/`` and its repo-relative path recorded in the
    cell (E2E + audit trail).
    """
    from live.oido.asr_file import transcribe_file  # noqa: E402

    wavs = wav_paths(piece, repo_root)
    if level not in wavs:
        raise KeyError(f"{piece['id']} has no {level!r} wav "
                       f"(has: {sorted(wavs)})")
    wer_details, recall_fn, srt_text = scorers_for_lang(piece["lang"])
    keywords = keywords_for_piece(piece, repo_root)
    paths = media_paths(piece, repo_root)
    texts = claim_texts(piece, repo_root)
    kwargs = {} if post is None else {"post": post}
    t0 = time.time()
    resp = transcribe_file(
        wavs[level], api_key, language_bias=language_bias(piece["lang"]),
        keywords=keywords, timeout_s=timeout_s, **kwargs)
    elapsed = round(time.time() - t0, 1)
    hyp = extract_transcript(resp)
    ref = srt_text(paths["srt"])
    details = wer_details(ref, hyp)
    recall = recall_fn(texts, hyp)
    audio_ms = resp.get("audioDurationMs") or 0
    usd = round((audio_ms / 3600000.0) * ASR_USD_PER_HOUR, 5)
    tpath = transcript_path(piece["id"], level, transcripts_dir)
    os.makedirs(os.path.dirname(tpath), exist_ok=True)
    with open(tpath, "w", encoding="utf-8") as fh:
        fh.write(hyp + "\n")
    try:
        trel = os.path.relpath(tpath, repo_root)
    except ValueError:
        trel = tpath
    return {"piece": piece["id"], "lang": piece["lang"], "level": level,
            "snr_db": snr_db(level),
            "wer": round(details["wer"], 4),
            "sub": details.get("sub"), "ins": details.get("ins"),
            "del": details.get("del"),
            "n_ref": details.get("n_ref"), "n_hyp": details.get("n_hyp"),
            "recall": round(recall["recall"], 4),
            "n_hits": len(recall.get("hits", [])),
            "n_claims": recall.get("n", len(texts)),
            "misses": recall.get("misses", []),
            "n_keywords": len(keywords), "keywords": True,
            "language_bias": piece["lang"],
            "audio_ms": audio_ms, "usd": usd, "elapsed_s": elapsed,
            "transcript": trel}


def _cell_key(cell: dict) -> tuple:
    return (cell.get("piece"), cell.get("level"))


def run_grid(api_key: str, repo_root: str = REPO_ROOT, post=None,
             piece_ids: list[str] | None = None,
             levels: list[str] | None = None,
             grid_path: str = NOISE_GRID_PATH,
             transcripts_dir: str = NOISE_TRANSCRIPTS_DIR,
             overwrite: bool = False,
             retries: int = 3,
             progress=None) -> dict:
    """Transcribe the 12x4 grid (resume: present cells are skipped).

    Writes ``noise_grid.json`` after every cell (crash-safe resume).
    Each cell is retried up to ``retries`` times on transient ASR
    failures (backoff 20 s / 60 s / 120 s); a cell that keeps failing
    aborts the run but the grid on disk keeps every finished cell, so
    re-running resumes where it stopped.
    ``progress`` (optional) is called with each finished cell.
    Returns the full grid (cells + carried-over e2e rows).
    """
    grid = load_json(grid_path, {}) or {}
    cells = list(grid.get("cells", []))
    have = {_cell_key(c) for c in cells}
    targets = [p for p in PIECES
               if piece_ids is None or p["id"] in piece_ids]
    for piece in targets:
        for level in levels_for_lang(piece["lang"]):
            if levels is not None and level not in levels:
                continue
            if _cell_key({"piece": piece["id"], "level": level}) in have \
                    and not overwrite:
                continue
            cell = None
            for attempt in range(retries):
                try:
                    cell = transcribe_cell(
                        piece, level, api_key, repo_root, post=post,
                        transcripts_dir=transcripts_dir)
                    break
                except Exception as exc:  # noqa: BLE001 — retry then abort
                    wait = (20, 60, 120)[min(attempt, 2)]
                    print(f"[noise] {piece['id']}@{level} attempt "
                          f"{attempt + 1}/{retries} failed "
                          f"({type(exc).__name__}); retry in {wait}s",
                          flush=True)
                    time.sleep(wait)
            if cell is None:
                raise RuntimeError(
                    f"{piece['id']}@{level}: {retries} attempts failed; "
                    f"grid kept ({len(cells)} cells), re-run to resume")
            cells = [c for c in cells
                     if _cell_key(c) != (piece["id"], level)]
            cells.append(cell)
            have.add((piece["id"], level))
            grid = {"generated_utc": datetime.now(timezone.utc).isoformat(),
                    "n_cells": len(cells), "cells": cells,
                    "e2e": grid.get("e2e", [])}
            os.makedirs(os.path.dirname(grid_path), exist_ok=True)
            with open(grid_path, "w", encoding="utf-8") as fh:
                json.dump(grid, fh, ensure_ascii=False, indent=2)
            if progress is not None:
                progress(cell)
    grid = {"generated_utc": grid.get(
        "generated_utc", datetime.now(timezone.utc).isoformat()),
        "n_cells": len(cells), "cells": cells,
        "e2e": grid.get("e2e", [])}
    return grid


# ---------------------------------------------------------------------------
# Aggregation (offline): WER/recall per language x level + Wilson on recall
# ---------------------------------------------------------------------------

def aggregate(grid: dict) -> dict:
    """Per (lang, level): micro WER + recall with Wilson 95% (n=claims).

    WER is micro-averaged (total sub+ins+del / total ref words) so long
    pieces weigh their words; the plain mean is kept as ``wer_mean``
    for reference. Recall is exact hits/claims; the Wilson interval is
    over claims (n = total truth claims in the slice).
    """
    buckets: dict[tuple, dict] = {}
    for cell in grid.get("cells", []):
        key = (cell.get("lang"), cell.get("level"))
        slot = buckets.setdefault(key, {"err": 0, "n_ref": 0, "wers": [],
                                       "hits": 0, "claims": 0, "usd": 0.0,
                                       "n_cells": 0})
        slot["err"] += (cell.get("sub") or 0) + (cell.get("ins") or 0) \
            + (cell.get("del") or 0)
        slot["n_ref"] += cell.get("n_ref") or 0
        if cell.get("wer") is not None:
            slot["wers"].append(cell["wer"])
        slot["hits"] += cell.get("n_hits") or 0
        slot["claims"] += cell.get("n_claims") or 0
        slot["usd"] += cell.get("usd") or 0.0
        slot["n_cells"] += 1
    out: dict[str, dict] = {}
    for (lang, level), slot in sorted(buckets.items()):
        lo, hi = wilson_interval(slot["hits"], slot["claims"])
        panel = {"n_cells": slot["n_cells"],
                 "wer": round(slot["err"] / slot["n_ref"], 4)
                 if slot["n_ref"] else None,
                 "wer_mean": round(sum(slot["wers"]) / len(slot["wers"]), 4)
                 if slot["wers"] else None,
                 "n_ref_words": slot["n_ref"],
                 "recall": round(slot["hits"] / slot["claims"], 4)
                 if slot["claims"] else None,
                 "n_hits": slot["hits"], "n_claims": slot["claims"],
                 "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
                 "usd": round(slot["usd"], 4)}
        out.setdefault(lang, {})[level] = panel
    return out


# ---------------------------------------------------------------------------
# E2E propagation (offline, $0): heuristic Detector over noisy transcripts
# ---------------------------------------------------------------------------

def detect_counts(transcript: str, detector=None) -> dict:
    """Heuristic claim count over one transcript (single-turn feed).

    ``detector`` is injectable (tests pass a double); default is a
    fresh heuristic :class:`Detector` (llm=None, never any network).
    A fresh instance per call keeps the cross-turn dedup window from
    leaking counts across (piece, level) cells.
    """
    det = detector if detector is not None else Detector()
    turn = {"turn": 0, "text": transcript or "", "speaker": None,
            "t_start": None, "t_end": None}
    claims, notes = det.process_turn(turn)
    return {"n_detected": len(claims), "notes": notes}


def run_e2e(repo_root: str = REPO_ROOT,
            grid_path: str = NOISE_GRID_PATH,
            piece_ids: tuple = E2E_PIECES,
            detector=None) -> list[dict]:
    """Detection-vs-SNR rows for 1 EN + 1 ES piece x 4 levels (offline).

    Reads the saved noisy transcripts (grid must exist), feeds each to
    a fresh heuristic Detector, and counts detected claims vs the 40
    truth claims. ``detector`` (optional factory-or-instance double)
    overrides the real Detector for tests.
    """
    grid = load_json(grid_path, {}) or {}
    by_cell = {_cell_key(c): c for c in grid.get("cells", [])}
    rows = []
    for pid in piece_ids:
        piece = piece_by_id(pid)
        truth = load_json(media_paths(piece, repo_root)["truth"], []) or []
        for level in levels_for_lang(piece["lang"]):
            cell = by_cell.get((pid, level), {})
            tpath = os.path.join(repo_root, cell.get("transcript", ""))
            try:
                with open(tpath, encoding="utf-8") as fh:
                    transcript = fh.read()
            except OSError:
                transcript = ""
            det = detector() if callable(detector) else detector
            counts = detect_counts(transcript, detector=det)
            rows.append({"piece": pid, "lang": piece["lang"], "level": level,
                         "snr_db": snr_db(level),
                         "n_detected": counts["n_detected"],
                         "n_truth": len(truth),
                         "detection_rate": round(counts["n_detected"]
                                                 / len(truth), 4)
                         if truth else None,
                         "wer": cell.get("wer"),
                         "recall": cell.get("recall"), "usd": 0.0})
    grid["e2e"] = rows
    grid["generated_utc"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(grid_path), exist_ok=True)
    with open(grid_path, "w", encoding="utf-8") as fh:
        json.dump(grid, fh, ensure_ascii=False, indent=2)
    return rows


def grid_usd(grid: dict) -> float:
    """Total live ASR USD in the grid."""
    return round(sum(c.get("usd", 0.0) for c in grid.get("cells", [])), 4)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the S5 noise grid."""
    parser = argparse.ArgumentParser(
        description="S5 noise grid: 12 pieces x clean+SNR ASR + E2E.")
    parser.add_argument("--live", action="store_true",
                        help="transcribe the grid via POST asr "
                        "(default: offline summary of noise_grid.json)")
    parser.add_argument("--e2e", action="store_true",
                        help="run heuristic detection over saved "
                        "transcripts (offline, $0)")
    parser.add_argument("--piece", action="append", default=None,
                        help="restrict to PIECE id (repeatable)")
    parser.add_argument("--level", action="append", default=None,
                        help="restrict to LEVEL (clean/snr20/...)")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-transcribe cells already in the grid")
    parser.add_argument("--api-key", default="",
                        help="Meta API key for --live "
                        "(default: MODEL_API_KEY env)")
    return parser


def print_summary(grid: dict) -> None:
    """Offline grid summary: per lang x level WER/recall/Wilson + USD."""
    agg = aggregate(grid)
    order = {name: rank for rank, name in enumerate(LEVEL_ORDER)}
    for lang in sorted(agg):
        for level in sorted(agg[lang], key=order.__getitem__):
            panel = agg[lang][level]
            print(f"[noise] {lang} {level}: cells={panel['n_cells']} "
                  f"wer={panel['wer']} recall={panel['recall']} "
                  f"wilson=[{panel['wilson_lo']},{panel['wilson_hi']}] "
                  f"n={panel['n_claims']} usd={panel['usd']}", flush=True)
    print(f"[noise] cells={grid.get('n_cells', 0)} "
          f"usd={grid_usd(grid)} e2e_rows={len(grid.get('e2e', []))}",
          flush=True)


def main(argv: list[str] | None = None) -> int:
    """Offline summary; --live transcribes, --e2e runs detection."""
    args = build_parser().parse_args(argv)
    if args.live:
        try:
            from dotenv import load_dotenv  # noqa: E402

            load_dotenv()
        except ImportError:
            pass
        api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
        if not api_key:
            raise SystemExit("MODEL_API_KEY not configured; source .env.")

        def _progress(cell: dict) -> None:
            print(f"[noise] {cell['piece']}@{cell['level']}: "
                  f"wer={cell['wer']} recall={cell['recall']} "
                  f"usd={cell['usd']} ({cell['elapsed_s']}s)", flush=True)

        grid = run_grid(api_key, piece_ids=args.piece, levels=args.level,
                        overwrite=args.overwrite, progress=_progress)
    else:
        grid = load_json(NOISE_GRID_PATH, {"cells": []}) or {"cells": []}
    if args.e2e:
        rows = run_e2e(piece_ids=tuple(args.piece) if args.piece
                       else E2E_PIECES)
        for row in rows:
            print(f"[noise] e2e {row['piece']}@{row['level']}: "
                  f"detected={row['n_detected']}/{row['n_truth']} "
                  f"wer={row['wer']}", flush=True)
        grid = load_json(NOISE_GRID_PATH, grid) or grid
    print_summary(grid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
