"""Multi-claim intake: split pasted text into atomic checkable claims.

ShipCheck stage (P1-A intake): an AI answer, landing page or slide contains
many factual assertions; the per-claim pipeline verifies one. The splitter
bridges them: model-first splitting under a strict decoding schema, with a
deterministic sentence-split fallback when no model is available.

Honesty rules (enforced in code, not in the prompt):
- a split span is kept only when its whitespace-normalized form appears
  verbatim in the input (no rephrasing, no invention);
- empty/duplicate spans are dropped; output is capped at MAX_SUBCLAIMS;
- the actor ("model"/"heuristic") and any truncation notes travel with
  the result so the roll-up receipt can disclose them.

Known limitation (inherent to clause-splitting, disclosed not fixed):
a verbatim span can drop a qualifier living in another clause
("GDP grew 5% in 2020" split out of "..., not in 2021, ..."),
changing the meaning. Spans are exact substrings, never complete
interpretations — the roll-up checks the span, not the discourse.
"""

from __future__ import annotations

import logging
import re

from ..connectors.countries import resolve_country

log = logging.getLogger(__name__)

# Input longer than this is truncated before splitting (disclosed in notes).
SPLIT_INPUT_CHARS = 6000
# Hard cap on sub-claims: each one costs a full pipeline run downstream.
MAX_SUBCLAIMS = 8
# Heuristic fallback drops segments shorter than this (greetings, fragments).
_MIN_SEGMENT_CHARS = 12

SPLIT_SYSTEM = """You split text into atomic factual claims for evidence verification.
Return ONLY a JSON object: {"claims": [{"text": "..."}]}
Rules: copy each claim VERBATIM from the input (exact sentences or clauses,
no rephrasing, no new words, no invented numbers). One checkable fact per
item; split conjunctions ("X and Y") into separate items. Keep numbers,
units, years and places attached to their claim. Skip greetings, opinions,
questions, advice and filler with no factual assertion. If the input holds
a single claim, return exactly one item. Never emit more than 8 items."""

# Strict decoding schema for the split call (mirrors SPLIT_SYSTEM;
# verified pattern live 2026-11-01).
SPLIT_SCHEMA = {
    "name": "claim_split",
    "schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
}

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+")

# Abbreviations whose periods must not split sentences (red-team F1:
# "The GDP of the U.S. was ..." split geo from quantity). Guarded by
# masking before the boundary split and restoring after, so returned
# segments stay exact substrings of the input.
_ABBREVIATIONS = ("U.S.", "U.K.", "E.U.", "e.g.", "i.e.", "U.S.A.")


def _mask_abbreviations(text: str) -> tuple[str, dict[str, str]]:
    masked, table = text, {}
    for i, abbr in enumerate(_ABBREVIATIONS):
        if abbr in masked:
            token = f"\ue000ABBR{i}\ue001"
            table[token] = abbr
            masked = masked.replace(abbr, token)
    return masked, table


def _normalize(span: str) -> str:
    return re.sub(r"\s+", " ", span).strip()


def split_heuristic(text: str) -> tuple[list[str], list[str]]:
    """Deterministic sentence split. No model, no invention: segments are
    exact substrings of the input, kept when long enough to assert
    something. Returns (segments, notes): truncation past MAX_SUBCLAIMS
    is disclosed, never silent (red-team F2)."""
    masked, table = _mask_abbreviations(text.strip())
    segments = [s.strip() for s in _SENTENCE_BOUNDARY.split(masked)]
    restored = []
    for s in segments:
        for token, abbr in table.items():
            s = s.replace(token, abbr)
        restored.append(s)
    kept = [s for s in restored if len(s) >= _MIN_SEGMENT_CHARS]
    notes: list[str] = []
    # F8: short-sentence drops are disclosed like the model path's
    # too-short spans (empty fragments from punctuation runs excluded).
    dropped_short = sum(
        1 for s in restored if s and len(s) < _MIN_SEGMENT_CHARS
    )
    if dropped_short:
        notes.append(f"dropped {dropped_short} too-short segment(s)")
    if len(kept) > MAX_SUBCLAIMS:
        notes.append(f"truncated split from {len(kept)} to {MAX_SUBCLAIMS}")
        kept = kept[:MAX_SUBCLAIMS]
    return kept, notes


