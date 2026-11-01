"""Deterministic verifier for Atlas Live M2a. CODE ONLY — no model calls.

Pipeline per claim (all steps deterministic):

1. Quote check: the package quote must appear literally in the snapshot
   (whitespace-normalized), and the snapshot sha256 must match.
2. Re-extraction: numbers are re-extracted from the quote with
   ``src.atlas.core.units`` (suffixes T/B/M, ES thousands) — the
   researcher's ``numbers[]`` are never trusted blindly.
3. Numeric compare vs the claim number (relative tolerance, default 2%):
   a 10^3k mismatch with no explicit scale word abstains with
   ``possible_scale_error``; an explicit scale that is 1000x off is a
   real ``contradicted``.
4. Temporal alignment: claim year vs source year must agree when both
   are known.
5. Hierarchy: usable primary packages outrank secondary ones.

Verdicts: ``supported`` / ``contradicted`` / ``disputed`` /
``unverifiable`` + ``reasons[]``. With no usable package the verdict is
ALWAYS ``unverifiable``. The verdict is emitted on the bus as
``claim.verdict``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from live.evidence.package import (
    EvidencePackage,
    sha256_of_bytes,
    sha256_of_text,
    source_meta_for_snapshot,
)

# Atlas core (deterministic). ``src.`` prefix when the repo root is on
# sys.path (live convention, see live/run_live.py); plain ``atlas``
# when ``src/`` itself is on sys.path (tests convention).
try:  # pragma: no cover - import shim
    from src.atlas.core.compare import DEFAULT_REL_TOLERANCE, is_year_quantity
    from src.atlas.core.units import (
        is_explicit_scale,
        parse_quantities,
        scale_error_power,
        units_compatible,
    )
except ImportError:  # pragma: no cover - import shim
    from atlas.core.compare import DEFAULT_REL_TOLERANCE, is_year_quantity
    from atlas.core.units import (
        is_explicit_scale,
        parse_quantities,
        scale_error_power,
        units_compatible,
    )

VerdictLabel = Literal["supported", "contradicted", "disputed", "unverifiable"]

_WS_RE = re.compile(r"\s+")


def normalize_ws(s: str) -> str:
    """Collapse all whitespace runs to single spaces and strip."""
    return _WS_RE.sub(" ", s or "").strip()


@dataclass
class ClaimInput:
    """Numeric claim under verification (from the detector)."""

    claim_id: str
    text: str = ""
    value: float = 0.0
    unit: str | None = None
    raw: str = ""  # original numeric span, e.g. "1.25 billion"
    claim_year: int | None = None
    tolerance: float = DEFAULT_REL_TOLERANCE


@dataclass
class Verdict:
    claim_id: str
    verdict: VerdictLabel
    reasons: list[str] = field(default_factory=list)
    package_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        return {
            "type": "claim.verdict",
            "claim_id": self.claim_id,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "package_ids": list(self.package_ids),
            "actor": "m2a-verifier",
        }


SnapshotLoader = Callable[[EvidencePackage], str | bytes | None]


def _snapshot_candidates(snapshot: str | bytes) -> list[str]:
    """Decoded texts to search the quote in (M2c bytes-aware loading).

    str snapshots pass through unchanged (all pre-M2c callers). bytes
    are the RAW captured bytes: UTF-8 decoding first, latin-1 as the
    legacy-charset fallback (a latin-1 quote such as "café" survives
    only in the second candidate).
    """
    if isinstance(snapshot, str):
        return [snapshot]
    raw = bytes(snapshot)
    out = [raw.decode("utf-8", "replace")]
    try:
        latin = raw.decode("latin-1")
    except Exception:
        latin = ""
    if latin and latin != out[0]:
        out.append(latin)
    return out


def _check_package(pkg: EvidencePackage, claim: ClaimInput,
                   loader: SnapshotLoader) -> tuple[str | None, str, dict[str, Any]]:
    """Check one package. Returns (outcome, reason, detail).

    outcome: "support" | "contradict" | None (unusable). reason is the
    machine-readable code recorded in the verdict reasons[].
    """
    if pkg.claim_id != claim.claim_id:
        return None, "claim_mismatch", {}
    try:
        snapshot = loader(pkg)
    except Exception:
        return None, "snapshot_unreadable", {}
    if not snapshot:
        return None, "snapshot_missing", {}
    if isinstance(snapshot, (bytes, bytearray)):
        # M2c canonical: hash over RAW bytes (matches fetch-side sha on
        # every charset); quote search over decoded candidates.
        if sha256_of_bytes(bytes(snapshot)) != pkg.sha256:
            return None, "snapshot_hash_mismatch", {}
        candidates = _snapshot_candidates(snapshot)
    else:
        if sha256_of_text(snapshot) != pkg.sha256:
            return None, "snapshot_hash_mismatch", {}
        candidates = [snapshot]
    # 1) quote must appear literally (whitespace-normalized).
    norm_quote = normalize_ws(pkg.quote)
    if not norm_quote or not any(
            norm_quote in normalize_ws(text) for text in candidates):
        return None, "quote_not_found", {}
    snapshot_text = candidates[0]
    detail: dict[str, Any] = {}
    start, end = pkg.offsets
    if 0 <= start < end <= len(snapshot_text):
        if normalize_ws(snapshot_text[start:end]) != norm_quote:
            detail["offsets_inconsistent"] = True
    # 2) re-extract numbers from the quote (never trust numbers[] blindly).
    cands = [q for q in parse_quantities(pkg.quote, label="quote")
             if not is_year_quantity(q)]
    if not cands:
        return None, "no_number_in_quote", detail
    # 3a) scale-slip backstop BEFORE units/tolerance (mirrors verify.py):
    # a 10^3k gap with no explicit scale word abstains; an explicit
    # scale that is 1000x off stays a real contradiction.
    if not is_explicit_scale(claim.raw or ""):
        for q in cands:
            power = scale_error_power(claim.value, q.value)
            if power is not None:
                detail["scale_power"] = power
                return None, "possible_scale_error", detail
    # 3b) unit compatibility, then closest candidate by relative gap.
    if claim.unit is None:
        return None, "claim_unit_unknown", detail
    compat = [q for q in cands if units_compatible(claim.unit, q.normalized_unit)]
    scale = 1.0
    meta_unit: str | None = None
    if not compat and pkg.source_meta is not None:
        # M2d structured fallback: unitless JSON quotes (WB API rows) get
        # their unit from the snapshot-declared indicator — re-derived
        # from the snapshot bytes here, never trusted from the package.
        # Values still come from the quote; scale still applies below.
        rederived = None
        for text in candidates:
            rederived = source_meta_for_snapshot(pkg.url, text)
            if rederived is not None:
                break
        if (rederived is not None
                and rederived.indicator == pkg.source_meta.indicator
                and units_compatible(claim.unit, rederived.unit)):
            compat = list(cands)
            scale = float(rederived.scale)
            meta_unit = rederived.unit
            detail["unit_from"] = "source_meta"
            detail["source_indicator"] = rederived.indicator
    if not compat:
        return None, "unit_mismatch", detail
    best = min(compat, key=lambda q: abs(claim.value - q.value * scale)
               / (abs(q.value * scale) or 1.0))
    evidence_value = best.value * scale
    detail["evidence_value"] = evidence_value
    detail["evidence_unit"] = best.normalized_unit or meta_unit
    denom = abs(evidence_value) if evidence_value != 0 else 1.0
    rel = abs(claim.value - evidence_value) / denom
    detail["rel_diff"] = rel
    if rel > claim.tolerance:
        return "contradict", "numeric_mismatch", detail
    # 4) temporal alignment (claim year vs source year, when both known).
    if (claim.claim_year is not None and pkg.source_year is not None
            and claim.claim_year != pkg.source_year):
        detail["claim_year"] = claim.claim_year
        detail["source_year"] = pkg.source_year
        return None, "temporal_mismatch", detail
    return "support", "numeric_match", detail


def verify_claim(claim: ClaimInput, packages: list[EvidencePackage],
                 loader: SnapshotLoader) -> Verdict:
    """Verify one claim against its evidence packages (deterministic)."""
    usable: list[tuple[EvidencePackage, str, str, dict[str, Any]]] = []
    unusable_reasons: list[str] = []
    details: dict[str, Any] = {}
    for pkg in packages or []:
        outcome, reason, detail = _check_package(pkg, claim, loader)
        details[pkg.package_id] = {"outcome": outcome or "unusable",
                                   "reason": reason, **detail}
        if outcome is None:
            unusable_reasons.append(f"{pkg.package_id}:{reason}")
        else:
            usable.append((pkg, outcome, reason, detail))
    # No usable package -> ALWAYS unverifiable.
    if not usable:
        reasons = ["no_usable_package"] + sorted(set(
            r.split(":", 1)[1] for r in unusable_reasons))
        return Verdict(claim_id=claim.claim_id, verdict="unverifiable",
                       reasons=reasons,
                       package_ids=[p.package_id for p in packages or []],
                       details=details)
    # 5) hierarchy: usable primaries outrank ALL secondaries.
    primaries = [u for u in usable if u[0].source_type == "primary"]
    deciding = primaries or usable
    used_ids = [p.package_id for p, _, _, _ in deciding]
    outcomes = {o for _, o, _, _ in deciding}
    if primaries and len(usable) > len(primaries):
        details["hierarchy"] = "primary_beats_secondary"
    if outcomes == {"support"}:
        label: VerdictLabel = "supported"
    elif outcomes == {"contradict"}:
        label = "contradicted"
    else:
        label = "disputed"
    reasons = sorted({r for _, _, r, _ in deciding})
    if label == "disputed":
        reasons = ["packages_disagree"] + reasons
    return Verdict(claim_id=claim.claim_id, verdict=label, reasons=reasons,
                   package_ids=used_ids, details=details)


async def verify_and_emit(claim: ClaimInput, packages: list[EvidencePackage],
                          loader: SnapshotLoader, bus: Any) -> Verdict:
    """Verify one claim and publish its ``claim.verdict`` event on the bus."""
    verdict = verify_claim(claim, packages, loader)
    await bus.publish(verdict.to_event())
    return verdict


__all__ = [
    "ClaimInput",
    "SnapshotLoader",
    "Verdict",
    "VerdictLabel",
    "normalize_ws",
    "verify_and_emit",
    "verify_claim",
]
