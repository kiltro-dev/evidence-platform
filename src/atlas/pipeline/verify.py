"""Deterministic verification of structured claims against evidence."""

from __future__ import annotations

import os
import re

from ..core.compare import (
    check_time_compatibility,
    compare_numbers,
    implied_resolution,
    is_year_quantity,
    materiality_threshold,
)
from ..core.types import Comparison, Evidence, StructuredClaim, TimeRef, Uncertainty
from ..core.units import normalize_unit


def _looks_like_year(q) -> bool:
    return is_year_quantity(q)


# Conservative opinion/non-claim lexicon: whole-word match only, so
# "shoulder" or "athletics bests" do not count as opinions.
_OPINION_RE = re.compile(r"\b(think|believe|best|worst|should)\b", re.IGNORECASE)
_HAS_NUMBER_RE = re.compile(r"\d")
# Split on sentence-ending punctuation ONLY when followed by whitespace or
# end-of-text, so decimals ("27.36") never split a sentence in two.
_SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")

COMPOUND_CAVEAT = "analyzed as one unit; verify parts separately"

# Team-E F3/F4: price-basis lexicon (EN+ES). Whole-word match only: the
# claim must name its price basis explicitly, otherwise a comparison
# against "current US$" evidence earns `price_basis_unchecked` (major).
# Extended (red-team M7): multi-word forms + spelled-out PPP + year-
# dollars ("in 2015 dollars", "dólares de 2015"). Known limitation: the
# trigger only fires on current-labeled evidence (the catalog's World
# Bank rows are current-US$); constant-price evidence is never examined.
_PRICE_BASIS_RE = re.compile(
    r"\b(current|constant|real|nominal|ppp|purchasing power parity|"
    r"inflation-adjusted|inflation adjusted|chained|constant prices|"
    r"constant dollars|real terms|actuales|constantes|reales|nominales|"
    r"ajustado por inflaci[oó]n|t[eé]rminos reales)\b",
    re.IGNORECASE,
)
_PRICE_BASIS_YEAR_DOLLARS_RE = re.compile(
    r"\b(in \d{4} dollars|d[oó]lares de \d{4})\b",
    re.IGNORECASE,
)


def _states_price_basis(raw_text: str) -> bool:
    """True when the claim text explicitly names its price basis."""
    text = raw_text or ""
    return bool(
        _PRICE_BASIS_RE.search(text)
        or _PRICE_BASIS_YEAR_DOLLARS_RE.search(text)
    )


def _precision_rule_enabled() -> bool:
    """Team-E F3 gate: ATLAS_PRECISION_RULE=0 disables the claimed-
    precision rule; any other value (default 1) enables it."""
    return os.environ.get("ATLAS_PRECISION_RULE", "1") != "0"


def _looks_opinionated(text: str, n_real_quantities: int) -> bool:
    """Conservative opinion/question test. Lexicon hit always counts; a bare
    question counts only when the text *ends* in "?" and states no real
    (non-year) quantity."""
    if _OPINION_RE.search(text):
        return True
    return text.strip().endswith("?") and n_real_quantities == 0


def _compound_reason(claim: StructuredClaim) -> str:
    """Return why the claim is compound, or "" when it is a single unit.

    Compound = 2+ real (non-year) quantities, OR 2+ sentences each
    containing a number/year. Detection only — never splits or rewrites."""
    real = [q for q in claim.quantities if not _looks_like_year(q)]
    if len(real) >= 2:
        return f"{len(real)} quantities"
    numbered = [
        s for s in _SENT_SPLIT_RE.split(claim.raw_text) if _HAS_NUMBER_RE.search(s)
    ]
    if len(numbered) >= 2:
        return f"{len(numbered)} numbered sentences"
    return ""


def detect_claim_shape(claim: StructuredClaim) -> list[Uncertainty]:
    """Flag non-claims and compound claims (detect+disclose, PRD §2 Out).

    Emitted by both verify paths so run.py needs no changes. The
    `opinion_or_nonclaim` flag steers decide_stance to `not_assessable`;
    `compound_claim` only adds a caveat — analysis still proceeds as one unit.
    """
    flags: list[Uncertainty] = []
    n_real = sum(1 for q in claim.quantities if not _looks_like_year(q))
    if _looks_opinionated(claim.raw_text, n_real):
        flags.append(
            Uncertainty(
                code="opinion_or_nonclaim",
                description=(
                    "The input reads as an opinion, recommendation or "
                    "question rather than a verifiable factual claim; no "
                    "verdict can be assessed."
                ),
                severity="blocking",
                related_ids=[claim.claim_id],
            )
        )
    reason = _compound_reason(claim)
    if reason:
        flags.append(
            Uncertainty(
                code="compound_claim",
                description=(
                    f"Compound claim detected ({reason}); {COMPOUND_CAVEAT}."
                ),
                severity="major",
                related_ids=[claim.claim_id],
            )
        )
    return flags


