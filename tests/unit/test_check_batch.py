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


def test_batch_runs_subclaims_in_parallel(monkeypatch):
    """3 sub-claims x 0.5s sleep: sequential takes >=1.5s, parallel ~0.5s.
    The 1.2s bar has 3x headroom over parallel and never passes sequential
    (sleep releases the GIL, so threads genuinely overlap)."""
    import time as _time

    def slow(text, **kw):
        _time.sleep(0.5)
        return _report(text, "supported")

    monkeypatch.setattr(runmod, "analyze", slow)
    out = check_document(
        "First slow claim filed today. Second slow claim filed today. "
        "Third slow claim filed today.",
        persist=False,
    )
    assert out["claim_count"] == 3
    assert out["errors"] == 0
    assert out["elapsed_ms"] < 1200, out["elapsed_ms"]
    # Order still follows the input, however threads finish.
    assert [r["claim"]["raw_text"] for r in out["reports"]] == [
        "First slow claim filed today.",
        "Second slow claim filed today.",
        "Third slow claim filed today.",
    ]


def test_fork_llm_clones_metaclient_only():
    """MetaClient (mutable usage state) is forked per sub-claim; test
    doubles (stateless) are shared."""
    from atlas.models.meta import MetaClient
    from atlas.models.scripted import ScriptedClient
    from atlas.pipeline.run import _fork_llm

    real = MetaClient(api_key="k", base_url="https://x.test", model="m")
    fork = _fork_llm(real)
    assert fork is not real
    assert (fork.api_key, fork.base_url, fork.model) == ("k", "https://x.test", "m")
    assert fork.last_usage_seq == 0  # fresh usage state
    double = ScriptedClient({})
    assert _fork_llm(double) is double
    assert _fork_llm(None) is None


def test_reports_are_json_serializable(fake_pipeline):
    import json

    out = check_document("This is good news today. This is bad news today.", persist=False)
    json.dumps(out)  # must not raise


def test_batch_reports_carry_split_context(fake_pipeline):
    # UT-g wiring: each sub-report carries its resolved context apart.
    out = check_document(
        "France's population is good today. Its GDP is good today.",
        persist=False)
    assert out["claim_count"] == 2
    assert out["reports"][1]["split_context"]["country_iso3"] == "FRA"
    assert out["reports"][1]["split_context"]["country_source"] == "inherited"


def test_check_document_accepts_deadline():
    # UT-l (l2): global deadline parameter exists.
    import inspect

    from atlas.pipeline.run import check_document

    assert "deadline_s" in inspect.signature(check_document).parameters


def test_deadline_zero_aborts_gracefully(fake_pipeline):
    # UT-l: expired deadline -> error entries + flag, never a crash.
    from atlas.pipeline.run import check_document

    out = check_document("This is good news today. This is bad news today.",
                         persist=False, deadline_s=0)
    assert out["errors"] == 2
    assert out["meta"]["deadline_exceeded"] is True
    assert all(r.get("error_code") == "deadline_exceeded"
               for r in out["reports"])
    assert out["rollup"].get("supported", 0) == 0


def test_no_deadline_completes(fake_pipeline):
    from atlas.pipeline.run import check_document

    out = check_document("This is good news today. This is bad news today.",
                         persist=False)
    assert out["errors"] == 0
    assert out["meta"]["deadline_exceeded"] is False


def test_on_event_sequence(fake_pipeline):
    from atlas.pipeline.run import check_document

    events: list[dict] = []
    check_document("This is good news today. This is bad news today.",
                   persist=False, on_event=events.append)
    assert events[0]["type"] == "started"
    assert events[0]["claim_count"] == 2
    claims = [e for e in events if e["type"] == "claim"]
    assert sorted(e["index"] for e in claims) == [0, 1]
    assert all(e["total"] == 2 for e in claims)


def test_fragile_on_event_does_not_kill_batch(fake_pipeline):
    calls = []

    def flaky(event):
        calls.append(event)
        if len(calls) == 2:  # started ok, first claim event explodes
            raise ConnectionError("dead SSE client")

    out = check_document(
        "This is good news today. This is bad news today.",
        persist=False, on_event=flaky,
    )
    assert out["claim_count"] == 2
    assert out["errors"] == 0
    assert out["rollup"]["supported"] == 1
    assert out["rollup"]["contradicted"] == 1
    assert len(calls) == 3  # started + 2 claims attempted
