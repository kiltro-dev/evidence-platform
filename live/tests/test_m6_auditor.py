"""M6 auditor tests: Wilson math, canary shape, panel with doubles.

All offline (0 live calls): tabulated Wilson values, static canary
well-formedness (incl. no-overlap with video truth claims and numeric
decidability shape), and summarize()/report_event() over doubles.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.canaries import CANARIES, report_event  # noqa: E402
from live.auditor.wilson import summarize, wilson_interval  # noqa: E402
from live.run_verify import claim_input_for  # noqa: E402

LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Wilson vs tabulated values (z=1.96, standard score interval)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k,n,lo,hi", [
    (8, 9, 0.565, 0.980),    # M2d-like panel: 8/9 decided-correct
    (0, 10, 0.000, 0.278),   # all wrong: touches 0, wide top
    (5, 10, 0.237, 0.763),   # symmetric coin flip
    (1, 1, 0.207, 1.000),    # single hit: touches 1, wide bottom
    (9, 9, 0.701, 1.000),    # perfect 9/9
    (11, 12, 0.646, 0.985),  # near-perfect audit
])
def test_wilson_matches_tabulated(k, n, lo, hi):
    got_lo, got_hi = wilson_interval(k, n)
    assert got_lo == pytest.approx(lo, abs=1e-3)
    assert got_hi == pytest.approx(hi, abs=1e-3)


def test_wilson_empty_is_maximal_uncertainty():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_brackets_sample_proportion():
    for k, n in [(0, 12), (3, 12), (7, 12), (12, 12)]:
        lo, hi = wilson_interval(k, n)
        assert 0.0 <= lo <= k / n <= hi <= 1.0


# ---------------------------------------------------------------------------
# Canaries: well-formed, disjoint from the video, decidable shape
# ---------------------------------------------------------------------------

def test_canary_count_and_balance():
    assert len(CANARIES) == 12
    exps = [c["expected"] for c in CANARIES]
    assert exps.count("true") == 6
    assert exps.count("false") == 6


def test_canary_schema_and_refs():
    seen = set()
    for c in CANARIES:
        assert set(("id", "text", "expected", "ref_url")) <= set(c), c
        assert c["id"] not in seen
        seen.add(c["id"])
        assert c["expected"] in ("true", "false")
        assert len(c["text"]) > 20
        assert c["ref_url"].startswith(
            "https://api.worldbank.org/v2/country/"), c["id"]


def test_canaries_disjoint_from_video_truth():
    with open(os.path.join(LIVE_DIR, "material", "truth.json"),
              encoding="utf-8") as fh:
        truth = json.load(fh)
    truth_ids = {r.get("id") for r in truth}
    truth_texts = {r.get("text") for r in truth}
    for c in CANARIES:
        assert c["id"] not in truth_ids
        assert c["text"] not in truth_texts
        assert not c["id"].endswith("-en")  # video slices use -en/-es


def test_canaries_have_decidable_shape():
    # Every canary must parse to a numeric claim with known unit + year,
    # else the verifier would abstain by construction (claim_unit_unknown).
    for c in CANARIES:
        ci = claim_input_for(c)
        assert ci.unit is not None, c["id"]
        assert ci.value > 0, c["id"]
        assert ci.claim_year == 2024, c["id"]


def test_canary_true_within_tolerance_false_clearly_off():
    # Pinned WB 2024 values (same numbers as the ref_url payloads).
    pinned = {
        "M6-T1": 130861007, "M6-T2": 283487931, "M6-T3": 232679478,
        "M6-T4": 3.69553951353415e12, "M6-T5": 2.38343556245812e12,
        "M6-T6": 41262329,
        "M6-F1": 130861007, "M6-F2": 283487931, "M6-F3": 232679478,
        "M6-F4": 3.69553951353415e12, "M6-F5": 2.38343556245812e12,
        "M6-F6": 41262329,
    }
    for c in CANARIES:
        rel = abs(claim_input_for(c).value - pinned[c["id"]]) / pinned[c["id"]]
        if c["expected"] == "true":
            assert rel < 0.002, (c["id"], rel)  # inside verifier 2%
        else:
            assert rel > 0.20, (c["id"], rel)  # must contradict, no border


def test_runner_reuses_run_verify_pipeline():
    # Contract pin: the audit runs through run_verify.run_claim.
    from live import run_verify

    assert callable(run_verify.run_claim)
    assert run_verify.MAX_CONCURRENCY == 4


# ---------------------------------------------------------------------------
# Panel with doubles: decided-only accuracy, abstention, falsos
# ---------------------------------------------------------------------------

def test_summarize_accuracy_over_decided_only():
    expected = {f"C{i}": ("true" if i % 2 else "false") for i in range(12)}
    verdicts = {
        "C0": "contradicted", "C1": "supported",      # correct x2
        "C2": "supported",                            # false-supported
        "C3": "contradicted",                         # FALSO contradicted
        "C4": "disputed",                             # decided, wrong
        # C5..C11 missing -> unverifiable (abstentions)
    }
    panel = summarize(expected, verdicts)
    assert panel["n"] == 12
    assert panel["decided"] == 5
    assert panel["correct"] == 2
    assert panel["accuracy"] == pytest.approx(0.4)
    assert panel["abstention_rate"] == pytest.approx(7 / 12, abs=1e-4)
    assert panel["falsos_contradicted"] == ["C3"]
    assert panel["falsos_supported"] == ["C2"]
    assert panel["n_disputed"] == 1
    assert panel["wilson_lo"] <= 0.4 <= panel["wilson_hi"]


def test_summarize_all_abstain():
    panel = summarize({"A": "true", "B": "false"}, {})
    assert panel["decided"] == 0
    assert panel["correct"] == 0
    assert panel["accuracy"] is None
    assert panel["abstention_rate"] == 1.0
    assert panel["falsos_contradicted"] == []


def test_report_event_shape():
    panel = summarize({"A": "true"}, {"A": "supported"})
    event = report_event(panel)
    assert event["type"] == "auditor.report"
    for key in ("n", "decided", "correct", "accuracy", "wilson_lo",
                "wilson_hi", "abstention_rate", "falsos_contradicted"):
        assert key in event, key
    assert event["n"] == 1 and event["decided"] == 1
    assert event["accuracy"] == 1.0
    json.dumps(event)  # bus requires JSON-serializable
