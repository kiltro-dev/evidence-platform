"""End-to-end analysis orchestrator with a full audit trail."""

from __future__ import annotations

from datetime import datetime, timezone

from ..connectors import for_domain  # noqa: F401  (registry access)
from ..core.types import AuditStep, Evidence, Report, Uncertainty
from ..storage.store import save_report
from . import extract as _extract  # noqa: F401
from .extract import structure_claim
from .synthesize import synthesize
from .verify import assess_science, verify_quantitative

import atlas.connectors.openalex  # noqa: F401  (self-register)
import atlas.connectors.worldbank  # noqa: F401  (self-register)

LIMITATIONS = [
    "Evidence Atlas is an analysis aid, not an arbiter of truth.",
    "Retrieved sources may disagree for legitimate methodological reasons.",
    "A measured discrepancy is not proof of falsehood.",
    "Results depend on the sources reachable at analysis time.",
]


def _step(audit: list[AuditStep], step: str, actor: str, detail: str = "") -> AuditStep:
    s = AuditStep(
        step=step, actor=actor, ended_at=datetime.now(timezone.utc), detail=detail
    )
    audit.append(s)
    return s


def analyze(raw_text: str, llm=None, limit: int = 5, persist: bool = True) -> Report:
    audit: list[AuditStep] = []
    report = Report(claim=structure_claim(raw_text, llm))
    claim = report.claim
    _step(
        audit,
        "extract",
        f"{claim.extraction_actor}:{claim.extraction_model or 'heuristic'}",
        f"domain={claim.domain} type={claim.claim_type} "
        f"quantities={len(claim.quantities)} times={len(claim.time_refs)} "
        f"geo={len(claim.geo_refs)}",
    )

    connectors = for_domain(claim.domain)
    _step(audit, "route", "code", f"connectors={[c.name for c in connectors]}")
    evidences: list[Evidence] = []
    for conn in connectors:
        try:
            found = conn.retrieve(claim, limit=limit)
        except Exception as exc:  # a dead source degrades, never kills analysis
            report.uncertainties.append(
                Uncertainty(
                    code="connector_error",
                    description=f"Connector {conn.name} failed: {exc}",
                    severity="major",
                    related_ids=[claim.claim_id],
                )
            )
            continue
        evidences.extend(found)
        _step(audit, "retrieve", "code", f"{conn.name}: {len(found)} evidence(s)")

    if claim.domain == "science":
        comparisons, uncertainties = assess_science(claim, evidences)
    else:
        comparisons, uncertainties = verify_quantitative(claim, evidences)
        if claim.domain == "general" and not comparisons:
            extra_c, extra_u = assess_science(claim, evidences)
            comparisons.extend(extra_c)
            uncertainties.extend(extra_u)
    _step(
        audit,
        "verify",
        "code",
        f"{len(comparisons)} comparison(s), {len(uncertainties)} uncertaintie(s)",
    )

    conclusion = synthesize(claim, comparisons, uncertainties, llm)
    _step(
        audit,
        "synthesize",
        f"{conclusion.actor}:{conclusion.model or 'deterministic-rules'}",
        f"stance={conclusion.stance}",
    )

    report.evidences = evidences
    report.comparisons = comparisons
    report.uncertainties = report.uncertainties + uncertainties
    report.limitations = list(LIMITATIONS)
    report.conclusion = conclusion
    report.audit = audit
    if persist:
        save_report(report)
        _step(audit, "persist", "code", f"report_id={report.report_id}")
    return report
