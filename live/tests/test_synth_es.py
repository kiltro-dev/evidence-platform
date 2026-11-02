"""Tests offline del corpus sintético ES (análogos a los del EN/keynote).

Totalmente offline: sin red, sin API key, sin TTS. No construyen el corpus;
validan guiones (PIECES), truths generados, helpers puros (SNR, WER) y
documentación. Los tests que necesitan artefactos de data/synth/es se
saltan (skip) si el corpus no está construido.
"""

import json
import os
import sys

import pytest

MATERIAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "material")
ROOT = os.path.dirname(os.path.dirname(MATERIAL))
sys.path.insert(0, MATERIAL)

from build_corpus_es import PIECES, mix_snr  # noqa: E402
from measure_wer import (  # noqa: E402
    asr_recall,
    extract_keywords,
    parse_srt,
    score_text,
    wer_details,
)

TRUTH_ES = os.path.join(MATERIAL, "truth_es")
OUTDIR = os.path.join(ROOT, "data", "synth", "es")
EXPECTED_FILES = {
    "K1": "keynote_economia.json", "K2": "keynote_ciencia.json",
    "P1": "panel_datos.json", "C1": "clase_poblacion.json",
    "C2": "clase_economia.json", "E1": "earnings_luminara.json",
}
REQUIRED_KEYS = {"id", "claim", "lang", "piece", "slide", "text", "t_aprox",
                 "category", "expected", "ref", "sources", "ground_truth",
                 "calculation"}


# ----------------------------------------------------------------------------
# Guiones (PIECES): 6 piezas x 40 claims.
# ----------------------------------------------------------------------------
def test_six_pieces_forty_claims_each():
    assert set(PIECES) == {"K1", "K2", "P1", "C1", "C2", "E1"}
    for pid, p in PIECES.items():
        n = sum(len(s["segs"]) for s in p["slides"])
        assert n == 40, f"{pid}: {n} != 40"
        assert len(p["slides"]) == 10, f"{pid}: slides != 10"


def test_cids_unique_globally():
    ids = [sg["cid"] for p in PIECES.values() for s in p["slides"]
           for sg in s["segs"]]
    assert len(ids) == 240
    assert len(set(ids)) == 240


def test_categories_and_expected_vocab():
    for pid, p in PIECES.items():
        for s in p["slides"]:
            for sg in s["segs"]:
                assert sg["category"] in "abcdef", sg["cid"]
                assert sg["text"] and sg["text"].strip(), sg["cid"]
                assert sg["ref"], sg["cid"]
    for pid in ("K1", "K2", "P1", "C1", "C2"):
        exps = {sg["expected"] for s in PIECES[pid]["slides"] for sg in s["segs"]}
        assert exps <= {"true", "false"}, f"{pid}: {exps}"
    exps_e1 = {sg["expected"] for s in PIECES["E1"]["slides"] for sg in s["segs"]}
    assert exps_e1 == {"coherent-internal"}, exps_e1


def test_each_piece_covers_a_to_f_or_declared_subset():
    # K1/K2/P1 cubren a-f; C1/C2 cubren a-f con b reducido; E1 es d/f interno.
    for pid in ("K1", "K2", "P1", "C1", "C2"):
        cats = {sg["category"] for s in PIECES[pid]["slides"] for sg in s["segs"]}
        assert cats == set("abcdef"), f"{pid}: {cats}"
    cats_e1 = {sg["category"] for s in PIECES["E1"]["slides"] for sg in s["segs"]}
    assert cats_e1 == {"d", "f"}, cats_e1


def test_model_reads_capped_per_piece():
    # Trues sin fuente primaria (WB/IMF/BLS) <= 5 por pieza.
    for pid in ("K1", "K2", "P1", "C1", "C2"):
        mr = [sg["cid"] for s in PIECES[pid]["slides"] for sg in s["segs"]
              if sg["expected"] == "true" and not sg.get("sources")]
        assert len(mr) <= 5, f"{pid}: {mr}"


def test_panel_has_two_voices():
    voices = [sg.get("voice", "A") for s in PIECES["P1"]["slides"]
              for sg in s["segs"]]
    assert set(voices) == {"A", "B"}
    assert voices.count("A") == voices.count("B") == 20


def test_keynotes_have_two_misleading_charts():
    misleading = {"truncated_usa", "broken_ind_usa", "truncated_life",
                  "mort_contrast", "truncated_mex", "truncated_arg",
                  "truncated_esp"}
    for pid in ("K1", "K2"):
        charts = [s["chart"] for s in PIECES[pid]["slides"] if s["chart"]]
        assert len([c for c in charts if c in misleading]) >= 2, pid


