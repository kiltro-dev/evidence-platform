"""Conclusion synthesis: deterministic stance rules + optional LLM summary."""

from __future__ import annotations

import logging
import re

from ..core.compare import is_year_quantity
from ..core.types import Comparison, Conclusion, StructuredClaim, Uncertainty

log = logging.getLogger(__name__)

_CMP_REF_RE = re.compile(r"\[cmp_[A-Za-z0-9_]+\]")

# Team-E F5b: strength words a model summary must never use. A measured
# difference is never proof; the summary reports the stance, it does not
# upgrade it.
FORBIDDEN_STRENGTH = (
    "prove",
    "proves",
    "proven",
    "confirm",
    "confirms",
    "definitively",
    "categorically",
    "exactly right",
)
_FORBIDDEN_STRENGTH_RES = tuple(
    re.compile(
        r"\b" + r"[\s-]+".join(map(re.escape, word.split())) + r"\b",
        re.IGNORECASE,
    )
    for word in FORBIDDEN_STRENGTH
)
_DELTA_PCT_RE = re.compile(r"\d\s*%")

SUMMARY_SYSTEM = """You write evidence-analysis summaries. Return ONLY a JSON object:
{"summary": "max 4 sentences", "caveats": ["short strings"]}
Rules: use ONLY the comparisons and uncertainties provided.
Quote the stance token verbatim (e.g. "partially_supported").
State the measured delta percent (rel_diff_pct) for every numeric verdict.
Cite comparison ids like [cmp_xxx] inside the summary. Never introduce new
facts, numbers, sources or verdicts. Never mention excerpts, documents or
materials not listed in the input. State plainly when evidence is missing.
Never use strength words that overclaim: prove, proves, proven, confirm,
confirms, definitively, categorically, "exactly right"."""


# Checks that corroborate context but can never verify a claim on their own.
# abstract_stance (science abstracts, Operation First Place M3): a model
# judgment over abstracts corroborates context but never verifies.
# falsification_review: advisory-only attacks are inconclusive by
# construction, but belt-and-braces membership keeps them out of every
# verifying set even if a future verdict slips through (delta review).
NON_VERIFYING_METRICS = frozenset(
    {
        "time_compatibility",
        "publication_year_match",
        "abstract_stance",
        "falsification_review",
    }
)


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
        # Team-E F3: claimed precision exceeded caps supported at
        # partially_supported (the code is verify's stance-cap signal).
        if any(u.code == "claimed_precision_exceeded" for u in uncertainties):
            return "partially_supported"
        # Team-E F5b compound cap: a compound claim with fewer verifying
        # comparisons than real (non-year) quantities analyzed only part
        # of itself; supported is capped at partially_supported.
        if claim is not None and any(
            u.code == "compound_claim" for u in uncertainties
        ):
            n_real = sum(
                1 for q in claim.quantities if not is_year_quantity(q)
            )
            if len(verifying) < n_real:
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


def validate_summary_strength(
    summary: str, stance: str, comparisons: list[Comparison]
) -> list[str]:
    """Team-E F5b: violations that disqualify a model summary (empty = ok).

    1. No forbidden strength-lexicon word (whole-word, case-insensitive).
    2. The stance token must appear verbatim.
    3. When numeric verdicts exist (rel_diff_pct recorded), the summary
       must state a measured delta percent ("<digits>%").
    """
    violations: list[str] = []
    for word, pat in zip(FORBIDDEN_STRENGTH, _FORBIDDEN_STRENGTH_RES):
        if pat.search(summary):
            violations.append(f"forbidden strength word: {word!r}")
    if stance not in summary:
        violations.append(f"stance token {stance!r} not quoted verbatim")
    has_numeric = any(
        c.rel_diff_pct is not None and c.verdict in ("consistent", "contradicts")
        for c in comparisons
    )
    if has_numeric and not _DELTA_PCT_RE.search(summary):
        violations.append("measured delta percent missing for numeric verdicts")
    return violations


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
                     "verdict": c.verdict, "notes": c.notes,
                     "rel_diff_pct": (
                         round(c.rel_diff_pct, 2)
                         if c.rel_diff_pct is not None else None
                     )}
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
            model_ok = False
            if isinstance(data.get("summary"), str) and data["summary"].strip():
                cleaned = validate_citations(
                    data["summary"].strip(), comparisons
                )
                violations = validate_summary_strength(
                    cleaned, stance, comparisons
                )
                if violations:
                    # Team-E F5b: discard the model summary, keep the
                    # deterministic template (actor stays "code"); the
                    # warning is the audit trail of the discard.
                    log.warning(
                        "LLM summary failed strength validation (%s), "
                        "using template",
                        "; ".join(violations),
                    )
                else:
                    summary = cleaned
                    actor = "model"
                    model = getattr(llm, "name", "model")
                    model_ok = True
            else:
                log.warning("LLM summary missing 'summary' key, using template")
            if model_ok and isinstance(data.get("caveats"), list):
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
