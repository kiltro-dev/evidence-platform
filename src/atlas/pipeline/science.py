"""Model-assisted stance assessment over scholarly abstracts.

Additive science path (Operation First Place, agent M3): judges how the
top-3 retrieved abstracts relate to the claim with ONE bounded model call.
Output is NON-VERIFYING by design — abstract stance corroborates context
but never verifies a claim.

WIRING (for lead, in run.py + synthesize.py; NOT done here):
  1. synthesize.NON_VERIFYING_METRICS must gain SCIENCE_METRIC
     ("abstract_stance"). Additive: no existing comparison uses that
     metric, so existing eval expectations are untouched.
  2. After the `assess_science(claim, evidences)` call in run.py, call
     `assess_science_abstracts(claim, evidences, llm)` and extend
     comparisons/uncertainties with its output.
Once wired, the science ceiling (partially_supported) is preserved: a
consistent abstract_stance comparison can only lift the stance to
partially_supported, never to supported or contradicted.
"""

from __future__ import annotations

import json
import logging

from ..core.types import Comparison, Evidence, StructuredClaim, Uncertainty
from .synthesize import NON_VERIFYING_METRICS

log = logging.getLogger(__name__)

# Metric for abstract-stance comparisons. Must join NON_VERIFYING_METRICS
# (lead wiring step); see module docstring.
SCIENCE_METRIC = "abstract_stance"

# Bounds: one call, at most 3 abstracts, slim prompt.
MAX_ABSTRACT_ITEMS = 3
ABSTRACT_PROMPT_CHARS = 600
CLAIM_PROMPT_CHARS = 500
ARGUMENT_MAX_CHARS = 500
MODEL_MAX_TOKENS = 1500

RELEVANCE_VALUES = frozenset({"relevant", "off_topic"})
DIRECTION_VALUES = frozenset({"supports", "contradicts", "unclear"})
_DIRECTION_TO_VERDICT = {
    "supports": "consistent",
    "contradicts": "contradicts",
    "unclear": "inconclusive",
}

SCIENCE_SYSTEM = """You judge scholarly abstracts against a claim. Return ONLY a JSON object:
{"assessments": [{"evidence_id": "...", "relevance": "relevant|off_topic", "direction": "supports|contradicts|unclear", "argument": "one sentence"}]}
Rules: one entry per input abstract, same evidence_id. relevance is off_topic when the abstract is about another subject. direction judges stance only for relevant abstracts. Base everything ONLY on the given abstracts."""


def _candidates(evidences: list[Evidence]) -> list[Evidence]:
    """First MAX_ABSTRACT_ITEMS evidence items carrying a non-blank abstract."""
    out = []
    for ev in evidences:
        abstract = ev.payload.get("abstract")
        if isinstance(abstract, str) and abstract.strip():
            out.append(ev)
        if len(out) >= MAX_ABSTRACT_ITEMS:
            break
    return out


def _prompt_user(claim: StructuredClaim, items: list[Evidence]) -> str:
    return json.dumps(
        {
            "claim": claim.raw_text[:CLAIM_PROMPT_CHARS],
            "abstracts": [
                {
                    "evidence_id": ev.evidence_id,
                    "title": str(ev.payload.get("title") or "")[:200],
                    "abstract": ev.payload["abstract"].strip()[:ABSTRACT_PROMPT_CHARS],
                }
                for ev in items
            ],
        }
    )


