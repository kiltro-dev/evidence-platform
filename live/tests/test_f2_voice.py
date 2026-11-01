"""F2-4 native-ES voice tests: WER, fuzzy-match, keyword coverage.

Fully offline: no network, no API key, no TTS. The live transcribe path
(measure_wer.main) is exercised manually and reported in live/F2_VOICE.md.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "live", "material"))

from measure_wer import (  # noqa: E402
    asr_recall,
    expand_numbers_es,
    extract_keywords,
    int_to_es,
    load_truth_es,
    normalize,
    parse_srt,
    score_text,
    span_found,
    wer_details,
)

TRUTH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "material", "truth.json")


# ----------------------------------------------------------------------------
# Normalization.
# ----------------------------------------------------------------------------
def test_normalize_accents_case_punct():
    assert normalize("El Sáhara, ¡qué grande!") == "el sahara que grande"
    assert normalize("29,3 BILLONES.") == "29 3 billones"


# ----------------------------------------------------------------------------
# WER exact values on fixtures.
# ----------------------------------------------------------------------------
def test_wer_identical_is_zero():
    ref = "el pib de alemania fue de 4 69 billones"
    assert wer_details(ref, ref)["wer"] == 0.0


def test_wer_empty_hypothesis_is_one():
    w = wer_details("uno dos tres", "")
    assert w["wer"] == 1.0
    assert w["del"] == 3


def test_wer_one_substitution():
    w = wer_details("uno dos tres cuatro", "uno dos CINCO cuatro")
    assert w == {"wer": 0.25, "sub": 1, "ins": 0, "del": 0,
                 "n_ref": 4, "n_hyp": 4}


def test_wer_insertion():
    w = wer_details("a b c", "a b x c")
    assert w == {"wer": 1 / 3, "sub": 0, "ins": 1, "del": 0,
                 "n_ref": 3, "n_hyp": 4}


def test_wer_deletion():
    w = wer_details("a b c d", "a b d")
    assert w == {"wer": 0.25, "sub": 0, "ins": 0, "del": 1,
                 "n_ref": 4, "n_hyp": 3}


def test_wer_ignores_accents_and_case():
    assert wer_details("El Sáhara abarca", "el sahara abarca")["wer"] == 0.0


# ----------------------------------------------------------------------------
# Spanish number expansion.
# ----------------------------------------------------------------------------
def test_int_to_es_units_tens_hundreds():
    assert int_to_es(0) == "cero"
    assert int_to_es(21) == "veintiuno"
    assert int_to_es(31) == "treinta y uno"
    assert int_to_es(100) == "cien"
    assert int_to_es(101) == "ciento uno"
    assert int_to_es(141) == "ciento cuarenta y uno"
    assert int_to_es(500) == "quinientos"


def test_int_to_es_thousands_millions():
    assert int_to_es(1000) == "mil"
    assert int_to_es(1410) == "mil cuatrocientos diez"
    assert int_to_es(2024) == "dos mil veinticuatro"
    assert int_to_es(40075) == "cuarenta mil setenta y cinco"
    assert int_to_es(920000) == "novecientos veinte mil"
    assert int_to_es(299792458) == (
        "doscientos noventa y nueve millones setecientos noventa y dos "
        "mil cuatrocientos cincuenta y ocho")


def test_expand_numbers_decimals():
    assert expand_numbers_es("29,3") == "veintinueve coma tres"
    assert expand_numbers_es("4,69") == "cuatro coma sesenta y nueve"
    assert expand_numbers_es("4,19 billones") == "cuatro coma diecinueve billones"


def test_wer_matches_digits_to_spelled_words():
    ref = "Somos 8140 millones de personas"
    hyp = "somos ocho mil ciento cuarenta millones de personas"
    assert wer_details(ref, hyp)["wer"] == 0.0
    assert score_text("En 2024.") == "en dos mil veinticuatro"


def test_recall_matches_digits_to_spelled_words():
    spans = ["El agua hierve a 100 grados Celsius al nivel del mar."]
    hyp = "el agua hierve a cien grados celsius al nivel del mar"
    assert asr_recall(spans, hyp)["recall"] == 1.0


# ----------------------------------------------------------------------------
# Fuzzy span match.
# ----------------------------------------------------------------------------
HYP = ("bienvenidos a la mejor cumbre del planeta esta charla cambiara "
       "su forma de ver el mundo")


def _found(span):
    return span_found(normalize(HYP), normalize(HYP).split(),
                      normalize(span))


def test_fuzzy_literal_hit():
    assert _found("Bienvenidos a la mejor cumbre del planeta.")


def test_fuzzy_near_miss_within_threshold():
    # 1 word differs out of 8 -> token ratio 0.875... use longer span:
    # 1 word differs out of 12 -> 12/13 window ratio > 0.9.
    span = ("bienvenidos a la mejor cumbre del planeta esta charla "
            "cambiara su forma de ver el orbe")
    assert _found(span)


def test_fuzzy_different_span_is_miss():
    assert not _found("La economía de Japón se disparó sin control alguno")


def test_fuzzy_short_span_requires_literal():
    assert _found("mejor cumbre")
    assert not _found("mejorx cumbre")


# ----------------------------------------------------------------------------
# Keywords cover ALL ES entities in truth.json.
# ----------------------------------------------------------------------------
def test_keywords_cover_named_entities():
    rows = load_truth_es(TRUTH)
    assert len(rows) == 41
    kws = extract_keywords([r["text"] for r in rows])
    expected = ["Estados Unidos", "Banco Mundial", "Naciones Unidas",
                "China", "Brasil", "Alemania", "Francia", "Japón", "India",
                "Asia", "Everest", "Sáhara", "Nepal", "Sídney", "Australia",
                "Celsius", "PIB", "noviembre"]
    missing = [e for e in expected if e not in kws]
    assert not missing, f"missing entities: {missing}"


def test_keywords_cover_every_number_in_truth():
    import re
    rows = load_truth_es(TRUTH)
    kws = extract_keywords([r["text"] for r in rows])
    for r in rows:
        for num in re.findall(r"\d[\d.,]*", r["text"]):
            num = num.rstrip(".,")
            assert num in kws, f"{num} from {r['id']} not in keywords"


def test_keywords_cover_quantities():
    rows = load_truth_es(TRUTH)
    kws = extract_keywords([r["text"] for r in rows])
    for q in ["8140 millones", "29,3 billones", "40 por ciento",
              "100 grados", "8849 metros", "920000 kilómetros"]:
        assert q in kws, f"{q} not in keywords"


def test_keywords_deduped_and_bounded():
    rows = load_truth_es(TRUTH)
    kws = extract_keywords([r["text"] for r in rows])
    assert len(kws) == len(set(kws))
    assert len(kws) < 200  # sane payload for the transcribe request


# ----------------------------------------------------------------------------
# SRT parsing + recall wiring.
# ----------------------------------------------------------------------------
def test_parse_srt_two_entries(tmp_path):
    srt = ("1\n00:00:00,000 --> 00:00:02,500\nHola mundo\n\n"
           "2\n00:00:02,500 --> 00:00:05,000\nAdiós mundo\n")
    p = tmp_path / "t.srt"
    p.write_text(srt, encoding="utf-8")
    blocks = parse_srt(str(p))
    assert len(blocks) == 2
    assert blocks[0] == (0.0, 2.5, "Hola mundo")
    assert blocks[1][2] == "Adiós mundo"


def test_asr_recall_perfect_and_partial():
    spans = ["Bienvenidos a la mejor cumbre del planeta.",
             "Esta charla cambiará su forma de ver el mundo."]
    full = asr_recall(spans, HYP)
    assert full["recall"] == 1.0
    partial = asr_recall(spans, "bienvenidos a la mejor cumbre del planeta")
    assert partial["recall"] == 0.5
    assert partial["misses"] == [spans[1]]
