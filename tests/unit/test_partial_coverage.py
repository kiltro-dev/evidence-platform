"""H5-minimal pins: partial_coverage on half-checked conjunctions.

synthesize() must disclose (major partial_coverage uncertainty, surfaced
in conclusion caveats) when a DECISIVE stance rests on only part of a
multi-part claim — and must stay silent when nothing ships silently
(abstentions) or the claim is whole. Stance rules are untouched: every
case also pins the unchanged stance.
"""

from atlas.core.types import Comparison, Quantity, StructuredClaim, Uncertainty
from atlas.pipeline.synthesize import PARTIAL_COVERAGE_CODE, synthesize

UNEMPLOYMENT_TEXT = (
    "Unemployment in Germany was 3.1% in 2023 and "
    "inflation in France was high."
)


def _numeric(verdict="consistent", cid="cmp_num"):
    return Comparison(
        claim_id="clm_t", metric="Unemployment", verdict=verdict,
        comparison_id=cid, rel_diff_pct=0.0 if verdict == "consistent"
        else 50.0, tolerance_rule="|claim-evidence|/|evidence| <= 0.02",
    )


def _pct_quantity():
    return Quantity(raw_text="3.1%", value=3.1, unit="percent",
                    normalized_unit="percent")


def _codes(conclusion_uncertainties):
    return [u.code for u in conclusion_uncertainties]


def test_checkable_plus_vague_emits_partial_coverage():
    """E2 e2_compound_half_vague, closed: the vague half has no quantity,
    so compound detection misses it — the caveat must not."""
    claim = StructuredClaim(raw_text=UNEMPLOYMENT_TEXT,
                            quantities=[_pct_quantity()])
    uncertainties = []
    conclusion = synthesize(claim, [_numeric()], uncertainties, llm=None)
    assert conclusion.stance == "supported"
    hit = [u for u in uncertainties if u.code == PARTIAL_COVERAGE_CODE]
    assert len(hit) == 1
    assert hit[0].severity == "major"
    assert any("partial coverage" in c.lower()
               for c in conclusion.caveats)


def test_checkable_plus_opinion_stays_silent():
    """The opinion half is already disclosed via opinion_or_nonclaim and
    the stance abstains — nothing ships silently, so no extra caveat."""
    claim = StructuredClaim(
        raw_text=("Unemployment in Germany was 3.1% in 2023 and democracy "
                  "is the best form of government."),
        quantities=[_pct_quantity()],
    )
    uncertainties = [Uncertainty(code="opinion_or_nonclaim",
                                 description="opinion flag",
                                 severity="blocking",
                                 related_ids=["clm_t"])]
    conclusion = synthesize(claim, [_numeric()], uncertainties, llm=None)
    assert conclusion.stance == "not_assessable"
    assert PARTIAL_COVERAGE_CODE not in _codes(uncertainties)


def test_checkable_plus_unretrievable_geo_emits_partial_coverage():
    """Two measured halves, one decisive comparison: the F5b compound cap
    demotes the stance, and partial_coverage names the unchecked half."""
    claim = StructuredClaim(
        raw_text=("The GDP of the United States was $27.36 trillion in "
                  "2023 and the GDP of Atlantis was $2 trillion in 2020."),
        quantities=[
            Quantity(raw_text="$27.36 trillion", value=27.36e12,
                     unit="USD", normalized_unit="USD"),
            Quantity(raw_text="$2 trillion", value=2e12,
                     unit="USD", normalized_unit="USD"),
        ],
    )
    uncertainties = [Uncertainty(code="compound_claim",
                                 description="compound flag",
                                 severity="major",
                                 related_ids=["clm_t"])]
    conclusion = synthesize(claim, [_numeric()], uncertainties, llm=None)
    assert conclusion.stance == "partially_supported"
    assert PARTIAL_COVERAGE_CODE in _codes(uncertainties)
    assert any("partial coverage" in c.lower()
               for c in conclusion.caveats)


def test_vague_plus_vague_stays_silent():
    """Nothing verified at all: the unverifiable stance already discloses
    fully — partial coverage would be noise, not disclosure."""
    claim = StructuredClaim(
        raw_text="Inflation in France was high and growth was strong.",
        quantities=[],
    )
    uncertainties = []
    conclusion = synthesize(claim, [], uncertainties, llm=None)
    assert conclusion.stance == "unverifiable"
    assert PARTIAL_COVERAGE_CODE not in _codes(uncertainties)