def assess_science_abstracts(
    claim: StructuredClaim, evidences: list[Evidence], llm=None
) -> tuple[list[Comparison], list[Uncertainty]]:
    """Assess top-3 abstracts with one bounded model call.

    Returns (comparisons, uncertainties). No-op ([], []) when llm is None,
    unavailable, or no evidence carries an abstract. Relevant items yield
    NON-VERIFYING `abstract_stance` comparisons; off-topic and dropped ids
    yield minor uncertainties. Invalid JSON / unknown ids / bad enums are
    dropped and logged, never raised.
    """
    comparisons: list[Comparison] = []
    uncertainties: list[Uncertainty] = []
    if llm is None or not getattr(llm, "available", True):
        return comparisons, uncertainties
    items = _candidates(evidences)
    if not items:
        return comparisons, uncertainties
    if SCIENCE_METRIC not in NON_VERIFYING_METRICS:
        log.warning(
            "SCIENCE_METRIC %r not in NON_VERIFYING_METRICS; "
            "lead wiring step pending, science ceiling not enforced.",
            SCIENCE_METRIC,
        )
    wanted = {ev.evidence_id for ev in items}
    by_id = {ev.evidence_id: ev for ev in items}
    try:
        # reasoning_effort="low": same truncation risk as falsify.py at
        # default depth on these small judgment calls (live 2026-11-01).
        data = llm.complete_json(
            SCIENCE_SYSTEM, _prompt_user(claim, items),
            max_tokens=MODEL_MAX_TOKENS,
            reasoning_effort="low",
        )
    except Exception as exc:
        log.warning("Abstract stance assessment failed, dropping: %r", exc)
        data = None
    entries: list = []
    if isinstance(data, dict) and isinstance(data.get("assessments"), list):
        entries = data["assessments"]
    else:
        log.warning(
            "Abstract stance assessment returned invalid JSON shape; "
            "dropping %d item(s).",
            len(items),
        )
    seen: set[str] = set()
    model_name = getattr(llm, "name", "model")
    for entry in entries:
        if not isinstance(entry, dict):
            log.warning("Dropping non-dict abstract assessment: %r", entry)
            continue
        eid = entry.get("evidence_id")
        if eid not in wanted:
            log.warning("Dropping abstract assessment for unknown id: %r", eid)
            continue
        if eid in seen:
            log.warning("Dropping duplicate abstract assessment for %r", eid)
            continue
        relevance = entry.get("relevance")
        direction = entry.get("direction")
        if relevance not in RELEVANCE_VALUES or direction not in DIRECTION_VALUES:
            log.warning(
                "Dropping abstract assessment for %r: bad relevance/direction "
                "(%r/%r).",
                eid, relevance, direction,
            )
            continue
        seen.add(eid)
        argument = entry.get("argument")
        argument = str(argument).strip() if argument else ""
        argument = argument[:ARGUMENT_MAX_CHARS]
        if relevance == "off_topic":
            uncertainties.append(
                Uncertainty(
                    code="abstract_off_topic",
                    description=(
                        f"Abstract for {by_id[eid].locator or eid} is off-topic "
                        f"for this claim; no stance was assessed."
                        + (f" Model note: {argument}" if argument else "")
                    ),
                    severity="minor",
                    related_ids=[eid],
                )
            )
            continue
        ev = by_id[eid]
        comparisons.append(
            Comparison(
                claim_id=claim.claim_id,
                evidence_id=eid,
                actor="model",
                model=model_name,
                metric=SCIENCE_METRIC,
                claim_value=claim.raw_text[:CLAIM_PROMPT_CHARS],
                evidence_value=(str(ev.payload.get("title") or ev.excerpt))[:200],
                tolerance_rule=(
                    "model abstract-stance judgment; contextual only, "
                    "never verifying"
                ),
                within_tolerance=None,
                verdict=_DIRECTION_TO_VERDICT[direction],  # type: ignore[arg-type]
                notes=(
                    f"Abstract {direction} the claim."
                    + (f" {argument}" if argument else "")
                ),
            )
        )
    for ev in items:
        if ev.evidence_id not in seen:
            uncertainties.append(
                Uncertainty(
                    code="abstract_assessment_dropped",
                    description=(
                        f"Abstract stance for {ev.locator or ev.evidence_id} "
                        "could not be assessed (missing or invalid model "
                        "output); dropped."
                    ),
                    severity="minor",
                    related_ids=[ev.evidence_id],
                )
            )
    return comparisons, uncertainties