def test_no_real_persons_named():
    import re
    banned = re.compile(
        r"\b(sánchez|feijóo|trump|biden|milei|macron|scholz|lula|amlo|pedro sánchez)\b",
        re.I)
    for p in PIECES.values():
        for s in p["slides"]:
            for sg in s["segs"]:
                assert not banned.search(sg["text"]), sg["cid"]


# ----------------------------------------------------------------------------
# Coherencia aritmética interna de E1 (empresa ficticia).
# ----------------------------------------------------------------------------
E1_CHECKS = [
    ("margen ebitda", 201.5 / 847 * 100, 23.8),
    ("margen neto", 96.3 / 847 * 100, 11.4),
    ("crec ventas", 847 / 753.6 - 1, 0.124),
    ("suma 9M", 762 + 801 + 847, 2410),
    ("suma neto 9M", 81.2 + 90.5 + 96.3, 268.0),
    ("margen 9M", 268 / 2410 * 100, 11.1),
    ("mix", 512 + 335, 847),
    ("peso residencial", 512 / 847 * 100, 60.5),
    ("clientes", 2.34 / 2.16 - 1, 0.083),
    ("plantilla", 1840 / 1730 - 1, 0.064),
    ("deuda", 312 / 329.1 - 1, -0.052),
    ("bpa", 96.3 / 120, 0.80),
    ("dividendo", 0.42 / 0.36 - 1, 0.167),
    ("capex", 88 / 847 * 100, 10.4),
    ("guía", 3250 / 2907 - 1, 0.118),
    ("payout", (0.42 * 120) / 96.3 * 100, 52.3),
]


def test_e1_internal_arithmetic():
    for name, got, want in E1_CHECKS:
        assert abs(got - want) < 0.06, f"{name}: {got} vs {want}"


def test_e1_disclaims_fictional_company():
    texts = [sg["text"] for s in PIECES["E1"]["slides"] for sg in s["segs"]]
    joined = " ".join(texts)
    assert joined.count("ficticia") >= 3
    assert "Luminara" in joined


