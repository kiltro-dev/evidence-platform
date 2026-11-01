"""Offline data-integrity tests for the HalluBench case set.

No network, no model, no .env: these tests pin the frozen benchmark
contract (counts, schema, vintage, frozen margins). They NEVER call
run_ablation live arms. Live behavior is recorded under
eval/hallubench/results/, not asserted here.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval" / "hallubench"))

from cases import CASES, CATEGORIES  # noqa: E402

BY_ID = {c["id"]: c for c in CASES}
VINTAGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRUTH_KEYS = {"indicator", "iso3", "year", "value", "claimed", "vintage",
              "fetched_at", "source_url", "note"}


def test_counts_and_categories():
    assert len(CASES) >= 24, f"need >=24 cases, have {len(CASES)}"
    cats = [c["category"] for c in CASES]
    assert cats.count("true") == 8
    assert cats.count("false") == 8
    assert cats.count("nonclaim") == 4
    assert cats.count("adversarial") == 4
    assert set(cats) <= set(CATEGORIES)


def test_ids_unique_and_schema():
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == len(ids)
    texts = [c["text"] for c in CASES]
    assert all(isinstance(t, str) and t.strip() for t in texts)
    assert len(set(texts)) == len(texts), "duplicate claim text"
    for c in CASES:
        assert c["expected"] in ("true", "false", "non-claim"), c["id"]
        assert c["lang"] in ("en", "es"), c["id"]
        assert isinstance(c["mutation"], str) and c["mutation"], c["id"]
        assert isinstance(c["rationale"], str) and c["rationale"], c["id"]
        if c["category"] == "true":
            assert c["expected"] == "true", c["id"]
        if c["category"] == "false":
            assert c["expected"] == "false", c["id"]
        if c["category"] == "nonclaim":
            assert c["expected"] == "non-claim", c["id"]


def test_false_mutation_coverage():
    muts = [c["mutation"] for c in CASES if c["category"] == "false"]
    for fam in ("año-trocado", "x2", "x1000", "tasa-vs-nivel"):
        assert muts.count(fam) == 2, f"{fam}: {muts.count(fam)}"


def test_adversarial_langs_and_verdicts():
    adv = [c for c in CASES if c["category"] == "adversarial"]
    langs = sorted(c["lang"] for c in adv)
    assert langs == ["en", "en", "es", "es"], langs
    assert BY_ID["hb_a01"]["expected"] == "false"
    assert BY_ID["hb_a02"]["expected"] == "false"
    assert BY_ID["hb_a03"]["expected"] == "true"
    assert BY_ID["hb_a04"]["expected"] == "false"


def test_truth_presence_rules():
    for c in CASES:
        if c["category"] in ("true", "false"):
            assert isinstance(c["truth"], dict), c["id"]
        if c["category"] == "nonclaim":
            assert c["truth"] is None, c["id"]
    assert BY_ID["hb_a02"]["truth"] is None  # injection has no WB truth
    for aid in ("hb_a01", "hb_a03", "hb_a04"):
        assert isinstance(BY_ID[aid]["truth"], dict), aid


def test_truth_schema_and_vintage():
    for c in CASES:
        t = c.get("truth")
        if t is None:
            continue
        assert TRUTH_KEYS <= set(t), c["id"]
        assert isinstance(t["value"], (int, float)), c["id"]
        assert isinstance(t["claimed"], (int, float)), c["id"]
        assert VINTAGE_RE.match(t["vintage"]), c["id"]
        assert VINTAGE_RE.match(t["fetched_at"]), c["id"]
        assert t["source_url"].startswith("https://data.worldbank.org/"), c["id"]
        assert re.fullmatch(r"[A-Z]{3}", t["iso3"]), c["id"]
        assert isinstance(t["year"], int) and 1900 < t["year"] < 2100, c["id"]


def _rel_gap(claimed: float, value: float) -> float:
    return abs(claimed - value) / (abs(value) if value != 0 else 1.0)


def test_true_claims_within_tolerance():
    # True claims sit at <2% from frozen truth (the pipeline's relative
    # tolerance), so a correct pipeline never contradicts them.
    for c in CASES:
        if c["category"] == "true" or c["id"] == "hb_a03":
            gap = _rel_gap(c["truth"]["claimed"], c["truth"]["value"])
            assert gap < 0.02, f"{c['id']}: gap={gap:.4f}"


def test_false_claims_outside_margin():
    # año/x2/x1000 falses sit >10% from frozen truth: unambiguously
    # wrong even under WB revisions. tasa-vs-nivel is structural
    # (rate/level confusion), pinned by its aux frozen reference instead.
    for c in CASES:
        if c["category"] != "false":
            continue
        if c["mutation"] == "tasa-vs-nivel":
            assert "aux" in c["truth"], c["id"]
            continue
        gap = _rel_gap(c["truth"]["claimed"], c["truth"]["value"])
        assert gap > 0.10, f"{c['id']}: gap={gap:.4f}"


def test_x2_and_x1000_ratios():
    for c in CASES:
        if c["mutation"] not in ("x2", "x1000"):
            continue
        ratio = c["truth"]["claimed"] / c["truth"]["value"]
        want = 2.0 if c["mutation"] == "x2" else 1000.0
        assert abs(ratio - want) / want < 0.05, f"{c['id']}: ratio={ratio}"


def test_no_live_imports_at_collection():
    # cases.py must import without env, network or model deps.
    import cases

    assert isinstance(cases.CASES, list)
    assert "run_ablation" not in sys.modules
