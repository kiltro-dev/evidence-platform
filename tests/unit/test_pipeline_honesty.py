"""Pipeline honesty tests (A2a): not_assessable, compound/opinion flags,
prompt hardening, LLM-JSON validation, mixed provenance, caveat union,
citation validation. Offline: ScriptedClient + constructed objects only."""

import logging
import sys
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import (  # noqa: E402
    Comparison,
    GeoRef,
    Quantity,
    Stance,
    StructuredClaim,
    TimeRef,
    Uncertainty,
)
from atlas.models.scripted import ScriptedClient  # noqa: E402
from atlas.pipeline.extract import (  # noqa: E402
    STRUCTURE_SYSTEM,
    _from_llm_json,
    structure_claim,
)
from atlas.pipeline.synthesize import (  # noqa: E402
    decide_stance,
    synthesize,
    validate_citations,
)
from atlas.pipeline.verify import (  # noqa: E402
    COMPOUND_CAVEAT,
    assess_science,
    detect_claim_shape,
    verify_quantitative,
)


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="test claim", domain="economy", claim_type="quantitative")
    base.update(kw)
    return StructuredClaim(**base)


def _cmp(metric: str, verdict: str, cid: str = "cmp_real") -> Comparison:
    return Comparison(  # type: ignore[arg-type]
        claim_id="c", comparison_id=cid, metric=metric, verdict=verdict
    )


def _unc(code: str, severity: str) -> Uncertainty:
    return Uncertainty(  # type: ignore[arg-type]
        code=code, description=f"{code} happened", severity=severity
    )


def _llm_json(**kw) -> dict:
    base = {
        "domain": "economy",
        "claim_type": "quantitative",
        "subject": "s",
        "indicator_hint": None,
        "entities": [],
        "quantities": [],
        "time_refs": [],
        "geo_refs": [],
        "conditions": [],
        "context_notes": "",
    }
    base.update(kw)
    return base


# --- 1. not_assessable stance ---


def test_stance_literal_has_five_outcomes():
    assert set(get_args(Stance)) == {
        "supported",
        "partially_supported",
        "contradicted",
        "unverifiable",
        "not_assessable",
    }


def test_opinion_uncertainty_forces_not_assessable_even_with_verdicts():
    stance = decide_stance(
        [_cmp("gdp", "consistent"), _cmp("gdp", "contradicts", "cmp_2")],
        [_unc("opinion_or_nonclaim", "blocking")],
        _claim(),
    )
    assert stance == "not_assessable"


def test_whitespace_only_input_is_not_assessable():
    claim = _claim(raw_text="   ", claim_type="other", quantities=[],
                   time_refs=[], geo_refs=[])
    assert decide_stance([], [], claim) == "not_assessable"


def test_factual_text_without_refs_is_unverifiable_not_not_assessable():
    # Moon-cheese rule: a factual claim with zero parsed refs still flows
    # through retrieval (subject search) and abstains as unverifiable.
    claim = _claim(raw_text="The moon is made of cheese.",
                   claim_type="other", quantities=[], time_refs=[],
                   geo_refs=[])
    assert decide_stance([], [], claim) == "unverifiable"


def test_empty_but_typed_claim_falls_through_to_old_rules():
    # claim_type != "other" -> emptiness branch does not fire.
    claim = _claim(claim_type="quantitative", quantities=[])
    assert decide_stance([], [], claim) == "unverifiable"


def test_claim_with_time_ref_is_not_not_assessable():
    claim = _claim(
        claim_type="other",
        quantities=[],
        time_refs=[TimeRef(raw="2023", year=2023, period_type="year")],
        geo_refs=[],
    )
    assert decide_stance([], [], claim) == "unverifiable"


def test_verify_emits_opinion_flag_for_opinion_text():
    claim = _claim(raw_text="I think France has the best economy in Europe.")
    _, uncertainties = verify_quantitative(claim, [])
    flag = next(u for u in uncertainties if u.code == "opinion_or_nonclaim")
    assert flag.severity == "blocking"


def test_verify_emits_opinion_flag_for_bare_question():
    claim = _claim(raw_text="What was the GDP of France?")
    _, uncertainties = verify_quantitative(claim, [])
    assert any(u.code == "opinion_or_nonclaim" for u in uncertainties)


def test_verify_opinion_flag_is_conservative():
    # Factual declarative: no flag.
    claim = _claim(raw_text="GDP grew 5% in France in 2023.")
    _, uncs = verify_quantitative(claim, [])
    assert not any(u.code == "opinion_or_nonclaim" for u in uncs)
    # "shoulder" must not match lexicon word "should" (word boundaries).
    claim = _claim(raw_text="Shoulder injuries rose 5% in 2023.")
    _, uncs = verify_quantitative(claim, [])
    assert not any(u.code == "opinion_or_nonclaim" for u in uncs)
    # Mid-text "?" in a declarative sentence with quantities: no flag.
    claim = _claim(raw_text="Who knows? GDP grew 5% in 2023.")
    _, uncs = verify_quantitative(claim, [])
    assert not any(u.code == "opinion_or_nonclaim" for u in uncs)


