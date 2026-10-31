"""Deterministic verification of structured claims against evidence."""

from __future__ import annotations

from ..core.compare import check_time_compatibility, compare_numbers
from ..core.types import Comparison, Evidence, StructuredClaim, TimeRef, Uncertainty
from ..core.units import normalize_unit


def _looks_like_year(q) -> bool:
    return (
        q.raw_text.strip().isdigit()
        and len(q.raw_text.strip()) == 4
        and 1000 <= q.value <= 2100
    )


def _evidence_year(ev: Evidence) -> TimeRef:
    year = ev.payload.get("year")
    try:
        y = int(str(year)) if year is not None else None
    except (TypeError, ValueError):
        y = None
    if y is None:
        return TimeRef(raw=str(year or ""), period_type="none")
    return TimeRef(raw=str(y), year=y, period_type="year")


def verify_quantitative(
    claim: StructuredClaim, evidences: list[Evidence]
) -> tuple[list[Comparison], list[Uncertainty]]:
    comparisons: list[Comparison] = []
    uncertainties: list[Uncertainty] = []
    numeric = [e for e in evidences if isinstance(e.payload.get("value"), (int, float))]
    measured = [q for q in claim.quantities if normalize_unit(q.normalized_unit)]
    bare = [
        q
        for q in claim.quantities
        if not normalize_unit(q.normalized_unit) and not _looks_like_year(q)
    ]
    if not measured and not bare:
        uncertainties.append(
            Uncertainty(
                code="no_measurable_quantity",
                description=(
                    "The claim states no numeric quantity at all; "
                    "numeric comparison is impossible."
                ),
                severity="blocking",
                related_ids=[claim.claim_id],
            )
        )
        return comparisons, uncertainties
    if not numeric:
        uncertainties.append(
            Uncertainty(
                code="no_direct_evidence",
                description=(
                    "No retrieved source provided a numeric value for this claim."
                ),
                severity="blocking",
                related_ids=[claim.claim_id],
            )
        )
        return comparisons, uncertainties
    claim_time = claim.time_refs[0] if claim.time_refs else TimeRef(period_type="none")
    # Bare numbers (no recognizable unit) may only match count evidence, and
    # the assumption is flagged explicitly on the comparison + as uncertainty.
    candidates: list[tuple] = [(q, False) for q in measured] + [(q, True) for q in bare]
    for q, is_bare in candidates:
        matched_unit = False
        for ev in numeric:
            ev_unit = ev.payload.get("unit")
            if is_bare:
                if normalize_unit(ev_unit) != "count":
                    continue
            elif normalize_unit(ev_unit) != normalize_unit(q.normalized_unit):
                continue
            matched_unit = True
            ev_time = _evidence_year(ev)
            time_cmp = check_time_compatibility(
                claim_id=claim.claim_id, claim_time=claim_time, evidence_time=ev_time
            )
            time_cmp.evidence_id = ev.evidence_id
            comparisons.append(time_cmp)
            if time_cmp.verdict != "consistent":
                uncertainties.append(
                    Uncertainty(
                        code="period_mismatch",
                        description=(
                            f"Evidence {ev.locator} does not overlap the claim period; "
                            "values compared across periods would mislead."
                        ),
                        severity="major",
                        related_ids=[ev.evidence_id, time_cmp.comparison_id],
                    )
                )
                continue
            cmp = compare_numbers(
                claim_id=claim.claim_id,
                evidence_id=ev.evidence_id,
                metric=ev.payload.get("label", ev.connector),
                claim_value=q.value,
                claim_unit=q.normalized_unit if not is_bare else "count",
                evidence_value=float(ev.payload["value"]),
                evidence_unit=ev_unit,
                claim_text=f"{q.raw_text or q.value} [{q.normalized_unit}]",
                evidence_text=ev.excerpt,
            )
            if is_bare:
                cmp.notes += (
                    " Unit assumption: the claim states a bare number; it was "
                    "compared as a count because the evidence indicator counts "
                    "individuals. Treat this comparison with caution."
                )
                uncertainties.append(
                    Uncertainty(
                        code="unit_assumed_from_context",
                        description=(
                            f"Claim quantity {q.raw_text or q.value} has no "
                            "explicit unit; compared as count (contextual "
                            "assumption, flagged on the comparison)."
                        ),
                        severity="minor",
                        related_ids=[cmp.comparison_id],
                    )
                )
            comparisons.append(cmp)
        if not matched_unit:
            uncertainties.append(
                Uncertainty(
                    code="unit_mismatch",
                    description=(
                        f"Claim quantity {q.raw_text or q.value} "
                        f"[{q.normalized_unit}] has no same-unit evidence; "
                        "incompatible magnitudes were not compared."
                    ),
                    severity="major",
                    related_ids=[claim.claim_id],
                )
            )
    return comparisons, uncertainties


def assess_science(
    claim: StructuredClaim, evidences: list[Evidence]
) -> tuple[list[Comparison], list[Uncertainty]]:
    """Deterministic bibliographic checks. Title metadata alone never verifies
    a scientific claim; that limit is recorded, not hidden."""
    comparisons: list[Comparison] = []
    uncertainties: list[Uncertainty] = []
    if not evidences:
        uncertainties.append(
            Uncertainty(
                code="no_sources_found",
                description="No scholarly works were retrieved for this claim.",
                severity="blocking",
                related_ids=[claim.claim_id],
            )
        )
        return comparisons, uncertainties
    claim_years = {t.year for t in claim.time_refs if t.year}
    for ev in evidences:
        wy = ev.payload.get("year")
        if claim_years and isinstance(wy, int):
            match = wy in claim_years
            comparisons.append(
                Comparison(
                    claim_id=claim.claim_id,
                    evidence_id=ev.evidence_id,
                    metric="publication_year_match",
                    claim_value=str(sorted(claim_years)),
                    evidence_value=str(wy),
                    tolerance_rule="exact year membership",
                    within_tolerance=match,
                    verdict="consistent" if match else "inconclusive",
                    notes=(
                        "Work published in the claimed year."
                        if match
                        else "Different publication year; relevant works may "
                        "still exist outside the claimed year."
                    ),
                )
            )
    uncertainties.append(
        Uncertainty(
            code="metadata_only",
            description=(
                "Evidence consists of bibliographic metadata (titles, years, "
                "venues). Verifying the claim requires reading full texts, "
                "which this MVP does not do."
            ),
            severity="major",
            related_ids=[e.evidence_id for e in evidences],
        )
    )
    return comparisons, uncertainties
