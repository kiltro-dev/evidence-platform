"""Offline end-to-end pipeline tests: scripted LLM + stubbed connectors."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import atlas.pipeline.run as runmod  # noqa: E402
from atlas.core.types import Evidence  # noqa: E402
from atlas.models.scripted import ScriptedClient  # noqa: E402
from atlas.pipeline.run import analyze  # noqa: E402

LLM_STRUCTURE = {
    "domain": "economy",
    "claim_type": "quantitative",
    "subject": "United States GDP 2023",
    "indicator_hint": "GDP (current US$)",
    "entities": ["United States"],
    "quantities": [{"label": "gdp", "value": 27.36e12, "unit": "USD"}],
    "time_refs": [{"raw": "2023", "year": 2023, "start_year": None,
                   "end_year": None, "period_type": "year"}],
    "geo_refs": [{"raw": "United States", "name": "united states", "iso3": "USA"}],
    "conditions": [],
    "context_notes": "",
}


class StubWB:
    name = "worldbank"
    domains = ("economy", "general")

    def __init__(self, value: float, delay: float = 0.0):
        self.value = value
        self.delay = delay

    def retrieve(self, claim, limit=5):
        if self.delay:
            time.sleep(self.delay)
        return [
            Evidence(
                claim_id=claim.claim_id,
                connector=self.name,
                source_name="World Bank Open Data (stub)",
                source_kind="secondary",
                evidence_kind="direct",
                locator="NY.GDP.MKTP.CD USA 2023",
                excerpt=f"GDP in 2023: {self.value}",
                payload={"label": "GDP (current US$)", "unit": "USD",
                         "year": "2023", "value": self.value},
            )
        ]


def _run_with_stub(value: float, delay: float = 0.0):
    # F5b: the scripted summary quotes the stance token and states the
    # measured delta so the strength validator accepts the model path.
    llm = ScriptedClient(
        {"Claim:": LLM_STRUCTURE,
         "stance": {"summary": "Stance supported: measured delta 0.00%.",
                    "caveats": ["scripted"]}},
        default={},
    )
    orig = runmod.for_domain
    runmod.for_domain = lambda domain: [StubWB(value, delay)]  # noqa: E731
    try:
        return analyze("The GDP of the United States was $27.36 trillion in 2023.",
                       llm=llm, persist=False), llm
    finally:
        runmod.for_domain = orig


def test_e2e_supported_path_uses_model_and_code():
    # Exact evidence match: the claimed-precision rule (Team-E F1) stays
    # silent at zero gap, so the happy supported path keeps its stance.
    # (A $27.36tn claim vs $27.81tn evidence now caps at
    # partially_supported; that path is pinned in
    # tests/e2e/test_methodology_e2e.py and the eval precision pair.)
    report, llm = _run_with_stub(27.36e12)
    assert report.conclusion.stance == "supported"
    assert report.claim.extraction_actor == "model"
    assert report.conclusion.actor == "model"  # scripted summary
    assert any(c.actor == "code" and c.verdict == "consistent"
               for c in report.comparisons)
    # structure + falsify + summary; each stage calls at most once
    assert len(llm.calls) == 3
    steps = [a.step for a in report.audit]
    assert steps == ["extract", "route", "retrieve", "verify", "falsify",
                     "synthesize"]


def test_e2e_contradicted_path():
    report, _ = _run_with_stub(15.0e12)
    assert report.conclusion.stance == "contradicted"
    assert any(c.verdict == "contradicts" for c in report.comparisons)


def test_e2e_every_conclusion_traces_to_evidence():
    report, _ = _run_with_stub(27.81e12)
    ev_ids = {e.evidence_id for e in report.evidences}
    for cid in (report.conclusion.support_comparison_ids
                + report.conclusion.contradict_comparison_ids):
        cmp = next(c for c in report.comparisons if c.comparison_id == cid)
        assert cmp.evidence_id in ev_ids


def test_e2e_retrieve_audit_step_carries_connector_wall_time():
    # Perf MEDIUM (observability): the retrieve audit step must reflect
    # the connector's measured wall time, not ~0.0ms. The per-connector
    # steps are created after the parallel fan-out, so run.py backdates
    # them by retrieve_all's per-connector timings.
    report, _ = _run_with_stub(27.36e12, delay=0.2)
    steps = [a for a in report.audit if a.step == "retrieve"]
    assert len(steps) == 1
    assert steps[0].duration_ms >= 150
    assert "worldbank" in steps[0].detail


def test_e2e_yearless_claim_skips_numeric_compare_with_period_mismatch():
    # Eval MEDIUM (mrnev): a year-less claim retrieves most-recent-value
    # evidence, but verify must never numerically compare against an
    # unstated period. Pinned BY DESIGN: period_mismatch major, a
    # not_comparable time check, zero numeric verdicts, unverifiable.
    class StubPct(StubWB):
        def retrieve(self, claim, limit=5):
            assert claim.time_refs == []
            return [Evidence(
                claim_id=claim.claim_id, connector=self.name,
                source_name="World Bank Open Data (stub)",
                source_kind="secondary", evidence_kind="direct",
                locator="SL.UEM.TOTL.ZS DEU mrnev",
                excerpt="Unemployment 2023: 3.1",
                payload={"label": "Unemployment, total",
                         "unit": "percent", "year": "2023", "value": 3.1},
            )]

    orig = runmod.for_domain
    runmod.for_domain = lambda domain: [StubPct(3.1)]  # noqa: E731
    try:
        report = analyze("Unemployment in Germany is 3.1%.",
                         llm=None, persist=False)
    finally:
        runmod.for_domain = orig
    assert report.conclusion.stance == "unverifiable"
    assert any(u.code == "period_mismatch" and u.severity == "major"
               for u in report.uncertainties)
    assert any(c.metric == "time_compatibility"
               and c.verdict == "not_comparable" for c in report.comparisons)
    assert all(c.metric == "time_compatibility" for c in report.comparisons)
