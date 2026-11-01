"""Unit tests: ShipCheck reference Python client (offline, mocked transport)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clients" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402

from shipcheck import ShipCheck, ShipCheckError  # noqa: E402


def _mock(handler):
    return httpx.MockTransport(handler)


def test_analyze_posts_body_and_returns_report():
    seen: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"conclusion": {"stance": "supported"}})

    sc = ShipCheck("http://x", api_key="k", transport=_mock(handle))
    out = sc.analyze("GDP was $5.", limit=3, persist=True)
    assert out["conclusion"]["stance"] == "supported"
    assert seen["body"]["text"] == "GDP was $5."
    assert seen["body"]["limit"] == 3
    assert seen["body"]["persist"] is True
    assert seen["key"] == "k"


def test_error_envelope_raises_typed_error():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {
            "code": "unauthorized", "message": "nope", "request_id": "r"}})

    sc = ShipCheck("http://x", transport=_mock(handle))
    try:
        sc.analyze("hi")
    except ShipCheckError as exc:
        assert exc.code == "unauthorized"
        assert exc.status == 401
    else:
        raise AssertionError("must raise")


def test_non_json_error_still_typed():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    sc = ShipCheck("http://x", transport=_mock(handle))
    try:
        sc.status()
    except ShipCheckError as exc:
        assert exc.code == "http_error" and exc.status == 500
    else:
        raise AssertionError("must raise")


def test_check_stream_yields_events():
    body = (
        'data: {"type": "started", "claim_count": 1}\n\n'
        'data: {"type": "claim", "index": 0, "stance": "supported"}\n\n'
        'data: {"type": "done", "result": {"claim_count": 1}}\n\n'
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/check/stream"
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    sc = ShipCheck("http://x", transport=_mock(handle))
    events = list(sc.check_stream("hi"))
    assert [e["type"] for e in events] == ["started", "claim", "done"]
    assert events[-1]["result"]["claim_count"] == 1


def test_reports_and_delete_paths():
    seen: list = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    sc = ShipCheck("http://x", transport=_mock(handle))
    sc.reports(limit=3)
    sc.report("rep_1")
    sc.delete_report("rep_1")
    assert ("GET", "/api/reports") in seen
    assert ("GET", "/api/reports/rep_1") in seen
    assert ("DELETE", "/api/reports/rep_1") in seen


def test_cors_middleware_registered():
    # UT-n (n1): CORSMiddleware configured on the app.
    import atlas.api.app as appmod

    mw = [m.cls.__name__ for m in appmod.app.user_middleware]
    assert "CORSMiddleware" in mw


def test_reference_client_shipped():
    # UT-n (n2): the reference client file exists.
    path = (Path(__file__).resolve().parents[2] / "clients" / "python"
            / "shipcheck.py")
    assert path.exists()
