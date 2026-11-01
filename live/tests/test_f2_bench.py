"""F2-1 eval2 tests: metrics, Wilson, fidelity, tamper (all offline).

0 live calls: exact metrics over hand-computed fixtures, tabulated
Wilson values, fidelity over doubles (fetch + pages), tamper roundtrip
(pristine PASS -> 1-byte FAIL) on the real demo bundle, and contract
pins (reuse-only arms, dev default, prereg hash seal).

The retention set is NEVER opened here: the seal test compares its
sha256 against prereg.md without printing or asserting on contents.
"""

import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.ablation import report as R  # noqa: E402
from live.auditor.wilson import wilson_interval  # noqa: E402
from live.eval2 import fidelity as F  # noqa: E402
from live.eval2 import run as runmod  # noqa: E402
from live.eval2 import tamper as T  # noqa: E402

LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL2_DIR = os.path.join(LIVE_DIR, "eval2")
REPO_ROOT = os.path.dirname(LIVE_DIR)


# ---------------------------------------------------------------------------
# Wilson (tabulated, independent of the implementation under test)
# ---------------------------------------------------------------------------

def test_wilson_8_of_9_matches_m8_tabulation():
    lo, hi = wilson_interval(8, 9)
    assert lo == pytest.approx(0.565, abs=1e-3)
    assert hi == pytest.approx(0.980, abs=1e-3)


def test_wilson_no_data_is_maximal_uncertainty():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_brackets_the_point_estimate():
    for ok, n in [(0, 5), (1, 5), (3, 6), (5, 5), (36, 72)]:
        lo, hi = wilson_interval(ok, n)
        assert 0.0 <= lo <= ok / n <= hi <= 1.0


def test_wilson_perfect_small_n_stays_honest():
    # 2/2 is perfect but weak evidence: interval must stay wide.
    lo, hi = wilson_interval(2, 2)
    assert lo < 0.5 and hi > 0.9


# ---------------------------------------------------------------------------
# Metrics over a hand-computed eval2-shaped fixture
# ---------------------------------------------------------------------------

# 6 claims: 3 TRUE (2 supported, 1 contradicted=P0) + 3 FALSE
# (1 contradicted=caught, 1 supported=false alarm, 1 abstention).
FIX_EXPECTED = {"F2D-A1": "true", "F2D-A2": "true", "F2D-A3": "true",
                "F2D-C1": "false", "F2D-C2": "false", "F2D-C3": "false"}
FIX_VERDICTS = {"F2D-A1": "supported", "F2D-A2": "supported",
                "F2D-A3": "contradicted", "F2D-C1": "contradicted",
                "F2D-C2": "supported", "F2D-C3": "unverifiable"}


def test_arm_metrics_exact_eval2_fixture():
    costs = {k: 0.02 for k in FIX_EXPECTED}
    lat = {"F2D-A1": 5.0, "F2D-A2": 15.0, "F2D-A3": 25.0,
           "F2D-C1": 35.0, "F2D-C2": 45.0, "F2D-C3": 55.0}
    m = R.arm_metrics("atlas-run1", FIX_EXPECTED, FIX_VERDICTS, costs, lat)
    assert (m["n"], m["decided"], m["correct"]) == (6, 5, 3)
    assert m["accuracy_decided"] == pytest.approx(0.6)
    assert m["abstention_rate"] == pytest.approx(round(1 / 6, 4))
    assert m["hallucinations_detected"] == ["F2D-C1"]
    assert m["falsos_contradicted"] == ["F2D-A3"]
    assert m["falsos_supported"] == ["F2D-C2"]
    assert m["total_cost_usd"] == pytest.approx(0.12)
    assert m["cost_per_claim_usd"] == pytest.approx(0.02)
    assert (m["latency_s"]["p50"], m["latency_s"]["p95"]) == (25.0, 55.0)
    json.dumps(m)


def test_panel_recall_alias():
    entries = [{"id": cid, "expected": exp}
               for cid, exp in FIX_EXPECTED.items()]
    panel = runmod._panel_for("x", entries, FIX_VERDICTS,
                              {k: 0.0 for k in FIX_EXPECTED}, {})
    assert panel["recall"] == pytest.approx(round(5 / 6, 4))


def test_consistency_exact():
    v1 = dict(FIX_VERDICTS)
    v2 = dict(FIX_VERDICTS, **{"F2D-C3": "supported"})
    cons = R.consistency(v1, v2)
    assert (cons["n"], cons["agree"]) == (6, 5)
    assert cons["disagreements"] == ["F2D-C3"]


