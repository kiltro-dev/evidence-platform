"""S5 noise-grid tests: aggregation, Wilson, manifest schema, doubles.

All offline (0 live calls, 0 ffmpeg): the (lang, level) aggregation is
exact on synthetic grids, Wilson matches tabulated values, the
extended manifest schema is checked over a tmp repo root, grid cells
are transcribed through a stubbed ``post`` seam, and detection counts
run against detector doubles (+ the real heuristic, which is offline).
"""

import json
import os
import struct
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import wilson_interval  # noqa: E402
from live.synthqa import PIECES, media_paths, piece_by_id  # noqa: E402
from live.synthqa import noise as N  # noqa: E402
from live.synthqa import report as R  # noqa: E402
from live.synthqa.report import wav_paths  # noqa: E402

EN_TRUTH = [
    {"id": "T-1", "text": "Spain GDP grew 3 percent in 2024."},
    {"id": "T-2", "text": "Unemployment fell to 5 percent last year."},
]
ES_TRUTH = [
    {"id": "T-1", "text": "El PIB crecio 3 por ciento en 2024."},
    {"id": "T-2", "text": "El desempleo bajo al 5 por ciento."},
]

EN_SRT = ("1\n00:00:00,000 --> 00:00:01,000\n"
          "Spain GDP grew 3 percent in 2024.\n\n"
          "2\n00:00:01,000 --> 00:00:02,000\n"
          "Unemployment fell to 5 percent last year.\n")
