"""Server tests (M1b). TestClient HTTP + WS against 1 recorded transcript."""

from fastapi.testclient import TestClient

from live.server.app import create_app, latency_summary, percentile

# Recorded fixture #1 (stand-in until M1c delivers E2E material):
# 4 turns, ES keynote-style, 2 checkable stats + 1 repeat + filler.
RECORDED_TRANSCRIPT = [
    {
        "turn": 1,
        "speaker": "Anfitriona",
        "text": "Buenas noches, bienvenidos al debate económico.",
        "t_start": 0.0,
        "t_end": 3.2,
    },
    {
        "turn": 2,
        "speaker": "Ministra",
        "text": "España cerró el año con un crecimiento histórico.",
        "t_start": 3.2,
        "t_end": 7.1,
    },
    {
        "turn": 3,
        "speaker": "Ministra",
        "text": "Su PIB creció un 5,2 por ciento en 2023.",
        "t_start": 7.1,
        "t_end": 11.0,
    },
    {
        "turn": 4,
        "speaker": "Ministra",
        "text": "El desempleo bajó al 11,8 por ciento el mismo año.",
        "t_start": 11.0,
        "t_end": 15.4,
    },
]


def _client(**kwargs):
    return TestClient(create_app(**kwargs))


def test_health_and_index():
    client = _client()
    assert client.get("/health").json() == {"status": "ok", "milestone": "m1b"}
    page = client.get("/")
    assert page.status_code == 200
    assert "Atlas Live" in page.text
    assert 'id="player"' in page.text  # video slot for the M1c player


def test_run_replays_transcript_through_detector():
    client = _client()
    res = client.post("/run", json={"transcript": RECORDED_TRANSCRIPT})
    assert res.status_code == 200
    body = res.json()
    assert body["turns"] == 4
    # Heuristic path: turn 3 (number+year) and turn 4 (number) detected;
    # turn 1 greeting dropped; turn 2 has indicator words... assert >= 2
    # and exact contract keys rather than overfitting the heuristic.
    assert body["claims"] >= 2
    assert set(body["latency_ms"]) == {"count", "p50_ms", "p95_ms"}
    assert body["latency_ms"]["count"] == body["claims"]


def test_run_rejects_bad_payloads():
    client = _client()
    assert client.post("/run", json={}).status_code == 400
    assert client.post("/run", json={"transcript_path": "/no/such.jsonl"}).status_code == 400
    big = {"transcript": [{"text": "x"}] * 501}
    assert client.post("/run", json=big).status_code == 400


def test_run_accepts_jsonl_path(tmp_path):
    import json as _json

    p = tmp_path / "t.jsonl"
    p.write_text(
        "\n".join(_json.dumps(t) for t in RECORDED_TRANSCRIPT[:2])
        + "\nnot-json\n",
        encoding="utf-8",
    )
    client = _client()
    res = client.post("/run", json={"transcript_path": str(p)})
    assert res.status_code == 200
    assert res.json()["turns"] == 2  # bad line skipped, not fatal


def test_ws_receives_contract_events_in_order():
    client = _client()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        res = client.post("/run", json={"transcript": RECORDED_TRANSCRIPT})
        assert res.status_code == 200
        n_claims = res.json()["claims"]
        # 4 transcript.final + n*(claim.detected + claim.state)
        frames = [ws.receive_json() for _ in range(4 + 2 * n_claims)]
    types = [f["type"] for f in frames]
    assert types.count("transcript.final") == 4
    assert types.count("claim.detected") == n_claims
    states = [f for f in frames if f["type"] == "claim.state"]
    assert all(s["state"] == "escuchada" for s in states)
    detected = [f for f in frames if f["type"] == "claim.detected"]
    assert all(
        {"claim_id", "text", "resolved", "t_start", "t_end"} <= set(f)
        for f in detected
    )
    # claim.state references real detected ids; every frame enveloped.
    ids = {f["claim_id"] for f in detected}
    assert {s["claim_id"] for s in states} <= ids
    assert all("seq" in f and "bus_ts" in f for f in frames)


def test_metrics_reports_latency():
    client = _client()
    client.post("/run", json={"transcript": RECORDED_TRANSCRIPT})
    m = client.get("/metrics").json()
    assert m["events"] > 0
    assert m["latency_ms"]["count"] >= 2
    assert m["latency_ms"]["p50_ms"] <= m["latency_ms"]["p95_ms"]


def test_percentile_helper():
    assert percentile([], 50) is None
    assert percentile([10.0], 95) == 10.0
    assert latency_summary([1.0, 2.0, 3.0, 4.0])["count"] == 4