def test_pool_runs_namespaces_and_duplicates():
    exp = {"a": "true", "b": "false"}
    v1 = {"a": "supported", "b": "contradicted"}
    v2 = {"a": "unverifiable", "b": "supported"}
    pexp, pver = runmod.pool_runs(exp, [v1, v2])
    assert pexp == {"a::r1": "true", "b::r1": "false",
                    "a::r2": "true", "b::r2": "false"}
    assert pver == {"a::r1": "supported", "b::r1": "contradicted",
                    "a::r2": "unverifiable", "b::r2": "supported"}


def _panel(acc=None, hal=0, fal_c=(), fal_s=(), abst=0.0, cost=0.01, p50=1.0):
    return {"accuracy_decided": acc, "n_hallucinations": hal,
            "falsos_contradicted": list(fal_c),
            "falsos_supported": list(fal_s), "abstention_rate": abst,
            "cost_per_claim_usd": cost, "latency_s": {"p50": p50}}


def test_leaders_names_winners_and_ties():
    panels = {"atlas-run1": _panel(acc=0.9, hal=2, fal_c=["x"], cost=0.03,
                                   p50=20.0),
              "baseline-run1": _panel(acc=0.8, hal=3, cost=0.01, p50=5.0)}
    lead = runmod.leaders(panels)
    assert lead["accuracy_decided_higher"] == "atlas-run1"
    assert lead["hallucinations_higher"] == "baseline-run1"
    assert lead["falsos_total_lower"] == "baseline-run1"
    assert lead["cost_per_claim_lower"] == "baseline-run1"
    assert lead["latency_p50_lower"] == "baseline-run1"


def test_leaders_none_accuracy_excluded_tie_named():
    panels = {"a": _panel(acc=None), "b": _panel(acc=None)}
    assert runmod.leaders(panels)["accuracy_decided_higher"] == "n/a"
    panels = {"a": _panel(acc=1.0), "b": _panel(acc=1.0)}
    assert runmod.leaders(panels)["accuracy_decided_higher"] == "a+b"


def test_decide_total_runs_guards():
    assert runmod.decide_total_runs({"baseline": 0.03, "atlas": 0.03},
                                    36, 2) == (2, None)
    runs, note = runmod.decide_total_runs({"baseline": 0.31, "atlas": 0.03},
                                          36, 2)
    assert runs == 1 and "baseline" in (note or "")
    runs, note = runmod.decide_total_runs({"baseline": 0.10, "atlas": 0.10},
                                          36, 2)
    assert runs == 1 and "projected $14.40" in (note or "")
    assert runmod.decide_total_runs({"baseline": 9.0}, 36, 1) == (1, None)


# ---------------------------------------------------------------------------
# Fidelity core (pure) + doubles (no network)
# ---------------------------------------------------------------------------

PAGE = ("<html><body><p>Canada's GDP was 2.27 trillion dollars in 2024, "
        "says the Bank. Other text 999 kg.</p></body></html>")
QUOTE = "Canada's GDP was 2.27 trillion dollars in 2024"


def test_assess_quote_faithful():
    v = F.assess_quote(PAGE, QUOTE, 2.27e12, "USD")
    assert v["faithful"] and v["reason"] == "faithful"
    assert v["quote_found"] and v["number_backed"]
    assert v["rel_diff"] == pytest.approx(0.0)


def test_assess_quote_not_found():
    v = F.assess_quote(PAGE, "Mexico GDP 1.83 trillion dollars", 1.83e12,
                       "USD")
    assert not v["faithful"] and v["reason"] == "quote_not_found"


def test_assess_quote_numeric_mismatch():
    v = F.assess_quote(PAGE, QUOTE, 3.0e12, "USD")
    assert not v["faithful"] and v["reason"] == "numeric_mismatch"
    assert v["rel_diff"] == pytest.approx(abs(3.0 - 2.27) / 2.27, abs=1e-3)


def test_assess_quote_unit_mismatch():
    v = F.assess_quote(PAGE, QUOTE, 2.27e12, "EUR")
    assert not v["faithful"] and v["reason"] == "unit_mismatch"


def test_assess_quote_no_number_and_no_quote():
    page2 = "<p>Canada's economy is large and growing steadily.</p>"
    v = F.assess_quote(page2, "Canada's economy is large", 2.27e12, "USD")
    assert not v["faithful"] and v["reason"] == "no_number_in_quote"
    v = F.assess_quote(PAGE, None, 2.27e12, "USD")
    assert not v["faithful"] and v["reason"] == "no_quote"
    v = F.assess_quote(None, QUOTE, 2.27e12, "USD")
    assert not v["faithful"] and v["reason"] == "page_unresolved"


