"""Deterministic claim-vs-evidence comparison. No models here.

A discrepancy is reported as a measured difference, never as proof of
falsehood. Comparability is strict: unknown or mismatched units, or
non-overlapping time references, yield `not_comparable` / explicit
uncertainty instead of a number.
"""

from __future__ import annotations

import re

from .types import Comparison, Quantity, TimeRef
from .units import (
    SCALE_WORDS,
    convert_temperature,
    normalize_unit,
    units_compatible,
)

DEFAULT_REL_TOLERANCE = 0.02  # 2%: explicit, documented, overridable per call

# Team-E F1: absolute materiality thresholds by normalized unit. A
# "consistent" verdict whose absolute gap exceeds the threshold is still
# consistent (the relative rule owns the verdict) but the gap is disclosed
# on the comparison + as a minor `material_absolute_gap` uncertainty.
#
# Rationale (red-team M6): these are ORDER-OF-MAGNITUDE disclosure guides,
# not validated constants — same honesty status as the 2% prior. USD/EUR/
# GBP share $100B (same order of magnitude); count 1M (a city); percent
# 0.5pp (binds only when the value exceeds 25, since consistent implies
# abs <= 2% of value). JPY is DELIBERATELY absent (~150x FX would need its
# own row; no JPY claims in scope). Units absent here (years, t, m/s,
# temperatures, JPY) get no materiality check (None).
MATERIALITY: dict[str, float] = {
    "USD": 100e9,
    "EUR": 100e9,
    "GBP": 100e9,
    "count": 1e6,
    "percent": 0.5,
    "pp": 0.5,
}


def materiality_threshold(unit: str | None, metric: str = "") -> float | None:
    """Absolute materiality threshold for a comparison, or None when the
    unit has no materiality rule. `metric` is accepted for future
    metric-specific rules; the current table is unit-keyed only."""
    if not unit:
        return None
    return MATERIALITY.get(unit.strip())


def compare_numbers(
    *,
    claim_id: str,
    evidence_id: str | None,
    metric: str,
    claim_value: float,
    claim_unit: str | None,
    evidence_value: float,
    evidence_unit: str | None,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
    claim_text: str = "",
    evidence_text: str = "",
) -> Comparison:
    cu, eu = normalize_unit(claim_unit), normalize_unit(evidence_unit)
    rule = f"|claim-evidence|/|evidence| <= {rel_tolerance} (relative)"
    if cu is None or eu is None:
        return Comparison(
            claim_id=claim_id,
            evidence_id=evidence_id,
            metric=metric,
            claim_value=claim_text or f"{claim_value} [{claim_unit}]",
            evidence_value=evidence_text or f"{evidence_value} [{evidence_unit}]",
            unit=cu or eu,
            tolerance_rule=rule,
            verdict="not_comparable",
            notes="Unknown unit on at least one side; magnitudes not compared.",
        )
    ev = evidence_value
    if cu != eu:
        converted = convert_temperature(evidence_value, eu, cu)
        if converted is None:
            return Comparison(
                claim_id=claim_id,
                evidence_id=evidence_id,
                metric=metric,
                claim_value=claim_text or f"{claim_value} [{cu}]",
                evidence_value=evidence_text or f"{evidence_value} [{eu}]",
                unit=None,
                tolerance_rule=rule,
                verdict="not_comparable",
                notes=f"Incompatible units: claim uses {cu}, evidence uses {eu}.",
            )
        ev = converted
    denom = abs(ev) if ev != 0 else 1.0
    abs_diff = abs(claim_value - ev)
    rel = abs_diff / denom
    within = rel <= rel_tolerance
    return Comparison(
        claim_id=claim_id,
        evidence_id=evidence_id,
        metric=metric,
        claim_value=claim_text or f"{claim_value} [{cu}]",
        evidence_value=evidence_text or f"{ev} [{cu}]",
        unit=cu,
        abs_diff=abs_diff,
        rel_diff_pct=rel * 100.0,
        tolerance_rule=rule,
        within_tolerance=within,
        verdict="consistent" if within else "contradicts",
        notes=(
            "Values agree within tolerance."
            if within
            else "Measured difference exceeds tolerance; check methodology, "
            "period and definitions before drawing conclusions."
        ),
    )