def test_science_path_also_flags_opinion():
    claim = _claim(
        raw_text="I believe this is the worst study ever.",
        domain="science",
        claim_type="other",
    )
    _, uncertainties = assess_science(claim, [])
    assert any(u.code == "opinion_or_nonclaim" for u in uncertainties)
    assert decide_stance([], uncertainties, claim) == "not_assessable"


# --- 2. compound detect+disclose ---


def test_two_quantities_flagged_compound():
    claim = _claim(
        raw_text="GDP was $27 trillion and the population was 300 million.",
        quantities=[
            Quantity(raw_text="$27 trillion", value=27e12, unit="USD"),
            Quantity(raw_text="300 million", value=300e6, unit=None),
        ],
    )
    flags = detect_claim_shape(claim)
    compound = next(u for u in flags if u.code == "compound_claim")
    assert compound.severity == "major"
    assert COMPOUND_CAVEAT in compound.description
    assert "analyzed as one unit; verify parts separately" in compound.description
    # No splitting: the claim still carries both quantities as one unit.
    assert len(claim.quantities) == 2


def test_two_numbered_sentences_flagged_compound():
    claim = _claim(
        raw_text="GDP grew 5% in 2020. Inflation then hit 3% in 2021.",
        quantities=[Quantity(raw_text="5%", value=5.0, unit="percent")],
    )
    flags = detect_claim_shape(claim)
    assert any(u.code == "compound_claim" for u in flags)


def test_single_quantity_plus_year_is_not_compound():
    # "2023" is a year, not a second quantity; "27.36" decimal must not
    # split into two numbered sentences.
    claim = _claim(
        raw_text="The GDP of the United States was $27.36 trillion in 2023.",
        quantities=[
            Quantity(raw_text="$27.36 trillion", value=27.36e12, unit="USD"),
            Quantity(raw_text="2023", value=2023.0, unit=None),
        ],
    )
    flags = detect_claim_shape(claim)
    assert not any(u.code == "compound_claim" for u in flags)


def test_compound_does_not_change_stance_by_itself():
    # compound_claim is major, not blocking: a lone consistent verifying
    # check still yields supported (flagged partial analysis, not refusal).
    stance = decide_stance(
        [_cmp("gdp", "consistent")], [_unc("compound_claim", "major")], _claim()
    )
    assert stance == "supported"


# --- 3. STRUCTURE_SYSTEM hardening ---


def test_structure_system_hardened():
    assert "unambiguously implied" not in STRUCTURE_SYSTEM
    assert "literally" in STRUCTURE_SYSTEM  # literal-presence rule
    assert 'string "null"' in STRUCTURE_SYSTEM  # forbid string "null"
    assert "JSON null" in STRUCTURE_SYSTEM  # unit null is JSON null
    assert "never quantities" in STRUCTURE_SYSTEM  # years are not quantities
    assert "EVERY quantity" in STRUCTURE_SYSTEM  # compound: list all
    assert '"quantities": []' in STRUCTURE_SYSTEM  # opinion: empty quantities


# --- 4. _from_llm_json validation ---


def test_string_null_and_unknown_units_become_none():
    claim = _from_llm_json(
        "GDP was big.",
        _llm_json(
            quantities=[
                {"label": "a", "value": 1.0, "unit": "null"},
                {"label": "b", "value": 2.0, "unit": "furlongs"},
                {"label": "c", "value": 3.0, "unit": "USD"},
            ]
        ),
        "m",
    )
    by_label = {q.label: q for q in claim.quantities}
    assert by_label["a"].unit is None
    assert by_label["a"].normalized_unit is None
    assert by_label["b"].unit is None
    assert by_label["c"].normalized_unit == "USD"


def test_nonfinite_floats_dropped_and_counted():
    claim = _from_llm_json(
        "GDP was big.",
        _llm_json(
            quantities=[
                {"label": "inf", "value": float("inf"), "unit": "USD"},
                {"label": "nan", "value": float("nan"), "unit": "USD"},
                {"label": "ok", "value": 5.0, "unit": "USD"},
            ]
        ),
        "m",
    )
    assert [q.label for q in claim.quantities] == ["ok"]
    assert "dropped 2 non-finite quantities" in claim.context_notes


