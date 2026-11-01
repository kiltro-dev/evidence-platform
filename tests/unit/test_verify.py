"""Focused tests for verification + stance rules (offline, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import (
    Comparison,
    Evidence,
    Quantity,
    StructuredClaim,
    TimeRef,
    Uncertainty,
)
from atlas.pipeline.synthesize import decide_stance
from atlas.pipeline.verify import (
    assess_science,
    detect_claim_shape,
    verify_quantitative,
)


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="test claim", domain="economy", claim_type="quantitative")
    base.update(kw)
    return StructuredClaim(**base)


def _wb_evidence(claim_id: str, year: str, value: float, unit: str = "count") -> Evidence:
    return Evidence(
        claim_id=claim_id,
        connector="worldbank",
        source_name="World Bank Open Data",
        source_kind="secondary",
        evidence_kind="direct",
        locator=f"SP.POP.TOTL JPN {year}",
        excerpt=f"Population in {year}: {value}",
        payload={"label": "Population, total", "unit": unit, "year": year, "value": value},
    )


def _cmp(metric: str, verdict: str) -> Comparison:
    return Comparison(claim_id="c", metric=metric, verdict=verdict)  # type: ignore[arg-type]


def _unc(severity: str) -> Uncertainty:
    return Uncertainty(code="x", description="x", severity=severity)  # type: ignore[arg-type]


# --- stance rules ---


def test_contradiction_wins_over_support():
    stance = decide_stance(
        [_cmp("GDP (current US$)", "consistent"), _cmp("GDP (current US$)", "contradicts")],
        [],
    )
    assert stance == "contradicted"


def test_year_match_alone_never_supported():
    stance = decide_stance([_cmp("publication_year_match", "consistent")], [])
    assert stance == "partially_supported"


def test_time_check_alone_never_supported():
    stance = decide_stance([_cmp("time_compatibility", "consistent")], [])
    assert stance == "partially_supported"


def test_consistent_with_blocking_is_partial():
    stance = decide_stance(
        [_cmp("GDP (current US$)", "consistent")], [_unc("blocking")]
    )
    assert stance == "partially_supported"


def test_no_checks_is_unverifiable():
    assert decide_stance([], []) == "unverifiable"


# --- quantitative verification ---


def test_bare_number_matches_count_with_explicit_flag():
    claim = _claim(
        quantities=[Quantity(raw_text="500 million", value=500e6, unit=None)],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2020", 125_800_000.0)
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert len(numeric) == 1
    assert numeric[0].verdict == "contradicts"
    assert "Unit assumption" in numeric[0].notes
    assert any(u.code == "unit_assumed_from_context" for u in uncertainties)


def test_bare_number_never_matches_money():
    claim = _claim(
        quantities=[Quantity(raw_text="27", value=27.0, unit=None)],
        time_refs=[TimeRef(raw="2023", year=2023, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2023", 27e12, unit="USD")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric == []
    assert any(u.code == "unit_mismatch" for u in uncertainties)


def test_period_mismatch_blocks_comparison():
    claim = _claim(
        quantities=[
            Quantity(raw_text="$27t", value=27e12, unit="USD", normalized_value=27e12,
                     normalized_unit="USD")
        ],
        time_refs=[TimeRef(raw="2023", year=2023, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2010", 15e12, unit="USD")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric == []
    assert any(u.code == "period_mismatch" for u in uncertainties)


def test_year_numbers_are_not_quantities():
    claim = _claim(
        quantities=[
            Quantity(raw_text="500 million", value=500e6, unit=None),
            Quantity(raw_text="2020", value=2020.0, unit=None),
        ],
        time_refs=[TimeRef(raw="2020", year=2020, period_type="year")],
    )
    ev = _wb_evidence(claim.claim_id, "2020", 125_800_000.0)
    comparisons, _ = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert len(numeric) == 1  # "2020" must not produce a second comparison


def test_no_quantity_at_all_is_blocking():
    claim = _claim(quantities=[])
    comparisons, uncertainties = verify_quantitative(claim, [])
    assert comparisons == []
    assert uncertainties[0].code == "no_measurable_quantity"
    assert uncertainties[0].severity == "blocking"


# --- science assessment ---


def test_science_without_sources_is_blocking():
    claim = _claim(domain="science")
    comparisons, uncertainties = assess_science(claim, [])
    assert comparisons == []
    assert uncertainties[0].code == "no_sources_found"


def test_science_always_flags_metadata_only():
    claim = _claim(
        domain="science",
        time_refs=[TimeRef(raw="2021", year=2021, period_type="year")],
    )
    ev = Evidence(
        claim_id=claim.claim_id,
        connector="openalex",
        source_name="OpenAlex",
        source_kind="secondary",
        evidence_kind="contextual",
        locator="W1 (2021)",
        excerpt="Some study (2021)",
        payload={"year": 2021},
    )
    comparisons, uncertainties = assess_science(claim, [ev])
    assert comparisons[0].verdict == "consistent"
    assert comparisons[0].metric == "publication_year_match"
    assert any(u.code == "metadata_only" for u in uncertainties)
    # ...and that consistency must not flip the stance to supported
    assert decide_stance(comparisons, uncertainties) == "partially_supported"


def test_best_estimate_is_not_opinion():
    """F4: data-quality hedging must not trigger opinion_or_nonclaim."""
    claim = _claim(raw_text="Our best estimate is that GDP was $27t in 2023.")
    flags = detect_claim_shape(claim)
    assert not any(u.code == "opinion_or_nonclaim" for u in flags)


def test_best_available_data_is_not_opinion():
    """F4: 'best available data' collocation is a qualifier, not an opinion."""
    claim = _claim(raw_text="The best available data show GDP at $27t in 2023.")
    flags = detect_claim_shape(claim)
    assert not any(u.code == "opinion_or_nonclaim" for u in flags)


def test_bare_best_still_opinion():
    """F4 guard: the collocation carve-out must not neuter bare 'best'."""
    claim = _claim(raw_text="This was the best economic year ever.")
    flags = detect_claim_shape(claim)
    assert any(u.code == "opinion_or_nonclaim" for u in flags)


def test_no_year_claim_names_claim_side_gap():
    """F6: a PAST-tense claim with no year gets a claim-side description.

    UT-f override: year-less PRESENT claims now compare against latest
    (assumed_period, capped); only explicit-past year-less claims keep
    the mrnev rule pinned here.
    """
    claim = _claim(
        raw_text="GDP was $27t.",
        quantities=[
            Quantity(raw_text="$27t", value=27e12, unit="USD", normalized_value=27e12,
                     normalized_unit="USD")
        ],
        time_refs=[],
    )
    ev = _wb_evidence(claim.claim_id, "2023", 27.8e12, unit="USD")
    comparisons, uncertainties = verify_quantitative(claim, [ev])
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert numeric == []
    mismatches = [u for u in uncertainties if u.code == "period_mismatch"]
    assert len(mismatches) == 1
    assert "no year or period" in mismatches[0].description
    assert mismatches[0].severity == "major"


def _ev_wb(year, value, unit, code, label):
    return Evidence(claim_id="c", connector="worldbank",
                    source_name="WB (t)", source_kind="secondary",
                    evidence_kind="direct", locator=f"{code} USA {year}",
                    excerpt=f"{label} {year}: {value}",
                    payload={"label": label, "unit": unit, "year": str(year),
                             "value": value, "indicator": code})


def _q(raw, value, unit):
    return Quantity(label="q", raw_text=raw, value=value, unit=unit,
                    normalized_value=value, normalized_unit=unit)


def test_rate_vs_level_skips_compare_with_code():
    # UT-c: "exports rose 5%" must never compare against Exports (% of GDP).
    claim = _claim(raw_text="US exports rose 5% in 2023.",
                   quantities=[_q("5%", 5.0, "percent")],
                   time_refs=[TimeRef(raw="2023", year=2023,
                                      period_type="year")])
    cmps, uncs = verify_quantitative(
        claim, [_ev_wb(2023, 12.0, "percent", "NE.EXP.GNFS.ZS",
                       "Exports of goods and services (% of GDP)")])
    codes = [u.code for u in uncs]
    assert "rate_level_mismatch" in codes
    assert all(c.verdict != "contradicts" for c in cmps)


def test_rate_vs_rate_still_compares():
    # UT-c: change vs annual-rate evidence is a legitimate comparison.
    claim = _claim(raw_text="US GDP grew 5% in 2023.",
                   quantities=[_q("5%", 5.0, "percent")],
                   time_refs=[TimeRef(raw="2023", year=2023,
                                      period_type="year")])
    cmps, uncs = verify_quantitative(
        claim, [_ev_wb(2023, 2.1, "percent", "NY.GDP.MKTP.KD.ZG",
                       "GDP growth (annual %)")])
    assert any(c.verdict == "contradicts" for c in cmps)
    assert "rate_level_mismatch" not in [u.code for u in uncs]


def test_to_level_phrasing_compares_normally():
    # UT-c: "fell from 4% to 3%" pins levels, not a change magnitude.
    claim = _claim(raw_text="Unemployment fell from 4% in 2020 to 3% in 2023.",
                   quantities=[_q("3%", 3.0, "percent")],
                   time_refs=[TimeRef(raw="2023", year=2023,
                                      period_type="year")])
    cmps, uncs = verify_quantitative(
        claim, [_ev_wb(2023, 3.1, "percent", "SL.UEM.TOTL.ZS",
                       "Unemployment, total")])
    assert "rate_level_mismatch" not in [u.code for u in uncs]


def test_mixed_change_and_level_split_positionally():
    # UT-c iter2 (judge): "rose 5% to 10T" — 5% abstains, 10T compares.
    from atlas.connectors.worldbank import is_change_quantity as g

    assert g("GDP rose 5% to 10 trillion", "5%") is True
    assert g("GDP rose 5% to 10 trillion", "10 trillion") is False
    assert g("fell from 4% in 2020 to 3% in 2023", "4%") is False
    assert g("fell from 4% in 2020 to 3% in 2023", "3%") is False
    assert g("pasó de 3% a 5%", "3%") is False
    assert g("pasó de 3% a 5%", "5%") is False
    assert g("aumento de 5%", "5%") is True


def test_rate_guard_ordering_and_scope():
    # pp-vs-percent keeps unit_mismatch (unit gate first); non-WB
    # evidence never fires the rate guard.
    claim = _claim(raw_text="Unemployment rose 0.5 percent in 2023.",
                   quantities=[_q("0.5 pp", 0.5, "pp")],
                   time_refs=[TimeRef(raw="2023", year=2023,
                                      period_type="year")])
    _, uncs = verify_quantitative(
        claim, [_ev_wb(2023, 3.1, "percent", "SL.UEM.TOTL.ZS", "U")])
    codes = [u.code for u in uncs]
    assert "unit_mismatch" in codes
    assert "rate_level_mismatch" not in codes
    sci = Evidence(claim_id="c", connector="openalex",
                   source_name="OA", source_kind="primary",
                   evidence_kind="direct", locator="w1", excerpt="e",
                   payload={"label": "L", "unit": "percent", "year": 2023,
                            "value": 3.1})
    _, uncs2 = verify_quantitative(claim, [sci])
    assert "rate_level_mismatch" not in [u.code for u in uncs2]


def test_iter4_judge_materiality_m1_m2():
    # UT-c iter4: judge-verified material cases, end to end.
    # M1: year-shaped magnitude with marking still abstains.
    cmps, uncs = verify_quantitative(
        _claim(raw_text="US GDP rose 2000 dollars per capita in 2023.",
               quantities=[_q("2000 dollars", 2000.0, "USD")],
               time_refs=[TimeRef(raw="2023", year=2023,
                                  period_type="year")]),
        [_ev_wb("2023", 80000.0, "USD", "NY.GDP.PCAP.CD",
                "GDP per capita")])
    assert "rate_level_mismatch" in [u.code for u in uncs]
    assert all(c.verdict != "contradicted" for c in cmps)
    # M2: stacked to-qualifier level still compares.
    cmps, uncs = verify_quantitative(
        _claim(raw_text="Unemployment rose to just 5% in 2023.",
               quantities=[_q("5%", 5.0, "percent")],
               time_refs=[TimeRef(raw="2023", year=2023,
                                  period_type="year")]),
        [_ev_wb("2023", 5.05, "percent", "SL.UEM.TOTL.ZS",
                "Unemployment")])
    assert "rate_level_mismatch" not in [u.code for u in uncs]


def _d_claim(text, quants, years):
    return _claim(
        raw_text=text,
        quantities=[_q(r, v, u) for (r, v, u) in quants],
        time_refs=[TimeRef(raw=str(y), year=y, period_type="year")
                   for y in years])


def _d_stance(text, quants, years, evs):
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim(text, quants, years)
    cmps, uncs = verify_quantitative(c, evs)
    return decide_stance(cmps, uncs, c), cmps, uncs


def test_multiyear_pairing_order_free_supported():
    # UT-d (d1): verdict must not depend on time_ref order.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_ev_wb(2020, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
           _ev_wb(2023, 28e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s1, _, _ = _d_stance("t", q, [2020, 2023], evs)
    s2, _, _ = _d_stance("t", q, [2023, 2020], evs)
    assert (s1, s2) == ("supported", "supported")


def test_multiyear_anchored_crossed_values_contradict():
    # UT-d: with textual anchors, crossed values are caught.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_ev_wb(2020, 28e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
           _ev_wb(2023, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s, _, _ = _d_stance(
        "GDP was 27 trillion in 2020 and 28 trillion in 2023.",
        q, [2020, 2023], evs)
    assert s == "contradicted"


def test_multiyear_unanchored_flags_pairing_uncertainty():
    # UT-d: numeric fallback without textual anchors is disclosed.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_ev_wb(2020, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
           _ev_wb(2023, 28e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s, _, uncs = _d_stance("t", q, [2020, 2023], evs)
    assert s == "supported"
    assert "year_pairing_unanchored" in [u.code for u in uncs]


def test_multiyear_quantity_without_year_evidence():
    # UT-d: a quantity whose year has no evidence -> period_mismatch.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_ev_wb(2020, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s, cmps, uncs = _d_stance("t", q, [2020, 2023], evs)
    assert "period_mismatch" in [u.code for u in uncs]
    assert s != "contradicted"


def test_single_year_all_pairs_compared_legacy():
    # UT-d: single-year claims keep compare-against-all behavior.
    c = _d_claim("GDP was 27 trillion in 2023.",
                 [("27 trillion", 27e12, "USD")], [2023])
    cmps, _ = verify_quantitative(c, [
        _ev_wb(2023, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
        _ev_wb(2023, 27.1e12, "USD", "NY.GDP.MKTP.CD", "GDP")])
    numerics = [x for x in cmps
                if x.metric not in ("time_compatibility",
                                    "rate_level_compatibility")]
    assert len(numerics) == 2


def test_range_claim_uses_legacy_path():
    # UT-d: ranges keep the legacy first-ref behavior (no pairing).
    c = _claim(raw_text="GDP over 2020-2023.",
               quantities=[_q("27 trillion", 27e12, "USD")],
               time_refs=[TimeRef(raw="2020-2023", start_year=2020,
                                  end_year=2023, period_type="range")])
    _, uncs = verify_quantitative(c, [
        _ev_wb(2021, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP")])
    assert "year_pairing_unanchored" not in [u.code for u in uncs]


def test_multiyear_repeated_spans_pair_by_nearest_occurrence():
    # UT-d iter2 (judge P3): repeats anchor by nearest occurrence.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_ev_wb(2020, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
           _ev_wb(2023, 28e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s, _, uncs = _d_stance(
        "Analysts guessed 28 trillion for 2020, but actual 2020 GDP was "
        "27 trillion and 2023 GDP was 28 trillion.",
        q, [2020, 2023], evs)
    assert s == "supported"
    assert "year_pairing_occurrence_ambiguous" in [u.code for u in uncs]


def test_multiyear_more_quantities_than_years_no_crash():
    # UT-d iter2 (judge P4a): 3 quantities, 2 years — no crash.
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD"),
         ("29 trillion", 29e12, "USD")]
    evs = [_ev_wb(2020, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
           _ev_wb(2023, 28e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    s, _, _ = _d_stance(
        "GDP was 27 trillion in 2020, 28 trillion in 2023, then "
        "29 trillion.",
        q, [2020, 2023], evs)
    assert s in ("supported", "partially_supported", "unverifiable",
                 "contradicted")


def test_multiyear_no_pairs_no_unanchored_disclosure():
    # UT-d iter2 (judge P10/P13): zero pairs -> no unanchored code.
    q = [("27 trillion", 27e12, "USD")]
    evs = [_ev_wb(2015, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP")]
    _, _, uncs = _d_stance("t", q, [2020, 2023], evs)
    codes = [u.code for u in uncs]
    assert "period_mismatch" in codes
    assert "year_pairing_unanchored" not in codes


def test_compound_cap_ignores_abstentions_multiyear():
    # UT-d iter2 (judge probe 12): one guarded + one consistent pair
    # is half coverage -> partially_supported, never supported.
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim("US exports rose 5% in 2020 to 6% in 2023.",
                 [("5%", 5.0, "percent"), ("6%", 6.0, "percent")],
                 [2020, 2023])
    cmps, uncs = verify_quantitative(c, [
        _ev_wb(2020, 12.0, "percent", "NE.EXP.GNFS.ZS", "Exports"),
        _ev_wb(2023, 6.05, "percent", "NE.EXP.GNFS.ZS", "Exports")])
    assert "rate_level_mismatch" in [u.code for u in uncs]
    assert decide_stance(cmps, uncs, c) == "partially_supported"


def test_compound_cap_ignores_abstentions_legacy():
    # UT-d iter2: same latent bug in the single-year legacy path.
    # Mixed text: "5%" is guarded, "6%"/"27T" compare (2 decisive of
    # 3 real quantities -> capped, never full supported).
    from atlas.pipeline.synthesize import decide_stance
    c = _claim(raw_text="US exports rose 5% to 6% in 2023 and GDP was "
                        "27 trillion.",
               quantities=[_q("5%", 5.0, "percent"),
                           _q("6%", 6.0, "percent"),
                           _q("27 trillion", 27e12, "USD")],
               time_refs=[TimeRef(raw="2023", year=2023,
                                  period_type="year")])
    cmps, uncs = verify_quantitative(c, [
        _ev_wb(2023, 6.0, "percent", "NE.EXP.GNFS.ZS", "Exports"),
        _ev_wb(2023, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP")])
    assert "rate_level_mismatch" in [u.code for u in uncs]
    assert "compound_claim" in [u.code for u in uncs]
    assert decide_stance(cmps, uncs, c) == "partially_supported"


def test_scale_guard_abstains_unscaled_1000x_gap():
    # UT-e (e1): scale-less parse + 10^k gap -> abstain, not contradicted.
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim("t", [("27.72", 27.72, "USD")], [2023])
    cmps, uncs = verify_quantitative(
        c, [_ev_wb(2023, 27.72e12, "USD", "NY.GDP.MKTP.CD", "GDP")])
    codes = [u.code for u in uncs]
    assert "possible_scale_error" in codes
    assert decide_stance(cmps, uncs, c) == "unverifiable"


def test_scale_guard_explicit_scale_stays_contradicted():
    # UT-e (e2): explicit scale + real 1000x gap -> contradicted.
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim("t", [("1.25 billion", 1.25e9, "USD")], [2023])
    cmps, uncs = verify_quantitative(
        c, [_ev_wb(2023, 1.25e12, "USD", "NY.GDP.MKTP.CD", "GDP")])
    assert "possible_scale_error" not in [u.code for u in uncs]
    assert decide_stance(cmps, uncs, c) == "contradicted"


def test_scale_guard_non_10k_gap_stays_contradicted():
    # UT-e: 500x gap is not a scale slip -> contradicted.
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim("t", [("2", 2.0, "USD")], [2023])
    cmps, uncs = verify_quantitative(
        c, [_ev_wb(2023, 1000.0, "USD", "NY.GDP.MKTP.CD", "GDP")])
    assert "possible_scale_error" not in [u.code for u in uncs]
    assert decide_stance(cmps, uncs, c) == "contradicted"


def test_scale_guard_single_letter_is_ambiguous():
    # UT-e: "$27.72T" states a letter, not an unambiguous scale word.
    from atlas.pipeline.synthesize import decide_stance
    c = _d_claim("t", [("$27.72T", 27.72e12, "USD")], [2023])
    cmps, uncs = verify_quantitative(
        c, [_ev_wb(2023, 27.72e9, "USD", "NY.GDP.MKTP.CD", "GDP")])
    assert "possible_scale_error" in [u.code for u in uncs]
    assert decide_stance(cmps, uncs, c) != "contradicted"


def _yl_claim(text, quants):
    # Year-less claim builder (UT-f).
    return _claim(raw_text=text,
                  quantities=[_q(r, v, u) for (r, v, u) in quants],
                  time_refs=[])


def _yl_stance(text, quants, evs):
    from atlas.pipeline.synthesize import decide_stance
    c = _yl_claim(text, quants)
    cmps, uncs = verify_quantitative(c, evs)
    return decide_stance(cmps, uncs, c), cmps, uncs


def test_yearless_present_match_caps_partially():
    # UT-f (f1/f2): present-tense + matching latest -> capped partial
    # with disclosed assumed period.
    s, _, uncs = _yl_stance("population is 125 million",
                            [("125 million", 125e6, "count")],
                            [_ev_wb(2023, 125e6, "count", "SP.POP.TOTL",
                                    "Population, total")])
    codes = [u.code for u in uncs]
    blob = " ".join(u.description for u in uncs)
    assert s == "partially_supported"
    assert "assumed_period" in codes and "2023" in blob


def test_yearless_present_es_match():
    s, _, uncs = _yl_stance("El paro es del 3,1%.",
                            [("3,1%", 3.1, "percent")],
                            [_ev_wb(2023, 3.1, "percent", "SL.UEM.TOTL.ZS",
                                    "Unemployment")])
    assert s == "partially_supported"
    assert "assumed_period" in [u.code for u in uncs]


def test_yearless_past_stays_legacy_unverifiable():
    # UT-f: explicit past without a year keeps the mrnev rule.
    for text in ("Unemployment was 3.1%.",
                 "Unemployment were 3.1%.",
                 "El paro era del 3,1%.",
                 "El desempleo fue del 3,1%.",
                 "Inflation has reached 5%.",
                 "La inflación ha alcanzado el 5%.",
                 "GDP used to be 27 trillion."):
        s, _, uncs = _yl_stance(text, [("3.1%", 3.1, "percent")],
                                [_ev_wb(2023, 3.1, "percent",
                                        "SL.UEM.TOTL.ZS", "Unemployment")])
        assert s == "unverifiable", text
        assert "period_mismatch" in [u.code for u in uncs], text


def test_yearless_large_gap_contradicts():
    # UT-f: beyond the lag band (>10%), latest still falsifies.
    s, _, uncs = _yl_stance("population is 125 million",
                            [("125 million", 125e6, "count")],
                            [_ev_wb(2023, 330e6, "count", "SP.POP.TOTL",
                                    "Population, total")])
    assert s == "contradicted"
    assert "assumed_period" in [u.code for u in uncs]


def test_yearless_small_gap_caps_partially():
    # UT-f: 4% off latest (publication lag) -> capped, not contradicted.
    s, _, _ = _yl_stance("population is 130 million",
                         [("130 million", 130e6, "count")],
                         [_ev_wb(2023, 125e6, "count", "SP.POP.TOTL",
                                 "Population, total")])
    assert s == "partially_supported"


def test_yearless_scale_slip_still_abstains():
    # UT-f + UT-e: scale guard runs before latest-compare.
    s, _, uncs = _yl_stance("population is 125",
                            [("125", 125.0, "count")],
                            [_ev_wb(2023, 125e6, "count", "SP.POP.TOTL",
                                    "Population, total")])
    assert "possible_scale_error" in [u.code for u in uncs]
    assert s != "contradicted"


def test_yearless_uses_latest_evidence_year():
    # UT-f: several evidences -> the most recent one wins, labeled.
    s, _, uncs = _yl_stance("population is 125 million",
                            [("125 million", 125e6, "count")],
                            [_ev_wb(2020, 100e6, "count", "SP.POP.TOTL",
                                    "Population, total"),
                             _ev_wb(2023, 125e6, "count", "SP.POP.TOTL",
                                     "Population, total")])
    blob = " ".join(u.description for u in uncs)
    assert s == "partially_supported"
    assert "2023" in blob


def test_yearless_evidence_without_year_no_crash_no_assumed():
    # UT-f iter2 (judge P11): None years never crash, never fabricate.
    from atlas.pipeline.synthesize import decide_stance

    def _ev_noyear(value, unit):
        return Evidence(claim_id="c", connector="worldbank",
                        source_name="WB (t)", source_kind="secondary",
                        evidence_kind="direct", locator="X USA ?",
                        excerpt="e", payload={"label": "L", "unit": unit,
                                              "year": None, "value": value})
    # Single year-less evidence: legacy, no "(None)" assumed period.
    s, _, uncs = _yl_stance("population is 125 million",
                            [("125 million", 125e6, "count")],
                            [_ev_noyear(125e6, "count")])
    codes = [u.code for u in uncs]
    assert s == "unverifiable"
    assert "assumed_period" not in codes
    # Mixed: dated latest wins, no crash.
    s, _, uncs = _yl_stance(
        "population is 125 million", [("125 million", 125e6, "count")],
        [_ev_noyear(999.0, "count"),
         _ev_wb(2023, 125e6, "count", "SP.POP.TOTL", "Population")])
    assert s == "partially_supported"
    assert "assumed_period" in [u.code for u in uncs]


def test_yearless_past_lexicon_iter3():
    # UT-f iter3 (judge): extended past forms stay legacy.
    from atlas.pipeline.verify import is_past_tense as f

    for text in ("GDP saw 27 trillion.", "Sales posted 5 billion.",
                 "The market witnessed 3% growth.",
                 "The agency reported 3.1%.",
                 "Stocks closed at 5000.", "GDP rose to 27 trillion.",
                 "Output fell to 5 million.", "GDP had grown.",
                 "Los precios registraban un 3%.",
                 "El PIB ascendía a 27 billones.",
                 "La inflación alcanzaba el 5%."):
        assert f(text) is True, text
    for text in ("Unemployment is reported at 3.1%.",
                 "Reports show 3.1%.",
                 "The post is 5 meters.",
                 "Erasmus students number 5 million.",
                 "La fuerza es de 5 newtons."):
        assert f(text) is False, text


def test_yearless_per_quantity_latest():
    # UT-f iter3 (judge minor): latest is per-quantity, so an older
    # same-unit row is used (no misleading unit_mismatch).
    from atlas.pipeline.synthesize import decide_stance
    s, _, uncs = _yl_stance(
        "Unemployment is 3%.", [("3%", 3.0, "percent")],
        [_ev_wb(2023, 27e12, "USD", "NY.GDP.MKTP.CD", "GDP"),
         _ev_wb(2020, 3.05, "percent", "SL.UEM.TOTL.ZS", "Unemployment")])
    codes = [u.code for u in uncs]
    assert s == "partially_supported"
    assert "assumed_period" in codes
    assert "unit_mismatch" not in codes


def test_yearless_float_year_compares():
    # UT-f iter3: float years ("2023.0") parse like ints.
    from atlas.pipeline.synthesize import decide_stance
    ev = _ev_wb("2023.0", 125e6, "count", "SP.POP.TOTL", "Population")
    s, _, uncs = _yl_stance("population is 125 million",
                            [("125 million", 125e6, "count")], [ev])
    assert s == "partially_supported"
    assert "assumed_period" in [u.code for u in uncs]


def test_spanish_opinion_lexicon():
    # UT-h: ES opinion markers mirror the EN lexicon.
    from atlas.pipeline.verify import _looks_opinionated as f

    for text in ("Creo que la economia va bien",
                 "el mejor pais del mundo",
                 "deberian bajar los impuestos ya",
                 "Pienso que es correcto.",
                 "En mi opinión, subirá.",
                 "Me parece mal.",
                 "Deben bajar los impuestos.",
                 "A mi juicio es culpable.",
                 "Desde mi punto de vista, no.",
                 "Personalmente, lo apruebo.",
                 "Considero que es justo.",
                 "Siento que va mal."):
        assert f(text, 0) is True, text


def test_spanish_opinion_collocations_not_opinion():
    # UT-h: honest ES hedging/causation is not opinion.
    from atlas.pipeline.verify import _looks_opinionated as f

    for text in ("la mejor estimación disponible",
                 "los mejores datos disponibles",
                 "El aumento se debe a la inflación.",
                 "Se debe al aumento del paro.",
                 "el debe y el haber"):
        assert f(text, 0) is False, text
    # ...but impersonal deontic "se debe + infinitive" is a
    # recommendation (judge iter3), not causation.
    for text in ("Se debe actuar ya.",
                 "Se deben bajar los impuestos.",
                 "No se debe fumar aquí."):
        assert f(text, 0) is True, text


def test_spanish_opinion_iter2_conjugations():
    # UT-h iter2 (judge): deber paradigm + 2sg + past belief.
    from atlas.pipeline.verify import _looks_opinionated as f

    for text in ("deberías bajar los impuestos",
                 "debemos actuar ya", "debo irme", "debes callar",
                 "crees que va bien", "piensas mal", "opinas sin saber",
                 "creía que era bueno", "pensaba que era justo",
                 "parecía estar mal", "debiera bajar impuestos",
                 "tendría que bajar el paro"):
        assert f(text, 0) is True, text


def test_spanish_opinion_iter2_dato_fuente():
    # UT-h iter2 (judge fix 1): singular hedges stay factual.
    from atlas.pipeline.verify import _looks_opinionated as f

    for text in ("el mejor dato disponible",
                 "la mejor fuente disponible",
                 "best source available"):
        assert f(text, 0) is False, text


def test_spanish_opinion_bare_dato_fuente_is_opinion():
    # UT-h iter3 (judge): bare superlative judgment stays opinion;
    # only the disponible-hedge scrubs.
    from atlas.pipeline.verify import _looks_opinionated as f

    for text in ("El mejor dato es 5",
                 "La mejor fuente es la BBC",
                 "Los mejores datos son los oficiales"):
        assert f(text, 0) is True, text
    for text in ("el mejor dato disponible",
                 "la mejor fuente disponible"):
        assert f(text, 0) is False, text


def test_opinion_shouldnt_negation_and_owe_debe():
    # UT-h iter4 (judge): fused negation counts; owe+amount factual.
    from atlas.pipeline.verify import _looks_opinionated as f

    assert f("You shouldn't trust that figure.", 0) is True
    assert f("You shouldnt trust that figure.", 0) is True
    assert f("You shouldn’t trust that figure.", 0) is True
    assert f("YOU SHOULDN’T TRUST THAT FIGURE.", 0) is True
    assert f("Argentina debe 44.000 millones al FMI.", 1) is False
    assert f("Me debe 500 euros.", 1) is False
    assert f("Deben bajar los impuestos.", 0) is True
