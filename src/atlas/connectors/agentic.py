"""Agentic retrieval: the model proposes, the code disposes.

A model may propose ``{source, indicator, iso3, year, expected_semantic}``
under a strict schema, but no proposal is trusted:

1. **Schema gate** — strict pydantic parse, ``extra="forbid"``. Any shape
   violation abstains with ``invalid_schema`` (no JSON repair, ever).
2. **Allowlist gate** — the ``(source, indicator)`` pair must exist in the
   per-source semantic allowlist, and ``expected_semantic`` must equal the
   allowlisted semantic (level vs rate vs share-of-GDP vs share). A wrong
   semantic abstains with ``semantic_mismatch`` — this is what stops the
   classic "GDP level cited for a GDP growth claim" fabrication.
3. **Existence gate** — a REAL call to the source must return at least one
   observation for ``(indicator, iso3, year)``. Empty abstains with
   ``no_evidence``; transport failures abstain with ``provider_error``.

Only a proposal that survives all three gates produces Evidence. Nothing
here calls a model: :func:`parse_model_output` parses a JSON string (in
production, the model's raw output), so this module costs $0 and is fully
deterministic. The model is an untrusted proposer; provenance comes from
the code path, never from model text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.types import Evidence
from . import ProviderError
from .extra import (
    FRED_SERIES,
    OWID_DATASETS,
    WD_COUNTRIES,
    WD_PROPS,
    fetch_fred_evidence,
    fetch_owid_evidence,
    fetch_wikidata_evidence,
)
from .worldbank import (
    INDICATORS as WB_INDICATORS,
)
from .worldbank import (
    RATE_INDICATORS as WB_RATE_INDICATORS,
)
from .worldbank import (
    fetch_observations as wb_fetch_observations,
)
from .worldbank import (
    is_allowed_iso3,
)

__all__ = [
    "Semantic",
    "Source",
    "Proposal",
    "Abstention",
    "Outcome",
    "SEMANTIC_ALLOWLIST",
    "parse_model_output",
    "validate_proposal",
    "retrieve_proposal",
]

Semantic = Literal["level", "rate", "share_gdp", "share", "other"]
Source = Literal["worldbank", "fred", "owid", "wikidata"]

_WB_GDP_SHARE = frozenset({
    "GC.DOD.TOTL.GD.ZS",  # central government debt (% of GDP)
    "NE.EXP.GNFS.ZS",  # exports (% of GDP)
    "NE.IMP.GNFS.ZS",  # imports (% of GDP)
})


def _wb_semantic(code: str, unit: str) -> str:
    if code in WB_RATE_INDICATORS:
        return "rate"
    if code in _WB_GDP_SHARE:
        return "share_gdp"
    if unit in ("percent", "per_100", "per_1000"):
        return "share"
    return "level"


def _build_allowlist() -> dict[str, dict[str, str]]:
    allow: dict[str, dict[str, str]] = {
        "worldbank": {},
        "fred": {},
        "owid": {},
        "wikidata": {},
    }
    for _kw, (code, _label, unit) in WB_INDICATORS.items():
        allow["worldbank"].setdefault(code, _wb_semantic(code, unit))
    for series_id, (_label, _unit, semantic, _kw) in FRED_SERIES.items():
        allow["fred"][series_id] = semantic
    for slug, (_label, _unit, semantic, _kw) in OWID_DATASETS.items():
        allow["owid"][slug] = semantic
    for prop, (_label, semantic, _kind, _kw) in WD_PROPS.items():
        allow["wikidata"][prop] = semantic
    return allow


#: source -> indicator -> semantic class. Derived from the connectors'
#: own catalogs, so it cannot drift from what the code can fetch.
SEMANTIC_ALLOWLIST: dict[str, dict[str, str]] = _build_allowlist()


class Proposal(BaseModel):
    """Strict schema for a model retrieval proposal."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    indicator: str = Field(min_length=1, max_length=128)
    iso3: str = Field(min_length=3, max_length=3)
    year: int | None = Field(default=None, ge=1900, le=2100)
    expected_semantic: Semantic


@dataclass(frozen=True)
class Abstention:
    code: Literal[
        "invalid_schema",
        "invalid_geo",
        "unknown_indicator",
        "semantic_mismatch",
        "no_evidence",
        "provider_error",
    ]
    reason: str


@dataclass(frozen=True)
class Outcome:
    evidences: tuple[Evidence, ...] = ()
    abstention: Abstention | None = None
    requests_made: int = 0


def parse_model_output(text: str) -> Proposal | Abstention:
    """Strictly parse model output into a Proposal. No repair attempted."""
    import json

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return Abstention("invalid_schema", f"output is not valid JSON: {exc}")
    if not isinstance(obj, dict):
        return Abstention("invalid_schema", "proposal must be a JSON object")
    try:
        return Proposal.model_validate(obj)
    except ValidationError as exc:
        return Abstention("invalid_schema", f"schema violation: {exc.errors()}")


