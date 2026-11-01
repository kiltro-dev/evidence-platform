"""Claim structuring: LLM first, heuristic fallback. Always explicit."""

from __future__ import annotations

import logging
import math
import re

from ..core.types import GeoRef, Quantity, StructuredClaim, TimeRef
from ..core.units import normalize_unit, parse_quantities
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
  "quantities": [{"label": "", "raw": "<exact numeric phrase copied from the claim>",
                 "value": 0.0, "unit": <canonical unit or null>}],
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
Span rule: "raw" must be the EXACT numeric phrase copied verbatim from the
claim (e.g. "27.36 trillion", "3,1%"), number plus scale words as written.
Code keeps it only when it is a verbatim substring of the claim.
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

# UT-o: hidden-reasoning budget for structuring. "medium": schema
# extraction is the pipeline's hardest generation step, and hidden
# reasoning that overruns max_tokens truncates into a silent
# heuristic fallback — an explicit budget contains that failure.
STRUCTURE_REASONING_EFFORT = "medium"

# Strict decoding schema for the structuring call (mirrors STRUCTURE_SYSTEM
# exactly; verified live 2026-11-01). Units stay free-form strings on
# purpose: forcing an enum choice when the true unit is outside the list
# would distort extraction — code validates against KNOWN_UNITS instead.
STRUCTURE_SCHEMA = {
    "name": "claim_structure",
    "schema": {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "enum": ["economy", "science", "general"]},
            "claim_type": {
                "type": "string",
                "enum": ["quantitative", "comparative", "existence", "other"],
            },
            "subject": {"type": "string"},
            "indicator_hint": {"type": ["string", "null"]},
            "entities": {"type": "array", "items": {"type": "string"}},
            "quantities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "raw": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": ["string", "null"]},
                    },
                    "required": ["label", "raw", "value", "unit"],
                    "additionalProperties": False,
                },
            },
            "time_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw": {"type": "string"},
                        "year": {"type": ["integer", "null"]},
                        "start_year": {"type": ["integer", "null"]},
                        "end_year": {"type": ["integer", "null"]},
                        "period_type": {
                            "type": "string",
                            "enum": ["year", "range", "date", "vague", "none"],
                        },
                    },
                    "required": ["raw", "year", "start_year", "end_year", "period_type"],
                    "additionalProperties": False,
                },
            },
            "geo_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw": {"type": "string"},
                        "name": {"type": ["string", "null"]},
                        "iso3": {"type": ["string", "null"]},
                    },
                    "required": ["raw", "name", "iso3"],
                    "additionalProperties": False,
                },
            },
            "conditions": {"type": "array", "items": {"type": "string"}},
            "context_notes": {"type": "string"},
        },
        "required": [
            "domain", "claim_type", "subject", "indicator_hint", "entities",
            "quantities", "time_refs", "geo_refs", "conditions", "context_notes",
        ],
        "additionalProperties": False,
    },
}

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
                STRUCTURE_SYSTEM, f"Claim: {text}", max_tokens=4000,
                reasoning_effort=STRUCTURE_REASONING_EFFORT,
                json_schema=STRUCTURE_SCHEMA,
            )
            return _from_llm_json(text, data, getattr(llm, "name", "model"))
        except Exception as exc:
            # fall through to heuristic; audit + log record the fallback
            log.warning("LLM structuring failed, using heuristic: %r", exc)
    return extract_heuristic(text)


