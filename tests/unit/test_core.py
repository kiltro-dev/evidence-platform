"""Unit tests for deterministic core: units and comparison."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.compare import (
    check_time_compatibility,
    compare_numbers,
    time_refs_overlap,
)
from atlas.core.types import TimeRef
from atlas.core.units import (
    convert_temperature,
    normalize_unit,
    parse_quantities,
    units_compatible,
)


def test_parse_trillion_dollars():
    qs = parse_quantities("GDP was $27.36 trillion in 2023")
    # "2023" also parses as a bare number; the monetary quantity must be exact
    money = [q for q in qs if q.normalized_unit == "USD"]
    assert len(money) == 1
    assert money[0].value == 27.36e12


def test_parse_percent_and_counts():
    qs = parse_quantities("inflation of 3.2% and 5 million people")
    assert len(qs) == 2
    assert qs[0].normalized_unit == "percent"
    assert qs[1].value == 5e6


def test_parse_comma_numbers():
    qs = parse_quantities("299,792,458 m/s")
    assert len(qs) == 1
    assert qs[0].value == 299792458.0
    assert qs[0].normalized_unit == "m/s"


def test_units_compatible_strict():
    assert units_compatible("USD", "$")
    assert units_compatible("percent", "%")
    assert not units_compatible("USD", "EUR")
    assert not units_compatible("USD", None)
    assert not units_compatible(None, None)


def test_normalize_unit_variants():
    assert normalize_unit("dollars") == "USD"
    assert normalize_unit("°c") == "celsius"
    assert normalize_unit(None) is None


def test_temperature_conversion():
    assert convert_temperature(100, "celsius", "fahrenheit") == 212.0
    assert convert_temperature(32, "fahrenheit", "celsius") == 0.0
    assert convert_temperature(1, "USD", "EUR") is None


def test_compare_within_tolerance():
    c = compare_numbers(
        claim_id="c",
        evidence_id="e",
        metric="gdp",
        claim_value=27.36e12,
        claim_unit="USD",
        evidence_value=27.72e12,
        evidence_unit="USD",
        rel_tolerance=0.02,
    )
    assert c.verdict == "consistent"
    assert c.within_tolerance is True
    assert c.rel_diff_pct is not None and c.rel_diff_pct < 2.0


def test_compare_outside_tolerance_is_measured_not_falsehood():
    c = compare_numbers(
        claim_id="c",
        evidence_id="e",
        metric="gdp",
        claim_value=20e12,
        claim_unit="USD",
        evidence_value=27.72e12,
        evidence_unit="USD",
    )
    assert c.verdict == "contradicts"
    assert "methodology" in c.notes


def test_compare_incompatible_units():
    c = compare_numbers(
        claim_id="c",
        evidence_id="e",
        metric="m",
        claim_value=1,
        claim_unit="USD",
        evidence_value=1,
        evidence_unit="EUR",
    )
    assert c.verdict == "not_comparable"
    assert c.abs_diff is None


def test_compare_unknown_unit_never_compared():
    c = compare_numbers(
        claim_id="c",
        evidence_id="e",
        metric="m",
        claim_value=1,
        claim_unit=None,
        evidence_value=1,
        evidence_unit="USD",
    )
    assert c.verdict == "not_comparable"


def test_compare_temperature_cross_unit():
    c = compare_numbers(
        claim_id="c",
        evidence_id="e",
        metric="temp",
        claim_value=100,
        claim_unit="celsius",
        evidence_value=212,
        evidence_unit="fahrenheit",
    )
    assert c.verdict == "consistent"


def test_time_overlap():
    a = TimeRef(raw="2023", year=2023, period_type="year")
    b = TimeRef(raw="2020-2024", start_year=2020, end_year=2024, period_type="range")
    assert time_refs_overlap(a, b) is True
    c = TimeRef(raw="2019", year=2019, period_type="year")
    assert time_refs_overlap(a, c) is False
    vague = TimeRef(raw="recently", period_type="vague")
    assert time_refs_overlap(a, vague) is False


def test_time_compatibility_comparison():
    ok = check_time_compatibility(
        claim_id="c",
        claim_time=TimeRef(raw="2023", year=2023, period_type="year"),
        evidence_time=TimeRef(raw="2023", year=2023, period_type="year"),
    )
    assert ok.verdict == "consistent"
    bad = check_time_compatibility(
        claim_id="c",
        claim_time=TimeRef(raw="2023", year=2023, period_type="year"),
        evidence_time=TimeRef(raw="2010", year=2010, period_type="year"),
    )
    assert bad.verdict == "not_comparable"


def test_parse_percentage_points_is_pp_not_percent():
    # MEDIUM audit: alternation tried 'percent' first, so 'percentage
    # points' prefix-matched as percent in degraded mode.
    (q,) = [q for q in parse_quantities("rose 2 percentage points")
            if q.value == 2.0]
    assert q.normalized_unit == "pp"


def test_parse_pp_abbreviation():
    for text in ("rose 2 pp", "rose 2pp"):
        (q,) = [q for q in parse_quantities(text) if q.value == 2.0]
        assert q.normalized_unit == "pp"


def test_parse_percent_still_percent_and_ppm_not_pp():
    (q,) = [q for q in parse_quantities("rose 2 percent") if q.value == 2.0]
    assert q.normalized_unit == "percent"
    (m,) = [q for q in parse_quantities("2 ppm of lead") if q.value == 2.0]
    assert m.normalized_unit != "pp"