def split_claims(
    text: str, llm=None
) -> tuple[list[str], str, str | None, list[str]]:
    """Split `text` into atomic checkable claims.

    Returns (claims, actor, model, notes). `actor` is "model" or
    "heuristic"; `model` the client name on the model path, else None.
    Model spans survive only when verbatim in the input; on any model
    failure the heuristic split is used and the fallback is noted.
    """
    notes: list[str] = []
    clean = text.strip()
    if len(clean) > SPLIT_INPUT_CHARS:
        clean = clean[:SPLIT_INPUT_CHARS]
        # F9: the roll-up verdicts the analyzed slice, never the whole
        # document past this cut — say so on the receipt itself.
        notes.append(
            f"input truncated to {SPLIT_INPUT_CHARS} chars before split; "
            "roll-up covers analyzed claims only"
        )
    if llm is not None and getattr(llm, "available", False):
        try:
            data = llm.complete_json(
                SPLIT_SYSTEM, f"Text: {clean}", max_tokens=4000,
                reasoning_effort="low", json_schema=SPLIT_SCHEMA,
            )
            return _from_model_json(clean, data, getattr(llm, "name", "model"), notes)
        except Exception as exc:
            log.warning("Claim splitting failed, using heuristic: %r", exc)
            notes.append("model split failed; heuristic sentence split used")
    else:
        notes.append("no model available; heuristic sentence split used")
    segs, hnotes = split_heuristic(clean)
    notes.extend(hnotes)
    return segs, "heuristic", None, notes


# UT-g: years mentioned in a span (candidate context, never asserted).
_YEAR_CTX_RE = re.compile(r"\b(19|20|21)\d{2}\b")


def span_contexts(spans: list[str]) -> list[dict]:
    """Resolved discourse context per span, carried APART from the text.

    Each span keeps its verbatim text untouched; the context dict holds
    what the pipeline may assume about it: the country (own mention
    wins, else the nearest preceding span's country) and mentioned
    years (same rule). Every entry declares its provenance ("own" vs
    "inherited") so consumers never mistake discourse context for an
    asserted fact — and nothing is invented when no antecedent
    exists. Deterministic, model-free, order-dependent by design.
    """
    ctxs: list[dict] = []
    last_country: tuple[str | None, str | None] = (None, None)
    last_years: list[int] = []
    for span in spans:
        ctx: dict = {}
        name, iso3 = resolve_country(span)
        if iso3 is not None:
            ctx["country"] = name
            ctx["country_iso3"] = iso3
            ctx["country_source"] = "own"
            last_country = (name, iso3)
        elif last_country[1] is not None:
            ctx["country"] = last_country[0]
            ctx["country_iso3"] = last_country[1]
            ctx["country_source"] = "inherited"
        years = sorted({int(m.group(0)) for m in _YEAR_CTX_RE.finditer(span)})
        if years:
            ctx["years"] = years
            ctx["years_source"] = "own"
            last_years = years
        elif last_years:
            ctx["years"] = list(last_years)
            ctx["years_source"] = "inherited"
        ctxs.append(ctx)
    return ctxs


def split_with_context(
    text: str, llm=None
) -> tuple[list[str], list[dict], str, list[str]]:
    """Split `text` like split_claims, plus per-span resolved context.

    Returns (spans, contexts, actor, notes): spans are verbatim (same
    guarantees as split_claims); contexts[i] carries the resolved
    country/years for spans[i] as a separate dict (see
    span_contexts). `actor` is "model"/"heuristic" and notes disclose
    the model name when the model path ran.
    """
    spans, actor, model, notes = split_claims(text, llm)
    if actor == "model" and model:
        notes = list(notes) + [f"split model: {model}"]
    return spans, span_contexts(spans), actor, notes


def _from_model_json(
    clean: str, data: dict, model_name: str, notes: list[str]
) -> tuple[list[str], str, str | None, list[str]]:
    """Validate model spans: verbatim-only, deduped, capped."""
    raw_items = data.get("claims") if isinstance(data, dict) else None
    items = raw_items if isinstance(raw_items, list) else []
    haystack = _normalize(clean)
    claims: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for item in items:
        span = item.get("text") if isinstance(item, dict) else None
        if not isinstance(span, str) or not span.strip():
            dropped += 1
            continue
        norm = _normalize(span)
        # Same floor as the heuristic path: verbatim fragments
        # ("2023.", "US") assert nothing and must not each cost a
        # full pipeline run downstream.
        if not norm or len(norm) < _MIN_SEGMENT_CHARS or norm in seen \
                or norm not in haystack:
            dropped += 1
            continue
        seen.add(norm)
        claims.append(span.strip())
    if dropped:
        notes.append(
            f"dropped {dropped} non-verbatim/duplicate/empty/too-short span(s)"
        )
    if len(claims) > MAX_SUBCLAIMS:
        notes.append(f"truncated split from {len(claims)} to {MAX_SUBCLAIMS}")
        claims = claims[:MAX_SUBCLAIMS]
    if not claims:
        notes.append("model split yielded no usable spans; heuristic used instead")
        segs, hnotes = split_heuristic(clean)
        notes.extend(hnotes)
        return segs, "heuristic", None, notes
    return claims, "model", model_name, notes