def _model_span(raw: object, claim_text: str) -> str:
    """Model-proposed raw span, kept only when verbatim in the claim."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    span = raw.strip()
    if span in claim_text:
        return span
    return ""


def _recover_raw_spans(claim_text: str, quantities: list[Quantity]) -> None:
    """Attach verbatim raw_text spans to model quantities missing them.

    Operation First Place lead fix for the full-vs-degraded asymmetry:
    model extraction used to emit raw_text="" always, which silently
    disabled the claimed-precision rule in full mode. Recovery reuses the
    tested heuristic number parser (scales, ES decimal commas) and adopts
    a span only when its parsed value matches the model value — no
    guessing, no invention. Spans that cannot be matched stay "" and the
    precision rule keeps skipping them (documented limitation).
    """
    pending = [q for q in quantities if not q.raw_text]
    if not pending or not claim_text.strip():
        return
    try:
        heur = parse_quantities(claim_text)
    except Exception as exc:  # never let span recovery break extraction
        log.warning("Raw-span recovery failed, keeping empty spans: %r", exc)
        return
    unused = list(heur)
    for q in pending:
        for h in unused:
            if h.raw_text and math.isclose(
                h.value, q.value, rel_tol=1e-9, abs_tol=1e-12
            ):
                q.raw_text = h.raw_text
                unused.remove(h)
                break


def _parse_llm_quantities(
    raw_items: object, notes: list[str], claim_text: str = ""
) -> list[Quantity]:
    """Validate model quantity dicts. Drops (with counts recorded in `notes`):
    non-dict/missing/unparseable values, non-finite floats, and year-like
    entries per verify._looks_like_year. Units are validated against
    KNOWN_UNITS (unknown, incl. the string "null", -> None). The model's
    "raw" span is kept only when it is a verbatim substring of the claim;
    deterministic span recovery runs later in _from_llm_json. Model unit
    conversions (value contradicting its own verbatim span while the
    span carries no unit) reset to the span-literal parse, counted."""
    quantities: list[Quantity] = []
    dropped_invalid = 0
    dropped_nonfinite = 0
    dropped_years = 0
    reset_conversions = 0
    reset_renormalizations = 0
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
            raw_text=_model_span(q.get("raw"), claim_text),
            value=value,
            unit=unit,
            normalized_value=value,
            normalized_unit=unit,
        )
        # Year probe: use label-as-raw under the SAME rule verify uses
        # (imported, not duplicated), then restore the accepted span.
        kept_span = candidate.raw_text
        candidate.raw_text = label
        if _looks_like_year(candidate):
            dropped_years += 1
            continue
        candidate.raw_text = kept_span
        # No-conversion rule: extraction must not compute. When the
        # model's own verbatim span deterministically parses to exactly
        # one number that disagrees with the model value (>1% rel),
        # while the span carries no unit and the model assigned one,
        # the model converted units instead of extracting (observed:
        # "310 basis points" -> 3.1 percent, 1/3 live runs). Reset to
        # the span-literal parse — the honest extraction. Spans the
        # heuristic cannot parse (or parses ambiguously) are trusted
        # as today; equal values never trigger, so scale application
        # ("$27.36 trillion" -> 27.36e12 USD) and same-value unit
        # inference are untouched.
        if kept_span and unit is not None:
            try:
                span_parsed = parse_quantities(kept_span)
            except Exception:
                span_parsed = []
            if len(span_parsed) == 1:
                lit = span_parsed[0]
                if lit.normalized_unit is None and not math.isclose(
                    lit.value, value, rel_tol=0.01, abs_tol=0.0
                ):
                    reset_conversions += 1
                    candidate.value = lit.value
                    candidate.unit = None
                    candidate.normalized_value = lit.value
                    candidate.normalized_unit = None
                elif (
                    lit.normalized_unit == unit
                    and not math.isclose(
                        lit.value, value, rel_tol=0.01, abs_tol=0.0
                    )
                ):
                    # V3 E-hb_f06: same unit, different digits — the model
                    # renormalized the number instead of extracting
                    # (observed live: EN "3,071%" read as 3.071, decimal
                    # comma). Reset the value to span-literal, keep unit.
                    reset_renormalizations += 1
                    candidate.value = lit.value
                    candidate.normalized_value = lit.value
        quantities.append(candidate)
    if dropped_invalid:
        notes.append(f"dropped {dropped_invalid} invalid quantities")
    if dropped_nonfinite:
        notes.append(f"dropped {dropped_nonfinite} non-finite quantities")
    if dropped_years:
        notes.append(f"dropped {dropped_years} year-like quantities")
    if reset_conversions:
        notes.append(
            f"reset {reset_conversions} model unit conversion(s) "
            "to span-literal"
        )
    if reset_renormalizations:
        notes.append(
            f"reset {reset_renormalizations} model digit "
            "renormalization(s) to span-literal"
        )
    return quantities


def _coerce_year(raw: object) -> int | None:
    """Model year field -> int or None. Strings like "unknown" become None
    (a claim-side gap the pipeline discloses) instead of raising."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _parse_llm_times(raw_items: object, notes: list[str]) -> list[TimeRef]:
    """Validate model time_ref dicts per-item.

    R5: one malformed entry (e.g. year "unknown", a bad period_type)
    must not nuke the whole model extraction into heuristic fallback.
    Unparseable years coerce to None and unknown period types to
    "none", each counted in `notes` for the audit trail.
    """
    valid_periods = {"year", "range", "date", "vague", "none"}
    times: list[TimeRef] = []
    dropped_invalid = 0
    coerced_years = 0
    coerced_periods = 0
    items = raw_items if isinstance(raw_items, list) else []
    for t in items:
        if not isinstance(t, dict):
            dropped_invalid += 1
            continue
        year = _coerce_year(t.get("year"))
        start_year = _coerce_year(t.get("start_year"))
        end_year = _coerce_year(t.get("end_year"))
        for raw_key, coerced in (
            ("year", year),
            ("start_year", start_year),
            ("end_year", end_year),
        ):
            if t.get(raw_key) is not None and coerced is None:
                coerced_years += 1
        period_type = t.get("period_type", "none")
        if period_type not in valid_periods:
            coerced_periods += 1
            period_type = "none"
        times.append(
            TimeRef(
                raw=str(t.get("raw", "")),
                year=year,
                start_year=start_year,
                end_year=end_year,
                period_type=period_type,
            )
        )
    if dropped_invalid:
        notes.append(f"dropped {dropped_invalid} invalid time_refs")
    if coerced_years:
        notes.append(f"coerced {coerced_years} unparseable years to none")
    if coerced_periods:
        notes.append(f"coerced {coerced_periods} unknown period_types to none")
    return times


