"""Core domain types for Evidence Atlas.

Every conclusion in a Report must trace back to Evidence via Comparison ids.
The `actor` field on each pipeline artifact records whether a model or
deterministic code produced it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


Actor = Literal["code", "model", "heuristic"]
Domain = Literal["economy", "science", "general"]
ClaimType = Literal["quantitative", "comparative", "existence", "other"]
SourceKind = Literal["primary", "secondary", "reproduction"]
EvidenceKind = Literal["direct", "contextual"]
Verdict = Literal["consistent", "contradicts", "inconclusive", "not_comparable"]
Stance = Literal[
    "supported", "partially_supported", "contradicted", "unverifiable",
    "not_assessable",
]


class Quantity(BaseModel):
    label: str = ""
    raw_text: str = ""
    value: float
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None


class TimeRef(BaseModel):
    raw: str = ""
    year: int | None = None
    start_year: int | None = None
    end_year: int | None = None
    period_type: Literal["year", "range", "date", "vague", "none"] = "none"


class GeoRef(BaseModel):
    raw: str = ""
    name: str | None = None
    iso3: str | None = None


class StructuredClaim(BaseModel):
    claim_id: str = Field(default_factory=lambda: _uid("clm"))
    raw_text: str
    domain: Domain = "general"
    claim_type: ClaimType = "other"
    subject: str = ""
    indicator: str | None = None
    entities: list[str] = Field(default_factory=list)
    quantities: list[Quantity] = Field(default_factory=list)
    time_refs: list[TimeRef] = Field(default_factory=list)
    geo_refs: list[GeoRef] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    context_notes: str = ""
    extraction_actor: Actor = "code"
    extraction_model: str | None = None


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _uid("ev"))
    claim_id: str
    connector: str
    source_name: str
    source_url: str | None = None
    source_kind: SourceKind
    evidence_kind: EvidenceKind
    locator: str = ""
    excerpt: str = ""
    payload: dict = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=_utcnow)


class Comparison(BaseModel):
    comparison_id: str = Field(default_factory=lambda: _uid("cmp"))
    claim_id: str
    evidence_id: str | None = None
    actor: Actor = "code"
    model: str | None = None
    metric: str
    claim_value: str = ""
    evidence_value: str = ""
    unit: str | None = None
    abs_diff: float | None = None
    rel_diff_pct: float | None = None
    tolerance_rule: str = ""
    within_tolerance: bool | None = None
    verdict: Verdict = "inconclusive"
    notes: str = ""


class Uncertainty(BaseModel):
    code: str
    description: str
    severity: Literal["blocking", "major", "minor"] = "major"
    related_ids: list[str] = Field(default_factory=list)


class Conclusion(BaseModel):
    stance: Stance
    summary: str
    support_comparison_ids: list[str] = Field(default_factory=list)
    contradict_comparison_ids: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    actor: Actor = "code"
    model: str | None = None


class AuditStep(BaseModel):
    step: str
    actor: str
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    detail: str = ""
    duration_ms: float = 0.0


class Report(BaseModel):
    report_id: str = Field(default_factory=lambda: _uid("rep"))
    claim: StructuredClaim
    evidences: list[Evidence] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conclusion: Conclusion | None = None
    audit: list[AuditStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    meta: dict = Field(default_factory=dict)
    elapsed_ms: float = 0.0
