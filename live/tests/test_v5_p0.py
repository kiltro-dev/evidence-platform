"""V5-P0 arithmetic guard + deterministic compute. Offline only ($0).

(a) GUARD: claims needing computation (difference, ratio, percent change,
average, sum — EN/ES patterns, never claim ids) that the code did not
compute from verified values are unverifiable, NEVER contradicted.
(b) COMPUTE: derive-and-compare over re-extracted endpoint values with
aligned year/unit/scale; 2% tolerance on the RESULT after rounding to
the claim's stated precision; every derived verdict carries a receipt.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    EvidencePackage,
    NumberMention,
    SourceMeta,
    sha256_of_text,
)
from live.run_verify import claim_input_for  # noqa: E402
from live.verifier.arithmetic import (  # noqa: E402
    claim_quantum,
    compute_claim,
    compute_verdict,
    needs_computation,
    round_to_quantum,
)
from live.verifier.check import verify_claim  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _wb_body(value, date="2024", iso3="USA",
             indicator="NY.GDP.MKTP.CD"):
    return (
        '[{"page":1}, '
        f'[{{"indicator":{{"id":"{indicator}"}},'
        f'"countryiso3code":"{iso3}","date":"{date}","value":{value}}}]]'
    )


def _wb_quote(body, date="2024"):
    i = body.find(f'"date":"{date}"')
    assert i >= 0
    j = body.find("}", i)
    return body[i:j]


_WB_UNITS = {"NY.GDP.MKTP.CD": "USD", "SP.POP.TOTL": "count"}


def _wb_pkg(claim_id, pid, value, date="2024", iso3="USA",
            indicator="NY.GDP.MKTP.CD", year=2024):
    body = _wb_body(value, date, iso3, indicator)
    quote = _wb_quote(body, date)
    return body, EvidencePackage(
        package_id=pid, claim_id=claim_id,
        url=f"https://api.worldbank.org/v2/country/{iso3}/x",
        captured_at="2026-11-02T00:00:00+00:00",
        sha256=sha256_of_text(body),
        snapshot_path="data/snap/fixture.txt", quote=quote,
        offsets=(0, len(quote)), source_type="primary",
        numbers=(NumberMention(value=0.0, unit=None, scale_raw=""),),
        extractor="code", source_year=year,
        source_meta=SourceMeta(indicator, _WB_UNITS[indicator], 1.0))


def _loader_for(*bodies):
    snaps = {sha256_of_text(b): b for b in bodies}
    return lambda p: snaps[p.sha256]


# ---------------------------------------------------------------------------
# needs_computation: 5 ops x EN/ES
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,op", [
    # diff
    ("India surpassed China by 42.0 million people in 2024.", "diff"),
    ("The gap between the two cities was 1.2 million people.", "diff"),
    ("China lost 1.7 million people between 2023 and 2024.", "diff"),
    ("La India superó a China en 42,0 millones de habitantes en 2024.",
     "diff"),
    ("China perdió 1,7 millones de habitantes entre 2023 y 2024.", "diff"),
    ("La brecha entre EE. UU. y China fue de 10,57 billones.", "diff"),
    ("Llegaron 42 millones más que el año anterior.", "diff"),
    # ratio
    ("In 2024, the United States GDP was 779 percent of India's GDP.",
     "ratio"),
    ("The debt was 7.8 times larger than revenue.", "ratio"),
    ("El PIB fue el 150 por ciento del de Chile en 2024.", "ratio"),
    ("Era 2,5 veces mayor que el del país vecino.", "ratio"),
    # pct_change
    ("The United States GDP grew 5.3 percent from 2023 to 2024.",
     "pct_change"),
    ("GDP fell 2.1 percent between 2023 and 2024.", "pct_change"),
    ("El PIB creció 5,3 por ciento de 2023 a 2024.", "pct_change"),
    ("La población cayó 1,2 por ciento entre 2023 y 2024.", "pct_change"),
    # average (closed-set of-frame only)
    ("The average GDP of Spain and Italy in 2024 was 2.05 trillion dollars.",
     "average"),
    ("Japan and South Korea averaged 83.8 years of life expectancy in 2024.",
     "average"),
    ("El PIB medio de España e Italia en 2024 fue de 2,05 billones.",
     "average"),
    ("El promedio de ingresos de ambos fue de 5 billones.", "average"),
    # sum
    ("The combined GDP of Spain and Italy was 4.1 trillion dollars.",
     "sum"),
    ("The two segments sum to 4.82 billion.", "sum"),
    ("China and the US together span 18.5 million square kilometers.",
     "sum"),
    ("La suma de ambos países fue de 5 billones.", "sum"),
    ("Juntos, ambos sumaron 5 billones de dólares.", "sum"),
])
def test_needs_computation_fires(text, op):
    assert needs_computation(text) == op


@pytest.mark.parametrize("text", [
    # level assertions (EN mirrors the package guard; ES is V5-P0)
    "Banaba GDP doubled to 800 billion dollars in 2024.",
    "Banaba GDP grew to 600 billion dollars in 2024.",
    "The jobless rate fell to 3.1 percent in September.",
    "El PIB llegó a 5 billones en 2024.",
    "La población alcanzó 48 millones en 2024.",
    "El PIB creció a 5 billones en 2024.",
    # bounds are levels, not derivations
    "GDP was more than 5 trillion dollars in 2024.",
    "El PIB fue de más de 5 billones en 2024.",
    "La población mundial cayó por debajo de 8000 millones en 2024.",
    # single-year rates are directly stated (B1/B2 class)
    "The IMF's October 2026 World Economic Outlook projects global "
    "growth of 3.1 percent in 2026.",
    "The IMF's October 2026 World Economic Outlook projects global "
    "growth of 2.1 percent in 2026.",
    "Banaba GDP growth of 5 percent in 2024 outpaced its peers.",
    # scientific notation is not a ratio
    "One mole holds 6.02214076 times 10 to the 23 particles, exactly.",
    # rankings / levels with a comparative rider (no gap figure)
    "España superó a Italia en PIB en 2024.",
    "El Reino Unido produjo 4,70 billones, superando a Alemania.",
    "La India ya supera a Estados Unidos.",
    # open temporal aggregation has no closed input set
    "Unemployment in the United States averaged 4.0 percent in 2024.",
    "Carbon dioxide averaged 422.8 parts per million in 2024.",
    # "medio" look-alikes
    "El medio ambiente mejoró en 2024.",
    "La producción en medio de la crisis fue de 5 billones.",
    "Los medios produjeron 5 billones en 2024.",
    # plain levels
    "Chile had 12,500 people in 2024.",
    "Sound travels at 343 meters per second.",
    "Population, total was 8 billion.",
])
def test_needs_computation_silent(text):
    assert needs_computation(text) is None


@pytest.mark.parametrize("text", ["", None, 12345, "   "])
def test_needs_computation_never_raises(text):
    assert needs_computation(text) is None


def test_needs_computation_dev_only_d_fires():
    """Precision pin: on dev.json exactly the 6 d-claims fire."""
    with open(os.path.join(HERE, "..", "eval2", "dev.json"),
              encoding="utf-8") as fh:
        dev = json.load(fh)
    assert len(dev) == 36
    for entry in dev:
        op = needs_computation(entry["text"])
        if entry["category"] == "d":
            assert op is not None, entry["id"]
        else:
            assert op is None, (entry["id"], op)


def test_needs_computation_quote_side():
    """Quotes stating a computed figure engage directly (sound refute)."""
    assert needs_computation(
        "Banaba GDP grew 7.4 percent from 2023 to 2024") == "pct_change"
    quote = '"countryiso3code":"USA","date":"2024","value":29298013000000'
    assert needs_computation(quote) is None


# ---------------------------------------------------------------------------
# compute_claim: exact deterministic arithmetic
# ---------------------------------------------------------------------------

def test_compute_diff_c1d5_truth_numbers():
    comp = compute_claim([1450935791, 1408975000], "diff")
    assert comp is not None
    assert comp.result == 41960791  # truth: 1450935791-1408975000


def test_compute_diff_c1d7_truth_numbers():
    comp = compute_claim([130861007, 52886363], "diff")
    assert comp is not None
    assert comp.result == 77974644


def test_compute_ratio_d3_truth_numbers():
    comp = compute_claim([29298013000000, 3760813470500.86], "ratio")
    assert comp is not None
    assert comp.result == pytest.approx(7.7903, rel=1e-4)


def test_compute_pct_d1_truth_numbers():
    comp = compute_claim([27811517000000, 29298013000000], "pct_change")
    assert comp is not None
    assert comp.result == pytest.approx(5.345, rel=1e-4)


def test_compute_average_d5_truth_numbers():
    comp = compute_claim([1725671652742.19, 2383435562458.12], "average")
    assert comp is not None
    assert comp.result == pytest.approx(2054553607600.155, rel=1e-9)


def test_compute_sum_three_way():
    comp = compute_claim([1.0, 2.0, 3.0], "sum")
    assert comp is not None and comp.result == 6.0


@pytest.mark.parametrize("values,op", [
    ([1.0], "diff"),            # binary arity
    ([1.0, 2.0, 3.0], "diff"),
    ([1.0, 0.0], "ratio"),      # division by zero
    ([0.0, 5.0], "pct_change"),  # zero base
    ([7.0], "average"),         # n-ary arity
    ([1.0], "sum"),
    ([1.0, float("nan")], "sum"),  # non-finite
    ([1.0, float("inf")], "diff"),
    (None, "diff"),
    ([1.0, 2.0], "nope"),       # unknown op
])
def test_compute_claim_rejects(values, op):
    assert compute_claim(values, op) is None


def test_compute_claim_never_raises():
    assert compute_claim("junk", "sum") is None
    assert compute_claim([object(), 1.0], "diff") is None


# ---------------------------------------------------------------------------
# rounding: stated precision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,value,quantum", [
    ("1,7 millones", 1.7e6, 1e5),       # C1D3: 0.1M
    ("42,0 millones", 42.0e6, 1e5),     # C1D5
    ("78,0 millones", 78.0e6, 1e5),     # C1D7
    ("5.3", 5.3, 0.1),                  # D1
    ("779", 779.0, 1.0),                # D3
    ("2.05 trillion", 2.05e12, 1e10),   # D5
    ("27,811,517,000,000", 27811517e6, 1.0),  # EN thousands
    ("1.408.975", 1408975.0, 1.0),      # ES thousands
])
def test_claim_quantum(raw, value, quantum):
    assert claim_quantum(raw, value) == pytest.approx(quantum)


@pytest.mark.parametrize("raw,value", [
    ("", 5.0), ("no digits", 5.0), ("5.3", 0.0),
    ("1,735", 1735.0),  # ambiguous token resolves to thousands (q=1.0)?
])
def test_claim_quantum_none_or_thousands(raw, value):
    q = claim_quantum(raw, value)
    if raw == "1,735":
        assert q == pytest.approx(1.0)
    else:
        assert q is None


@pytest.mark.parametrize("result,quantum,expected", [
    (1735000.0, 1e5, 1700000.0),       # C1D3: 1.735M -> 1.7M
    (41960791.0, 1e5, 42000000.0),     # C1D5: 41.96M -> 42.0M
    (77974644.0, 1e5, 78000000.0),     # C1D7: 77.97M -> 78.0M
    (5.345, 0.1, 5.3),                 # D1
    (779.03, 1.0, 779.0),              # D3
    (2054553607600.15, 1e10, 2.05e12),  # D5
    (5.345, None, 5.345),              # no quantum: identity
])
def test_round_to_quantum(result, quantum, expected):
    assert round_to_quantum(result, quantum) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# guard: computation claims without computation never contradict
# ---------------------------------------------------------------------------

def _guard_case(text, value, reason, indicator="NY.GDP.MKTP.CD"):
    """One mismatching component row vs a computation claim."""
    claim = claim_input_for({"id": "g", "text": text})
    body, pkg = _wb_pkg("g", "pkg_g", value, "2024", "USA", indicator)
    verdict = verify_claim(claim, [pkg], _loader_for(body))
    assert verdict.verdict == "unverifiable", verdict
    assert reason in verdict.reasons, verdict.reasons
    assert verdict.verdict != "contradicted"


def test_guard_diff_en():
    # "surpassed by" is new V5-P0 coverage (old guard silent here).
    _guard_case("India surpassed China by 42.0 million people in 2024.",
                1450935791, "arithmetic_not_computed", "SP.POP.TOTL")


def test_guard_diff_es():
    _guard_case("La India superó a China en 42,0 millones de habitantes "
                "en 2024.",
                1450935791, "arithmetic_not_computed", "SP.POP.TOTL")


def test_guard_ratio_times():
    _guard_case("The 7.8 trillion dollar debt was 7.8 times larger than "
                "revenue in 2024.",
                29298013000000, "arithmetic_not_computed")


def test_guard_sum_en():
    _guard_case("The combined GDP of Spain and Italy was 4.1 trillion "
                "dollars in 2024.", 1725671652742.19,
                "arithmetic_not_computed")


def test_guard_sum_es():
    _guard_case("La suma de ambos países fue de 5 billones de dólares "
                "en 2024.",
                1725671652742.19, "arithmetic_not_computed")


def test_guard_average_keeps_legacy_reason():
    # Average/pct/ratio-of were already guarded pre-V5-P0: the legacy
    # reason string is preserved (existing suite pins it).
    claim = claim_input_for(
        {"id": "g", "text": "The average GDP of Spain and Italy in 2024 "
                            "was 9 trillion dollars."})
    body, pkg = _wb_pkg("g", "pkg_g", 1725671652742.19)
    verdict = verify_claim(claim, [pkg], _loader_for(body))
    assert verdict.verdict == "unverifiable", verdict
    assert "arithmetic_needs_computation" in verdict.reasons


def test_guard_pct_span_keeps_legacy_reason():
    claim = claim_input_for(
        {"id": "g", "text": "Banaba GDP grew 12.4 percent from 2023 "
                            "to 2024."})
    snap = "Report: the rate was 2.1 percent in 2024."
    quote = "the rate was 2.1 percent in 2024"
    pkg = EvidencePackage(
        package_id="pkg_g", claim_id="g", url="https://example.com/r",
        captured_at="2026-01-01T00:00:00", sha256=sha256_of_text(snap),
        snapshot_path="data/snap/f.txt", quote=quote,
        offsets=(0, len(quote)), source_type="secondary", numbers=(),
        extractor="code", source_year=2024)
    verdict = verify_claim(claim, [pkg], lambda p: snap)
    assert verdict.verdict == "unverifiable", verdict
    assert "arithmetic_needs_computation" in verdict.reasons


# ---------------------------------------------------------------------------
# regression: C1D3 / C1D5 / C1D7 (S4 §3.4 falsos) with real claim texts
# ---------------------------------------------------------------------------

def _c1_case(cid, text, endpoints, expected):
    """Two WB population rows -> derived verdict with receipt."""
    claim = claim_input_for({"id": cid, "text": text})
    bodies, pkgs = [], []
    for i, (iso3, value, date, year) in enumerate(endpoints):
        body, pkg = _wb_pkg(cid, f"pkg_{cid}_{i}", value, date, iso3,
                            "SP.POP.TOTL", year)
        bodies.append(body)
        pkgs.append(pkg)
    verdict = verify_claim(claim, pkgs, _loader_for(*bodies))
    assert verdict.verdict == expected, verdict
    return verdict


def test_c1d5_supported_with_receipt():
    v = _c1_case(
        "C1D5", "La India superó a China en 42,0 millones de habitantes "
                "en 2024.",
        [("IND", 1450935791, "2024", 2024),
         ("CHN", 1408975000, "2024", 2024)],
        "supported")
    assert v.reasons == ["computed_match"]
    comp = v.details["computation"]
    assert comp["op"] == "diff"
    assert sorted(i["value"] for i in comp["inputs"]) == [
        1408975000, 1450935791]
    assert comp["rounded_result"] == pytest.approx(42000000.0)


def test_c1d7_supported_with_receipt():
    v = _c1_case(
        "C1D7", "México superó a Colombia en 78,0 millones de habitantes "
                "en 2024.",
        [("MEX", 130861007, "2024", 2024),
         ("COL", 52886363, "2024", 2024)],
        "supported")
    assert v.reasons == ["computed_match"]
    assert v.details["computation"]["op"] == "diff"


def test_c1d3_temporal_diff_supported():
    v = _c1_case(
        "C1D3", "China perdió 1,7 millones de habitantes entre 2023 "
                "y 2024.",
        [("CHN", 1410710000, "2023", 2023),
         ("CHN", 1408975000, "2024", 2024)],
        "supported")
    assert v.reasons == ["computed_match"]
    comp = v.details["computation"]
    assert comp["op"] == "diff"
    assert comp["rounded_result"] == pytest.approx(1700000.0)


@pytest.mark.parametrize("text,endpoints", [
    ("La India superó a China en 42,0 millones de habitantes en 2024.",
     [("IND", 1450935791, "2024", 2024)]),  # one endpoint only
    ("México superó a Colombia en 78,0 millones de habitantes en 2024.",
     [("MEX", 130861007, "2024", 2024)]),
    ("China perdió 1,7 millones de habitantes entre 2023 y 2024.",
     [("CHN", 1408975000, "2024", 2024)]),
])
def test_c1_single_endpoint_abstains(text, endpoints):
    v = _c1_case("Cx", text, endpoints, "unverifiable")
    assert "arithmetic_not_computed" in v.reasons


# ---------------------------------------------------------------------------
# dev d-claims end to end (real texts, F2-1 endpoint values)
# ---------------------------------------------------------------------------

def test_d3_ratio_supported():
    claim = claim_input_for(
        {"id": "F2D-D3",
         "text": "In 2024, the United States GDP was 779 percent of "
                 "India's GDP, according to the World Bank."})
    b1, p1 = _wb_pkg("F2D-D3", "p1", 29298013000000, "2024", "USA")
    b2, p2 = _wb_pkg("F2D-D3", "p2", 3760813470500.86, "2024", "IND")
    verdict = verify_claim(claim, [p1, p2], _loader_for(b1, b2))
    assert verdict.verdict == "supported", verdict
    assert verdict.reasons == ["computed_match"]
    comp = verdict.details["computation"]
    assert comp["op"] == "ratio"
    assert comp["rounded_result"] == pytest.approx(779.0)


def test_d5_average_supported_dedupes_repeat_packages():
    """F2-1 brought each endpoint twice: duplicates collapse to one input."""
    claim = claim_input_for(
        {"id": "F2D-D5",
         "text": "The average GDP of Spain and Italy in 2024 was 2.05 "
                 "trillion dollars, according to the World Bank."})
    b1, p1 = _wb_pkg("F2D-D5", "p1", 1725671652742.19, "2024", "ESP")
    b2, p2 = _wb_pkg("F2D-D5", "p2", 2383435562458.12, "2024", "ITA")
    b3, p3 = _wb_pkg("F2D-D5", "p3", 1725671652742.19, "2024", "ESP")
    b4, p4 = _wb_pkg("F2D-D5", "p4", 2383435562458.12, "2024", "ITA")
    verdict = verify_claim(claim, [p1, p2, p3, p4],
                           _loader_for(b1, b2, b3, b4))
    assert verdict.verdict == "supported", verdict
    comp = verdict.details["computation"]
    assert comp["op"] == "average"
    assert len(comp["inputs"]) == 2
    assert any("duplicate_value" in n for n in comp["operand_notes"])


def test_d1_pct_supported():
    claim = claim_input_for(
        {"id": "F2D-D1",
         "text": "The United States GDP grew 5.3 percent from 2023 to "
                 "2024, according to the World Bank."})
    b1, p1 = _wb_pkg("F2D-D1", "p1", 27811517000000, "2023", "USA",
                     "NY.GDP.MKTP.CD", 2023)
    b2, p2 = _wb_pkg("F2D-D1", "p2", 29298013000000, "2024", "USA")
    verdict = verify_claim(claim, [p1, p2], _loader_for(b1, b2))
    assert verdict.verdict == "supported", verdict
    comp = verdict.details["computation"]
    assert comp["op"] == "pct_change"
    assert comp["rounded_result"] == pytest.approx(5.3)


@pytest.mark.parametrize("cid,text,op1,op2", [
    ("F2D-D2",  # 12.4% claimed, 7.424% derived
     "India's GDP grew 12.4 percent from 2023 to 2024, according to the "
     "World Bank.",
     ("IND", 3500906030645.0, "2023", 2023),
     ("IND", 3760813470501.0, "2024", 2024)),
    ("F2D-D4",  # 175% claimed, 126.79% derived
     "In 2024, Germany's GDP was 175 percent of the United Kingdom's "
     "GDP, according to the World Bank.",
     ("DEU", 4685592577805.0, "2024", 2024),
     ("GBR", 3695539513534.0, "2024", 2024)),
    ("F2D-D6",  # 48.5M claimed, 43.48M derived
     "The average population of Canada and Argentina in 2024 was 48.5 "
     "million people, according to the World Bank.",
     ("CAN", 41262329, "2024", 2024),
     ("ARG", 45696159, "2024", 2024)),
])
def test_d_false_claims_contradict_with_computation(cid, text, op1, op2):
    """Sound refutations: verified endpoints derive a different figure."""
    claim = claim_input_for({"id": cid, "text": text})
    ind = "SP.POP.TOTL" if cid == "F2D-D6" else "NY.GDP.MKTP.CD"
    b1, p1 = _wb_pkg(cid, "p1", op1[1], op1[2], op1[0], ind, op1[3])
    b2, p2 = _wb_pkg(cid, "p2", op2[1], op2[2], op2[0], ind, op2[3])
    verdict = verify_claim(claim, [p1, p2], _loader_for(b1, b2))
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["computed_mismatch"]
    assert verdict.details["computation"]["op"] in (
        "pct_change", "ratio", "average")


def test_pct_sign_mismatch_contradicts():
    """"Grew x%" with endpoints showing a decline: genuine refutation."""
    claim = claim_input_for(
        {"id": "s", "text": "Banaba GDP grew 5.3 percent from 2023 "
                            "to 2024."})
    b1, p1 = _wb_pkg("s", "p1", 29298013000000, "2023", "BUB",
                     "NY.GDP.MKTP.CD", 2023)
    b2, p2 = _wb_pkg("s", "p2", 27811517000000, "2024", "BUB")
    verdict = verify_claim(claim, [p1, p2], _loader_for(b1, b2))
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["computed_sign_mismatch"]


def test_sum_supported_es():
    claim = claim_input_for(
        {"id": "s", "text": "La suma de ambos países fue de 4,10 billones "
                            "de dólares en 2024."})
    b1, p1 = _wb_pkg("s", "p1", 1725671652742.19, "2024", "ESP")
    b2, p2 = _wb_pkg("s", "p2", 2383435562458.12, "2024", "ITA")
    verdict = verify_claim(claim, [p1, p2], _loader_for(b1, b2))
    assert verdict.verdict == "supported", verdict
    assert verdict.details["computation"]["op"] == "sum"


def test_prose_quote_stating_gap_still_refutes():
    """A quote directly stating the gap figure engages soundly (ES)."""
    text = ("La India superó a China en 90,0 millones de habitantes "
            "en 2024.")
    claim = claim_input_for({"id": "g", "text": text})
    snap = ("<html><body><p>La India superó a China en 42,0 millones de "
            "habitantes en 2024, según el Banco Mundial.</p></body></html>")
    quote = "La India superó a China en 42,0 millones de habitantes en 2024"
    assert needs_computation(quote) == "diff"
    pkg = EvidencePackage(
        package_id="pkg_prose", claim_id="g",
        url="https://example.com/pob", captured_at="2026-01-01T00:00:00",
        sha256=sha256_of_text(snap), snapshot_path="data/snap/f.txt",
        quote=quote, offsets=(0, len(quote)), source_type="secondary",
        numbers=(), extractor="code", source_year=2024)
    verdict = verify_claim(claim, [pkg], lambda p: snap)
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["numeric_mismatch"]


# ---------------------------------------------------------------------------
# alignment rejects misaligned operands (guard, never contradicted)
# ---------------------------------------------------------------------------

def _aligned_case(text, specs, year=2024):
    claim = claim_input_for({"id": "a", "text": text})
    bodies, pkgs = [], []
    for i, spec in enumerate(specs):
        iso3, value, date, pyear, ind = spec
        body, pkg = _wb_pkg("a", f"p{i}", value, date, iso3, ind, pyear)
        bodies.append(body)
        pkgs.append(pkg)
    return verify_claim(claim, pkgs, _loader_for(*bodies))


def test_average_mixed_years_abstains():
    v = _aligned_case(
        "The average GDP of Spain and Italy in 2024 was 2.05 trillion "
        "dollars.",
        [("ESP", 1725671652742.19, "2023", 2023, "NY.GDP.MKTP.CD"),
         ("ITA", 2383435562458.12, "2024", 2024, "NY.GDP.MKTP.CD")])
    assert v.verdict == "unverifiable", v
    assert v.verdict != "contradicted"


def test_pct_same_year_abstains():
    v = _aligned_case(
        "Banaba GDP grew 5.3 percent from 2023 to 2024.",
        [("BUB", 27811517000000, "2024", 2024, "NY.GDP.MKTP.CD"),
         ("BUB", 29298013000000, "2024", 2024, "NY.GDP.MKTP.CD")])
    assert v.verdict == "unverifiable", v


def test_pct_years_not_in_claim_abstains():
    v = _aligned_case(
        "Banaba GDP grew 5.3 percent from 2023 to 2024.",
        [("BUB", 27811517000000, "2021", 2021, "NY.GDP.MKTP.CD"),
         ("BUB", 29298013000000, "2022", 2022, "NY.GDP.MKTP.CD")])
    assert v.verdict == "unverifiable", v


def test_mixed_operand_units_abstain():
    v = _aligned_case(
        "The average of Spain and Italy in 2024 was 2.05 trillion dollars.",
        [("ESP", 1725671652742.19, "2024", 2024, "NY.GDP.MKTP.CD"),
         ("ITA", 59042883, "2024", 2024, "SP.POP.TOTL")])
    assert v.verdict == "unverifiable", v


def test_ratio_with_level_unit_abstains():
    v = _aligned_case(
        "The fund holds 100 dollars, 7.8 times its fee in 2024.",
        [("BUB", 29298013000000, "2024", 2024, "NY.GDP.MKTP.CD"),
         ("CHL", 3760813470501.0, "2024", 2024, "NY.GDP.MKTP.CD")])
    assert v.verdict == "unverifiable", v


def test_unvalidated_quote_operand_skipped():
    """An operand whose quote is absent from the snapshot is unusable."""
    claim = claim_input_for(
        {"id": "a", "text": "La India superó a China en 42,0 millones de "
                            "habitantes en 2024."})
    b1, p1 = _wb_pkg("a", "p1", 1450935791, "2024", "IND", "SP.POP.TOTL")
    b2, p2 = _wb_pkg("a", "p2", 1408975000, "2024", "CHN", "SP.POP.TOTL")
    verdict = verify_claim(claim, [p1, p2], lambda p: b1)  # p2 unvalidated
    assert verdict.verdict == "unverifiable", verdict
    assert "arithmetic_not_computed" in verdict.reasons


def test_compute_verdict_never_raises():
    claim = claim_input_for({"id": "a", "text": "x"})
    assert compute_verdict(claim, [], lambda p: None, "diff") is None
    assert compute_verdict(claim, None, lambda p: None, "bogus") is None

    def _loader_boom(p):
        raise RuntimeError("loader down")

    assert compute_verdict(claim, [], _loader_boom, "average") is None
