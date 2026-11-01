"""The four swarm roles: prompts, strict schemas, and code validation.

Roles (all advisory-only; the pipeline verdict is never touched):

- estructurador: structures the claim into StructuredClaim JSON. Reuses
  the pipeline's own STRUCTURE_SYSTEM / STRUCTURE_SCHEMA single source
  of truth (imported, not copied) via structure_claim().
- recuperador: proposes CANDIDATE {source, indicator} pairs the pipeline
  could consult. Candidates are suggestions; coverage is checked in
  code against actually retrieved evidence.
- red_team: attacks the claim (weak phrasing, missing qualifiers,
  alternative readings). Attacks surface as uncertainties, never as
  verdict flips.
- explicador: plain-language summary of the checked claim for readers.
  Never replaces the pipeline conclusion.

Every role returns a RoleOutcome dict::

    {"role": str, "ok": bool, "skipped": str | None,
     "data": dict, "notes": list[str], "usage": dict | None}

``usage`` is the forked client's last_usage snapshot (tokens) or None.
Model failures degrade to ok=False with a note; they never raise.
"""

from __future__ import annotations

import logging
import re

from ..core.types import StructuredClaim
from ..pipeline.extract import (
    STRUCTURE_SCHEMA,
    STRUCTURE_SYSTEM,
    structure_claim,
)
from ..pipeline.synthesize import FORBIDDEN_STRENGTH

log = logging.getLogger(__name__)

ROLE_NAMES = ("estructurador", "recuperador", "red_team", "explicador")

# Estructurador prompt/schema ARE the pipeline's (single source of truth).
ESTRUCTURADOR_SYSTEM = STRUCTURE_SYSTEM
ESTRUCTURADOR_SCHEMA = STRUCTURE_SCHEMA

RECUPERADOR_SYSTEM = """You propose candidate evidence sources for a factual claim.
Return ONLY a JSON object: {"candidates": [{"source": "...", "indicator": "...", "rationale": "..."}]}
Rules: "source" is a public data provider name (e.g. "World Bank", "OpenAlex", "UN Data").
"indicator" is the specific dataset or indicator name that could hold the figure.
"rationale" is one sentence linking the claim to that indicator.
Propose at most 10 candidates, most relevant first. Never invent URLs, DOIs or
record ids. Never state what the source says — propose where to look, nothing more."""

RECUPERADOR_SCHEMA = {
    "name": "source_candidates",
    "schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "indicator": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["source", "indicator", "rationale"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["candidates"],
        "additionalProperties": False,
    },
}

REDTEAM_SYSTEM = """You red-team a factual claim before verification. You are ADVISORY ONLY:
you list weaknesses, never verdicts. Never say the claim is true or false.
Return ONLY a JSON object:
{"attacks": [{"claim_span": "...", "weakness": "...", "severity": "low|medium|high"}]}
Rules: "claim_span" is copied VERBATIM from the claim (exact substring).
"weakness" names the specific weakness (vague unit, missing year, dropped
qualifier, ambiguous geo, alternative reading). "severity" is low (nitpick),
medium (could change interpretation) or high (plausibly misleading as stated).
At most 10 attacks. If the claim is crisp and complete, return {"attacks": []}."""

REDTEAM_SCHEMA = {
    "name": "claim_attacks",
    "schema": {
        "type": "object",
        "properties": {
            "attacks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_span": {"type": "string"},
                        "weakness": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                    "required": ["claim_span", "weakness", "severity"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["attacks"],
        "additionalProperties": False,
    },
}

EXPLICADOR_SYSTEM = """You explain a fact-check in plain language for a general reader.
Return ONLY a JSON object: {"summary": "...", "caveats": ["..."]}
Rules: "summary" is at most 4 short sentences, no jargon, no new facts or
numbers beyond what the input gives. "caveats" lists what is still unknown
or uncertain (empty list when nothing). Never use strength words: prove,
proves, proven, confirm, confirms, definitively, categorically, "exactly
right". Never invent sources, ids or figures."""

EXPLICADOR_SCHEMA = {
    "name": "plain_explanation",
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "caveats": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "caveats"],
        "additionalProperties": False,
    },
}

ROLE_SYSTEM = {
    "recuperador": RECUPERADOR_SYSTEM,
    "red_team": REDTEAM_SYSTEM,
    "explicador": EXPLICADOR_SYSTEM,
}
ROLE_SCHEMA = {
    "recuperador": RECUPERADOR_SCHEMA,
    "red_team": REDTEAM_SCHEMA,
    "explicador": EXPLICADOR_SCHEMA,
}

