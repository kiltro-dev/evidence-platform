"""Deterministic claim-vs-evidence comparison. No models here.

A discrepancy is reported as a measured difference, never as proof of
falsehood. Comparability is strict: unknown or mismatched units, or
non-overlapping time references, yield `not_comparable` / explicit
uncertainty instead of a number.
"""

from __future__ import annotations

from .types import Comparison, TimeRef
from .units import convert_temperature, normalize_unit, units_compatible

DEFAULT_REL_TOLERANCE = 0.02  # 2%: explicit, documented, overridable per call


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


__all__ = [
    "DEFAULT_REL_TOLERANCE",
    "compare_numbers",
    "check_time_compatibility",
    "time_refs_overlap",
    "units_compatible",
]