WB_JSON_PAGE = ('[{"page":1,"pages":1},[{"indicator":{"id":"SP.POP.TOTL",'
                '"value":"Population, total"},"country":{"id":"IN"},'
                '"date":"2024","value":1450935791}]]')
WB_JSON_QUOTE = '"date":"2024","value":1450935791'
WB_JSON_URL = ("https://api.worldbank.org/v2/country/IN/indicator/"
               "SP.POP.TOTL?format=json&date=2024")


def test_assess_quote_wb_unitless_json_backed_via_source_meta():
    v = F.assess_quote(WB_JSON_PAGE, WB_JSON_QUOTE, 1.45e9, "count",
                       url=WB_JSON_URL)
    assert v["faithful"] and v["unit_from"] == "source_meta"
    assert v["evidence_value"] == pytest.approx(1450935791)


def test_assess_quote_wb_fallback_rejected_on_wrong_indicator():
    v = F.assess_quote(WB_JSON_PAGE, WB_JSON_QUOTE, 1.45e9, "USD",
                       url=WB_JSON_URL)
    assert not v["faithful"] and v["reason"] == "unit_mismatch"


def test_assess_quote_deterministic():
    a = F.assess_quote(PAGE, QUOTE, 2.27e12, "USD")
    b = F.assess_quote(PAGE, QUOTE, 2.27e12, "USD")
    assert a == b


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_assess_run_with_fetch_double(tmp_path):
    claims = _write(tmp_path, "claims.json", [
        {"id": "X1", "lang": "en", "expected": "true",
         "text": "Canada's GDP was 2.27 trillion dollars in 2024."},
        {"id": "X2", "lang": "en", "expected": "false",
         "text": "Mexico's GDP was 9.99 trillion dollars in 2024."},
        {"id": "X3", "lang": "en", "expected": "true",
         "text": "Spain's GDP was 1.73 trillion dollars in 2024."},
    ])
    run = _write(tmp_path, "run.json", {"run_id": "baseline-run1",
                                        "claims": [
                                            {"claim_id": "X1",
                                             "verdict": "supported",
                                             "urls": ["https://bank.example/gdp"],
                                             "quote": QUOTE},
                                            {"claim_id": "X2",
                                             "verdict": "contradicted",
                                             "urls": ["https://dead.example/x"],
                                             "quote": "Mexico GDP 1.83 trillion dollars"},
                                            {"claim_id": "X3",
                                             "verdict": "supported",
                                             "urls": [], "quote": None},
                                        ]})
    pages = {"https://bank.example/gdp": PAGE}

    def fake_fetch(url):
        if url not in pages:
            raise ConnectionError("dns")
        return pages[url]

    rep = F.assess_run(run, claims, fetch_fn=fake_fetch)
    assert (rep["n_checked"], rep["n_faithful"]) == (1, 1)
    assert rep["fraction_faithful"] == 1.0
    assert rep["unresolved"] == ["X2"]
    assert rep["n_unchecked"] == 1  # X3: no quote/URL
    assert rep["n_snapshot_used"] == 0
    json.dumps(rep)


def test_assess_run_stated_url_fallback(tmp_path):
    # M8/F2 client shape: empty browsed urls, sources in judge_urls.
    claims = _write(tmp_path, "claims.json", [
        {"id": "X1", "lang": "en", "expected": "true",
         "text": "Canada's GDP was 2.27 trillion dollars in 2024."},
    ])
    run = _write(tmp_path, "run.json", {"run_id": "baseline-run1",
                                        "claims": [
                                            {"claim_id": "X1",
                                             "verdict": "supported",
                                             "urls": [],
                                             "judge_urls": [
                                                 "https://bank.example/gdp"],
                                             "quote": QUOTE},
                                        ]})
    rep = F.assess_run(run, claims,
                       fetch_fn=lambda u: PAGE)
    assert (rep["n_checked"], rep["n_faithful"]) == (1, 1)
    assert rep["rows"][0]["url_source"] == "stated"
    assert rep["n_url_stated"] == 1 and rep["n_url_browsed"] == 0


def test_assess_run_snapshot_fallback_declared(tmp_path):
    import hashlib as _hl
    claims = _write(tmp_path, "claims.json", [
        {"id": "X1", "lang": "en", "expected": "true",
         "text": "Canada's GDP was 2.27 trillion dollars in 2024."},
    ])
    run = _write(tmp_path, "run.json", {"run_id": "baseline-run1",
                                        "claims": [
                                            {"claim_id": "X1",
                                             "verdict": "supported",
                                             "urls": ["https://gone.example/g"],
                                             "quote": QUOTE},
                                        ]})
    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    (snapdir / f"{_hl.sha256(b'https://gone.example/g').hexdigest()}.html"
     ).write_text(PAGE, encoding="utf-8")

    def dead_fetch(url):
        raise ConnectionError("gone")

    rep = F.assess_run(run, claims, snapshot_dir=str(snapdir),
                       fetch_fn=dead_fetch)
    assert (rep["n_checked"], rep["n_faithful"]) == (1, 1)
    assert rep["n_snapshot_used"] == 1
    assert rep["rows"][0]["snapshot_used"] is True