_MAX_ITEMS = 10
_MAX_CAVEATS = 8
_MAX_SUMMARY_CHARS = 1200
_MAX_RATIONALE_CHARS = 300
_EXC_TEXT_LIMIT = 200

_STRENGTH_RES = tuple(
    re.compile(
        r"\b" + r"[\s-]+".join(map(re.escape, w.split())) + r"\b",
        re.IGNORECASE,
    )
    for w in FORBIDDEN_STRENGTH
)


def _blank(role: str) -> dict:
    return {
        "role": role, "ok": False, "skipped": None,
        "data": {}, "notes": [], "usage": None,
    }


def _take_usage(llm) -> dict | None:
    usage = getattr(llm, "last_usage", None)
    return dict(usage) if isinstance(usage, dict) else None


def _unavailable(role: str, llm) -> dict | None:
    """Blank skipped outcome when no usable model is attached."""
    if llm is None or not getattr(llm, "available", False):
        out = _blank(role)
        out["skipped"] = "no model available; role skipped (pipeline still runs)"
        return out
    return None


def _failed(role: str, llm, exc: Exception) -> dict:
    out = _blank(role)
    out["notes"] = [f"role call failed, degraded: {str(exc)[:_EXC_TEXT_LIMIT]}"]
    out["usage"] = _take_usage(llm)
    log.warning("Swarm role %s failed, degraded: %r", role, exc)
    return out


def _role_user(role: str, claim_text: str, context: str = "") -> str:
    # [ROLE:...] marker lets scripted doubles key canned responses per role.
    user = f"[ROLE:{role}]\nClaim: {claim_text.strip()}"
    if context.strip():
        user += f"\nContext: {context.strip()[:2000]}"
    return user


def run_estructurador(claim_text: str, llm, context: str = "") -> dict:
    """Structure the claim via the pipeline's own structure_claim.

    Uses STRUCTURE_SYSTEM/STRUCTURE_SCHEMA through the shared code path,
    so the role's output is a validated StructuredClaim, never raw JSON.
    """
    out = _blank("estructurador")
    skip = _unavailable("estructurador", llm)
    if skip is not None:
        return skip
    try:
        claim: StructuredClaim = structure_claim(claim_text, llm)
    except Exception as exc:  # structure_claim already falls back internally
        return _failed("estructurador", llm, exc)
    out["ok"] = True
    out["usage"] = _take_usage(llm)
    out["data"] = {
        "claim_id": claim.claim_id,
        "domain": claim.domain,
        "claim_type": claim.claim_type,
        "subject": claim.subject,
        "indicator": claim.indicator,
        "quantities": [
            {"label": q.label, "raw_text": q.raw_text, "value": q.value,
             "unit": q.unit}
            for q in claim.quantities
        ],
        "time_refs": [t.model_dump() for t in claim.time_refs],
        "geo_refs": [g.model_dump() for g in claim.geo_refs],
        "conditions": list(claim.conditions),
        "extraction_actor": claim.extraction_actor,
        "extraction_model": claim.extraction_model,
    }
    if context.strip():
        out["notes"] = ["context carried for audit only; structuring used claim text"]
    return out


def _validate_candidates(raw: object) -> tuple[list[dict], int, list[str]]:
    items = raw if isinstance(raw, list) else []
    kept: list[dict] = []
    seen: set[tuple[str, str]] = set()
    dropped = 0
    for c in items:
        if not isinstance(c, dict):
            dropped += 1
            continue
        source = c.get("source")
        indicator = c.get("indicator")
        rationale = c.get("rationale")
        if (not isinstance(source, str) or not source.strip()
                or not isinstance(indicator, str) or not indicator.strip()):
            dropped += 1
            continue
        key = (source.strip().lower(), indicator.strip().lower())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append({
            "source": source.strip()[:120],
            "indicator": indicator.strip()[:200],
            "rationale": (rationale.strip()[:_MAX_RATIONALE_CHARS]
                          if isinstance(rationale, str) else ""),
        })
    notes: list[str] = []
    if len(kept) > _MAX_ITEMS:
        notes.append(f"truncated candidates from {len(kept)} to {_MAX_ITEMS}")
        kept = kept[:_MAX_ITEMS]
    if dropped:
        notes.append(f"dropped {dropped} empty/duplicate/non-dict candidate(s)")
    return kept, dropped, notes


