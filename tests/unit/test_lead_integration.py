"""Lead-integration tests (Operation First Place): raw-span recovery and
per-stage usage attribution. Both close correctness gaps the feature
agents flagged but could not own."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import Quantity  # noqa: E402
from atlas.models.scripted import ScriptedClient  # noqa: E402
from atlas.pipeline.extract import (  # noqa: E402
    _from_llm_json,
    _model_span,
    _recover_raw_spans,
    structure_claim,
)
from atlas.pipeline.run import _take_usage, _usage_seq  # noqa: E402


def _q(value: float, raw_text: str = "") -> Quantity:
    return Quantity(label="q", raw_text=raw_text, value=value, unit=None,
                    normalized_value=value, normalized_unit=None)


# --- _model_span: model-proposed spans are verbatim-or-nothing ---

def test_model_span_verbatim_kept():
    assert _model_span("27.36 trillion", "GDP was 27.36 trillion in 2023") == \
        "27.36 trillion"


def test_model_span_non_verbatim_dropped():
    assert _model_span("27.4 trillion", "GDP was 27.36 trillion") == ""


def test_model_span_blank_and_non_string_dropped():
    assert _model_span("   ", "GDP was 27.36 trillion") == ""
    assert _model_span(None, "GDP was 27.36 trillion") == ""
    assert _model_span(27.36, "GDP was 27.36 trillion") == ""


# --- _recover_raw_spans: deterministic value-anchored recovery ---

def test_recovery_matches_en_trillion():
    (q,) = qs = [_q(27.36e12)]
    _recover_raw_spans(
        "The GDP of the United States was $27.36 trillion in 2023.", qs)
    assert q.raw_text == "$27.36 trillion"


def test_recovery_matches_es_decimal_comma_percent():
    (q,) = qs = [_q(3.1)]
    _recover_raw_spans("El desempleo en Alemania fue del 3,1% en 2023.", qs)
    assert q.raw_text == "3,1%"


def test_recovery_leaves_unmatched_empty():
    (q,) = qs = [_q(12345.0)]
    _recover_raw_spans("The GDP of the United States was $27.36 trillion.", qs)
    assert q.raw_text == ""


def test_recovery_keeps_existing_spans():
    (q,) = qs = [_q(27.36e12, raw_text="27.36 trillion")]
    _recover_raw_spans("GDP was 27.36 trillion and also 27.36 trillion.", qs)
    assert q.raw_text == "27.36 trillion"


def test_recovery_consumes_each_span_once():
    qs = [_q(5.0), _q(5.0)]
    _recover_raw_spans("Value 5 appears once.", qs)
    assert qs[0].raw_text == "5"
    assert qs[1].raw_text == ""  # only one heuristic span with value 5


# --- end-to-end through the model path (scripted, offline) ---

_STRUCTURE = {
    "domain": "economy",
    "claim_type": "quantitative",
    "subject": "US GDP",
    "indicator_hint": "GDP",
    "entities": ["United States"],
    "quantities": [{"label": "gdp", "value": 27.36e12, "unit": "USD"}],
    "time_refs": [{"raw": "2023", "year": 2023, "start_year": None,
                   "end_year": None, "period_type": "year"}],
    "geo_refs": [{"raw": "United States", "name": "United States",
                  "iso3": "USA"}],
    "conditions": [],
    "context_notes": "",
}


def test_model_path_recovers_span_without_model_raw():
    claim = _from_llm_json(
        "The GDP of the United States was $27.36 trillion in 2023.",
        dict(_STRUCTURE), "scripted-test-double")
    assert claim.extraction_actor == "model"
    assert claim.quantities[0].raw_text == "$27.36 trillion"


def test_model_path_prefers_verbatim_model_raw():
    payload = dict(_STRUCTURE)
    payload["quantities"] = [{"label": "gdp", "raw": "$27.36 trillion",
                              "value": 27.36e12, "unit": "USD"}]
    claim = _from_llm_json(
        "The GDP of the United States was $27.36 trillion in 2023.",
        payload, "scripted-test-double")
    assert claim.quantities[0].raw_text == "$27.36 trillion"


def test_structure_claim_scripted_gets_span():
    llm = ScriptedClient({"Claim:": _STRUCTURE}, default={})
    claim = structure_claim(
        "The GDP of the United States was $27.36 trillion in 2023.", llm)
    assert claim.extraction_actor == "model"
    assert claim.quantities[0].raw_text == "$27.36 trillion"


# --- _usage_seq / _take_usage: attribute spend to the spending stage ---

class _UsageClient:
    def __init__(self):
        self.last_usage = None
        self.last_usage_seq = 0

    def spend(self, usage):
        self.last_usage = usage
        self.last_usage_seq += 1


def test_usage_seq_absent_is_zero():
    assert _usage_seq(None) == 0
    assert _usage_seq(object()) == 0
    assert _take_usage(None, 0) is None
    assert _take_usage(object(), 0) is None


def test_take_usage_records_spend_and_copies():
    llm = _UsageClient()
    before = _usage_seq(llm)
    spent = {"prompt_tokens": 10, "completion_tokens": 5,
             "reasoning_tokens": 2, "total_tokens": 15}
    llm.spend(spent)
    got = _take_usage(llm, before)
    assert got == spent
    assert got is not spent  # later calls must not alias this record


def test_take_usage_none_when_stage_made_no_call():
    # Regression: without the seq check, a stage that made no model call
    # would copy the previous stage's usage and misattribute it.
    llm = _UsageClient()
    llm.spend({"prompt_tokens": 10, "completion_tokens": 5,
               "reasoning_tokens": 0, "total_tokens": 15})
    before = _usage_seq(llm)
    assert _take_usage(llm, before) is None


# --- reasoning_effort plumbing: small judgment calls run at low depth ---

class _RecordingClient(ScriptedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kwargs_seen: list[dict] = []

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.kwargs_seen.append({"max_tokens": max_tokens,
                                 "reasoning_effort": reasoning_effort,
                                 "json_schema": json_schema})
        return super().complete_json(
            system, user, max_tokens=max_tokens,
            reasoning_effort=reasoning_effort, json_schema=json_schema)


def _scripted_claim():
    from atlas.core.types import StructuredClaim
    return StructuredClaim(raw_text="GDP was $27.36 trillion in 2023.")


def test_falsify_passes_low_reasoning_effort():
    from atlas.pipeline.falsify import falsify_draft
    llm = _RecordingClient({"draft_stance": {"attacks": []}}, default={})
    falsify_draft(_scripted_claim(), [], [], "supported", llm)
    assert llm.kwargs_seen and all(
        k["reasoning_effort"] == "low" for k in llm.kwargs_seen)


def _load_run_eval():
    import importlib.util
    repo = Path(__file__).resolve().parents[2]
    if str(repo / "eval") not in sys.path:
        sys.path.insert(0, str(repo / "eval"))
    spec = importlib.util.spec_from_file_location(
        "run_eval_under_test", repo / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_requires_code_gate_fails_closed():
    # Red-team M3: a pin whose rule is missing must go RED, never
    # green-by-exclusion. The gate trips before analyze(), so no llm.
    run_eval = _load_run_eval()
    assert run_eval._rule_present("claimed_precision_exceeded") is True
    assert run_eval._rule_present("definitely_not_a_rule_xyz") is False
    suite = run_eval.run_suite(
        [{"id": "ghost", "requires_code": "definitely_not_a_rule_xyz",
          "mode": "scripted"}], runner_llm=None, skip_live=False)
    assert suite["executed"] == 1
    assert suite["passed"] == 0
    (res,) = suite["results"]
    assert res["passed"] is False
    assert res["checks"] == {"requires_code_present": False}


def test_science_passes_low_reasoning_effort():
    from atlas.core.types import Evidence
    from atlas.pipeline.science import assess_science_abstracts
    ev = Evidence(claim_id="c", connector="openalex", source_name="OA",
                  source_kind="secondary", evidence_kind="contextual",
                  payload={"abstract": "Some abstract text about GDP."})
    llm = _RecordingClient({"abstracts": {"assessments": []}}, default={})
    assess_science_abstracts(_scripted_claim(), [ev], llm)
    assert llm.kwargs_seen and all(
        k["reasoning_effort"] == "low" for k in llm.kwargs_seen)
