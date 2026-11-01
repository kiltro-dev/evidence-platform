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


def test_parse_attached_uppercase_t_is_trillion():
    # P0a: "$27.72T" misread as 27.72 USD caused a false contradicted.
    (q,) = parse_quantities("$27.72T")
    assert q.value == 27.72e12
    assert q.normalized_unit == "USD"


def test_parse_lowercase_t_stays_tonnes():
    (q,) = parse_quantities("5 t")
    assert q.value == 5.0


def test_parse_spanish_thousand_dots():
    # P0a: ES thousand separators are dots of exactly 3 digits.
    (q,) = parse_quantities("1.400 millones de dolares")
    assert q.value == 1.4e9
    qs = parse_quantities("1.400.000 habitantes")
    assert len(qs) == 1
    assert qs[0].value == 1400000.0
    assert qs[0].normalized_unit == "count"


def test_parse_decimal_dots_untouched():
    (q,) = parse_quantities("value 27.36")
    assert q.value == 27.36
    (q,) = parse_quantities("pi is about 3.14159")
    assert q.value == 3.14159


def test_parse_mil_millones_is_billion():
    # P0a: compound ES scale was truncated to "mil" (5e3, false contradict).
    (q,) = parse_quantities("5 mil millones")
    assert q.value == 5e9
    (q,) = parse_quantities("5 mil")
    assert q.value == 5000.0


def test_parse_three_digit_decimals_stay_decimal():
    # UT-a iter2 (judge): EN 3dp readings must not become thousands.
    for text, want in (("3.142", 3.142), ("0.500", 0.5), ("1.025", 1.025),
                       ("9.875", 9.875), ("1.500 million", 1.5e6),
                       ("27.360 trillion", 27.36e12), ("1.400 USD", 1.4),
                       ("price 1.459 EUR", 1.459)):
        (q,) = parse_quantities(text)
        assert q.value == want, (text, q.value)


def test_parse_thousand_dots_need_spanish_marker_or_multigroup():
    # UT-a iter2: single "1.500" needs an ES marker; multi-group never lies.
    (q,) = parse_quantities("1.400 dólares")
    assert q.value == 1400.0
    (q,) = parse_quantities("1.400 pesos")
    assert q.value == 1400.0
    (q,) = parse_quantities("12.345.678")
    assert q.value == 12345678.0
    (q,) = parse_quantities("27 T")
    assert q.value == 27.0


def test_parse_scale_prefixes_take_no_scale():
    # UT-a iter2 (judge): prefixes must not fabricate scale.
    for text in ("5 millionaires", "3 billionaires", "5 millonarios",
                 "2 billonarios", "1 thousandth"):
        (q,) = parse_quantities(text)
        assert q.value == float(text.split()[0]), (text, q.value)
    for text, want in (("5 millions", 5e6), ("2 billions", 2e9),
                       ("3 trillions", 3e12)):
        (q,) = parse_quantities(text)
        assert q.value == want, (text, q.value)


def test_parse_euro_marker_and_dot_comma_decimals():
    # UT-a iter3 (judge): eur-shadowed-euros bug + ES 1.500,25 mirror.
    for text, want in (("1.500 euros", 1500.0), ("0.500 euros", 500.0),
                       ("1.500 euro", 1500.0), ("1.500,25", 1500.25),
                       ("1.000,50", 1000.50), ("1.400.000,75", 1400000.75)):
        (q,) = parse_quantities(text)
        assert q.value == want, (text, q.value)


def test_parse_guard_bypasses_hold():
    (q,) = parse_quantities("5 millionsx")
    assert q.value == 5.0
    (q,) = parse_quantities("5 T-shirts")
    assert q.value == 5.0


def test_explicit_scale_words():
    # UT-e: full scale words are explicit; bare digits and single
    # letters are not.
    from atlas.core.units import is_explicit_scale as f

    for raw in ("1.25 billion", "27 trillion dollars", "5 million people",
                "3 thousand units", "5 hundred units", "27 billones",
                "1 millardo", "5 millones", "3 mil personas", "2 BILLION USD"):
        assert f(raw) is True, raw
    for raw in ("27.72", "$27.72T", "27T", "5 M", "3 K", "27720000000000",
                "1.25 b", "the 2023 total"):
        assert f(raw) is False, raw


def test_mil_is_explicit_scale():
    # UT-e: ES "mil" (and EN informal "mil") states a scale.
    from atlas.core.units import is_explicit_scale as f

    assert f("5 mil") is True
    assert f("5 thousand") is True


def test_scale_error_power():
    # UT-e: ratio within 1% of 10^±{3,6,9,12} matches, else None.
    from atlas.core.units import scale_error_power as f

    assert f(27.72, 27.72e12) == 12
    assert f(27.72e12, 27.72) == -12
    assert f(1.0, 1000.0) == 3
    assert f(1000.0, 1.0) == -3
    assert f(1.0, 1.009e3) == 3      # inside the 1% band
    assert f(1.0, 1.011e3) is None   # outside the band
    assert f(1.0, 500.0) is None     # not a 10^k ratio
    assert f(1.0, 1e15) is None      # beyond the ±12 range
    assert f(0.0, 1000.0) is None
    assert f(1.0, 0.0) is None