# ----------------------------------------------------------------------------
# truth_es/*.json generados.
# ----------------------------------------------------------------------------
def _load_truth(pid):
    p = os.path.join(TRUTH_ES, EXPECTED_FILES[pid])
    if not os.path.exists(p):
        pytest.skip(f"corpus no construido: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_truth_files_shape():
    for pid in EXPECTED_FILES:
        rows = _load_truth(pid)
        assert len(rows) == 40, pid
        for r in rows:
            assert REQUIRED_KEYS <= set(r), r.get("id")
            assert r["lang"] == "es" and r["piece"] == pid
            assert 1 <= r["slide"] <= 10
        ts = [r["t_aprox"] for r in rows]
        assert ts == sorted(ts), f"{pid}: t_aprox desordenado"
        ids = [r["id"] for r in rows]
        assert len(set(ids)) == 40


def test_truth_ids_unique_across_corpus():
    ids = []
    for pid in EXPECTED_FILES:
        ids += [r["id"] for r in _load_truth(pid)]
    assert len(ids) == 240 and len(set(ids)) == 240


# ----------------------------------------------------------------------------
# SNR (helpers puros).
# ----------------------------------------------------------------------------
def test_mix_snr_hits_target_and_is_deterministic():
    import numpy as np
    rng = np.random.default_rng(0)
    clean = (rng.standard_normal(16000 * 5) * 8000).astype(np.int16)
    for snr in (20, 10, 5):
        y, meas = mix_snr(clean, 16000, snr, seed=100 + snr)
        assert abs(meas - snr) < 0.1, (snr, meas)
        assert y.dtype == np.int16 and len(y) == len(clean)
    y1, _ = mix_snr(clean, 16000, 10, seed=42)
    y2, _ = mix_snr(clean, 16000, 10, seed=42)
    y3, _ = mix_snr(clean, 16000, 10, seed=43)
    assert (y1 == y2).all() and not (y1 == y3).all()


# ----------------------------------------------------------------------------
# WER / keywords sobre el ES del corpus (reutiliza measure_wer).
# ----------------------------------------------------------------------------
def test_keywords_cover_corpus_entities():
    texts = [sg["text"] for p in PIECES.values() for s in p["slides"]
             for sg in s["segs"]]
    kws = extract_keywords(texts)
    for e in ["Luminara", "Bilbao", "FMI", "Estados Unidos",
              "Everest", "Sáhara", "Japón", "EBITDA", "Luminara"]:
        assert e in kws or e.lower() in [k.lower() for k in kws], e


def test_keywords_cover_every_corpus_number():
    import re
    texts = [sg["text"] for p in PIECES.values() for s in p["slides"]
             for sg in s["segs"]]
    kws = extract_keywords(texts)
    for t in texts:
        for num in re.findall(r"\d[\d.,]*", t):
            num = num.rstrip(".,")
            assert num in kws, f"{num} de {t[:60]} sin keyword"


def test_wer_digits_to_words_spanish():
    ref = "Luminara tiene 2,34 millones de clientes"
    hyp = "luminara tiene dos coma treinta y cuatro millones de clientes"
    assert wer_details(ref, hyp)["wer"] == 0.0
    assert score_text("En sep-2026.") == "en sep dos mil veintiseis"


def test_asr_recall_perfect_corpus_span():
    spans = ["El agua hierve a 100 grados Celsius al nivel del mar.",
             "Luminara tiene 2,34 millones de clientes."]
    hyp = ("el agua hierve a cien grados celsius al nivel del mar "
           "luminara tiene dos coma treinta y cuatro millones de clientes")
    assert asr_recall(spans, hyp)["recall"] == 1.0


# ----------------------------------------------------------------------------
# Charts + slides (offline, matplotlib).
# ----------------------------------------------------------------------------
def test_charts_build_offline(tmp_path, monkeypatch):
    import build_corpus_es as b
    monkeypatch.setattr(b, "CHARTS", str(tmp_path))
    paths = b.make_charts()
    assert len(paths) == 14
    for k, p in paths.items():
        assert os.path.getsize(p) > 5000, k


def test_slide_renders_1080p(tmp_path):
    from PIL import Image
    import build_corpus_es as b
    charts = {c: os.path.join(ROOT, "data", "synth", "es", "charts",
                              f"chart_{c}.png")
              for c in ("growth_bars",)}
    if not os.path.exists(charts["growth_bars"]):
        pytest.skip("charts no construidos")
    piece = PIECES["K1"]
    slide = dict(piece["slides"][4])
    png = b.render_slide_png(piece, slide, charts, str(tmp_path))
    with Image.open(png) as im:
        assert im.size == (1920, 1080)


def test_srt_entries_parse(tmp_path):
    p = tmp_path / "t.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:02,500\nHola mundo\n\n"
                 "2\n00:00:02,500 --> 00:00:05,000\nAdiós mundo\n",
                 encoding="utf-8")
    blocks = parse_srt(str(p))
    assert len(blocks) == 2 and blocks[0][2] == "Hola mundo"


# ----------------------------------------------------------------------------
# Artefactos (skip si el corpus no está construido).
# ----------------------------------------------------------------------------
def _out(pid, ext):
    return os.path.join(OUTDIR, PIECES[pid]["file"] + ext)


def test_artifacts_present_and_sized():
    for pid in PIECES:
        mp4 = _out(pid, ".mp4")
        if not os.path.exists(mp4):
            pytest.skip("corpus no construido")
        assert os.path.getsize(mp4) > 100_000, pid
        assert os.path.exists(_out(pid, ".srt")), pid
        for suf in ("_clean.wav", "_snr20db.wav", "_snr10db.wav", "_snr5db.wav"):
            w = _out(pid, suf)
            assert os.path.exists(w), w
            assert os.path.getsize(w) > 100_000, w


def test_durations_inside_window():
    import wave
    for pid in PIECES:
        w = _out(pid, "_clean.wav")
        if not os.path.exists(w):
            pytest.skip("corpus no construido")
        with wave.open(w, "rb") as fh:
            dur = fh.getnframes() / fh.getframerate()
        # El mp4 añade PAD por slide sobre el mix; el mix debe estar cerca.
        assert 140 <= dur <= 300, f"{pid}: {dur:.1f}s"


def test_snapshots_present_with_hashes():
    snap = os.path.join(OUTDIR, "snap")
    sums = os.path.join(snap, "SHA256SUMS.txt")
    if not os.path.exists(sums):
        pytest.skip("snapshots no descargados")
    assert os.path.getsize(sums) > 100


def test_provenance_mentions_voice_and_variant():
    prov = os.path.join(MATERIAL, "PROVENANCE_ES.md")
    assert os.path.exists(prov)
    body = open(prov, encoding="utf-8").read()
    assert "carlfm" in body and "dominio público" in body
    assert "variante" in body.lower()
