"""H4 truth-table sweep for decide_stance (verdict x severity x cap-code).

Every cell below pins DOCUMENTED behavior from decide_stance's own rule
comments with a literal expected stance — no re-implementation of the
rules, so a rule change fails loudly here instead of agreeing with
itself. Rule order under test:

  1. opinion_or_nonclaim (or empty raw_text) -> not_assessable, first.
  2. any verifying contradicts -> contradicted (dominates caps/blocking).
  3. any verifying consistent -> supported, capped to partially_supported
     by (a) any blocking severity, (b) claimed_precision_exceeded, or
     (c) the compound cap (compound_claim + fewer verifying comparisons
     than real quantities).
  4. ceiling-allowlisted non-verifying consistent only ->
     partially_supported (blocking and caps do NOT apply on this
     path). Falsify metrics (falsification_review, web_counter_evidence)
     NEVER lift, even if consistent (rule 4b).
  5. otherwise -> unverifiable.
"""

import pytest

from atlas.core.types import Comparison, Quantity, StructuredClaim, Uncertainty
from atlas.pipeline.synthesize import NON_VERIFYING_METRICS, decide_stance

VERIFYING = "GDP (current US$)"
NON_VERIFYING = sorted(NON_VERIFYING_METRICS)[0]  # stable representative


def _cmp(verdict, metric=VERIFYING, cid="cmp_t"):
    return Comparison(claim_id="clm_t", metric=metric, verdict=verdict,
                      comparison_id=cid)


def _unc(code="some_flag", severity="major"):
    return Uncertainty(code=code, description=f"{code} flag",
                       severity=severity, related_ids=["clm_t"])


def _claim(n_real=1, text="GDP was $5 in 2020.", n_year=0):
    quants = [
        Quantity(raw_text=f"${i} billion", value=float(i) * 1e9,
                 unit="USD", normalized_unit="USD")
        for i in range(1, n_real + 1)
    ]
    quants += [
        Quantity(raw_text="2020", value=2020.0) for _ in range(n_year)
    ]
    return StructuredClaim(raw_text=text, quantities=quants)


def _case(name, comparisons, uncertainties, expected, n_real=1, text=None,
          claim_none=False, n_year=0):
    claim = None if claim_none else _claim(
        n_real, "GDP was $5 in 2020." if text is None else text, n_year)
    return pytest.param(comparisons, uncertainties, claim, expected, id=name)


def _compound(n_real, n_verifying, verdict="consistent"):
    comps = [_cmp(verdict, cid=f"cmp_{i}") for i in range(n_verifying)]
    uncs = [_unc("compound_claim")]
    return comps, uncs


