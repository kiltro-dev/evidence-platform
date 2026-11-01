"""Property-based never-crash + invariant fuzz for core/units (pre-barrido).

Batería War-Room pedía hypothesis en core/. Invariants:
- parse_quantities/normalize_unit/is_explicit_scale never raise on
  arbitrary unicode (incl. digits mixed with ES/EN scale words).
- Every parsed quantity value is finite; raw_text (when set) is a
  substring of the input.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from atlas.core.units import (  # noqa: E402
    is_explicit_scale,
    normalize_unit,
    parse_quantities,
)

NUM_FRAGMENTS = st.sampled_from([
    "27.36", "1.400", "1,000.50", "1.500,25", "$27.72T", "5 mil millones",
    "1.400.000", "3.1%", "2e6", "1,000", "0", "-4.5", "1e+21", "NaN", "inf",
    "1.2.3", ",,,", "...", "１２３", "  ", "\u00a0", "$", "%", "T", "bn",
])
WORD_FRAGMENTS = st.sampled_from([
    "trillion", "billion", "million", "billones", "millones", "mil",
    "trillones", "dollars", "USD", "habitantes", "people", "%", "percent",
    "GDP", "PIB", "the", "de", "los", "in", "en", "2023", "was", "fue",
])
TEXTS = st.lists(
    st.one_of(NUM_FRAGMENTS, WORD_FRAGMENTS), min_size=1, max_size=12
).map(" ".join)


@settings(max_examples=400)
@given(TEXTS)
def test_parse_quantities_never_crash_finite_verbatim(text):
    for q in parse_quantities(text):
        assert math.isfinite(q.value), (text, q)
        if q.raw_text:
            assert q.raw_text in text, (text, q)


@settings(max_examples=200)
@given(st.text(min_size=0, max_size=40))
def test_normalize_unit_never_crash(text):
    normalize_unit(text)
    normalize_unit(None)


@settings(max_examples=200)
@given(st.text(min_size=0, max_size=40))
def test_is_explicit_scale_never_crash(text):
    assert isinstance(is_explicit_scale(text), bool)
