"""Conclusion synthesis: deterministic stance rules + optional LLM summary."""

from __future__ import annotations

import logging
import re

from ..core.types import Comparison, Conclusion, StructuredClaim, Uncertainty

log = logging.getLogger(__name__)

_CMP_REF_RE = re.compile(r"\[cmp_[A-Za-z0-9_]+\]")

SUMMARY_SYSTEM = """You write evidence-analysis summaries. Return ONLY a JSON object:
{"summary": "max 4 sentences", "caveats": ["short strings"]}
Rules: use ONLY the comparisons and uncertainties provided.
Cite comparison ids like [cmp_xxx] inside the summary. Never introduce new
facts, numbers, sources or verdicts. Never mention excerpts, documents or
materials not listed in the input. State plainly when evidence is missing."""


# Checks that corroborate context but can never verify a claim on their own.
NON_VERIFYING_METRICS = frozenset({"time_compatibility", "publication_year_match"})


def decide_stance(
    comparisons: list[Comparison],
    uncertainties: list[Uncertainty],
    claim: StructuredClaim | None = None,
) -> str:
    # not_assessable rule (PRD §4; A2a honest-stances, lead fix 2026-11-01).
    # Checked FIRST; when the input is not a verifiable claim there is no
    # verdict to give:
    #   (a) an `opinion_or_nonclaim` uncertainty is present (opinion,
    #       recommendation or bare question per verify.detect_claim_shape), OR
    #   (b) the raw input is empty/whitespace: literally nothing to assess.
    # A factual claim with zero parsed refs is NOT not_assessable: retrieval
    # also searches the subject text, so it must flow through and land
    # `unverifiable` when nothing usable comes back. (An earlier revision
    # used "no quantities/times/geos + type other" here; full-mode eval
    # showed it misclassified bare factual claims such as moon-cheese.)
    # `claim` is optional so older callers keep working; with claim=None
    # only branch (a) can fire.
    if any(u.code == "opinion_or_nonclaim" for u in uncertainties):
        return "not_assessable"
    if claim is not None and not (claim.raw_text or "").strip():
        return "not_assessable"
    verifying = [c for c in comparisons if c.metric not in NON_VERIFYING_METRICS]
    if any(c.verdict == "contradicts" for c in verifying):
        return "contradicted"
    if any(c.verdict == "consistent" for c in verifying):
        if any(u.severity == "blocking" for u in uncertainties):
            return "partially_supported"
        return "supported"
    if any(c.verdict == "consistent" for c in comparisons):
        return "partially_supported"
    return "unverifiable"


def template_summary(
    stance: str,
    comparisons: list[Comparison],
    uncertainties: list[Uncertainty],
) -> tuple[str, list[str]]:
    n_cons = sum(1 for c in comparisons if c.verdict == "consistent")
    n_con = sum(1 for c in comparisons if c.verdict == "contradicts")
    n_nc = sum(1 for c in comparisons if c.verdict == "not_comparable")
    parts = [
        f"Stance: {stance} ({n_cons} consistent, {n_con} contradicting, "
        f"{n_nc} not comparable out of {len(comparisons)} checks)."
    ]
    for c in comparisons:
        if c.verdict in ("consistent", "contradicts"):
            parts.append(f"[{c.comparison_id}] {c.metric}: {c.notes}")
    caveats = [u.description for u in uncertainties]
    return " ".join(parts), caveats


def required_caveats(uncertainties: list[Uncertainty]) -> list[str]:
    """Template caveats that must ALWAYS be retained (caveat union, PRD §4:
    caveats are code∪model): the descriptions of blocking/major
    uncertainties. Minor flags stay on their comparisons and yield to the
    model's own caveats in the LLM path."""
    return [
        u.description for u in uncertainties if u.severity in ("blocking", "major")
    ]


def validate_citations(summary: str, comparisons: list[Comparison]) -> str:
    """Strip [cmp_*] ids in a model summary that match no real comparison.

    Invented citations are removed (never replaced with guesses) and nothing
    is appended; stripping is logged so it stays auditable.
    """
    valid = {c.comparison_id for c in comparisons}
    stripped: list[str] = []

    def _keep(match: re.Match) -> str:
        token = match.group(0)
        if token[1:-1] in valid:
            return token
        stripped.append(token)
        return ""

    cleaned = _CMP_REF_RE.sub(_keep, summary)
    if stripped:
        log.warning(
            "Stripped %d unknown comparison citation(s) from model summary: %s",
            len(stripped),
            stripped,
        )
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned


def synthesize(
    claim: StructuredClaim,
    comparisons: list[Comparison],
    uncertainties: list[Uncertainty],
    llm=None,
) -> Conclusion:
    stance = decide_stance(comparisons, uncertainties, claim)
    support = [c.comparison_id for c in comparisons if c.verdict == "consistent"]
    contra = [c.comparison_id for c in comparisons if c.verdict == "contradicts"]
    actor: str = "code"
    model: str | None = None
    summary, caveats = template_summary(stance, comparisons, uncertainties)
    if llm is not None and llm.available:
        try:
            # Slim payload: muse-spark spends its token budget on hidden
            # reasoning, so send only what the summary needs.
            payload = {
                "claim": claim.raw_text,
                "stance": stance,
                "comparisons": [
                    {"id": c.comparison_id, "metric": c.metric,
                     "verdict": c.verdict, "notes": c.notes}
                    for c in comparisons
                ],
                "uncertainties": [
                    {"code": u.code, "description": u.description,
                     "severity": u.severity}
                    for u in uncertainties
                ],
            }
            import json as _json

            data = llm.complete_json(
                SUMMARY_SYSTEM, _json.dumps(payload)[:3000], max_tokens=4000
            )
            if isinstance(data.get("summary"), str) and data["summary"].strip():
                summary = validate_citations(data["summary"].strip(), comparisons)
                actor = "model"
                model = getattr(llm, "name", "model")
            else:
                log.warning("LLM summary missing 'summary' key, using template")
            if isinstance(data.get("caveats"), list):
                # Caveat union: required (blocking/major) template caveats
                # are ALWAYS retained; model caveats are appended, deduped.
                caveats = required_caveats(uncertainties)
                for c in data["caveats"]:
                    if str(c) not in caveats:
                        caveats.append(str(c))
        except Exception as exc:
            # keep deterministic summary; audit + log note the fallback
            log.warning("LLM summary failed, using template: %r", exc)
    return Conclusion(
        stance=stance,  # type: ignore[arg-type]
        summary=summary,
        support_comparison_ids=support,
        contradict_comparison_ids=contra,
        caveats=caveats,
        actor=actor,  # type: ignore[arg-type]
        model=model,
    )
