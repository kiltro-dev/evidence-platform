"""Unit tests for the unified API error envelope (PRD §4).

Every 4xx/5xx must be ``{"error": {"code", "message", "request_id"}}`` with
``request_id`` a uuid4 hex string. Fully offline: the pipeline is never
invoked (or monkeypatched to raise), and storage points at a tmp db.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import atlas.api.app as appmod  # noqa: E402
import atlas.pipeline.run as runmod  # noqa: E402

HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    with TestClient(appmod.app, raise_server_exceptions=False) as c:
        yield c


def assert_envelope(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body.keys()) == {"error"}, body
    err = body["error"]
    assert set(err.keys()) == {"code", "message", "request_id"}, err
    assert err["code"] == code, err
    assert isinstance(err["message"], str) and err["message"], err
    assert HEX32.match(err["request_id"]), err
    return err


def test_empty_claim_400(client):
    assert_envelope(client.post("/api/analyze", json={"text": "   "}), 400, "empty_claim")


def test_missing_text_422_normalized(client):
    # 422 detail arrives list-shaped from FastAPI; the envelope message
    # must be a single string.
    err = assert_envelope(client.post("/api/analyze", json={}), 422, "empty_claim")
    assert "text" in err["message"]


def test_oversize_claim_400(client):
    assert_envelope(
        client.post("/api/analyze", json={"text": "x" * 4001}), 400, "claim_too_long"
    )


def test_invalid_domain_400(client):
    assert_envelope(
        client.post("/api/analyze", json={"text": "GDP grew.", "domain": "space"}),
        400,
        "invalid_domain",
    )


def test_invalid_limit_type_422(client):
    err = assert_envelope(
        client.post("/api/analyze", json={"text": "GDP grew.", "limit": "many"}),
        422,
        "invalid_limit",
    )
    assert "limit" in err["message"]


def test_unknown_report_id_404(client):
    assert_envelope(client.get("/api/reports/rep_nope"), 404, "report_not_found")


def test_delete_unknown_report_id_404(client):
    assert_envelope(
        client.delete("/api/reports/rep_nope"), 404, "report_not_found"
    )


def test_analysis_exception_maps_to_500_without_traceback(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connector exploded")

    monkeypatch.setattr(runmod, "analyze", boom)
    resp = client.post("/api/analyze", json={"text": "GDP grew 3% in 2023."})
    err = assert_envelope(resp, 500, "analysis_failed")
    assert "Traceback" not in resp.text
    assert "exploded" not in resp.text
    assert err["message"] == "Analysis failed."


def test_request_ids_unique_per_error(client):
    a = client.post("/api/analyze", json={"text": ""}).json()["error"]["request_id"]
    b = client.post("/api/analyze", json={"text": ""}).json()["error"]["request_id"]
    assert a != b


def test_rate_limit_loopback_variants_share_one_bucket():
    # Sec LOW (xff-rate): 127.0.0.1/::1/localhost/testclient resolve to the
    # same localhost caller, so XFF spellings must not multiply the quota.
    # UT-j: buckets are "ip:<addr>"/"key:<hash>"; only ip-loopbacks collapse.
    appmod._rate_hits.clear()
    for _ in range(60):
        appmod._check_rate_limit_locked("ip:127.0.0.1", 1)
    with pytest.raises(appmod.ApiError):
        appmod._check_rate_limit_locked("ip:localhost", 1)
    with pytest.raises(appmod.ApiError):
        appmod._check_rate_limit_locked("ip:::1", 1)
    # A genuinely different caller keeps its own bucket.
    appmod._check_rate_limit_locked("ip:203.0.113.9", 1)
    # Key buckets never collapse, even loopback-shaped ones.
    appmod._check_rate_limit_locked("key:abc", 1)
    appmod._check_rate_limit_locked("key:def", 1)
    assert "key:abc" in appmod._rate_hits and "key:def" in appmod._rate_hits


def test_openapi_doc_matches_served_schema(client):
    # Contract LOW (openapi-bytes): the checked-in schema must describe
    # the served app. Parsed comparison (not bytes): formatting drift is
    # not a contract break, schema drift is.
    import json

    doc_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert client.get("/openapi.json").json() == json.loads(doc_path.read_text())


def test_persist_defaults_to_false():
    # UT-j (j1): public default must not archive visitor texts.
    assert appmod.AnalyzeRequest.model_fields["persist"].default is False
    assert appmod.CheckRequest.model_fields["persist"].default is False


def test_require_api_key_exists():
    # UT-j (j2): auth dependency exists.
    assert hasattr(appmod, "require_api_key")


def _auth_client(tmp_path, monkeypatch, key="k-secret"):
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ATLAS_API_KEY", key)
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_auth_required_when_key_configured(tmp_path, monkeypatch):
    c = _auth_client(tmp_path, monkeypatch)
    with c:
        r = c.post("/api/analyze", json={"text": "GDP was $5 in 2023."})
        assert_envelope(r, 401, "unauthorized")
        r = c.post("/api/analyze", json={"text": "GDP was $5 in 2023."},
                   headers={"X-API-Key": "wrong"})
        assert_envelope(r, 401, "unauthorized")
        r = c.post("/api/analyze", json={"text": "GDP was $5 in 2023."},
                   headers={"X-API-Key": "k-secret"})
        assert r.status_code == 200, r.text
        r = c.get("/api/reports")
        assert_envelope(r, 401, "unauthorized")
        r = c.get("/api/reports", headers={"X-API-Key": "k-secret"})
        assert r.status_code == 200, r.text


def test_auth_open_without_key(client):
    # Localhost mode unchanged: no key configured -> no 401s.
    r = client.post("/api/analyze", json={"text": "GDP was $5 in 2023."})
    assert r.status_code == 200, r.text


def test_rate_limit_per_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "RATE_LIMIT_PER_MINUTE", 2)
    c = _auth_client(tmp_path, monkeypatch)
    with c:
        h1 = {"X-API-Key": "k-secret"}
        assert c.post("/api/analyze", json={"text": "one"},
                      headers=h1).status_code == 200
        assert c.post("/api/analyze", json={"text": "two"},
                      headers=h1).status_code == 200
        r = c.post("/api/analyze", json={"text": "three"}, headers=h1)
        assert_envelope(r, 429, "rate_limited")


def test_request_ip_trust_proxy():
    from fastapi import Request

    def _req(headers=None, host="9.9.9.9"):
        scope = {"type": "http", "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ], "client": (host, 123)}
        return Request(scope)

    assert appmod._request_ip(_req()) == "9.9.9.9"
    # XFF ignored unless explicitly trusted.
    r = _req({"x-forwarded-for": "1.2.3.4"})
    assert appmod._request_ip(r) == "9.9.9.9"


def test_check_cache_only_without_persist(tmp_path, monkeypatch):
    from atlas.core.types import Conclusion, Report, StructuredClaim

    calls: list[str] = []

    def fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        calls.append(text)
        return {"claim_count": 1, "echo": text}

    monkeypatch.setattr(runmod, "check_document", fake)
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._check_cache.clear()
    with TestClient(appmod.app, raise_server_exceptions=False) as c:
        c.post("/api/check", json={"text": "cached me", "persist": False})
        c.post("/api/check", json={"text": "cached me", "persist": False})
        assert calls == ["cached me"]
        c.post("/api/check", json={"text": "fresh me", "persist": True})
        c.post("/api/check", json={"text": "fresh me", "persist": True})
        assert calls == ["cached me", "fresh me", "fresh me"]