ES_SRT = ("1\n00:00:00,000 --> 00:00:01,000\n"
          "El PIB crecio 3 por ciento en 2024.\n\n"
          "2\n00:00:01,000 --> 00:00:02,000\n"
          "El desempleo bajo al 5 por ciento.\n")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _tiny_wav(path, seconds=1.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = int(seconds * 16000)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(16000)
        fh.writeframes(struct.pack("<%dh" % n, *([0] * n)))


def _scaffold_piece(root, piece_id, truth, srt, attempt=0,
                    levels=("clean",)):
    """Minimal repo layout for one piece under a tmp root."""
    piece = piece_by_id(piece_id)
    paths = media_paths(piece, root)
    _write(paths["truth"], json.dumps(truth))
    _write(paths["srt"], srt)
    _write(paths["wer"], json.dumps({"attempt": attempt}))
    for level in levels:
        _tiny_wav(wav_paths(piece, root)[level])
    return piece


def _cell(lang, level, sub=0, ins=0, dele=0, n_ref=100, wer=0.0,
          hits=40, claims=40, usd=0.01, piece="p", keywords=True):
    return {"piece": piece, "lang": lang, "level": level,
            "sub": sub, "ins": ins, "del": dele, "n_ref": n_ref,
            "wer": wer, "n_hits": hits, "n_claims": claims, "usd": usd,
            "keywords": keywords}


# ---------------------------------------------------------------------------
# Levels (builders' exact layouts)
# ---------------------------------------------------------------------------

def test_levels_match_builders_layouts():
    assert N.levels_for_lang("en") == ["clean", "snr20", "snr10", "snr0"]
    assert N.levels_for_lang("es") == ["clean", "snr20", "snr10", "snr5"]
    assert N.snr_db("clean") is None
    assert N.snr_db("snr10") == 10


def test_registry_is_12_pieces_x_4_levels():
    total = sum(len(N.levels_for_lang(p["lang"])) for p in PIECES)
    assert len(PIECES) == 12
    assert total == 48


# ---------------------------------------------------------------------------
# Aggregation: exact on fixtures
# ---------------------------------------------------------------------------

def test_aggregate_exact_micro_wer_recall_usd():
    grid = {"cells": [
        _cell("en", "snr10", sub=2, ins=1, dele=1, n_ref=100, wer=0.04,
              hits=36, claims=40, usd=0.01, piece="a"),
        _cell("en", "snr10", sub=0, ins=0, dele=4, n_ref=300, wer=0.02,
              hits=40, claims=40, usd=0.02, piece="b"),
    ]}
    panel = N.aggregate(grid)["en"]["snr10"]
    assert panel["n_cells"] == 2
    assert panel["wer"] == pytest.approx(8 / 400)  # micro, not mean
    assert panel["wer_mean"] == pytest.approx(0.03)
    assert panel["n_ref_words"] == 400
    assert panel["recall"] == pytest.approx(76 / 80)
    assert panel["n_hits"] == 76
    assert panel["n_claims"] == 80
    assert panel["usd"] == pytest.approx(0.03)


def test_aggregate_empty_grid():
    assert N.aggregate({}) == {}
    assert N.aggregate({"cells": []}) == {}


def test_wilson_on_recall_matches_auditor():
    grid = {"cells": [_cell("es", "clean", hits=37, claims=40)]}
    panel = N.aggregate(grid)["es"]["clean"]
    lo, hi = wilson_interval(37, 40)
    assert panel["wilson_lo"] == pytest.approx(round(lo, 4))
    assert panel["wilson_hi"] == pytest.approx(round(hi, 4))
    assert panel["wilson_lo"] > 0.75  # tabulated sanity: tight high band


def test_grid_usd_sums_cells():
    grid = {"cells": [_cell("en", "clean", usd=0.01234),
                      _cell("en", "clean", usd=0.00001)]}
    assert N.grid_usd(grid) == pytest.approx(0.0124, abs=0.0001)


# ---------------------------------------------------------------------------
# Transcript extraction (builder rule)
# ---------------------------------------------------------------------------

def test_extract_transcript_prefers_top_level():
    resp = {"transcript": "  hello world  ",
            "turns": [{"transcript": "ignored"}]}
    assert N.extract_transcript(resp) == "hello world"


def test_extract_transcript_falls_back_to_turns():
    resp = {"transcript": "  ",
            "turns": [{"transcript": "first"}, {"transcript": ""},
                      {"transcript": "second"}]}
    assert N.extract_transcript(resp) == "first  second"


# ---------------------------------------------------------------------------
# transcribe_cell through a stubbed post (bias+keywords parity)
# ---------------------------------------------------------------------------

def _stub_post_factory(seen, transcript="Spain GDP grew 3 percent "
                       "in 2024. Unemployment fell to 5 percent last year.",
                       audio_ms=2000):
    def _post(url, headers, files, timeout_s):
        seen["url"] = url
        seen["headers"] = headers
        seen["request"] = json.loads(files["request"][1])
        seen["timeout_s"] = timeout_s
        return {"transcript": transcript, "turns": [],
                "audioDurationMs": audio_ms}

    return _post


def test_transcribe_cell_en_scores_exact_and_passes_bias_keywords(tmp_path):
    root = str(tmp_path)
    piece = _scaffold_piece(root, "k1_econ", EN_TRUTH, EN_SRT, attempt=0)
    seen = {}
    tdir = os.path.join(root, "t")
    cell = N.transcribe_cell(piece, "clean", "test-key", root,
                             post=_stub_post_factory(seen),
                             transcripts_dir=tdir)
    assert cell["piece"] == "k1_econ"
    assert cell["lang"] == "en"
    assert cell["level"] == "clean"
    assert cell["snr_db"] is None
    assert cell["wer"] == 0.0  # identical ref/hyp
    assert cell["recall"] == 1.0
    assert cell["n_hits"] == 2
    assert cell["n_claims"] == 2
    assert cell["keywords"] is True
    assert cell["n_keywords"] > 0
    assert cell["language_bias"] == "en"
    assert cell["usd"] == pytest.approx((2000 / 3600000.0) * 0.18)
    # Bias + keywords actually sent on the wire.
    assert seen["request"]["languageBias"] == ["English"]
    assert seen["request"]["keywords"]  # non-empty, builder-mined
    assert len(seen["request"]["keywords"]) == cell["n_keywords"]
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    # Transcript saved + referenced.
    assert os.path.exists(os.path.join(root, cell["transcript"]))
    with open(os.path.join(root, cell["transcript"]),
              encoding="utf-8") as fh:
        assert "Unemployment" in fh.read()


def test_transcribe_cell_es_spanish_bias(tmp_path):
    root = str(tmp_path)
    piece = _scaffold_piece(root, "keynote_economia", ES_TRUTH, ES_SRT,
                            levels=("clean", "snr5"))
    seen = {}
    hyp = "El PIB crecio 3 por ciento en 2024. El desempleo bajo al 5."
    cell = N.transcribe_cell(
        piece, "snr5", "k", root,
        post=_stub_post_factory(seen, transcript=hyp),
        transcripts_dir=os.path.join(root, "t"))
    assert cell["level"] == "snr5"
    assert cell["snr_db"] == 5
    assert seen["request"]["languageBias"] == ["Spanish"]
    assert cell["keywords"] is True


def test_transcribe_cell_rejects_unknown_level(tmp_path):
    root = str(tmp_path)
    piece = _scaffold_piece(root, "k1_econ", EN_TRUTH, EN_SRT)
    with pytest.raises(KeyError):
        N.transcribe_cell(piece, "snr5", "k", root,
                          post=_stub_post_factory({}),
                          transcripts_dir=os.path.join(root, "t"))


def test_keywords_follow_builder_attempt_rule(tmp_path):
    root = str(tmp_path)
    sys.path.insert(0, os.path.join(N.REPO_ROOT, "live", "material"))
    from build_corpus_en import NUM_RE

    _scaffold_piece(root, "k2_clim", EN_TRUTH, EN_SRT, attempt=1)
    piece = piece_by_id("k2_clim")
    kws = N.keywords_for_piece(piece, root)
    assert kws  # numbers-only subset is non-empty here
    assert all(NUM_RE.search(k) for k in kws)
    _scaffold_piece(root, "k1_econ", EN_TRUTH, EN_SRT, attempt=0)
    full = N.keywords_for_piece(piece_by_id("k1_econ"), root)
    assert set(kws) <= set(full)


# ---------------------------------------------------------------------------
# run_grid resume (stubbed post, tmp root)
# ---------------------------------------------------------------------------

def test_run_grid_skips_present_cells(tmp_path):
    root = str(tmp_path)
    piece = _scaffold_piece(root, "k1_econ", EN_TRUTH, EN_SRT,
                            levels=("clean", "snr20"))
    grid_path = os.path.join(root, "qa", "noise_grid.json")
    tdir = os.path.join(root, "t")
    calls = []

    def _counting_post(url, headers, files, timeout_s):
        calls.append(files["audio"][0])
        return {"transcript": "Spain GDP grew 3 percent in 2024.",
                "turns": [], "audioDurationMs": 1000}

    grid = N.run_grid("k", root, post=_counting_post,
                      piece_ids=["k1_econ"], levels=["clean", "snr20"],
                      grid_path=grid_path, transcripts_dir=tdir)
    assert grid["n_cells"] == 2
    assert len(calls) == 2
    # Second run resumes: no new transcriptions.
    grid = N.run_grid("k", root, post=_counting_post,
                      piece_ids=["k1_econ"], levels=["clean", "snr20"],
                      grid_path=grid_path, transcripts_dir=tdir)
    assert grid["n_cells"] == 2
    assert len(calls) == 2
    # Overwrite forces re-transcription.
    grid = N.run_grid("k", root, post=_counting_post,
                      piece_ids=["k1_econ"], levels=["clean"],
                      grid_path=grid_path, transcripts_dir=tdir,
                      overwrite=True)
    assert grid["n_cells"] == 2
    assert len(calls) == 3
    assert piece["id"] == "k1_econ"


def test_run_grid_retries_transient_failures(tmp_path, monkeypatch):
    root = str(tmp_path)
    _scaffold_piece(root, "k1_econ", EN_TRUTH, EN_SRT, levels=("clean",))
    grid_path = os.path.join(root, "qa", "noise_grid.json")
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    calls = []

    def _flaky_post(url, headers, files, timeout_s):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("408 transient")
        return {"transcript": "Spain GDP grew 3 percent in 2024.",
                "turns": [], "audioDurationMs": 1000}

    grid = N.run_grid("k", root, post=_flaky_post,
                      piece_ids=["k1_econ"], levels=["clean"],
                      grid_path=grid_path,
                      transcripts_dir=os.path.join(root, "t"))
    assert grid["n_cells"] == 1
    assert len(calls) == 3

    def _dead_post(url, headers, files, timeout_s):
        raise RuntimeError("always down")

    with pytest.raises(RuntimeError):
        N.run_grid("k", root, post=_dead_post, piece_ids=["k1_econ"],
                   levels=["snr20"], grid_path=grid_path,
                   transcripts_dir=os.path.join(root, "t"), retries=2)


# ---------------------------------------------------------------------------
# Detection counts: doubles + real heuristic (offline)
# ---------------------------------------------------------------------------

class _DoubleDetector:
    def __init__(self, n):
        self._n = n
        self.turns = []

    def process_turn(self, turn, context_turns=None):
        self.turns.append(turn)
        return [object()] * self._n, ["double"]


def test_detect_counts_uses_injected_double():
    counts = N.detect_counts("anything at all here", detector=_DoubleDetector(3))
    assert counts["n_detected"] == 3
    assert counts["notes"] == ["double"]


def test_detect_counts_heuristic_offline():
    hyp = ("Spain GDP grew 3 percent in 2024. "
           "Unemployment fell to 5 percent last year. Hello there friend.")
    counts = N.detect_counts(hyp)
    assert counts["n_detected"] >= 2  # the two numeric sentences
    assert N.detect_counts("")["n_detected"] == 0
    assert N.detect_counts("hi")["n_detected"] == 0  # below floor


def test_run_e2e_rows_shape_and_rates(tmp_path):
    root = str(tmp_path)
    for pid, truth in (("k1_econ", EN_TRUTH),
                       ("keynote_economia", ES_TRUTH)):
        piece = piece_by_id(pid)
        _write(media_paths(piece, root)["truth"], json.dumps(truth))
    tdir = os.path.join(root, "data", "synth", "qa", "noise_transcripts")
    cells = []
    for pid in ("k1_econ", "keynote_economia"):
        for level in N.levels_for_lang(piece_by_id(pid)["lang"]):
            rel = os.path.join("data", "synth", "qa", "noise_transcripts",
                               f"{pid}_{level}.txt")
            _write(os.path.join(root, rel),
                   "Spain GDP grew 3 percent in 2024. Hello there friend.")
            cells.append({"piece": pid, "level": level, "wer": 0.05,
                          "recall": 0.9, "transcript": rel})
    grid_path = os.path.join(root, "data", "synth", "qa", "noise_grid.json")
    _write(grid_path, json.dumps({"cells": cells}))
    rows = N.run_e2e(root, grid_path=grid_path,
                     piece_ids=("k1_econ", "keynote_economia"))
    assert len(rows) == 8  # 2 pieces x 4 levels
    first = rows[0]
    assert first["piece"] == "k1_econ"
    assert first["level"] == "clean"
    assert first["n_truth"] == 2
    assert first["n_detected"] >= 1
    assert first["detection_rate"] == pytest.approx(
        first["n_detected"] / 2)
    assert first["usd"] == 0.0
    assert first["wer"] == 0.05
    # Levels stay in easy-first order per piece.
    assert [r["level"] for r in rows[:4]] == ["clean", "snr20", "snr10",
                                              "snr0"]
    assert [r["level"] for r in rows[4:]] == ["clean", "snr20", "snr10",
                                              "snr5"]
    # Rows persisted back into the grid.
    with open(grid_path, encoding="utf-8") as fh:
        assert len(json.load(fh)["e2e"]) == 8


def test_run_e2e_missing_transcript_is_zero_not_error(tmp_path):
    root = str(tmp_path)
    piece = piece_by_id("k1_econ")
    _write(media_paths(piece, root)["truth"], json.dumps(EN_TRUTH))
    grid_path = os.path.join(root, "g.json")
    _write(grid_path, json.dumps(
        {"cells": [{"piece": "k1_econ", "level": "clean",
                    "transcript": "no/such/file.txt"}]}))
    rows = N.run_e2e(root, grid_path=grid_path, piece_ids=("k1_econ",))
    assert len(rows) == 4
    assert all(r["n_detected"] == 0 for r in rows)


# ---------------------------------------------------------------------------
# report.py noise section + extended manifest schema (tmp root)
# ---------------------------------------------------------------------------

def _scaffold_repo(root):
    """Empty-but-present media files for all 12 pieces + qa stub."""
    for piece in PIECES:
        paths = media_paths(piece, root)
        _write(paths["mp4"], "mp4")
        _write(paths["srt"], "srt")
        _write(paths["truth"], json.dumps([{"text": "x"}]))
        _write(paths["wer"], json.dumps({"wer": 0.05, "recall": 0.9,
                                         "usd": 0.01}))
        for wav in wav_paths(piece, root).values():
            _tiny_wav(wav, seconds=0.1)
    qa_path = os.path.join(root, "data", "synth", "qa", "review.json")
    _write(qa_path, json.dumps({"results": []}))
    return qa_path


def test_noise_table_marks_spot_honestly(tmp_path):
    root = str(tmp_path)
    grid_path = os.path.join(root, "noise_grid.json")
    spot_path = os.path.join(root, "spot_wer.json")
    _write(grid_path, json.dumps({"generated_utc": "t", "cells": [
        {"piece": "k1_econ", "lang": "en", "level": "snr10", "wer": 0.05,
         "sub": 1, "ins": 1, "del": 0, "n_ref": 40, "n_hits": 38,
         "n_claims": 40, "usd": 0.01, "keywords": True},
    ], "e2e": [{"piece": "k1_econ"}]}))
    _write(spot_path, json.dumps({"cells": [
        {"piece": "k1_econ", "snr_db": 10, "wer": 0.0408, "recall": 0.95,
         "keywords": False},
        {"piece": "s1_sci", "snr_db": 20, "wer": 0.03, "recall": 1.0,
         "keywords": True},
    ]}))
    table = R.noise_table(root, grid_path=grid_path, spot_path=spot_path)
    assert table["n_cells"] == 1
    assert table["usd"] == pytest.approx(0.01)
    panel = table["by_lang_level"]["en"]["snr10"]
    assert panel["recall"] == pytest.approx(38 / 40)
    assert {"wilson_lo", "wilson_hi", "wer", "n_claims"} <= set(panel)
    assert table["e2e"] == [{"piece": "k1_econ"}]
    comp = {(c["piece"], c["snr_db"]): c
            for c in table["spot_comparison"]}
    # Same wav+scorer, different ASR config -> honestly not comparable.
    assert comp[("k1_econ", 10)]["comparable"] is False
    assert "keywords" in comp[("k1_econ", 10)]["reason"]
    assert comp[("k1_econ", 10)]["grid_wer"] == 0.05
    # No grid cell at all -> not comparable either.
    assert comp[("s1_sci", 20)]["comparable"] is False


def test_noise_table_empty_grid_is_benign(tmp_path):
    root = str(tmp_path)
    table = R.noise_table(root, grid_path=os.path.join(root, "nope.json"),
                          spot_path=os.path.join(root, "nope2.json"))
    assert table["n_cells"] == 0
    assert table["by_lang_level"] == {}
    assert table["spot_comparison"] == []
    assert table["usd"] == 0.0


def test_manifest_extended_schema(tmp_path):
    root = str(tmp_path)
    qa_path = _scaffold_repo(root)
    grid_path = os.path.join(root, "data", "synth", "qa", "noise_grid.json")
    _write(grid_path, json.dumps(
        {"generated_utc": "t",
         "cells": [{"piece": "k1_econ", "level": "snr10", "snr_db": 10,
                    "wer": 0.05, "recall": 0.9, "usd": 0.01}]}))
    manifest = R.build_manifest(root, qa_path, grid_path)
    assert manifest["n_pieces"] == 12
    assert set(manifest) >= {"generated_utc", "n_pieces", "pieces", "noise"}
    noise = manifest["noise"]
    assert noise["n_cells"] == 1
    assert noise["usd"] == pytest.approx(0.01)
    assert set(noise) >= {"grid", "n_cells", "usd", "wav_sha256", "cells"}
    assert noise["cells"] == {"k1_econ": {"snr10": {"wer": 0.05,
                                                   "recall": 0.9,
                                                   "snr_db": 10}}}
    assert set(noise["wav_sha256"]) == {p["id"] for p in PIECES}
    assert set(noise["wav_sha256"]["k1_econ"]) == {"clean", "snr20",
                                                   "snr10", "snr0"}
    assert set(noise["wav_sha256"]["panel_datos"]) == {"clean", "snr20",
                                                       "snr10", "snr5"}
    assert all(len(h or "") == 64
               for per_piece in noise["wav_sha256"].values()
               for h in per_piece.values())
    # S4 per-piece rows keep their exact schema (no per-row noise keys).
    first = manifest["pieces"][0]
    assert first["id"] == "k1_econ"
    assert set(first) == {"id", "lang", "kind", "speakers", "files",
                          "sha256", "voices", "licenses", "duration_s",
                          "qa_pass", "wer_clean", "recall_clean", "wer_usd"}
    assert set(first["sha256"]) == {"mp4", "srt", "truth"}
    assert first["wer_clean"] == 0.05
    assert first["recall_clean"] == 0.9


def test_manifest_without_grid_stays_valid(tmp_path):
    root = str(tmp_path)
    qa_path = _scaffold_repo(root)
    manifest = R.build_manifest(
        root, qa_path, os.path.join(root, "no-grid.json"))
    assert manifest["noise"]["n_cells"] == 0
    assert manifest["noise"]["usd"] == 0.0
    assert manifest["noise"]["cells"] == {}
    # Wav hashes do not depend on the grid.
    assert len(manifest["noise"]["wav_sha256"]) == 12