C = _cmp
CELLS = [
    # --- baseline: verdict combinations, no uncertainties ---
    _case("no-comparisons", [], [], "unverifiable"),
    _case("verifying-consistent", [C("consistent")], [], "supported"),
    _case("verifying-contradicts", [C("contradicts")], [], "contradicted"),
    _case("consistent-plus-contradicts", [C("consistent"), C("contradicts")],
          [], "contradicted"),
    _case("not-comparable-only", [C("not_comparable")], [], "unverifiable"),
    _case("inconclusive-only", [C("inconclusive")], [], "unverifiable"),
    _case("nonverifying-consistent-only", [C("consistent", NON_VERIFYING)],
          [], "partially_supported"),
    _case("nonverifying-consistent-plus-nc",
          [C("consistent", NON_VERIFYING), C("not_comparable")],
          [], "partially_supported"),
    _case("verifying-plus-nonverifying-consistent",
          [C("consistent"), C("consistent", NON_VERIFYING)],
          [], "supported"),
    _case("two-verifying-consistent",
          [C("consistent", cid="cmp_a"), C("consistent", cid="cmp_b")],
          [], "supported", n_real=2),
    # --- rule 1: opinion / empty input win over everything ---
    _case("opinion-no-comparisons", [], [_unc("opinion_or_nonclaim",
          "blocking")], "not_assessable"),
    _case("opinion-beats-consistent", [C("consistent")],
          [_unc("opinion_or_nonclaim", "blocking")], "not_assessable"),
    _case("opinion-beats-contradicts", [C("contradicts")],
          [_unc("opinion_or_nonclaim", "blocking")], "not_assessable"),
    _case("opinion-beats-mixed", [C("consistent"), C("contradicts")],
          [_unc("opinion_or_nonclaim", "blocking")], "not_assessable"),
    _case("opinion-beats-nonverifying",
          [C("consistent", NON_VERIFYING)],
          [_unc("opinion_or_nonclaim", "blocking")], "not_assessable"),
    _case("opinion-works-claim-none", [C("consistent")],
          [_unc("opinion_or_nonclaim", "blocking")], "not_assessable",
          claim_none=True),
    _case("empty-text-no-comparisons", [], [], "not_assessable", text="   "),
    _case("empty-text-beats-consistent", [C("consistent")], [],
          "not_assessable", text=""),
    _case("empty-text-beats-contradicts", [C("contradicts")], [],
          "not_assessable", text="  \n "),
    # --- rule 2: contradiction dominates blocking and caps ---
    _case("contradicts-beats-blocking", [C("contradicts")],
          [_unc("no_direct_evidence", "blocking")], "contradicted"),
    _case("contradicts-beats-precision-cap", [C("contradicts")],
          [_unc("claimed_precision_exceeded")], "contradicted"),
    _case("mixed-beats-blocking-plus-caps",
          [C("consistent"), C("contradicts")],
          [_unc("no_direct_evidence", "blocking"),
           _unc("claimed_precision_exceeded"), _unc("compound_claim")],
          "contradicted", n_real=2),
    # --- rule 3a: blocking caps supported ---
    _case("blocking-caps-consistent", [C("consistent")],
          [_unc("no_direct_evidence", "blocking")], "partially_supported"),
    _case("blocking-plus-nc-stays-unverifiable", [C("not_comparable")],
          [_unc("no_direct_evidence", "blocking")], "unverifiable"),
    _case("blocking-no-comparisons-stays-unverifiable", [],
          [_unc("no_direct_evidence", "blocking")], "unverifiable"),
    _case("minor-does-not-cap", [C("consistent")],
          [_unc("source_revision_risk", "minor")], "supported"),
    # --- rule 3b: precision cap ---
    _case("precision-caps-consistent", [C("consistent")],
          [_unc("claimed_precision_exceeded")], "partially_supported"),
    _case("precision-caps-two-consistent",
          [C("consistent", cid="cmp_a"), C("consistent", cid="cmp_b")],
          [_unc("claimed_precision_exceeded")], "partially_supported",
          n_real=2),
    _case("precision-irrelevant-off-nonverifying",
          [C("consistent", NON_VERIFYING)],
          [_unc("claimed_precision_exceeded")], "partially_supported"),
    # --- rule 3c: compound cap (coverage-dependent) ---
    _case("compound-full-coverage-stays-supported",
          *_compound(1, 1)[:2], "supported"),
    _case("compound-full-coverage-two-stays-supported",
          *_compound(2, 2)[:2], "supported", n_real=2),
    _case("compound-under-coverage-caps",
          *_compound(2, 1)[:2], "partially_supported", n_real=2),
    _case("compound-under-coverage-year-quantities-ignored",
          *_compound(1, 1)[:2], "supported", n_real=1, n_year=2),
    _case("compound-no-consistent-stays-unverifiable",
          [C("not_comparable")], [_unc("compound_claim")], "unverifiable",
          n_real=2),
    # --- rule 3 interactions: blocking x precision x compound ---
    _case("blocking-plus-precision-caps", [C("consistent")],
          [_unc("no_direct_evidence", "blocking"),
           _unc("claimed_precision_exceeded")], "partially_supported"),
    _case("blocking-plus-compound-under-caps", *_compound(2, 1)[:1],
          [_unc("no_direct_evidence", "blocking"), _unc("compound_claim")],
          "partially_supported", n_real=2),
    _case("precision-plus-compound-under-caps", *_compound(2, 1)[:1],
          [_unc("claimed_precision_exceeded"), _unc("compound_claim")],
          "partially_supported", n_real=2),
    _case("precision-fires-despite-full-compound-coverage",
          *_compound(1, 1)[:1],
          [_unc("claimed_precision_exceeded"), _unc("compound_claim")],
          "partially_supported"),
    _case("compound-without-flag-code-never-caps",
          [C("consistent")], [_unc("some_other_major")],
          "supported", n_real=2),
    # --- rule 4: non-verifying path ignores blocking and caps ---
    _case("blocking-ignored-on-nonverifying-path",
          [C("consistent", NON_VERIFYING)],
          [_unc("no_direct_evidence", "blocking")], "partially_supported"),
    _case("verifying-nc-plus-nonverifying-plus-blocking",
          [C("not_comparable"), C("consistent", NON_VERIFYING)],
          [_unc("no_direct_evidence", "blocking")], "partially_supported"),
    # --- rule 4b: ceiling allowlist (falsify metrics never lift) ---
    # Advisory-only structural guarantee: even a hypothetical
    # `consistent` on a falsify metric must not move the stance.
    _case("web-consistent-never-lifts",
          [C("consistent", "web_counter_evidence")], [], "unverifiable"),
    _case("falsify-consistent-never-lifts",
          [C("consistent", "falsification_review")], [], "unverifiable"),
    _case("ceiling-abstract-still-lifts",
          [C("consistent", "abstract_stance")], [], "partially_supported"),
    _case("ceiling-time-still-lifts",
          [C("consistent", "time_compatibility")], [], "partially_supported"),
    # --- claim=None legacy path ---
    _case("claim-none-consistent", [C("consistent")], [], "supported",
          claim_none=True),
    _case("claim-none-compound-flag-without-claim-no-cap",
          [C("consistent")], [_unc("compound_claim")], "supported",
          claim_none=True),
]


@pytest.mark.parametrize("comparisons,uncertainties,claim,expected", CELLS)
def test_decide_stance_truth_table(comparisons, uncertainties, claim,
                                   expected):
    assert decide_stance(comparisons, uncertainties, claim) == expected
