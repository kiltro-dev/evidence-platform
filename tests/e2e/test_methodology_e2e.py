"""Team-E M1 end-to-end: methodology rules through analyze() (offline).

Heuristic extraction (llm=None) + stubbed worldbank connector, so the
claimed-precision, materiality and stance-cap rules run on real parsed
raw_text spans.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import atlas.pipeline.run as runmod  # noqa: E402
from atlas.core.types import Evidence  # noqa: E402
from atlas.pipeline.run import analyze  # noqa: E402


class StubWB:
    name = "worldbank"

    def __init__(self, label: str, unit: str, year: str, value: float):
        self.label = label
        self.unit = unit
        self.year = year
        self.value = value

    def retrieve(self, claim, limit=5):
        return [
            Evidence(
                claim_id=claim.claim_id,
                connector=self.name,
                source_name="World Bank Open Data (stub)",
                source_kind="secondary",
                evidence_kind="direct",
                locator=f"STUB {self.year}",
                excerpt=f"{self.label} in {self.year}: {self.value}",
                payload={"label": self.label, "unit": self.unit,
                         "year": self.year, "value": self.value},
            )
        ]


def _run(text: str, stub: StubWB):
    orig = runmod.for_domain
    runmod.for_domain = lambda domain: [stub]  # noqa: E731
    try:
        return analyze(text, llm=None, persist=False)
    finally:
        runmod.for_domain = orig


def test_e2e_false_precision_caps_france_population():
    report = _run(
        "The population of France was 67,842,591 in 2020.",
        StubWB("Population, total", "count", "2020", 67_500_000.0),
    )
    assert report.conclusion.stance == "partially_supported"
    codes = {u.code for u in report.uncertainties}
    assert "claimed_precision_exceeded" in codes
    assert any(c.verdict == "consistent" for c in report.comparisons)
    assert any("Claimed-precision" in c.notes for c in report.comparisons)


def test_e2e_spanish_unemployment_stays_supported():
    report = _run(
        "El desempleo en Alemania fue del 3,1% en 2023.",
        StubWB("Unemployment, total (% of labor force)", "percent",
               "2023", 3.12),
    )
    assert report.claim.domain == "economy"
    assert report.conclusion.stance == "supported"
    codes = {u.code for u in report.uncertainties}
    assert "claimed_precision_exceeded" not in codes
    assert "indicator_definition_assumed" in codes
    assert "source_revision_risk" in codes


def test_e2e_gdp_gap_discloses_materiality_and_precision():
    report = _run(
        "The GDP of the United States was $27.36 trillion in 2023.",
        StubWB("GDP (current US$)", "USD", "2023", 27.81e12),
    )
    numeric = [c for c in report.comparisons
               if c.metric != "time_compatibility"]
    assert numeric and all(c.verdict == "consistent" for c in numeric)
    codes = {u.code for u in report.uncertainties}
    assert "material_absolute_gap" in codes
    assert "claimed_precision_exceeded" in codes
    assert "price_basis_unchecked" in codes
    assert report.conclusion.stance == "partially_supported"


def test_e2e_exact_gdp_match_stays_supported_with_methodology_notes():
    report = _run(
        "US GDP was $27.36 trillion in current prices in 2023.",
        StubWB("GDP (current US$)", "USD", "2023", 27.36e12),
    )
    assert report.conclusion.stance == "supported"
    codes = {u.code for u in report.uncertainties}
    assert "claimed_precision_exceeded" not in codes
    assert "material_absolute_gap" not in codes
    assert "price_basis_unchecked" not in codes
    assert "indicator_definition_assumed" in codes


def test_e2e_persisted_report_is_fully_stamped(tmp_path, monkeypatch):
    """HIGH-1: save ran before meta/elapsed stamping, so reloaded
    reports showed meta={} elapsed=0 with no input_sha for rerun
    proof and an unfinished persist audit step."""
    import hashlib

    from atlas.storage.store import get_report

    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "atlas.db"))
    text = "The GDP of the United States was $27.36 trillion in 2023."
    orig = runmod.for_domain
    runmod.for_domain = lambda domain: [  # noqa: E731
        StubWB("GDP (current US$)", "USD", "2023", 27.81e12)]
    try:
        report = analyze(text, llm=None, persist=True)
    finally:
        runmod.for_domain = orig
    loaded = get_report(report.report_id)
    assert loaded is not None
    assert loaded.meta["input_sha256"] == hashlib.sha256(
        text.encode("utf-8")).hexdigest()
    assert loaded.meta["app_version"] == report.meta["app_version"]
    assert loaded.elapsed_ms == report.elapsed_ms > 0
    persist_steps = [s for s in loaded.audit if s.step == "persist"]
    assert len(persist_steps) == 1
    assert persist_steps[0].ended_at is not None
    assert persist_steps[0].duration_ms > 0