def time_refs_overlap(a: TimeRef, b: TimeRef) -> bool:
    def span(t: TimeRef) -> tuple[int, int] | None:
        if t.year is not None:
            return (t.year, t.year)
        if t.start_year is not None and t.end_year is not None:
            return (t.start_year, t.end_year)
        return None

    sa, sb = span(a), span(b)
    if sa is None or sb is None:
        return False
    return sa[0] <= sb[1] and sb[0] <= sa[1]


def check_time_compatibility(
    *, claim_id: str, claim_time: TimeRef, evidence_time: TimeRef
) -> Comparison:
    overlap = time_refs_overlap(claim_time, evidence_time)
    return Comparison(
        claim_id=claim_id,
        evidence_id=None,
        metric="time_compatibility",
        claim_value=claim_time.raw or claim_time.period_type,
        evidence_value=evidence_time.raw or evidence_time.period_type,
        tolerance_rule="calendar-year overlap required",
        within_tolerance=overlap,
        verdict="consistent" if overlap else "not_comparable",
        notes=(
            "Claim and evidence refer to overlapping periods."
            if overlap
            else "Periods do not overlap or are unspecified; "
            "values from different periods must not be directly compared."
        ),
    )


_NUMBER_TOKEN_RE = re.compile(r"\d[\d.,]*")
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_ES_DECIMAL_COMMA_RE = re.compile(r"\d+,\d{1,2}")


def _scale_after_number(raw_text: str) -> float:
    """Scale factor from a scale word in the raw quantity text (EN+ES).

    Multi-letter words match whole-word; 2-letter abbreviations match
    attached or spaced ("27tn", "27 tn"); SINGLE letters match attached
    only ("$27m") — a spaced single letter is a unit, not a scale
    ("100 m" = meters, mirror of the units.py guard). First
    (longest-first) hit wins.
    """
    lowered = raw_text.lower()
    for word in sorted(SCALE_WORDS, key=len, reverse=True):
        if len(word) == 1:
            pat = r"[\d]" + re.escape(word) + r"\b"
        elif len(word) == 2:
            pat = r"[\d\s]" + re.escape(word) + r"\b"
        else:
            pat = r"\b" + re.escape(word) + r"\b"
        if re.search(pat, lowered):
            return SCALE_WORDS[word]
    return 1.0


def implied_resolution(raw_text: str, value: float) -> float | None:
    """Implied resolution of a claimed quantity: last-place unit x scale.

    "$27.36 trillion" -> 0.01 x 1e12 = 1e10; "3,1%" (ES decimal comma) ->
    0.1; "67,842,591" -> 1.0; "500 million" -> 1e6. Unparseable text (no
    digits, e.g. model extraction which carries raw_text="") -> None.
    `value` is part of the signature for call-site symmetry; the parsing
    uses the raw text only.
    """
    _ = value
    if not raw_text or not isinstance(raw_text, str):
        return None
    m = _NUMBER_TOKEN_RE.search(raw_text)
    if not m:
        return None
    token = m.group(0).strip(".,")
    if not token or not token[0].isdigit():
        return None
    if _THOUSANDS_RE.fullmatch(token):
        decimals = len(token.split(".")[1]) if "." in token else 0
    elif _ES_DECIMAL_COMMA_RE.fullmatch(token):
        decimals = len(token.split(",")[1])
    elif "." in token:
        decimals = len(token.rsplit(".", 1)[1])
    else:
        decimals = 0
    return (10.0 ** -decimals) * _scale_after_number(raw_text)


def is_year_quantity(q: Quantity) -> bool:
    """True when a parsed quantity is really a calendar year (4 digits)."""
    return (
        q.raw_text.strip().isdigit()
        and len(q.raw_text.strip()) == 4
        and 1000 <= q.value <= 2100
    )


__all__ = [
    "DEFAULT_REL_TOLERANCE",
    "MATERIALITY",
    "compare_numbers",
    "check_time_compatibility",
    "implied_resolution",
    "is_year_quantity",
    "materiality_threshold",
    "time_refs_overlap",
    "units_compatible",
]
