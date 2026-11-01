"""Claim structuring: LLM first, heuristic fallback. Always explicit."""

from __future__ import annotations

import logging
import math

from ..core.types import GeoRef, Quantity, StructuredClaim, TimeRef
from ..core.units import normalize_unit
from ..models.heuristic import extract_heuristic
from .verify import _looks_like_year

log = logging.getLogger(__name__)

STRUCTURE_SYSTEM = """You structure factual claims for evidence analysis.
Return ONLY a JSON object with exactly these keys:
{
  "domain": "economy" | "science" | "general",
  "claim_type": "quantitative" | "comparative" | "existence" | "other",
  "subject": "short search-query style subject, max 20 words",
  "indicator_hint": "economic indicator name if any, else null",
  "entities": ["named entities"],
  "quantities": [{"label": "", "value": 0.0, "unit": <canonical unit or null>}],
  "time_refs": [{"raw": "", "year": 0|null, "start_year": 0|null, "end_year": 0|null,
                 "period_type": "year|range|date|vague|none"}],
  "geo_refs": [{"raw": "", "name": null, "iso3": null}],
  "conditions": ["qualifiers and conditions stated in the claim"],
  "context_notes": "anything needed to interpret the claim correctly"
}
Rules: expand scale words into value (e.g. "$27.36 trillion" -> 27360000000000.0, unit USD).
Domain rule: prefer a specific domain. Demographic, economic, financial,
statistical and public-data claims are "economy"; studies, papers,
experiments, species and physical constants are "science". Use "general"
only when neither fits.
Use ISO3 country codes when identifiable.
Unit rule: "unit" is JSON null (no unit) or one of USD, EUR, GBP, JPY,
percent, pp, count, m/s, km/s, celsius, fahrenheit, kelvin, years.
Never emit the string "null"; unknown or absent units are JSON null.
Literal-presence rule: extract ONLY values, years and places literally
present in the claim text. Never infer, compute, guess or invent numbers,
years or places; if unsure, omit the item and use period_type "vague"/"none".
Years are time_refs, never quantities: do not list a calendar year
(e.g. 2023) in "quantities" even when it looks like a number.
Compound claims: list EVERY quantity stated (do not merge, summarize or
drop any); the parts are analyzed as one unit downstream.
Opinions, recommendations and questions ("I think", "best", "should", or a
"?" with no factual assertion): emit "quantities": [] and "claim_type":
"other", and note the non-claim nature in "context_notes"."""

# Canonical units accepted from model output. normalize_unit passes unknown
# strings through, so membership here (not normalize_unit alone) is the
# validity test: anything else — including the string "null" — becomes None.
KNOWN_UNITS = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "percent", "pp", "count",
        "m/s", "km/s", "celsius", "fahrenheit", "kelvin", "years",
    }
)

# Security cap (PRD §5): model list fields are truncated past this length.
_MAX_LIST_ITEMS = 20


