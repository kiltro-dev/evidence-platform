"""M8 ablation tests: baseline parsing/doubles + report math.

All offline (0 live calls): JSON-rescue parsing, judge_claim over
doubles (happy path, unparseable, unknown label, transport error,
cost math), decide_runs guards, and report.py over synthetic fixtures
with hand-computed matrices -> exact metrics. Contract pins: the
baseline imports no store/verifier/snapshot machinery.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.ablation import baseline as B  # noqa: E402
from live.ablation import report as R  # noqa: E402
from live.ablation.run import decide_runs  # noqa: E402

LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeClient:
    """Duck-typed Responses stand-in (text/citations/usage scripted)."""

    def __init__(self, text="", citations=None, usage=None,
                 truncated=False, error=None):
        self._text = text
        self._citations = citations or []
        self._usage = usage
        self._truncated = truncated
        self._error = error
        self.last_usage = None
        self.calls = []

    def search_counter_evidence(self, user_text, *, instructions,
                                max_output_tokens=2000):
        self.calls.append({"user_text": user_text,
                           "instructions": instructions,
                           "max_output_tokens": max_output_tokens})
        if self._error is not None:
            raise self._error
        self.last_usage = self._usage
        return SimpleNamespace(text=self._text,
                               citations=self._citations,
                               truncated=self._truncated)


GOOD_JSON = ('{"verdict": "contradicted", '
             '"urls": ["https://data.worldbank.org/x"], '
             '"quote": "population 1,408,975,000"}')


# ---------------------------------------------------------------------------
# JSON rescue + coercion (pure parsing)
# ---------------------------------------------------------------------------

def test_extract_clean_object():
    assert B.extract_json_object(GOOD_JSON)["verdict"] == "contradicted"


def test_extract_with_preamble_and_fences():
    text = ('Sure! Here is my judgment:\n```json\n' + GOOD_JSON
            + '\n```\nHope this helps.')
    assert B.extract_json_object(text)["verdict"] == "contradicted"


def test_extract_ignores_braces_inside_strings():
    text = '{"verdict": "supported", "quote": "a {b} c", "urls": []}'
    assert B.extract_json_object(text)["quote"] == "a {b} c"


def test_extract_rejects_garbage():
    assert B.extract_json_object("no json here") is None
    assert B.extract_json_object("") is None
    assert B.extract_json_object(None) is None
    assert B.extract_json_object('{"verdict": oops}') is None
    assert B.extract_json_object("[1, 2]") is None  # not an object


@pytest.mark.parametrize("raw,want", [
    ("supported", "supported"),
    (" Contradicted ", "contradicted"),
    ("UNVERIFIABLE", "unverifiable"),
    ("partially_supported", None),
    ("", None),
    (None, None),
    (123, None),
])
def test_normalize_verdict(raw, want):
    assert B.normalize_verdict(raw) == want


def test_coerce_urls_and_quote():
    assert B.coerce_urls([" https://a.example/ ", 7, ""]) == [
        "https://a.example/"]
    assert B.coerce_urls("https://a.example/") == []
    assert B.coerce_urls(None) == []
    assert B.coerce_quote(" some span ") == "some span"
    assert B.coerce_quote("") is None
    assert B.coerce_quote(None) is None


def test_instructions_carry_transcript_and_contract():
    text = B.build_instructions("TRANSCRIPT-BODY")
    assert "TRANSCRIPT-BODY" in text
    assert "unverifiable" in text and "do NOT guess" in text
    assert '{"verdict"' in text
    assert "context only" in text
    assert "T1-claim" in B.build_user_text("T1-claim")


# ---------------------------------------------------------------------------
# judge_claim over doubles (no deterministic verdict logic anywhere)
# ---------------------------------------------------------------------------

def test_judge_happy_path_verbatim():
    usage = {"prompt_tokens": 1200, "completion_tokens": 300,
             "total_tokens": 1500}
    cites = [SimpleNamespace(url="https://api.worldbank.org/v2/x",
                             title="WB", span="1,408,975,000")]
    client = FakeClient(GOOD_JSON, cites, usage)
    res = B.judge_claim(client, "F3-en", "China 141M", "SRT", "baseline-run1")
    assert res.claim_id == "F3-en"
    assert res.verdict == "contradicted"  # model's word, verbatim
    assert res.urls == ["https://api.worldbank.org/v2/x"]
    assert res.judge_urls == ["https://data.worldbank.org/x"]
    assert res.quote == "population 1,408,975,000"
    assert res.parse_ok and res.error is None and not res.truncated
    assert res.run_id == "baseline-run1"
    assert res.latency_s >= 0.0
    # exact cost: (1200*1.25 + 300*4.25)/1e6
    assert res.cost_usd == pytest.approx(0.002775)
    assert len(client.calls) == 1  # exactly one model call per claim
    assert "China 141M" in client.calls[0]["user_text"]
    assert "SRT" in client.calls[0]["instructions"]
    json.dumps(res.to_dict())  # JSON-serializable for run files


def test_judge_unparseable_abstains():
    client = FakeClient("I cannot decide, sorry.",
                        usage={"prompt_tokens": 10,
                               "completion_tokens": 5})
    res = B.judge_claim(client, "T1-en", "text", "SRT")
    assert res.verdict == "unverifiable"
    assert not res.parse_ok
    assert "unparseable" in (res.error or "")


def test_judge_unknown_label_abstains():
    client = FakeClient('{"verdict": "maybe", "urls": []}')
    res = B.judge_claim(client, "T1-en", "text", "SRT")
    assert res.verdict == "unverifiable"
    assert not res.parse_ok
    assert "unknown verdict" in (res.error or "")


def test_judge_transport_error_abstains():
    client = FakeClient(error=RuntimeError("boom-timeout"))
    res = B.judge_claim(client, "T1-en", "text", "SRT")
    assert res.verdict == "unverifiable"
    assert not res.parse_ok
    assert "RuntimeError" in (res.error or "")
    assert res.cost_usd == 0.0  # no response -> no usage -> no cost


def test_judge_dict_citations_and_missing_usage():
    cites = [{"url": "https://example.org/a"}, {"nourl": 1},
             SimpleNamespace(url="  ")]
    client = FakeClient('{"verdict": "supported", "urls": [], '
                        '"quote": null}', cites, None)
    res = B.judge_claim(client, "T1-en", "text", "SRT")
    assert res.verdict == "supported"
    assert res.urls == ["https://example.org/a"]
    assert res.quote is None
    assert res.cost_usd == 0.0


def test_judge_truncated_flag_propagates():
    client = FakeClient(GOOD_JSON, [], {"prompt_tokens": 1,
                                        "completion_tokens": 1},
                        truncated=True)
    res = B.judge_claim(client, "F3-en", "text", "SRT")
    assert res.truncated and res.verdict == "contradicted"


# ---------------------------------------------------------------------------
# decide_runs guards (pure)
# ---------------------------------------------------------------------------

def test_decide_runs_cheap_proceeds():
    runs, note = decide_runs(0.005, 31, 2)
    assert (runs, note) == (2, None)


def test_decide_runs_per_claim_guard():
    runs, note = decide_runs(0.31, 31, 2)
    assert runs == 1 and "$0.30" in (note or "")


def test_decide_runs_budget_guard():
    # $0.20/claim x 31 x 2 = $12.40 > $8, but under the per-claim cap.
    runs, note = decide_runs(0.20, 31, 2)
    assert runs == 1 and "projected $12.40" in (note or "")


def test_decide_runs_single_requested_untouched():
    assert decide_runs(9.99, 31, 1) == (1, None)


# ---------------------------------------------------------------------------
# report.py over synthetic fixtures (hand-computed -> exact)
# ---------------------------------------------------------------------------

# 6 claims: 3 TRUE (2 supported, 1 contradicted=P0) + 3 FALSE
# (1 contradicted=hallucination caught, 1 supported=false alarm,
# 1 unverifiable=abstention).
FIX_EXPECTED = {"a": "true", "b": "true", "c": "true",
                "d": "false", "e": "false", "f": "false"}
FIX_VERDICTS = {"a": "supported", "b": "supported",
                "c": "contradicted", "d": "contradicted",
                "e": "supported", "f": "unverifiable"}


def test_build_matrix_exact():
    assert R.build_matrix(FIX_EXPECTED, FIX_VERDICTS) == {
        "true": {"supported": 2, "contradicted": 1, "disputed": 0,
                 "unverifiable": 0},
        "false": {"supported": 1, "contradicted": 1, "disputed": 0,
                  "unverifiable": 1},
    }


def test_build_matrix_missing_verdict_abstains():
    matrix = R.build_matrix({"a": "true"}, {})
    assert matrix["true"]["unverifiable"] == 1


def test_arm_metrics_exact():
    costs = {k: 0.01 for k in FIX_EXPECTED}
    lat = {"a": 10.0, "b": 20.0, "c": 30.0,
           "d": 40.0, "e": 50.0, "f": 60.0}
    m = R.arm_metrics("arm-x", FIX_EXPECTED, FIX_VERDICTS, costs, lat)
    assert m["n"] == 6
    assert m["decided"] == 5
    assert m["correct"] == 3  # a, b supported + d contradicted
    assert m["accuracy_decided"] == pytest.approx(0.6)
    assert m["abstention_count"] == 1
    assert m["abstention_rate"] == pytest.approx(round(1 / 6, 4))
    assert m["hallucinations_detected"] == ["d"]
    assert m["n_hallucinations"] == 1
    assert m["falsos_contradicted"] == ["c"]  # TRUE judged contradicted
    assert m["falsos_supported"] == ["e"]  # FALSE judged supported
    assert m["n_disputed"] == 0
    assert m["total_cost_usd"] == pytest.approx(0.06)
    assert m["cost_per_claim_usd"] == pytest.approx(0.01)
    assert m["latency_s"]["count"] == 6
    # nearest-rank over [10..60]: p50=30, p95=60
    assert m["latency_s"]["p50"] == 30.0
    assert m["latency_s"]["p95"] == 60.0
    json.dumps(m)


def test_arm_metrics_wilson_matches_tabulated_8_of_9():
    # M2d-like panel: 8 correct over 9 decided -> [0.565, 0.980].
    expected = {f"t{i}": "true" for i in range(8)}
    expected["f0"] = "false"
    verdicts = {f"t{i}": "supported" for i in range(7)}
    verdicts["t7"] = "unverifiable"
    verdicts["f0"] = "supported"  # false-supported (decided, wrong)
    expected["f1"] = "false"
    verdicts["f1"] = "contradicted"  # 8th correct... recount below
    # decided: 7 supported-T + f0 + f1 = 9; correct: 7 + f1 = 8.
    m = R.arm_metrics("m2d-like", expected, verdicts)
    assert (m["decided"], m["correct"]) == (9, 8)
    assert m["accuracy_decided"] == pytest.approx(8 / 9)
    assert m["wilson_lo"] == pytest.approx(0.565, abs=1e-3)
    assert m["wilson_hi"] == pytest.approx(0.980, abs=1e-3)


def test_consistency_exact():
    v1 = {"a": "supported", "b": "supported", "c": "unverifiable"}
    v2 = {"a": "supported", "b": "contradicted", "c": "unverifiable"}
    cons = R.consistency(v1, v2)
    assert cons == {"n": 3, "agree": 2,
                    "agreement_rate": pytest.approx(round(2 / 3, 4)),
                    "disagreements": ["b"]}


def test_consistency_empty_is_trivially_perfect():
    assert R.consistency({}, {})["agreement_rate"] == 1.0


def _synth_run(run_id, verdicts, cost_each=0.01, lat_each=5.0):
    return {"run_id": run_id,
            "claims": [{"claim_id": cid, "verdict": v,
                        "cost_usd": cost_each, "latency_s": lat_each}
                       for cid, v in verdicts.items()]}


def test_compare_reports_baseline_win_honestly():
    # Atlas: abstains everywhere (0 decided). Baseline: perfect 4/4.
    # The report must name the baseline as leader where it wins.
    entries = [{"id": "a", "expected": "true"},
               {"id": "b", "expected": "true"},
               {"id": "c", "expected": "false"},
               {"id": "d", "expected": "false"}]
    atlas = {"n_claims": 4, "total_cost_usd": 1.0,
             "summary_path": "synth-m2d.json",
             "per_claim": [{"claim_id": cid, "expected": e,
                            "verdict": "unverifiable", "latency_s": 90.0}
                           for cid, e in [("a", "true"), ("b", "true"),
                                          ("c", "false"), ("d", "false")]]}
    perfect = {"a": "supported", "b": "supported",
               "c": "contradicted", "d": "contradicted"}
    almost = dict(perfect, d="unverifiable")
    rep = R.compare(entries, atlas,
                    [_synth_run("baseline-run1", perfect),
                     _synth_run("baseline-run2", almost)])
    assert rep["n_claims"] == 4
    assert set(rep["arms"]) == {"atlas-m2d", "baseline-run1",
                                "baseline-run2"}
    assert rep["arms"]["atlas-m2d"]["decided"] == 0
    assert rep["arms"]["atlas-m2d"]["accuracy_decided"] is None
    assert rep["arms"]["baseline-run1"]["accuracy_decided"] == 1.0
    assert rep["arms"]["baseline-run1"]["n_hallucinations"] == 2
    cons = rep["baseline_consistency_run1_vs_run2"]
    assert (cons["agree"], cons["n"]) == (3, 4)
    assert cons["disagreements"] == ["d"]
    leaders = rep["leaders"]
    # run2 abstains on d but stays perfect on decided (3/3): accuracy tie.
    assert leaders["accuracy_decided_higher"] == ("baseline-run1+"
                                                 "baseline-run2")
    assert leaders["hallucinations_higher"] == "baseline-run1"
    assert leaders["falsos_total_lower"] == ("atlas-m2d+baseline-run1+"
                                             "baseline-run2")  # 0-way tie
    assert leaders["cost_per_claim_lower"] in (
        "baseline-run1", "baseline-run1+baseline-run2")
    table = R.render_text_table(rep)
    assert "baseline-run1" in table and "leader accuracy" in table


def test_compare_partial_denominator_filters_arms():
    # --limit run: truth has 1 claim but the Atlas summary carries 2.
    # Cost/latency must count only the in-denominator claim.
    entries = [{"id": "a", "expected": "true"}]
    atlas = {"n_claims": 2, "total_cost_usd": 1.0,
             "per_claim": [{"claim_id": "a", "expected": "true",
                            "verdict": "supported", "latency_s": 10.0},
                           {"claim_id": "b", "expected": "false",
                            "verdict": "supported", "latency_s": 90.0}]}
    rep = R.compare(entries, atlas,
                    [_synth_run("baseline-run1", {"a": "supported",
                                                  "b": "supported"},
                                cost_each=0.02, lat_each=5.0)])
    m = rep["arms"]["atlas-m2d"]
    assert m["n"] == 1 and m["decided"] == 1 and m["correct"] == 1
    assert m["total_cost_usd"] == pytest.approx(0.5)  # 1.0/2, one claim only
    assert m["latency_s"]["p50"] == 10.0
    assert rep["arms"]["baseline-run1"]["total_cost_usd"] == pytest.approx(
        0.02)


def test_compare_single_run_has_no_consistency():
    entries = [{"id": "a", "expected": "true"}]
    atlas = {"n_claims": 1, "total_cost_usd": 0.5,
             "per_claim": [{"claim_id": "a", "expected": "true",
                            "verdict": "supported", "latency_s": 1.0}]}
    rep = R.compare(entries, atlas,
                    [_synth_run("baseline-run1", {"a": "supported"})])
    assert rep["baseline_consistency_run1_vs_run2"] is None
    assert "run1-vs-run2" not in R.render_text_table(rep)


# ---------------------------------------------------------------------------
# Contract pins (the baseline is model-only by construction)
# ---------------------------------------------------------------------------

def test_baseline_has_no_verifier_store_or_snapshot_imports():
    path = os.path.join(LIVE_DIR, "ablation", "baseline.py")
    with open(path, encoding="utf-8") as fh:
        import_lines = [ln for ln in fh.read().splitlines()
                        if ln.startswith(("import ", "from "))]
    blob = "\n".join(import_lines)
    for banned in ("EvidenceStore", "verify_and_emit", "snapshot",
                   "SnapshotLoader", "verifier", "ClaimInput"):
        assert banned not in blob, f"baseline imports {banned}"


def test_runner_reuses_truth_loader():
    import live.ablation.run as runmod

    assert runmod.load_entries is not None
    assert runmod.MAX_CONCURRENCY == 4
    assert runmod.CLAIM_DEADLINE_S == 120.0


def test_live_client_exposes_search_method():
    responses = pytest.importorskip("src.atlas.models.responses",
                                    reason="needs httpx (live client)")
    assert callable(responses.ResponsesClient.search_counter_evidence)