def run_recuperador(claim_text: str, llm, context: str = "") -> dict:
    """Propose candidate {source, indicator} pairs (suggestions only)."""
    out = _blank("recuperador")
    skip = _unavailable("recuperador", llm)
    if skip is not None:
        return skip
    try:
        data = llm.complete_json(
            RECUPERADOR_SYSTEM, _role_user("recuperador", claim_text, context),
            max_tokens=1500, reasoning_effort="low",
            json_schema=RECUPERADOR_SCHEMA,
        )
    except Exception as exc:
        return _failed("recuperador", llm, exc)
    kept, _, notes = _validate_candidates(
        data.get("candidates") if isinstance(data, dict) else None)
    out["ok"] = True
    out["usage"] = _take_usage(llm)
    out["data"] = {"candidates": kept}
    out["notes"] = notes
    return out


def _validate_attacks(raw: object, claim_text: str) -> tuple[list[dict], list[str]]:
    items = raw if isinstance(raw, list) else []
    kept: list[dict] = []
    dropped = 0
    for a in items:
        if not isinstance(a, dict):
            dropped += 1
            continue
        span = a.get("claim_span")
        weakness = a.get("weakness")
        severity = a.get("severity")
        if (not isinstance(span, str) or not span.strip()
                or span.strip() not in claim_text):
            dropped += 1  # non-verbatim spans are invention: dropped
            continue
        if not isinstance(weakness, str) or not weakness.strip():
            dropped += 1
            continue
        if severity not in ("low", "medium", "high"):
            dropped += 1
            continue
        kept.append({"claim_span": span.strip()[:300],
                     "weakness": weakness.strip()[:500],
                     "severity": severity})
    notes: list[str] = []
    if len(kept) > _MAX_ITEMS:
        notes.append(f"truncated attacks from {len(kept)} to {_MAX_ITEMS}")
        kept = kept[:_MAX_ITEMS]
    if dropped:
        notes.append(
            f"dropped {dropped} non-verbatim/empty/invalid attack(s)")
    return kept, notes


def run_red_team(claim_text: str, llm, context: str = "") -> dict:
    """Advisory attacks on the claim. Never a verdict, never a flip."""
    out = _blank("red_team")
    skip = _unavailable("red_team", llm)
    if skip is not None:
        return skip
    try:
        data = llm.complete_json(
            REDTEAM_SYSTEM, _role_user("red_team", claim_text, context),
            max_tokens=1500, reasoning_effort="low",
            json_schema=REDTEAM_SCHEMA,
        )
    except Exception as exc:
        return _failed("red_team", llm, exc)
    kept, notes = _validate_attacks(
        data.get("attacks") if isinstance(data, dict) else None, claim_text)
    out["ok"] = True
    out["usage"] = _take_usage(llm)
    out["data"] = {"attacks": kept}
    out["notes"] = notes
    return out


def _strength_hits(text: str) -> list[str]:
    return [w for w, rx in zip(FORBIDDEN_STRENGTH, _STRENGTH_RES)
            if rx.search(text)]


def _validate_explanation(raw: object) -> tuple[dict, list[str]]:
    data = raw if isinstance(raw, dict) else {}
    summary = data.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    notes: list[str] = []
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS]
        notes.append(f"truncated summary to {_MAX_SUMMARY_CHARS} chars")
    hits = _strength_hits(summary)
    if hits:
        notes.append(
            "strength words flagged (advisory text kept): " + ",".join(hits))
    caveats_raw = data.get("caveats")
    caveats = [c.strip() for c in caveats_raw
               if isinstance(c, str) and c.strip()] \
        if isinstance(caveats_raw, list) else []
    if len(caveats) > _MAX_CAVEATS:
        notes.append(f"truncated caveats from {len(caveats)} to {_MAX_CAVEATS}")
        caveats = caveats[:_MAX_CAVEATS]
    return {"summary": summary, "caveats": caveats}, notes


def run_explicador(claim_text: str, llm, context: str = "") -> dict:
    """Plain-language explanation. Never replaces the pipeline conclusion."""
    out = _blank("explicador")
    skip = _unavailable("explicador", llm)
    if skip is not None:
        return skip
    try:
        data = llm.complete_json(
            EXPLICADOR_SYSTEM, _role_user("explicador", claim_text, context),
            max_tokens=1500, reasoning_effort="low",
            json_schema=EXPLICADOR_SCHEMA,
        )
    except Exception as exc:
        return _failed("explicador", llm, exc)
    payload, notes = _validate_explanation(data)
    if not payload["summary"]:
        out["notes"] = ["empty summary dropped; role degraded"]
        out["usage"] = _take_usage(llm)
        return out
    out["ok"] = True
    out["usage"] = _take_usage(llm)
    out["data"] = payload
    out["notes"] = notes
    return out


ROLE_RUNNERS = {
    "estructurador": run_estructurador,
    "recuperador": run_recuperador,
    "red_team": run_red_team,
    "explicador": run_explicador,
}