def _from_llm_json(raw_text: str, data: dict, model_name: str) -> StructuredClaim:
    notes: list[str] = []
    quantities = _cap(
        _parse_llm_quantities(data.get("quantities", []), notes, raw_text),
        "quantities",
        notes,
    )
    times = _cap(
        _parse_llm_times(data.get("time_refs", []), notes),
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
    # UT-A2 literal filter (arch 5b): model time_refs/geo_refs must be
    # literal in the claim text. An invented year moves retrieval (claimed
    # years first) and an invented geo moves the country match. Years are
    # kept only as whole-digit tokens; ranges need both ends; vague/none
    # refs without numeric years are kept (they cannot move year fan-out).
    # Geos are kept when raw or name is a case-insensitive substring.
    def _year_literal(y: int | None) -> bool:
        return y is None or re.search(rf"(?<!\d){y}(?!\d)", raw_text) is not None

    literal_times = [
        t
        for t in times
        if _year_literal(t.year)
        and _year_literal(t.start_year)
        and _year_literal(t.end_year)
    ]
    if len(literal_times) != len(times):
        notes.append(
            f"dropped {len(times) - len(literal_times)} non-literal time_refs"
        )
        times = literal_times
    lowered = raw_text.lower()

    def _geo_literal(s: str | None) -> bool:
        if not s:
            return False
        s = s.lower()
        if len(s) <= 4:  # short codes need word boundaries ("US" in "status")
            return re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", lowered) is not None
        return s in lowered

    literal_geos = [
        g for g in geos if _geo_literal(g.raw) or _geo_literal(g.name)
    ]
    if len(literal_geos) != len(geos):
        notes.append(
            f"dropped {len(geos) - len(literal_geos)} non-literal geo_refs"
        )
        geos = literal_geos
    # Heuristic time/geo backfill: LLM output gaps must not silently drop
    # explicit years or countries present in the raw text.
    heur = extract_heuristic(raw_text)
    backfilled: list[str] = []
    has_model_year = any(
        t.year is not None or t.start_year is not None for t in times
    )
    if not has_model_year and heur.time_refs:
        times = heur.time_refs
        backfilled.append("time_refs")
    if not geos and heur.geo_refs:
        geos = heur.geo_refs
        backfilled.append("geo_refs")
    if not quantities and heur.quantities:
        quantities = heur.quantities
        backfilled.append("quantities")
    else:
        # Deterministic span recovery: model quantities whose "raw" was
        # missing or non-verbatim get a span only when the heuristic
        # parser finds the same value in the claim text.
        _recover_raw_spans(raw_text, quantities)
    # Mixed provenance (A2a): the model drove extraction, so the actor stays
    # "model", but backfilled fields are marked audit-visibly on the model
    # name — run.py logs "{actor}:{model}" in the extract audit step.
    extraction_model = (
        f"{model_name}+heuristic-backfill" if backfilled else model_name
    )
    # UT-A3 (arch 5c): model enums must stay inside the Literals —
    # an unknown domain silently misroutes verify (neither science nor
    # general branch). Coerce to the heuristic value, audit-visibly.
    # (Before context_notes assembly so the coercion is audited.)
    domain = data.get("domain", heur.domain)
    if domain not in ("economy", "science", "general"):
        notes.append(f"coerced unknown domain {domain!r} to {heur.domain!r}")
        domain = heur.domain
    claim_type = data.get("claim_type", heur.claim_type)
    if claim_type not in ("quantitative", "comparative", "existence", "other"):
        notes.append(
            f"coerced unknown claim_type {claim_type!r} to {heur.claim_type!r}"
        )
        claim_type = heur.claim_type
    context_notes = str(data.get("context_notes", ""))
    if notes:
        suffix = "Model output sanitized: " + "; ".join(notes) + "."
        context_notes = f"{context_notes} {suffix}".strip() if context_notes else suffix
        log.warning("LLM extraction sanitized (%s): %s", model_name, "; ".join(notes))
    if backfilled:
        log.info("LLM extraction backfilled (%s): %s", model_name, ",".join(backfilled))
    return StructuredClaim(
        raw_text=raw_text,
        domain=domain,
        claim_type=claim_type,
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