# ---------------------------------------------------------------------------
# Tamper roundtrip on the REAL demo bundle (offline verifier)
# ---------------------------------------------------------------------------

DEMO_BUNDLE = os.path.join(REPO_ROOT, "data", "demo",
                            "atlas-live-bundle.zip")


def test_tamper_pristine_pass_flipped_byte_fail(tmp_path):
    from live.bundle.verify_bundle import verify_bundle

    if not os.path.isfile(DEMO_BUNDLE):
        pytest.skip("demo bundle missing")
    dst = str(tmp_path / "tampered.zip")
    info = T.tamper_bundle(DEMO_BUNDLE, dst)
    assert info["arcname"].startswith("snapshots/")
    assert info["orig_byte"] != info["new_byte"]
    before = verify_bundle(DEMO_BUNDLE, engine="live")
    after = verify_bundle(dst, engine="live")
    assert before["ok"] is True
    assert after["ok"] is False
    bad = after["files"].get("bad", [])
    assert any(info["arcname"] in b for b in bad), bad


def test_tamper_demo_report_shape(tmp_path):
    if not os.path.isfile(DEMO_BUNDLE):
        pytest.skip("demo bundle missing")
    rep = T.demo(DEMO_BUNDLE, workdir=str(tmp_path))
    assert rep["demo_pass"] is True
    assert rep["pristine"]["ok"] is True
    assert rep["tampered"]["ok"] is False
    json.dumps(rep)


# ---------------------------------------------------------------------------
# Contract pins
# ---------------------------------------------------------------------------

def test_baseline_reuse_not_reimplementation():
    import live.ablation.baseline as B

    assert runmod.judge_claim is B.judge_claim
    assert runmod.BaselineResult is B.BaselineResult


def test_atlas_reuse_via_run_verify():
    import live.run_verify as RV

    assert callable(RV.run_claim)
    assert callable(RV.make_evidence_handler)
    assert callable(RV.claim_input_for)
    # run.py reaches the swarm ONLY through run_verify (no direct import).
    src = open(os.path.join(EVAL2_DIR, "run.py"), encoding="utf-8").read()
    assert "from live import run_verify" in src
    assert "from live.swarm" not in src
    assert "import live.swarm" not in src


def test_default_claims_is_dev_not_retention():
    args = runmod.build_parser().parse_args([])
    assert args.claims.endswith(os.path.join("eval2", "dev.json"))
    assert "retention" not in args.claims


def test_dev_loads_36_via_reused_loader():
    from live.run_verify import load_entries

    entries = load_entries(os.path.join(EVAL2_DIR, "dev.json"), "en")
    assert len(entries) == 36
    cats = {}
    for e in entries:
        cats.setdefault(e["category"], []).append(e["expected"])
    assert set(cats) == set("abcdef")
    for cat, exps in cats.items():
        assert len(exps) == 6, cat
    assert sum(1 for e in entries if e["expected"] == "true") == 15


def _prereg_hashes():
    text = open(os.path.join(EVAL2_DIR, "prereg.md"), encoding="utf-8").read()
    dev = re.search(r"dev\.json\s+sha256\s+([0-9a-f]{64})", text)
    ret = re.search(r"retention\.json\s+sha256\s+([0-9a-f]{64})", text)
    assert dev and ret, "prereg must record both sha256 seals"
    return dev.group(1), ret.group(1)


def test_prereg_seal_matches_dev():
    want_dev, _ = _prereg_hashes()
    got = hashlib.sha256(
        open(os.path.join(EVAL2_DIR, "dev.json"), "rb").read()).hexdigest()
    assert got == want_dev, \
        "dev.json changed after sealing: update hash + declare in prereg.md"


def test_prereg_seal_matches_retention_without_opening():
    # Seal integrity only: compares digests, never prints claim contents.
    _, want_ret = _prereg_hashes()
    got = hashlib.sha256(
        open(os.path.join(EVAL2_DIR, "retention.json"), "rb").read()
    ).hexdigest()
    assert got == want_ret, \
        "retention.json changed after sealing (F2-2 owns it now)"
