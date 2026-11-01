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
    """F6: a claim with no year gets a claim-side description, same code."""
    claim = _claim(
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
