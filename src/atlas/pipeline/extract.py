"""Claim structuring: LLM first, heuristic fallback. Always explicit."""

from __future__ import annotations

import logging

from ..core.types import GeoRef, Quantity, StructuredClaim, TimeRef
from ..models.heuristic import extract_heuristic

log = logging.getLogger(__name__)

STRUCTURE_SYSTEM = """You structure factual claims for evidence analysis.
Return ONLY a JSON object with exactly these keys:
{
  "domain": "economy" | "science" | "general",
  "claim_type": "quantitative" | "comparative" | "existence" | "other",
  "subject": "short search-query style subject, max 20 words",
  "indicator_hint": "economic indicator name if any, else null",
  "entities": ["named entities"],
  "quantities": [{"label": "", "value": 0.0, "unit": "USD|percent|count|years|m/s|celsius|null"}],
  "time_refs": [{"raw": "", "year": 0|null, "start_year": 0|null, "end_year": 0|null,
                 "period_type": "year|range|date|vague|none"}],
  "geo_refs": [{"raw": "", "name": null, "iso3": null}],
  "conditions": ["qualifiers and conditions stated in the claim"],
  "context_notes": "anything needed to interpret the claim correctly"
}
Rules: expand scale words into value (e.g. "$27.36 trillion" -> 27360000000000.0, unit USD).
Use ISO3 country codes when identifiable. Never invent values, years or places
not stated or unambiguously implied. If unsure, use period_type "vague"/"none"."""


def structure_claim(raw_text: str, llm=None) -> StructuredClaim:
    text = raw_text.strip()
    if llm is not None and llm.available:
        try:
            data = llm.complete_json(
                STRUCTURE_SYSTEM, f"Claim: {text}", max_tokens=4000
            )
            return _from_llm_json(text, data, getattr(llm, "name", "model"))
        except Exception as exc:
            # fall through to heuristic; audit + log record the fallback
            log.warning("LLM structuring failed, using heuristic: %r", exc)
    return extract_heuristic(text)


def _from_llm_json(raw_text: str, data: dict, model_name: str) -> StructuredClaim:
    quantities = [
        Quantity(
            label=str(q.get("label", "")),
            raw_text="",
            value=float(q.get("value", 0.0)),
            unit=q.get("unit"),
            normalized_value=float(q.get("value", 0.0)),
            normalized_unit=q.get("unit"),
        )
        for q in data.get("quantities", [])
        if isinstance(q, dict) and q.get("value") is not None
    ]
    times = [
        TimeRef(
            raw=str(t.get("raw", "")),
            year=t.get("year"),
            start_year=t.get("start_year"),
            end_year=t.get("end_year"),
            period_type=t.get("period_type", "none"),
        )
        for t in data.get("time_refs", [])
        if isinstance(t, dict)
    ]
    geos = [
        GeoRef(raw=str(g.get("raw", "")), name=g.get("name"), iso3=g.get("iso3"))
        for g in data.get("geo_refs", [])
        if isinstance(g, dict)
    ]
    # Heuristic time/geo backfill: LLM output gaps must not silently drop
    # explicit years or countries present in the raw text.
    heur = extract_heuristic(raw_text)
    if not times:
        times = heur.time_refs
    if not geos:
        geos = heur.geo_refs
    if not quantities:
        quantities = heur.quantities
    return StructuredClaim(
        raw_text=raw_text,
        domain=data.get("domain", heur.domain),
        claim_type=data.get("claim_type", heur.claim_type),
        subject=str(data.get("subject", heur.subject)),
        indicator=data.get("indicator_hint"),
        entities=[str(e) for e in data.get("entities", [])],
        quantities=quantities,
        time_refs=times,
        geo_refs=geos,
        conditions=[str(c) for c in data.get("conditions", [])],
        context_notes=str(data.get("context_notes", "")),
        extraction_actor="model",
        extraction_model=model_name,
    )
