"""Offline e2e: POST /api/check (ShipCheck batch contract).

Real check_document + heuristic split; only run.analyze is faked so no
model, connector, or network is touched; storage points at a tmp db.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import atlas.api.app as appmod  # noqa: E402
import atlas.pipeline.run as runmod  # noqa: E402
from atlas.core.types import Conclusion, Report, StructuredClaim  # noqa: E402


def make_report(text: str, stance: str = "unverifiable") -> Report:
    return Report(
        claim=StructuredClaim(raw_text=text),
        conclusion=Conclusion(stance=stance, summary="stub"),
    )


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    calls: list[dict] = []

    def fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        calls.append({"text": text, "limit": limit,
                      "persist": persist, "domain": domain})
        return make_report(text, "supported" if "good" in text else "unverifiable")

    monkeypatch.setattr(runmod, "analyze", fake)
    with TestClient(appmod.app, raise_server_exceptions=False) as c:
        yield c, calls


def test_check_happy_path(ctx):
    client, calls = ctx
    resp = client.post("/api/check", json={
        "text": "This is good news today. Something else happened too."})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_count"] == 2
    assert body["rollup"]["supported"] == 1
    assert body["rollup"]["unverifiable"] == 1
    assert body["errors"] == 0
    assert len(body["reports"]) == 2
    assert body["meta"]["splitter_actor"] == "heuristic"
    assert len(calls) == 2
    assert calls[0]["persist"] is True and calls[0]["domain"] == "auto"


def test_check_empty_rejected(ctx):
    client, _ = ctx
    resp = client.post("/api/check", json={"text": "   "})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "empty_claim"


def test_check_too_long_rejected(ctx):
    client, calls = ctx
    resp = client.post("/api/check", json={"text": "z" * 12001})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "claim_too_long"
    assert calls == []


def test_check_invalid_domain_rejected(ctx):
    client, calls = ctx
    resp = client.post("/api/check",
                       json={"text": "Some claim here today.", "domain": "nope"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_domain"
    assert calls == []


def test_check_limit_clamped(ctx):
    client, calls = ctx
    assert client.post("/api/check",
                       json={"text": "Some claim here today.", "limit": 999}
                       ).status_code == 200
    assert calls[0]["limit"] == 10


def test_check_no_cache_reruns(ctx):
    client, calls = ctx
    payload = {"text": "Some claim here today."}
    assert client.post("/api/check", json=payload).status_code == 200
    assert client.post("/api/check", json=payload).status_code == 200
    assert len(calls) == 2  # documented: each call re-runs


def test_check_costs_eight_rate_hits(ctx):
    # Red-team R4: a batch (up to 41 model calls) must not cost 1 quota unit.
    client, _ = ctx
    for i in range(7):
        resp = client.post("/api/check", json={"text": f"Batch claim {i} filed today."})
        assert resp.status_code == 200, (i, resp.text)
    resp = client.post("/api/check", json={"text": "One batch too many today."})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


def test_invalid_check_requests_do_not_burn_batch_quota(ctx):
    # Red-team R4: an invalid batch must not burn 8 quota units.
    client, _ = ctx
    assert client.post("/api/check", json={"text": "   "}).status_code == 400
    assert client.post(
        "/api/check",
        json={"text": "valid batch claim", "domain": "nope"}).status_code == 400
    assert appmod._rate_hits.get("testclient", []) == []