def test_unparseable_quantities_dropped_not_fatal():
    claim = _from_llm_json(
        "GDP was big.",
        _llm_json(
            quantities=[
                {"label": "bad", "value": "lots", "unit": "USD"},
                {"label": "missing"},
                "not-a-dict",
                {"label": "ok", "value": 5.0, "unit": "USD"},
            ]
        ),
        "m",
    )
    assert [q.label for q in claim.quantities] == ["ok"]
    assert "dropped 3 invalid quantities" in claim.context_notes


def test_lists_capped_at_20_and_recorded():
    claim = _from_llm_json(
        "GDP was big.",
        _llm_json(
            entities=[f"e{i}" for i in range(25)],
            conditions=[f"c{i}" for i in range(21)],
        ),
        "m",
    )
    assert len(claim.entities) == 20
    assert len(claim.conditions) == 20
    assert "truncated entities from 25 to 20" in claim.context_notes
    assert "truncated conditions from 21 to 20" in claim.context_notes


def test_year_like_llm_quantity_dropped_but_real_value_kept():
    claim = _from_llm_json(
        "GDP was $2023 in the year 2023.",
        _llm_json(
            quantities=[
                {"label": "2023", "value": 2023.0, "unit": None},
                {"label": "price", "value": 2023.0, "unit": "USD"},
            ]
        ),
        "m",
    )
    assert [q.label for q in claim.quantities] == ["price"]
    assert "dropped 1 year-like quantities" in claim.context_notes


# --- 5. mixed provenance ---


def test_backfill_marks_model_name_but_keeps_actor():
    llm = ScriptedClient(
        {
            "Claim:": _llm_json(
                quantities=[{"label": "gdp", "value": 1e12, "unit": "USD"}],
                time_refs=[],  # gaps the heuristic must backfill
                geo_refs=[],
            )
        }
    )
    claim = structure_claim(
        "The GDP of France was $1 trillion in 2023.", llm=llm
    )
    assert claim.extraction_actor == "model"
    assert claim.extraction_model == "scripted-test-double+heuristic-backfill"
    assert claim.time_refs  # backfilled year 2023
    assert claim.geo_refs  # backfilled France


def test_full_model_extraction_has_no_backfill_marker():
    llm = ScriptedClient(
        {
            "Claim:": _llm_json(
                quantities=[{"label": "gdp", "value": 1e12, "unit": "USD"}],
                time_refs=[
                    {
                        "raw": "2023",
                        "year": 2023,
                        "start_year": None,
                        "end_year": None,
                        "period_type": "year",
                    }
                ],
                geo_refs=[{"raw": "France", "name": "France", "iso3": "FRA"}],
            )
        }
    )
    claim = structure_claim(
        "The GDP of France was $1 trillion in 2023.", llm=llm
    )
    assert claim.extraction_actor == "model"
    assert claim.extraction_model == "scripted-test-double"


# --- 6. caveat union ---


def test_model_caveats_appended_to_required_template_caveats():
    llm = ScriptedClient(
        {"stance": {"summary": "Scripted.", "caveats": ["model-note", "block happened"]}}
    )
    conclusion = synthesize(
        _claim(),
        [_cmp("gdp", "consistent")],
        [
            _unc("block", "blocking"),
            _unc("maj", "major"),
            _unc("min", "minor"),
        ],
        llm,
    )
    # blocking+major template caveats ALWAYS retained, first...
    assert conclusion.caveats[0] == "block happened"
    assert conclusion.caveats[1] == "maj happened"
    # ...model caveats appended, deduped (no second "block happened").
    assert conclusion.caveats.count("block happened") == 1
    assert "model-note" in conclusion.caveats


def test_template_path_keeps_all_caveats():
    conclusion = synthesize(
        _claim(),
        [_cmp("gdp", "consistent")],
        [_unc("block", "blocking"), _unc("min", "minor")],
        None,
    )
    assert conclusion.caveats == ["block happened", "min happened"]


# --- 7. summary citation validation ---


def test_invented_citations_stripped_real_ones_kept(caplog):
    cmps = [_cmp("gdp", "consistent", "cmp_abc123")]
    with caplog.at_level(logging.WARNING, logger="atlas.pipeline.synthesize"):
        cleaned = validate_citations(
            "See [cmp_abc123] and also [cmp_invented9] for details.", cmps
        )
    assert "[cmp_abc123]" in cleaned
    assert "cmp_invented9" not in cleaned
    assert "Stripped 1 unknown comparison citation(s)" in caplog.text


def test_synthesize_validates_model_summary_citations():
    llm = ScriptedClient(
        {
            "stance": {
                "summary": "Verdict per [cmp_abc123] plus [cmp_fake].",
                "caveats": [],
            }
        }
    )
    conclusion = synthesize(
        _claim(),
        [_cmp("gdp", "consistent", "cmp_abc123")],
        [],
        llm,
    )
    assert conclusion.actor == "model"
    assert "[cmp_abc123]" in conclusion.summary
    assert "cmp_fake" not in conclusion.summary
