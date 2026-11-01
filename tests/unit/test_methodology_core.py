"""Team-E fix package (M1 methodology core): F1, F3, F4, F5b, F6.

Offline: constructed objects + verify/synthesize calls only. No network,
no live model calls.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.compare import (  # noqa: E402
    MATERIALITY,
    implied_resolution,
    is_year_quantity,
    materiality_threshold,
)
from atlas.core.types import (  # noqa: E402
    Comparison,
    Evidence,
    Quantity,
    StructuredClaim,
    TimeRef,
    Uncertainty,
)
from atlas.models.scripted import ScriptedClient  # noqa: E402
from atlas.pipeline.synthesize import (  # noqa: E402
    FORBIDDEN_STRENGTH,
    SUMMARY_SYSTEM,
    decide_stance,
    synthesize,
    validate_summary_strength,
)
from atlas.pipeline.verify import verify_quantitative  # noqa: E402


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="test claim", domain="economy",
                claim_type="quantitative")
    base.update(kw)
    return StructuredClaim(**base)


def _wb_evidence(claim_id: str, year: str, value: float, unit: str = "count",
                 label: str = "Population, total",
                 connector: str = "worldbank") -> Evidence:
    return Evidence(
        claim_id=claim_id,
        connector=connector,
        source_name="World Bank Open Data",
        source_kind="secondary",
        evidence_kind="direct",
        locator=f"LABEL {year}",
        excerpt=f"{label} in {year}: {value}",
        payload={"label": label, "unit": unit, "year": year, "value": value},
    )


def _cmp(metric: str, verdict: str, rel: float | None = None,
         cid: str = "cmp_real") -> Comparison:
    return Comparison(claim_id="c", comparison_id=cid, metric=metric,  # type: ignore[arg-type]
                      verdict=verdict, rel_diff_pct=rel)


def _unc(code: str, severity: str) -> Uncertainty:
    return Uncertainty(code=code, description=f"{code} happened",  # type: ignore[arg-type]
                       severity=severity)


def _qty(raw: str, value: float, unit=None, nunit=None) -> Quantity:
    return Quantity(raw_text=raw, value=value, unit=unit,
                    normalized_value=value, normalized_unit=nunit)


# --- Red-team follow-ups (M5/M6/M7/M8): scale guard, materiality scope,
# price-basis lexicon, precision-skip disclosure ---


def test_single_letter_scales_require_attachment():
    from atlas.core.units import parse_quantities
    assert parse_quantities("100 m")[0].value == 100.0  # meters, not mega
    assert parse_quantities("5 k")[0].value == 5.0
    assert parse_quantities("$27m")[0].value == 27e6  # attached: million
    assert parse_quantities("$27b")[0].value == 27e9  # attached: billion
    assert parse_quantities("27 tn")[0].value == 27e12  # 2-letter: spaced ok


def test_resolution_mirrors_scale_guard():
    assert implied_resolution("100 m", 100.0) == 1.0
    assert implied_resolution("$27m", 27e6) == 1e6
    assert implied_resolution("27 tn", 27e12) == 1e12


def test_materiality_jpy_absent_by_design():
    assert "JPY" not in MATERIALITY
    assert materiality_threshold("JPY") is None
    assert materiality_threshold("USD") == 100e9
    assert materiality_threshold("percent") == 0.5


def test_price_basis_extended_lexicon():
    from atlas.pipeline.verify import _states_price_basis
    for text in ("GDP in chained 2017 dollars", "in 2015 dollars",
                 "inflation-adjusted GDP", "GDP, inflation adjusted",
                 "GDP at purchasing power parity",
                 "PIB en dólares de 2015",
                 "PIB ajustado por inflación"):
        assert _states_price_basis(text), text
    assert not _states_price_basis("The GDP was $27 trillion in 2023")


def test_precision_skip_discloses_minor_code():
    # Span-less quantity + consistent verdict: the rule fails open AND
    # says so (precision_unchecked, minor) instead of skipping silently.
    claim = _claim(raw_text="GDP was big in 2023",
                   quantities=[_qty("", 27.36e12, "USD", "USD")],
                   time_refs=[TimeRef(raw="2023", year=2023,
                                      period_type="year")])
    ev = _wb_evidence("c", "2023", 27.5e12, "USD", "GDP (current US$)")
    cmps, uncs = verify_quantitative(claim, [ev])
    assert any(c.verdict == "consistent" for c in cmps)
    assert "precision_unchecked" in {u.code for u in uncs}
    assert "claimed_precision_exceeded" not in {u.code for u in uncs}


# --- F1: implied_resolution ---


def test_resolution_trillion_two_decimals():
    assert implied_resolution("$27.36 trillion", 27.36e12) == 1e10


def test_resolution_es_decimal_comma_percent():
    assert implied_resolution("3,1%", 3.1) == 0.1


def test_resolution_es_billones_long_scale():
    assert implied_resolution("27,36 billones", 27.36e12) == 1e10


def test_resolution_exact_count_ones():
    assert implied_resolution("67,842,591", 67842591.0) == 1.0


def test_resolution_millions_no_decimals():
    assert implied_resolution("500 million", 500e6) == 1e6


def test_resolution_percent_one_decimal():
    assert implied_resolution("3.1%", 3.1) == 0.1


def test_resolution_whole_trillion():
    assert implied_resolution("$2 trillion", 2e12) == 1e12


def test_resolution_unparseable_is_none():
    assert implied_resolution("", 27.36e12) is None
    assert implied_resolution("a lot", 0.0) is None
    assert implied_resolution("high", 0.0) is None


def test_resolution_attached_abbreviation():
    assert implied_resolution("$27m", 27e6) == 1e6


# --- F1: MATERIALITY ---


def test_materiality_table_values():
    assert MATERIALITY["USD"] == 100e9
    assert MATERIALITY["count"] == 1e6
    assert MATERIALITY["percent"] == 0.5
    assert MATERIALITY["pp"] == 0.5
    assert materiality_threshold("USD") == 100e9
    assert materiality_threshold("count", "Population, total") == 1e6
    assert materiality_threshold("percent") == 0.5
    assert materiality_threshold("years") is None
    assert materiality_threshold(None) is None


def test_is_year_quantity():
    assert is_year_quantity(_qty("2023", 2023.0))
    assert not is_year_quantity(_qty("$27.36 trillion", 27.36e12))
    assert not is_year_quantity(_qty("3.1%", 3.1))


# --- F3: precision rule ---


def _france_pop_claim() -> StructuredClaim:
    return _claim(
        raw_text="The population of France was 67,842,591 in 2020.",
        quantities=[_qty("67,842,591", 67842591.0)],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )


def test_precision_flagged_when_gap_exceeds_resolution():
    claim = _france_pop_claim()
    ev = _wb_evidence(claim.claim_id, "2020", 67_500_000.0)
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert len(numeric) == 1
    assert numeric[0].verdict == "consistent"  # 0.5% rel: verdict unchanged
    assert "Claimed-precision" in numeric[0].notes
    prec = [u for u in uncertainties if u.code == "claimed_precision_exceeded"]
    assert len(prec) == 1
    assert prec[0].severity == "major"
    assert decide_stance(comparisons, uncertainties, claim) == "partially_supported"


def test_precision_silent_on_exact_match():
    claim = _france_pop_claim()
    ev = _wb_evidence(claim.claim_id, "2020", 67842591.0)
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "claimed_precision_exceeded" for u in uncertainties)
    assert decide_stance(comparisons, uncertainties, claim) == "supported"


def test_precision_silent_on_contradiction():
    claim = _france_pop_claim()
    ev = _wb_evidence(claim.claim_id, "2020", 10_000_000.0)
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric[0].verdict == "contradicts"
    assert not any(u.code == "claimed_precision_exceeded" for u in uncertainties)


def test_precision_skipped_for_model_extraction_without_raw_text():
    claim = _claim(
        raw_text="The population of France was about 67.5 million in 2020.",
        quantities=[_qty("", 67_500_000.0, None, "count")],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2020", 67_000_000.0)
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "claimed_precision_exceeded" for u in uncertainties)


def test_precision_env_gate_off(monkeypatch):
    monkeypatch.setenv("ATLAS_PRECISION_RULE", "0")
    claim = _france_pop_claim()
    ev = _wb_evidence(claim.claim_id, "2020", 67_500_000.0)
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "claimed_precision_exceeded" for u in uncertainties)
    assert decide_stance(comparisons, uncertainties, claim) == "supported"


def test_precision_es_decimal_comma_percent():
    claim = _claim(
        raw_text="La inflación en Brasil fue del 8,3% en 2021.",
        quantities=[_qty("8,3%", 8.3, "percent", "percent")],
        time_refs=[TimeRef(raw="2021", year=2021, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2021", 8.42, unit="percent",
                      label="Inflation, consumer prices (annual %)")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric[0].verdict == "consistent"  # 1.4% rel
    assert any(u.code == "claimed_precision_exceeded" for u in uncertainties)


# --- F3: materiality disclosure ---


def _gdp_claim() -> StructuredClaim:
    return _claim(
        raw_text="The GDP of the United States was $27.36 trillion in 2023.",
        quantities=[_qty("$27.36 trillion", 27.36e12, "USD", "USD")],
        time_refs=[TimeRef(raw="2023", year=2023, period_type="year")],
    )


def test_materiality_disclosed_over_threshold_verdict_unchanged():
    claim = _gdp_claim()
    ev = _wb_evidence(claim.claim_id, "2023", 27.81e12, unit="USD",
                      label="GDP (current US$)")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric[0].verdict == "consistent"
    assert "Materiality" in numeric[0].notes
    mat = [u for u in uncertainties if u.code == "material_absolute_gap"]
    assert len(mat) == 1
    assert mat[0].severity == "minor"


def test_materiality_silent_under_threshold():
    claim = _gdp_claim()
    ev = _wb_evidence(claim.claim_id, "2023", 27.361e12, unit="USD",
                      label="GDP (current US$)")
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "material_absolute_gap" for u in uncertainties)


def test_materiality_percent_threshold():
    claim = _claim(
        raw_text="Literacy stood at 30.0% in 2020.",
        quantities=[_qty("30.0%", 30.0, "percent", "percent")],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2020", 30.55, unit="percent",
                      label="Literacy rate")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric[0].verdict == "consistent"  # 1.8% rel
    assert any(u.code == "material_absolute_gap" for u in uncertainties)


def test_materiality_no_rule_for_years():
    claim = _claim(
        raw_text="Life expectancy in Japan was 84 years in 2020.",
        quantities=[_qty("84 years", 84.0, "years", "years")],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2020", 84.5, unit="years",
                      label="Life expectancy at birth (years)")
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "material_absolute_gap" for u in uncertainties)


# --- F4: methodological codes ---


def test_indicator_definition_assumed_every_wb_comparison():
    claim = _gdp_claim()
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)")
    _, uncertainties = verify_quantitative(claim, [ev])
    found = [u for u in uncertainties if u.code == "indicator_definition_assumed"]
    assert len(found) == 1
    assert found[0].severity == "minor"


def test_source_revision_risk_every_wb_numeric_comparison():
    claim = _gdp_claim()
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)")
    _, uncertainties = verify_quantitative(claim, [ev])
    found = [u for u in uncertainties if u.code == "source_revision_risk"]
    assert len(found) == 1
    assert found[0].severity == "minor"


def test_methodological_codes_absent_for_non_wb_connector():
    claim = _gdp_claim()
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)", connector="custom")
    _, uncertainties = verify_quantitative(claim, [ev])
    codes = {u.code for u in uncertainties}
    assert "indicator_definition_assumed" not in codes
    assert "source_revision_risk" not in codes
    assert "price_basis_unchecked" not in codes


def test_price_basis_unchecked_once_per_claim():
    claim = _gdp_claim()  # no price-basis words
    evs = [
        _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                     label="GDP (current US$)"),
        _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                     label="GDP (current US$)"),
    ]
    comparisons, uncertainties = verify_quantitative(claim, evs)
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert len(numeric) == 2
    found = [u for u in uncertainties if u.code == "price_basis_unchecked"]
    assert len(found) == 1
    assert found[0].severity == "major"


def test_price_basis_present_en_silences_flag():
    claim = _gdp_claim()
    claim.raw_text = (
        "US GDP was $27.36 trillion in current prices in 2023.")
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)")
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "price_basis_unchecked" for u in uncertainties)


def test_price_basis_present_es_silences_flag():
    claim = _gdp_claim()
    claim.raw_text = "El PIB fue de $27,36 billones a precios constantes en 2023."
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)")
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "price_basis_unchecked" for u in uncertainties)


def test_price_basis_flag_needs_current_in_label():
    claim = _france_pop_claim()  # no basis words, but count label
    ev = _wb_evidence(claim.claim_id, "2020", 67842591.0)
    _, uncertainties = verify_quantitative(claim, [ev])
    assert not any(u.code == "price_basis_unchecked" for u in uncertainties)


def test_methodological_codes_do_not_move_stance_alone():
    claim = _gdp_claim()
    claim.raw_text = "US GDP was $27.36 trillion in current prices in 2023."
    ev = _wb_evidence(claim.claim_id, "2023", 27.36e12, unit="USD",
                      label="GDP (current US$)")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    assert decide_stance(comparisons, uncertainties, claim) == "supported"


# --- F3/F5b: stance caps ---


def test_precision_code_caps_supported_at_partial():
    stance = decide_stance(
        [_cmp("gdp", "consistent")],
        [_unc("claimed_precision_exceeded", "major")],
        _claim(),
    )
    assert stance == "partially_supported"


def test_compound_cap_fewer_verifying_than_quantities():
    claim = _claim(
        raw_text="GDP was $27 trillion in 2023 and unemployment was 3.1%.",
        quantities=[
            _qty("$27 trillion", 27e12, "USD", "USD"),
            _qty("3.1%", 3.1, "percent", "percent"),
        ],
    )
    stance = decide_stance(
        [_cmp("gdp", "consistent")], [_unc("compound_claim", "major")], claim
    )
    assert stance == "partially_supported"


def test_compound_no_cap_when_all_quantities_verified():
    claim = _claim(
        quantities=[
            _qty("$27 trillion", 27e12, "USD", "USD"),
            _qty("3.1%", 3.1, "percent", "percent"),
        ],
    )
    stance = decide_stance(
        [_cmp("gdp", "consistent"), _cmp("unemployment", "consistent")],
        [_unc("compound_claim", "major")],
        claim,
    )
    assert stance == "supported"


def test_compound_cap_ignores_year_quantities():
    claim = _claim(
        quantities=[
            _qty("$27.36 trillion", 27.36e12, "USD", "USD"),
            _qty("2023", 2023.0),
        ],
    )
    stance = decide_stance(
        [_cmp("gdp", "consistent")], [_unc("compound_claim", "major")], claim
    )
    assert stance == "supported"


def test_compound_cap_never_overrides_contradiction():
    claim = _claim(
        quantities=[
            _qty("$27 trillion", 27e12, "USD", "USD"),
            _qty("3.1%", 3.1, "percent", "percent"),
        ],
    )
    stance = decide_stance(
        [_cmp("gdp", "contradicts")], [_unc("compound_claim", "major")], claim
    )
    assert stance == "contradicted"


# --- F5b/F6: summary strength ---


def test_summary_system_pins_strength_rules():
    for word in FORBIDDEN_STRENGTH:
        assert word in SUMMARY_SYSTEM
    assert "verbatim" in SUMMARY_SYSTEM
    assert "rel_diff_pct" in SUMMARY_SYSTEM


def test_strength_validator_accepts_compliant_summary():
    cmps = [_cmp("gdp", "consistent", 1.6)]
    assert validate_summary_strength(
        "Stance supported: measured delta 1.6%.", "supported", cmps
    ) == []


def test_strength_validator_flags_each_forbidden_word():
    cmps = [_cmp("gdp", "consistent", 1.6)]
    for word in ("prove", "proves", "proven", "confirm", "confirms",
                 "definitively", "categorically", "exactly right",
                 "exactly-right"):
        summary = f"Stance supported: this {word} it, delta 1.6%."
        violations = validate_summary_strength(summary, "supported", cmps)
        assert any("forbidden" in v for v in violations), word


def test_strength_validator_whole_word_only():
    cmps = [_cmp("gdp", "consistent", 1.6)]
    assert validate_summary_strength(
        "Stance supported: data improve the picture, delta 1.6%.",
        "supported", cmps,
    ) == []


def test_strength_validator_requires_stance_token():
    assert validate_summary_strength("All good, delta 1.6%.", "supported",
                                     [_cmp("gdp", "consistent", 1.6)]) != []


def test_strength_validator_requires_delta_for_numeric():
    assert validate_summary_strength(
        "Stance supported, no numbers here.", "supported",
        [_cmp("gdp", "consistent", 1.6)]) != []


def test_strength_validator_no_delta_without_numeric():
    assert validate_summary_strength(
        "Stance unverifiable: nothing found.", "unverifiable",
        [_cmp("time_compatibility", "consistent")]) == []


def test_synthesize_discards_violating_model_summary(caplog):
    llm = ScriptedClient(
        {"stance": {"summary": "This proves it is supported, delta 1.6%.",
                    "caveats": ["model-note"]}}
    )
    with caplog.at_level(logging.WARNING, logger="atlas.pipeline.synthesize"):
        conclusion = synthesize(
            _claim(), [_cmp("gdp", "consistent", 1.6)], [], llm
        )
    assert conclusion.actor == "code"
    assert conclusion.model is None
    assert conclusion.summary.startswith("Stance: supported")
    assert "model-note" not in conclusion.caveats
    assert "strength validation" in caplog.text


def test_synthesize_discards_model_summary_missing_stance_token(caplog):
    llm = ScriptedClient(
        {"stance": {"summary": "The numbers agree, delta 1.6%.",
                    "caveats": []}}
    )
    with caplog.at_level(logging.WARNING, logger="atlas.pipeline.synthesize"):
        conclusion = synthesize(
            _claim(), [_cmp("gdp", "consistent", 1.6)], [], llm
        )
    assert conclusion.actor == "code"
    assert "strength validation" in caplog.text


def test_synthesize_keeps_compliant_model_summary():
    llm = ScriptedClient(
        {"stance": {"summary": "Stance supported: delta 1.6%.",
                    "caveats": ["model-note"]}}
    )
    conclusion = synthesize(_claim(), [_cmp("gdp", "consistent", 1.6)], [], llm)
    assert conclusion.actor == "model"
    assert "supported" in conclusion.summary
    assert "model-note" in conclusion.caveats