def _post_numeric_rules(
    *,
    claim: StructuredClaim,
    quantity,
    evidence: Evidence,
    comparison: Comparison,
    uncertainties: list[Uncertainty],
    price_basis_done: set[str],
) -> None:
    """Team-E F3/F4: precision, materiality and methodological codes.

    Runs after compare_numbers on every numeric comparison. Never changes
    the verdict (the relative rule owns it, so eval recomputation still
    holds); findings land in notes + uncertainty codes.
    """
    cmp = comparison
    if cmp.verdict == "consistent" and cmp.abs_diff is not None:
        if _precision_rule_enabled():
            resolution = implied_resolution(quantity.raw_text, quantity.value)
            if resolution is None:
                # Red-team M8: the rule fails open WITHOUT telling the
                # user no more — disclose the skip (minor, deduped).
                uncertainties.append(
                    Uncertainty(
                        code="precision_unchecked",
                        description=(
                            "No usable numeric span for the claimed "
                            "quantity, so the claimed-precision rule "
                            "could not bind; relative agreement stands "
                            "without a precision check."
                        ),
                        severity="minor",
                        related_ids=[cmp.comparison_id],
                    )
                )
            elif cmp.abs_diff > resolution:
                cmp.notes += (
                    " Claimed-precision check: the claim as written implies "
                    f"resolution ~{resolution:g} [{cmp.unit}], but the measured "
                    f"gap is {cmp.abs_diff:g}; the agreement is coarser than "
                    "the claimed precision."
                )
                uncertainties.append(
                    Uncertainty(
                        code="claimed_precision_exceeded",
                        description=(
                            f"Claim quantity {quantity.raw_text or quantity.value} "
                            f"implies resolution ~{resolution:g} [{cmp.unit}] but "
                            f"differs from evidence by {cmp.abs_diff:g}; stance "
                            "capped at partially_supported."
                        ),
                        severity="major",
                        related_ids=[cmp.comparison_id],
                    )
                )
        threshold = materiality_threshold(cmp.unit, cmp.metric)
        if threshold is not None and cmp.abs_diff > threshold:
            cmp.notes += (
                f" Materiality note: the absolute gap of {cmp.abs_diff:g} "
                f"[{cmp.unit}] exceeds the {threshold:g} disclosure threshold "
                "even though the values agree in relative terms."
            )
            uncertainties.append(
                Uncertainty(
                    code="material_absolute_gap",
                    description=(
                        f"Absolute gap {cmp.abs_diff:g} [{cmp.unit}] on "
                        f"{cmp.metric} exceeds the {threshold:g} materiality "
                        "threshold; reported for disclosure, verdict unchanged."
                    ),
                    severity="minor",
                    related_ids=[cmp.comparison_id],
                )
            )
    if evidence.connector != "worldbank":
        return
    uncertainties.append(
        Uncertainty(
            code="indicator_definition_assumed",
            description=(
                f"World Bank indicator "
                f"{evidence.payload.get('label', cmp.metric)!r} was matched by "
                "keyword; its definition is assumed to match the claim's "
                "intent without checking methodology notes."
            ),
            severity="minor",
            related_ids=[cmp.comparison_id],
        )
    )
    label = str(evidence.payload.get("label", ""))
    if (
        "current" in label.lower()
        and "price_basis" not in price_basis_done
        and not _states_price_basis(claim.raw_text or "")
    ):
        price_basis_done.add("price_basis")
        cmp.notes += (
            " Price-basis check: evidence is in current prices but the claim "
            "names no price basis (current/constant/real/nominal/PPP); the "
            "comparison assumes current prices."
        )
        uncertainties.append(
            Uncertainty(
                code="price_basis_unchecked",
                description=(
                    f"Evidence {label!r} is in current prices but the claim "
                    "states no price basis; compared as current prices "
                    "(unchecked assumption)."
                ),
                severity="major",
                related_ids=[cmp.comparison_id],
            )
        )
    uncertainties.append(
        Uncertainty(
            code="source_revision_risk",
            description=(
                "World Bank historical values are revised over time; today's "
                "match may not hold against past or future vintages."
            ),
            severity="minor",
            related_ids=[cmp.comparison_id],
        )
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
    uncertainties: list[Uncertainty] = detect_claim_shape(claim)
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
    price_basis_done: set[str] = set()
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
            _post_numeric_rules(
                claim=claim,
                quantity=q,
                evidence=ev,
                comparison=cmp,
                uncertainties=uncertainties,
                price_basis_done=price_basis_done,
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
    uncertainties: list[Uncertainty] = detect_claim_shape(claim)
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
