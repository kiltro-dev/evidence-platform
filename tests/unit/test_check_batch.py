"""Unit tests: check_document batch (roll-up + error isolation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest  # noqa: E402

import atlas.pipeline.run as runmod  # noqa: E402
from atlas.core.types import Conclusion, Report, StructuredClaim  # noqa: E402
from atlas.pipeline.run import check_document  # noqa: E402


def _report(text: str, stance: str) -> Report:
    return Report(
        claim=StructuredClaim(raw_text=text),
        conclusion=Conclusion(stance=stance, summary="stub"),
    )


@pytest.fixture()
def fake_pipeline(monkeypatch):
    calls: list[str] = []

    def fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        calls.append(text)
        if "BOOM" in text:
            raise RuntimeError("sub-claim exploded")
        stance = "supported" if "good" in text else "contradicted"
        return _report(text, stance)

    monkeypatch.setattr(runmod, "analyze", fake)
    return calls


def test_rollup_counts_stances(fake_pipeline):
    out = check_document("This is good news today. This is bad news today.", persist=False)
    assert out["claim_count"] == 2
    assert out["rollup"]["supported"] == 1
    assert out["rollup"]["contradicted"] == 1
    assert out["errors"] == 0
    assert len(out["reports"]) == 2
    assert out["reports"][0]["claim"]["raw_text"].startswith("This is good")
    assert set(out["rollup"]) == {"supported", "partially_supported", "contradicted",
                                  "unverifiable", "not_assessable"}
    assert out["meta"]["splitter_actor"] == "heuristic"
    assert out["meta"]["app_version"] == runmod.APP_VERSION
    assert out["meta"]["usage_split"] is None  # degraded: no model call
    assert len(out["meta"]["input_sha256"]) == 64


def test_one_failure_does_not_kill_batch(fake_pipeline):
    out = check_document("This is good news today. BOOM goes wrong here.", persist=False)
    assert out["claim_count"] == 2
    assert out["errors"] == 1
    assert out["rollup"]["supported"] == 1
    assert out["reports"][1]["error"] == "sub-claim exploded"
    assert "claim_text" in out["reports"][1]


def test_single_claim_passthrough(fake_pipeline):
    out = check_document("This is good news today.", persist=False)
    assert out["claim_count"] == 1
    assert out["rollup"]["supported"] == 1
    assert fake_pipeline == ["This is good news today."]


def test_invalid_domain_rejected(fake_pipeline):
    with pytest.raises(ValueError):
        check_document("This is good news today.", domain="nope")
    assert fake_pipeline == []


def test_reports_are_json_serializable(fake_pipeline):
    import json

    out = check_document("This is good news today. This is bad news today.", persist=False)
    json.dumps(out)  # must not raise
