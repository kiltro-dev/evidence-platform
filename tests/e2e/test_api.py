"""Offline API e2e tests via TestClient (PRD §4 contract).

The pipeline (run.analyze) is monkeypatched with a counting fake so no
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
from atlas.storage.store import save_report  # noqa: E402


def make_report(text: str, domain: str = "general") -> Report:
    return Report(
        claim=StructuredClaim(raw_text=text, domain=domain),
        conclusion=Conclusion(stance="unverifiable", summary="stub"),
    )


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """Isolated client + counting fake pipeline. Returns (client, calls)."""
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    calls: list[dict] = []

    def fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        calls.append(
            {"text": text, "llm": llm, "limit": limit,
             "persist": persist, "domain": domain}
        )
        return make_report(text)

    monkeypatch.setattr(runmod, "analyze", fake)
    with TestClient(appmod.app, raise_server_exceptions=False) as c:
        yield c, calls


def test_analyze_happy_path(ctx):
    client, calls = ctx
    resp = client.post("/api/analyze", json={"text": "GDP grew 3% in 2023."})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim"]["raw_text"] == "GDP grew 3% in 2023."
    assert body["report_id"].startswith("rep_")
    assert "error" not in body and "request_id" not in body
    assert len(calls) == 1
    assert calls[0]["limit"] == 5
    assert calls[0]["persist"] is True
    assert calls[0]["domain"] == "auto"


def test_analyze_exact_4000_chars_accepted(ctx):
    client, calls = ctx
    resp = client.post("/api/analyze", json={"text": "y" * 4000})
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1


def test_analyze_limit_clamped_not_rejected(ctx):
    client, calls = ctx
    assert client.post(
        "/api/analyze", json={"text": "claim one", "limit": 999}).status_code == 200
    assert client.post(
        "/api/analyze", json={"text": "claim two", "limit": 0}).status_code == 200
    assert client.post(
        "/api/analyze", json={"text": "claim three", "limit": -3}).status_code == 200
    assert [c["limit"] for c in calls] == [10, 1, 1]


def test_analyze_domain_and_persist_passed_through(ctx):
    client, calls = ctx
    resp = client.post(
        "/api/analyze",
        json={"text": "Unemployment fell.", "domain": "economy", "persist": False},
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["domain"] == "economy"
    assert calls[0]["persist"] is False


def test_reports_list_detail_round_trip(ctx):
    client, _ = ctx
    r1 = save_report(make_report("first claim"))
    r2 = save_report(make_report("second claim"))
    listing = client.get("/api/reports").json()["reports"]
    assert {r["report_id"] for r in listing} == {r1, r2}
    assert set(listing[0].keys()) == {
        "report_id", "created_at", "claim_text", "stance"}
    detail = client.get(f"/api/reports/{r1}")
    assert detail.status_code == 200
    assert detail.json()["claim"]["raw_text"] == "first claim"


def test_status_shape(ctx):
    client, _ = ctx
    body = client.get("/api/status").json()
    assert body["service"] == "evidence-atlas"
    assert body["version"] == runmod.APP_VERSION
    assert body["llm_available"] is False
    assert body["llm_model"] is None
    assert body["mode"] == "degraded (deterministic only)"
    assert body["connectors"] == ["worldbank", "openalex"]


def test_delete_round_trip(ctx):
    client, _ = ctx
    rid = save_report(make_report("doomed claim"))
    resp = client.delete(f"/api/reports/{rid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True}
    assert client.get(f"/api/reports/{rid}").status_code == 404
    gone = client.delete(f"/api/reports/{rid}")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "report_not_found"


def test_rate_limit_429_envelope(ctx):
    client, _ = ctx
    body = {"text": "same cached claim"}
    for _ in range(60):
        assert client.post("/api/analyze", json=body).status_code == 200
    resp = client.post("/api/analyze", json=body)
    assert resp.status_code == 429, resp.text
    err = resp.json()["error"]
    assert err["code"] == "rate_limited"
    assert "request_id" in err


def test_identical_text_cache_skips_analyze(ctx):
    client, calls = ctx
    first = client.post("/api/analyze", json={"text": "cached claim"})
    second = client.post("/api/analyze", json={"text": "cached claim"})
    assert first.status_code == second.status_code == 200
    assert len(calls) == 1  # second request served from cache
    assert first.json()["report_id"] == second.json()["report_id"]
    # Different domain is a different cache key -> pipeline runs again.
    third = client.post(
        "/api/analyze", json={"text": "cached claim", "domain": "economy"})
    assert third.status_code == 200
    assert len(calls) == 2


def test_cache_key_includes_persist_flag(ctx):
    # A cached persist=false report must never satisfy persist=true:
    # the report_id would 404 on re-fetch (nothing was written).
    client, calls = ctx
    no_save = client.post(
        "/api/analyze", json={"text": "persist split", "persist": False})
    do_save = client.post(
        "/api/analyze", json={"text": "persist split", "persist": True})
    assert no_save.status_code == do_save.status_code == 200
    assert len(calls) == 2  # cache miss across persist values
    assert no_save.json()["report_id"] != do_save.json()["report_id"]
    # Same persist value still hits the cache.
    again = client.post(
        "/api/analyze", json={"text": "persist split", "persist": True})
    assert len(calls) == 2
    assert again.json()["report_id"] == do_save.json()["report_id"]


def test_method_not_allowed_uses_neutral_code(ctx):
    client, _ = ctx
    resp = client.put("/api/analyze", json={"text": "x"})
    assert resp.status_code == 405
    err = resp.json()["error"]
    assert err["code"] == "bad_request"
    assert err["request_id"]


def test_client_allowed_loopback_only(monkeypatch):
    monkeypatch.delenv("ATLAS_ALLOW_NON_LOOPBACK", raising=False)
    assert appmod._client_allowed("127.0.0.1") is True
    assert appmod._client_allowed("::1") is True
    assert appmod._client_allowed("localhost") is True
    assert appmod._client_allowed("testclient") is True
    assert appmod._client_allowed("192.168.1.10") is False
    assert appmod._client_allowed(None) is False
    assert appmod._client_allowed("") is False


def test_client_allowed_override(monkeypatch):
    monkeypatch.setenv("ATLAS_ALLOW_NON_LOOPBACK", "1")
    assert appmod._client_allowed("192.168.1.10") is True
    assert appmod._client_allowed(None) is True


def test_delete_evicts_cached_report(ctx):
    # Red-team R5: DELETE-then-rePOST must not resurrect the deleted id.
    import os
    from pathlib import Path

    client, _ = ctx
    db = Path(os.environ["ATLAS_DB"])
    rep = make_report("Cache me once.")
    save_report(rep, path=db)
    appmod._cache_put(("Cache me once.", "auto", 5, True, None), rep)
    assert client.delete(f"/api/reports/{rep.report_id}").status_code == 200
    r2 = client.post("/api/analyze", json={"text": "Cache me once."})
    assert r2.status_code == 200, r2.text
    assert r2.json()["report_id"] != rep.report_id


def test_fresh_bypass_reruns_and_refreshes(ctx):
    # Red-team F10 backend: fresh=true skips the read, still refreshes.
    client, calls = ctx
    r1 = client.post("/api/analyze", json={"text": "Fresh claim here."})
    assert r1.status_code == 200
    id1 = r1.json()["report_id"]
    assert len(calls) == 1
    r2 = client.post("/api/analyze",
                     json={"text": "Fresh claim here.", "fresh": True})
    assert r2.status_code == 200, r2.text
    id2 = r2.json()["report_id"]
    assert id2 != id1
    assert len(calls) == 2
    r3 = client.post("/api/analyze", json={"text": "Fresh claim here."})
    assert r3.json()["report_id"] == id2  # cache refreshed by the re-run
    assert len(calls) == 2


def test_invalid_analyze_requests_do_not_consume_quota(ctx):
    # Red-team R4: validation precedes the charge — 400s are free.
    client, _ = ctx
    assert client.post("/api/analyze", json={"text": "   "}).status_code == 400
    assert client.post(
        "/api/analyze", json={"text": "x" * 4001}).status_code == 400
    assert client.post(
        "/api/analyze",
        json={"text": "valid claim here", "domain": "nope"}).status_code == 400
    assert appmod._rate_hits.get("testclient", []) == []
