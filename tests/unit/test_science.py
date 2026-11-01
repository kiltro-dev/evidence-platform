"""M3 science path: abstract stance assessment (offline, no live calls)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import Evidence, StructuredClaim
from atlas.models.scripted import FaultClient, ScriptedClient
from atlas.pipeline.science import (
    ABSTRACT_PROMPT_CHARS,
    MAX_ABSTRACT_ITEMS,
    SCIENCE_METRIC,
    assess_science_abstracts,
)
from atlas.pipeline.synthesize import NON_VERIFYING_METRICS, decide_stance


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="Vaccines reduce transmission", domain="science",
                claim_type="existence", subject="vaccines transmission")
    base.update(kw)
    return StructuredClaim(**base)


def _ev(claim, eid, abstract="We find vaccines reduce transmission.", **kw):
    payload = {"title": f"Paper {eid}", "year": 2021, "abstract": abstract}
    payload.update(kw.pop("payload", {}))
    return Evidence(claim_id=claim.claim_id, connector="openalex",
                    source_name="OpenAlex", source_kind="secondary",
                    evidence_kind="contextual", locator=f"W{eid} (2021)",
                    excerpt=f"Paper {eid}", payload=payload,
                    evidence_id=eid, **kw)


def _assess(eid, relevance="relevant", direction="supports", argument="It says so."):
    return {"evidence_id": eid, "relevance": relevance,
            "direction": direction, "argument": argument}


# --- no-op conditions -----------------------------------------------------

def test_llm_none_is_noop():
    claim = _claim()
    comps, uncs = assess_science_abstracts(
        claim, [_ev(claim, "ev_1")], llm=None)
    assert comps == [] and uncs == []


def test_llm_unavailable_is_noop():
    class _Down:
        name = "down"
        available = False

        def complete_json(self, *a, **k):
            raise AssertionError("must not be called")

    claim = _claim()
    comps, uncs = assess_science_abstracts(
        claim, [_ev(claim, "ev_1")], llm=_Down())
    assert comps == [] and uncs == []


def test_no_abstracts_makes_no_call():
    claim = _claim()
    llm = ScriptedClient({}, default={"assessments": []})
    evs = [_ev(claim, "ev_1", abstract=None),
           _ev(claim, "ev_2", abstract="   ")]
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert comps == [] and uncs == []
    assert llm.calls == []


# --- happy path -----------------------------------------------------------

def test_happy_path_one_bounded_call():
    claim = _claim()
    evs = [_ev(claim, f"ev_{i}") for i in (1, 2, 3)]
    llm = ScriptedClient({}, default={
        "assessments": [_assess("ev_1", direction="supports"),
                        _assess("ev_2", direction="contradicts"),
                        _assess("ev_3", direction="unclear")]})
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(llm.calls) == 1  # ONE bounded model call
    assert len(comps) == 3 and uncs == []
    assert {c.metric for c in comps} == {SCIENCE_METRIC} == {"abstract_stance"}
    assert [c.verdict for c in comps] == ["consistent", "contradicts",
                                          "inconclusive"]
    assert all(c.actor == "model" for c in comps)
    assert all(c.evidence_id in {"ev_1", "ev_2", "ev_3"} for c in comps)


def test_top3_cap_only():
    claim = _claim()
    evs = [_ev(claim, f"ev_{i}") for i in range(1, 6)]
    llm = ScriptedClient({}, default={
        "assessments": [_assess(f"ev_{i}") for i in (1, 2, 3)]})
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(llm.calls) == 1
    assert MAX_ABSTRACT_ITEMS == 3
    assert len(comps) == 3 and uncs == []
    assert "ev_4" not in llm.calls[0] and "ev_5" not in llm.calls[0]


def test_prompt_stays_slim():
    claim = _claim()
    big = "word " * 5000  # 25k chars
    llm = ScriptedClient({}, default={"assessments": [_assess("ev_1")]})
    assess_science_abstracts(claim, [_ev(claim, "ev_1", abstract=big)], llm=llm)
    assert len(llm.calls) == 1
    assert len(llm.calls[0]) < ABSTRACT_PROMPT_CHARS + 1500


# --- off-topic / dropped ---------------------------------------------------

def test_off_topic_yields_uncertainty_not_comparison():
    claim = _claim()
    evs = [_ev(claim, "ev_1"), _ev(claim, "ev_2")]
    llm = ScriptedClient({}, default={
        "assessments": [_assess("ev_1", relevance="off_topic"),
                        _assess("ev_2")]})
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(comps) == 1 and comps[0].evidence_id == "ev_2"
    assert len(uncs) == 1
    assert uncs[0].code == "abstract_off_topic"
    assert uncs[0].severity == "minor"
    assert uncs[0].related_ids == ["ev_1"]


def test_missing_ids_yield_dropped_uncertainties():
    claim = _claim()
    evs = [_ev(claim, "ev_1"), _ev(claim, "ev_2")]
    llm = ScriptedClient({}, default={"assessments": [_assess("ev_1")]})
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(comps) == 1
    assert len(uncs) == 1
    assert uncs[0].code == "abstract_assessment_dropped"
    assert uncs[0].severity == "minor"
    assert uncs[0].related_ids == ["ev_2"]


def test_unknown_ids_dropped_and_logged(caplog):
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = ScriptedClient({}, default={
        "assessments": [_assess("ev_1"), _assess("ev_NOPE")]})
    with caplog.at_level("WARNING", logger="atlas.pipeline.science"):
        comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(comps) == 1 and uncs == []
    assert "ev_NOPE" in caplog.text


def test_bad_enums_dropped_with_uncertainty(caplog):
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = ScriptedClient({}, default={
        "assessments": [_assess("ev_1", relevance="maybe")]})
    with caplog.at_level("WARNING", logger="atlas.pipeline.science"):
        comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert comps == []
    assert len(uncs) == 1 and uncs[0].code == "abstract_assessment_dropped"
    assert "ev_1" in caplog.text


def test_duplicate_entries_first_wins(caplog):
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = ScriptedClient({}, default={
        "assessments": [_assess("ev_1", direction="supports"),
                        _assess("ev_1", direction="contradicts")]})
    with caplog.at_level("WARNING", logger="atlas.pipeline.science"):
        comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert len(comps) == 1 and comps[0].verdict == "consistent"
    assert uncs == []
    assert "uplicate" in caplog.text


def test_invalid_json_shape_drops_all(caplog):
    claim = _claim()
    evs = [_ev(claim, "ev_1"), _ev(claim, "ev_2")]
    llm = FaultClient(faults={"abstracts": "garbage"})
    with caplog.at_level("WARNING", logger="atlas.pipeline.science"):
        comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert comps == []
    assert [u.code for u in uncs] == ["abstract_assessment_dropped"] * 2
    assert "invalid" in caplog.text.lower()


def test_transport_error_drops_all(caplog):
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = FaultClient(faults={"abstracts": "raise"})
    with caplog.at_level("WARNING", logger="atlas.pipeline.science"):
        comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    assert comps == []
    assert len(uncs) == 1 and uncs[0].code == "abstract_assessment_dropped"


# --- non-verifying semantics (science ceiling) ------------------------------

def test_emitted_comparisons_are_non_verifying_once_wired():
    """All emitted comparisons must fall outside the verifying set once the
    lead adds SCIENCE_METRIC to NON_VERIFYING_METRICS (wiring step)."""
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = ScriptedClient({}, default={"assessments": [_assess("ev_1")]})
    comps, _ = assess_science_abstracts(claim, evs, llm=llm)
    assert len(comps) == 1
    wired = set(NON_VERIFYING_METRICS) | {SCIENCE_METRIC}
    verifying = [c for c in comps if c.metric not in wired]
    assert verifying == []


def test_science_ceiling_preserved_once_wired():
    """A consistent abstract_stance comparison can at most yield
    partially_supported — never supported/contradicted — under the wired
    NON_VERIFYING set. Mirrors decide_stance logic against the wired set
    without mutating synthesize.py (owned by lead)."""
    claim = _claim()
    evs = [_ev(claim, "ev_1")]
    llm = ScriptedClient({}, default={"assessments": [_assess("ev_1")]})
    comps, uncs = assess_science_abstracts(claim, evs, llm=llm)
    wired = set(NON_VERIFYING_METRICS) | {SCIENCE_METRIC}
    verifying = [c for c in comps if c.metric not in wired]
    assert verifying == []
    # decide_stance inputs are unchanged in shape; stance over the wired
    # set cannot exceed partially_supported for these outputs.
    stance_now = decide_stance(comps, uncs, claim)
    assert stance_now == "partially_supported"  # UT-A1: ceiling real, no tautología
    # The ceiling claim itself: no verifying comparison exists, so under
    # the wired set this output cannot drive supported/contradicted.
    assert not any(c.verdict == "consistent" for c in verifying)
    assert not any(c.verdict == "contradicts" for c in verifying)
