"""Offline tests for the synthetic EN corpus (Atlas Live).

No network, no TTS, no ffmpeg: truths are validated structurally, the
badge is verified as a builder flag (not pixels), SNRs as distinct
constants, and the WER/keyword/recall helpers against exact fixtures.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "material"))

import build_corpus_en as B
import synth_en_data as se

PIECES = ["k1_econ", "k2_clim", "p1_panel", "s1_sci", "s2_sci", "e1_earn"]
TRUTH_DIR = os.path.join(B.MATERIAL, "truth_en")
EXPECTED_VALUES = {"true", "false", "unverifiable", "coherent-internal"}


def load_truth(piece):
    with open(os.path.join(TRUTH_DIR, f"{piece}.json"),
              encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# Truths well formed.
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("piece", PIECES)
def test_truth_count(piece):
    rows = load_truth(piece)
    assert 35 <= len(rows) <= 45, f"{piece}: {len(rows)} claims"


@pytest.mark.parametrize("piece", PIECES)
def test_truth_categories_covered(piece):
    rows = load_truth(piece)
    cats = {r["category"] for r in rows}
    assert cats == set("abcdef"), f"{piece}: {cats}"
    for c in "abcdef":
        n = sum(1 for r in rows if r["category"] == c)
        assert n >= 2, f"{piece}: category {c} has {n} claims"


@pytest.mark.parametrize("piece", PIECES)
def test_truth_minutes_nondecreasing(piece):
    rows = load_truth(piece)
    ts = [r["t_aprox"] for r in rows]
    assert ts == sorted(ts), f"{piece}: t_aprox not non-decreasing"
    ms = [r["minuto"] for r in rows]
    assert ms == sorted(ms), f"{piece}: minuto not non-decreasing"
    for r in rows:
        assert abs(r["minuto"] - r["t_aprox"] / 60.0) < 0.001, r["id"]


@pytest.mark.parametrize("piece", PIECES)
def test_truth_schema(piece):
    rows = load_truth(piece)
    ids = set()
    for r in rows:
        for k in ("id", "claim", "piece", "lang", "slide", "text",
                  "t_aprox", "minuto", "expected", "ref", "category",
                  "modality"):
            assert k in r, f"{r.get('id')}: missing {k}"
        assert r["id"] not in ids, f"duplicate {r['id']}"
        ids.add(r["id"])
        assert r["piece"] == piece
        assert r["lang"] == "en"
        assert r["expected"] in EXPECTED_VALUES, r["id"]
        assert r["modality"] in ("dicho", "mostrado", "ambos"), r["id"]
        assert r["text"] and r["ref"], r["id"]
        assert 1 <= r["slide"] <= 10, r["id"]


def test_coherent_internal_only_in_earnings():
    for piece in PIECES:
        rows = load_truth(piece)
        n = sum(1 for r in rows if r["expected"] == "coherent-internal")
        if piece == "e1_earn":
            assert n > 20, f"e1_earn should be mostly coherent-internal: {n}"
        else:
            assert n == 0, f"{piece}: coherent-internal outside earnings"


def test_wb_refs_point_at_existing_snapshots():
    manifest = B.load_manifest()
    checked = 0
    for piece in PIECES:
        for r in load_truth(piece):
            if "[snap " in r["ref"]:
                snap = r["snapshot"].rsplit("/", 1)[-1]
                assert snap in manifest, r["id"]
                assert os.path.exists(
                    os.path.join(B.SNAPDIR, snap)), r["id"]
                assert r["snapshot_sha256"] == manifest[snap]["sha256"]
                checked += 1
    assert checked > 60, f"only {checked} snapshot-backed claims"


def test_panel_uses_two_voices():
    slides = se.PIECES["p1_panel"]["slides"]
    voices = {s.get("voice", "A") for s in slides}
    assert voices == {"A", "B"}, voices
    other_voices = set()
    for p in PIECES:
        if p == "p1_panel":
            continue
        for s in se.PIECES[p]["slides"]:
            other_voices.add(s.get("voice", se.PIECES[p].get("voice", "A")))
    assert other_voices == {"A"}, other_voices


def test_keynotes_have_two_misleading_charts_each():
    # Each keynote piece references >= 2 misleading chart props
    # (truncated axis + said-vs-shown).
    misleading = {"trunc-usa", "rate-pop", "trunc-elec", "said-co2"}
    for piece in ("k1_econ", "k2_clim"):
        charts = {s["chart"] for s in se.PIECES[piece]["slides"]
                  if s["chart"]}
        assert len(charts & misleading) >= 2, f"{piece}: {charts}"


# ----------------------------------------------------------------------------
# Badge present: builder flag, not pixels.
# ----------------------------------------------------------------------------
def test_badge_flag_enabled():
    assert B.BADGE_ENABLED is True
    assert B.BADGE_TEXT == "Synthetic demo"


def test_render_defaults_to_badge():
    import inspect
    sig = inspect.signature(B.render_slide_png)
    assert sig.parameters["badge"].default is True


# ----------------------------------------------------------------------------
# SNRs distinct.
# ----------------------------------------------------------------------------
def test_snrs_distinct():
    assert len(B.SNR_DB) == 3
    assert len(set(B.SNR_DB)) == 3
    assert sorted(B.SNR_DB, reverse=True) == [20, 10, 0]


# ----------------------------------------------------------------------------
# WER helpers exact on fixtures.
# ----------------------------------------------------------------------------
def test_int_to_en_exact():
    assert B.int_to_en(0) == "zero"
    assert B.int_to_en(29) == "twenty nine"
    assert B.int_to_en(343) == "three hundred forty three"
    assert B.int_to_en(2024) == "two thousand twenty four"
    assert B.int_to_en(299792458) == (
        "two hundred ninety nine million seven hundred ninety two "
        "thousand four hundred fifty eight")


def test_expand_numbers_en_exact():
    assert B.expand_numbers_en("29.3") == "twenty nine point three"
    assert B.expand_numbers_en("5.60") == "five point six"
    assert B.expand_numbers_en("299,792,458") == B.int_to_en(299792458)
    assert B.expand_numbers_en("0.96") == "zero point nine six"
    assert B.expand_numbers_en("84.0") == "eighty four"


def test_wer_exact_fixtures():
    w = B.wer_details("the cat sat", "the cat sat")
    assert w == {"wer": 0.0, "sub": 0, "ins": 0, "del": 0,
                 "n_ref": 3, "n_hyp": 3}
    w = B.wer_details("the cat sat on the mat", "the cat sat mat")
    assert w["wer"] == pytest.approx(2 / 6)
    assert (w["sub"], w["ins"], w["del"]) == (0, 0, 2)
    w = B.wer_details("a b c", "a x c")
    assert (w["sub"], w["ins"], w["del"]) == (1, 0, 0)
    # Number expansion applies to both sides: digits vs words score equal.
    w = B.wer_details("value 29.3", "value twenty nine point three")
    assert w["wer"] == 0.0


def test_claim_recall_exact():
    rec = B.claim_recall(["the cat sat"], "well the cat sat here")
    assert rec["recall"] == 1.0
    rec = B.claim_recall(["the cat sat", "dogs never fly"],
                         "well the cat sat here")
    assert rec["recall"] == 0.5
    assert rec["misses"] == ["dogs never fly"]


def test_keywords_within_server_limit():
    for piece in PIECES:
        rows = load_truth(piece)
        kws = B.extract_keywords_en([r["text"] for r in rows])
        assert len(kws) > 20, f"{piece}: too few keywords"
        over = [k for k in kws if len(k) > B.MAX_KEYWORD_CHARS]
        assert over == [], f"{piece}: overlong keywords {over}"
    # The 31-char entity that once caused a server 400 is shingled.
    kws = B.extract_keywords_en(
        ["The World Meteorological Organization confirmed it."])
    assert "World Meteorological Organization" not in kws
    assert "World Meteorological" in kws
    assert "Meteorological Organization" in kws