def _valid_unit(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    norm = normalize_unit(raw)
    return norm if norm in KNOWN_UNITS else None


def _cap(items: list, name: str, notes: list[str]) -> list:
    if len(items) > _MAX_LIST_ITEMS:
        notes.append(f"truncated {name} from {len(items)} to {_MAX_LIST_ITEMS}")
        return items[:_MAX_LIST_ITEMS]
    return items


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


def _parse_llm_quantities(raw_items: object, notes: list[str]) -> list[Quantity]:
    """Validate model quantity dicts. Drops (with counts recorded in `notes`):
    non-dict/missing/unparseable values, non-finite floats, and year-like
    entries per verify._looks_like_year. Units are validated against
    KNOWN_UNITS (unknown, incl. the string "null", -> None)."""
    quantities: list[Quantity] = []
    dropped_invalid = 0
    dropped_nonfinite = 0
    dropped_years = 0
    items = raw_items if isinstance(raw_items, list) else []
    for q in items:
        if not isinstance(q, dict) or q.get("value") is None:
            dropped_invalid += 1
            continue
        try:
            value = float(q["value"])
        except (TypeError, ValueError):
            dropped_invalid += 1
            continue
        if not math.isfinite(value):
            dropped_nonfinite += 1
            continue
        label = str(q.get("label", ""))
        unit = _valid_unit(q.get("unit"))
        candidate = Quantity(
            label=label,
            raw_text="",
            value=value,
            unit=unit,
            normalized_value=value,
            normalized_unit=unit,
        )
        # LLM JSON carries no raw_text span; the label is the only anchor
        # for year detection, so probe with label-as-raw under the SAME rule
        # verify uses (imported, not duplicated), then restore raw_text="".
        candidate.raw_text = label
        if _looks_like_year(candidate):
            dropped_years += 1
            continue
        candidate.raw_text = ""
        quantities.append(candidate)
    if dropped_invalid:
        notes.append(f"dropped {dropped_invalid} invalid quantities")
    if dropped_nonfinite:
        notes.append(f"dropped {dropped_nonfinite} non-finite quantities")
    if dropped_years:
        notes.append(f"dropped {dropped_years} year-like quantities")
    return quantities


def _from_llm_json(raw_text: str, data: dict, model_name: str) -> StructuredClaim:
    notes: list[str] = []
    quantities = _cap(
        _parse_llm_quantities(data.get("quantities", []), notes),
        "quantities",
        notes,
    )
    times = _cap(
        [
            TimeRef(
                raw=str(t.get("raw", "")),
                year=t.get("year"),
                start_year=t.get("start_year"),
                end_year=t.get("end_year"),
                period_type=t.get("period_type", "none"),
            )
            for t in data.get("time_refs", [])
            if isinstance(t, dict)
        ],
        "time_refs",
        notes,
    )
    geos = _cap(
        [
            GeoRef(raw=str(g.get("raw", "")), name=g.get("name"), iso3=g.get("iso3"))
            for g in data.get("geo_refs", [])
            if isinstance(g, dict)
        ],
        "geo_refs",
        notes,
    )
    entities = _cap([str(e) for e in data.get("entities", [])], "entities", notes)
    conditions = _cap(
        [str(c) for c in data.get("conditions", [])], "conditions", notes
    )
    # Heuristic time/geo backfill: LLM output gaps must not silently drop
    # explicit years or countries present in the raw text.
    heur = extract_heuristic(raw_text)
    backfilled: list[str] = []
    if not times and heur.time_refs:
        times = heur.time_refs
        backfilled.append("time_refs")
    if not geos and heur.geo_refs:
        geos = heur.geo_refs
        backfilled.append("geo_refs")
    if not quantities and heur.quantities:
        quantities = heur.quantities
        backfilled.append("quantities")
    # Mixed provenance (A2a): the model drove extraction, so the actor stays
    # "model", but backfilled fields are marked audit-visibly on the model
    # name — run.py logs "{actor}:{model}" in the extract audit step.
    extraction_model = (
        f"{model_name}+heuristic-backfill" if backfilled else model_name
    )
    context_notes = str(data.get("context_notes", ""))
    if notes:
        suffix = "Model output sanitized: " + "; ".join(notes) + "."
        context_notes = f"{context_notes} {suffix}".strip() if context_notes else suffix
        log.warning("LLM extraction sanitized (%s): %s", model_name, "; ".join(notes))
    if backfilled:
        log.info("LLM extraction backfilled (%s): %s", model_name, ",".join(backfilled))
    return StructuredClaim(
        raw_text=raw_text,
        domain=data.get("domain", heur.domain),
        claim_type=data.get("claim_type", heur.claim_type),
        subject=str(data.get("subject", heur.subject)),
        indicator=data.get("indicator_hint"),
        entities=entities,
        quantities=quantities,
        time_refs=times,
        geo_refs=geos,
        conditions=conditions,
        context_notes=context_notes,
        extraction_actor="model",
        extraction_model=extraction_model,
    )