def validate_proposal(
    proposal: Proposal,
    claim_id: str,
    limit: int = 5,
) -> Outcome:
    """Run the allowlist + existence gates and fetch on success."""
    if not is_allowed_iso3(proposal.iso3):
        return Outcome(
            abstention=Abstention("invalid_geo", f"iso3 {proposal.iso3!r} rejected")
        )
    allowed = SEMANTIC_ALLOWLIST.get(proposal.source, {})
    actual = allowed.get(proposal.indicator)
    if actual is None:
        return Outcome(
            abstention=Abstention(
                "unknown_indicator",
                f"{proposal.indicator!r} is not a known {proposal.source} indicator",
            )
        )
    if actual != proposal.expected_semantic:
        return Outcome(
            abstention=Abstention(
                "semantic_mismatch",
                f"{proposal.source}/{proposal.indicator} is {actual!r}, "
                f"not {proposal.expected_semantic!r}",
            )
        )
    if proposal.source == "fred" and proposal.iso3 != "USA":
        return Outcome(
            abstention=Abstention(
                "invalid_geo", "FRED series describe the USA only"
            )
        )
    if proposal.source == "wikidata" and proposal.iso3 not in WD_COUNTRIES:
        return Outcome(
            abstention=Abstention(
                "invalid_geo",
                f"{proposal.iso3!r} has no Wikidata country mapping",
            )
        )
    try:
        evidences = _fetch(proposal, claim_id, limit=limit)
    except ProviderError as exc:
        return Outcome(
            abstention=Abstention("provider_error", str(exc)), requests_made=1
        )
    if not evidences:
        return Outcome(
            abstention=Abstention(
                "no_evidence",
                f"{proposal.source}/{proposal.indicator} returned no "
                f"observations for {proposal.iso3} {proposal.year or ''}".rstrip(),
            ),
            requests_made=1,
        )
    return Outcome(evidences=tuple(evidences), requests_made=1)


def _fetch(proposal: Proposal, claim_id: str, limit: int) -> list[Evidence]:
    if proposal.source == "fred":
        return fetch_fred_evidence(
            claim_id, proposal.indicator, limit=limit,
            year=proposal.year, month=None,
        )
    if proposal.source == "owid":
        return fetch_owid_evidence(
            claim_id, proposal.indicator, proposal.iso3,
            limit=limit, year=proposal.year,
        )
    if proposal.source == "wikidata":
        return fetch_wikidata_evidence(
            claim_id, proposal.iso3, proposal.indicator,
            limit=limit, year=proposal.year,
        )
    return _fetch_worldbank(proposal, claim_id, limit=limit)


def _fetch_worldbank(
    proposal: Proposal, claim_id: str, limit: int
) -> list[Evidence]:
    code = proposal.indicator
    if proposal.year is not None:
        rows = wb_fetch_observations(
            code, proposal.iso3, proposal.year, proposal.year
        )
        query = f"{code} {proposal.iso3} {proposal.year}:{proposal.year}"
    else:
        rows = wb_fetch_observations(code, proposal.iso3, mrnev=True)
        query = f"{code} {proposal.iso3} mrnev=1"
    label, unit = _wb_label_unit(code)
    retrieved = datetime.now(timezone.utc)
    return [
        Evidence(
            claim_id=claim_id,
            connector="worldbank",
            source_name="World Bank Open Data",
            source_url=(
                f"https://data.worldbank.org/indicator/{code}"
                f"?locations={proposal.iso3}"
            ),
            source_kind="secondary",
            evidence_kind="direct",
            locator=f"{code} {proposal.iso3} {row.get('date')}",
            excerpt=(
                f"{label} for {row.get('country', {}).get('value', proposal.iso3)} "
                f"in {row.get('date')}: {row.get('value')}"
            ),
            payload={
                "indicator": code,
                "label": label,
                "unit": unit,
                "iso3": proposal.iso3,
                "year": row.get("date"),
                "value": row.get("value"),
                "query": query,
                "source_vintage": row.get("_source_vintage"),
                "proposed_by": "model",
                "validated_by": "code",
            },
            retrieved_at=retrieved,
        )
        for row in rows[:limit]
    ]


def _wb_label_unit(code: str) -> tuple[str, str]:
    for _kw, (c, label, unit) in WB_INDICATORS.items():
        if c == code:
            return label, unit
    return code, ""


def retrieve_proposal(
    model_output: str,
    claim_id: str,
    limit: int = 5,
) -> Outcome:
    """End-to-end: parse model output, validate, fetch or abstain."""
    parsed = parse_model_output(model_output)
    if isinstance(parsed, Abstention):
        return Outcome(abstention=parsed)
    return validate_proposal(parsed, claim_id, limit=limit)
