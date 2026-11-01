"""M4 tests: replay endpoints + pacing + event contract + UI DOM hooks.

Uses starlette/fastapi TestClient (already in requirements) — no extra
deps, no network, $0 live.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from live.replay.player import (
    DEFAULT_MAX_GAP_S,
    is_safe_run_name,
    iter_schedule,
    list_runs,
    load_events,
    resolve_run,
)
from live.server.app import create_app

RUN = "run-m1e-heuristic"


def _client(**kwargs):
    return TestClient(create_app(**kwargs))


# --- replay listing -------------------------------------------------------


def test_replay_list_contains_real_run():
    client = _client()
    res = client.get("/replay")
    assert res.status_code == 200
    runs = {r["run"]: r for r in res.json()["runs"]}
    assert RUN in runs
    assert runs[RUN]["events"] == 51
    # Partial ASR logs are not replayable runs.
    assert not any(r.endswith(".asr") for r in runs)


def test_replay_get_metadata():
    client = _client()
    res = client.get(f"/replay/{RUN}")
    assert res.status_code == 200
    info = res.json()
    assert info["run"] == RUN
    assert info["events"] == 51
    assert info["types"] == {
        "transcript.final": 11,
        "claim.detected": 20,
        "claim.state": 20,
    }
    assert info["span_s"] == pytest.approx(178.4, abs=0.5)
    assert info["media_end_s"] == pytest.approx(208.94)
    assert info["video"] == "/media/keynote_en.mp4"
    assert info["max_gap_s"] == DEFAULT_MAX_GAP_S


def test_replay_get_rejects_unknown_and_traversal():
    client = _client()
    assert client.get("/replay/does-not-exist").status_code == 404
    assert client.get("/replay/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
    assert resolve_run("../../etc/passwd") is None
    assert resolve_run("") is None
    assert not is_safe_run_name("../x")
    assert is_safe_run_name(RUN)


# --- replay WS stream -----------------------------------------------------


def _drain_replay(client, run=RUN, rate=100000.0):
    with client.websocket_connect(f"/ws?replay={run}&rate={rate}") as ws:
        hello = ws.receive_json()
        frames = [ws.receive_json() for _ in range(52)]  # 51 events + done
    return hello, frames


def test_replay_ws_streams_log_verbatim_in_order():
    client = _client()
    hello, frames = _drain_replay(client)
    assert hello["type"] == "hello"
    assert hello["milestone"] == "m1b"  # existing contract untouched
    assert hello["mode"] == "replay"
    assert hello["replay"] == RUN
    assert hello["events"] == 51

    recorded = load_events(RUN)
    assert recorded is not None and len(recorded) == 51
    body, done = frames[:-1], frames[-1]
    for frame, orig in zip(body, recorded):
        assert frame["replay"] is True
        for key, val in orig.items():
            assert frame[key] == val  # verbatim + tag
    assert [f.get("seq") for f in body] == [e["seq"] for e in recorded]
    assert done["type"] == "replay.done"
    assert done["replay"] is True
    assert done["run"] == RUN
    assert done["events"] == 51


def test_replay_ws_unknown_run_errors_and_closes():
    from starlette.websockets import WebSocketDisconnect

    client = _client()
    with client.websocket_connect("/ws?replay=nope&rate=10") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_replay_ws_bad_rate_errors_and_closes():
    from starlette.websockets import WebSocketDisconnect

    client = _client()
    with client.websocket_connect(f"/ws?replay={RUN}&rate=0") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_live_ws_unchanged_without_replay_param():
    client = _client()
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "milestone": "m1b"}
        ws.send_text("ping")  # client frames ignored, stays open
        ws.send_text("ping")
    assert client.get("/health").json() == {"status": "ok", "milestone": "m1b"}


# --- pacing ---------------------------------------------------------------


def test_schedule_follows_original_bus_ts_gaps_over_rate():
    events = load_events(RUN)
    sched = list(iter_schedule(events, rate=2.0, max_gap_s=1000.0))
    assert len(sched) == 51
    assert sched[0][0] == 0.0  # first frame prompt
    total = 0.0
    for (delay, frame), prev, cur in zip(sched[1:], events[:-1], events[1:]):
        expect = max(0.0, (cur["bus_ts"] - prev["bus_ts"]) / 2.0)
        assert delay == pytest.approx(expect)
        assert frame["replay"] is True
        total += delay
    span = events[-1]["bus_ts"] - events[0]["bus_ts"]
    assert total == pytest.approx(span / 2.0)


def test_schedule_caps_gaps_and_rejects_bad_rate():
    events = load_events(RUN)
    sched = list(iter_schedule(events, rate=1.0, max_gap_s=5.0))
    assert all(d <= 5.0 for d, _ in sched)
    assert any(d == 5.0 for d, _ in sched)  # the 29s idle gap is capped
    with pytest.raises(ValueError):
        list(iter_schedule(events, rate=0))
    with pytest.raises(ValueError):
        list(iter_schedule(events, rate=-3))


def test_schedule_does_not_mutate_recorded_events():
    events = load_events(RUN)
    before = [json.dumps(e, sort_keys=True) for e in events]
    list(iter_schedule(events, rate=4.0))
    assert [json.dumps(e, sort_keys=True) for e in events] == before
    assert all("replay" not in e for e in events)


def test_replay_wallclock_matches_pacing():
    client = _client()
    t0 = time.monotonic()
    _drain_replay(client, rate=100000.0)
    assert time.monotonic() - t0 < 15.0  # 178s of log in seconds at 1e5x


def test_list_runs_reads_runs_dir():
    runs = {r["run"]: r["events"] for r in list_runs()}
    assert runs[RUN] == 51


def test_replay_supports_m2_evidence_verdict_flow(tmp_path, monkeypatch):
    """A log with the full M1+M2 flow replays verbatim (theater feed)."""
    import live.replay.player as player

    monkeypatch.setattr(player, "RUNS_DIR", tmp_path)
    base = 1_700_000_000.0
    log = [
        {"seq": 1, "bus_ts": base, "type": "transcript.final",
         "turn": 1, "text": "España creció un 5 por ciento.", "speaker": "M",
         "t_start": 0.0, "t_end": 3.0},
        {"seq": 2, "bus_ts": base + 0.1, "type": "claim.detected",
         "claim_id": "clm_x", "text": "España creció un 5 por ciento.",
         "resolved": None, "t_start": 0.0, "t_end": 3.0},
        {"seq": 3, "bus_ts": base + 0.11, "type": "claim.state",
         "claim_id": "clm_x", "state": "escuchada", "prev": None,
         "actor": "m1-detector", "note": ""},
        {"seq": 4, "bus_ts": base + 1.0, "type": "claim.state",
         "claim_id": "clm_x", "state": "investigando", "prev": "escuchada",
         "actor": "m2b-swarm", "note": ""},
        {"seq": 5, "bus_ts": base + 2.0, "type": "claim.evidence",
         "claim_id": "clm_x", "packages": [{"package_id": "p1"}],
         "degraded": False, "note": "ok", "cost_usd": 0.0, "usage": {}},
        {"seq": 6, "bus_ts": base + 3.0, "type": "claim.verdict",
         "claim_id": "clm_x", "verdict": "supported",
         "reasons": ["numeric_match"], "package_ids": ["p1"],
         "actor": "m2a-verifier"},
        {"seq": 7, "bus_ts": base + 3.01, "type": "claim.state",
         "claim_id": "clm_x", "state": "veredicto", "prev": "investigando",
         "actor": "m2a-verifier", "note": ""},
        # A second claim left mid-investigation (theater shows it active).
        {"seq": 8, "bus_ts": base + 3.5, "type": "claim.detected",
         "claim_id": "clm_y", "text": "Francia decreció.",
         "resolved": None, "t_start": 3.0, "t_end": 5.0},
        {"seq": 9, "bus_ts": base + 3.6, "type": "claim.state",
         "claim_id": "clm_y", "state": "escuchada", "prev": None,
         "actor": "m1-detector", "note": ""},
        {"seq": 10, "bus_ts": base + 4.0, "type": "claim.state",
         "claim_id": "clm_y", "state": "investigando", "prev": "escuchada",
         "actor": "m2b-swarm", "note": ""},
    ]
    (tmp_path / "run-m2demo.jsonl").write_text(
        "\n".join(json.dumps(e) for e in log) + "\n", encoding="utf-8"
    )
    client = _client()
    # Shipped committed logs also list (clean-clone fallback); the local
    # run must be present with its tmp path.
    runs = {r["run"]: r for r in client.get("/replay").json()["runs"]}
    assert runs["run-m2demo"] == {
        "run": "run-m2demo", "events": 10,
        "path": str(tmp_path / "run-m2demo.jsonl"),
    }
    info = client.get("/replay/run-m2demo").json()
    assert info["types"]["claim.evidence"] == 1
    assert info["types"]["claim.verdict"] == 1
    with client.websocket_connect("/ws?replay=run-m2demo&rate=1000") as ws:
        assert ws.receive_json()["events"] == 10
        frames = [ws.receive_json() for _ in range(11)]
    assert frames[-1]["type"] == "replay.done"
    assert [f["type"] for f in frames[:-1]] == [e["type"] for e in log]
    assert all(f["replay"] is True for f in frames)
    verdict = next(f for f in frames if f["type"] == "claim.verdict")
    assert verdict["verdict"] == "supported"


# --- event contract (replay frames) ----------------------------------------


def test_replay_frames_satisfy_ui_contract():
    client = _client()
    _, frames = _drain_replay(client)
    body = frames[:-1]
    for f in body:
        assert "type" in f and "seq" in f and "bus_ts" in f
        assert f["replay"] is True
        json.dumps(f)  # JSON-serializable
    finals = [f for f in body if f["type"] == "transcript.final"]
    detected = [f for f in body if f["type"] == "claim.detected"]
    states = [f for f in body if f["type"] == "claim.state"]
    assert len(finals) == 11 and len(detected) == 20 and len(states) == 20
    for f in finals:
        assert {"turn", "text", "speaker", "t_start", "t_end"} <= set(f)
    for f in detected:
        assert {"claim_id", "text", "resolved", "t_start", "t_end"} <= set(f)
    for f in states:
        assert {"claim_id", "state", "prev", "actor"} <= set(f)
        assert f["state"] in ("escuchada", "investigando", "veredicto")
    ids = {f["claim_id"] for f in detected}
    assert {s["claim_id"] for s in states} <= ids


# --- media -----------------------------------------------------------------


def test_media_serves_keynote_with_ranges_and_allowlist():
    client = _client()
    res = client.get("/media/keynote_en.mp4", headers={"Range": "bytes=0-99"})
    assert res.status_code == 206
    assert res.headers["content-type"] == "video/mp4"
    assert len(res.content) == 100
    assert client.get("/media/evil.mp4").status_code == 404
    assert client.get("/media/..%2F..%2Fx").status_code == 404


# --- UI DOM (no-browser verification; Playwright not installed) ------------


def _page():
    return _client().get("/").text


def test_index_keeps_existing_contract_hooks():
    html = _page()
    for hook in ('id="player"', 'id="subs"', 'id="claims"', 'id="conn"',
                 'id="demoRun"', 'id="demoClear"', 'id="langEs"', 'id="langEn"',
                 "data-es=", "data-en="):
        assert hook in html


def test_index_has_m4_newsroom_hooks():
    html = _page()
    for hook in ('id="modeBanner"', 'id="modeTitle"', 'id="subOverlay"',
                 'id="theater"', 'id="timeline"', 'id="replaySel"',
                 'id="rateSel"', 'id="replayGo"', 'id="liveGo"',
                 'id="replayStatus"', "REPLAY DE CORRIDA REAL",
                 "claim.evidence", "claim.verdict"):
        assert hook in html


def test_index_single_file_no_framework_and_mobile_css():
    html = _page()
    assert "<script src=" not in html and "@import" not in html
    for marker in ("react", "vue.js", "angular", "jquery"):
        assert marker not in html.lower()
    assert "@media(max-width:860px)" in html
    assert "@media(max-width:520px)" in html
    assert html.count("<script>") == 1 == html.count("</script>")


def test_index_replay_wiring_in_script():
    html = _page()
    script = html.split("<script>")[1]
    for snippet in ("?replay=", "encodeURIComponent(REPLAY)", "/replay/",
                    "player.src", "currentTime", "scrollIntoView",
                    "REPLAY DE CORRIDA REAL", "REAL-RUN REPLAY"):
        assert snippet in script


def test_shipped_replay_fallback_clean_clone(tmp_path, monkeypatch):
    """Fresh clone (empty RUNS_DIR) still replays committed logs."""
    from live.replay import player
    empty = tmp_path / "noruns"
    empty.mkdir()
    monkeypatch.setattr(player, "RUNS_DIR", empty)
    names = {r["run"] for r in player.list_runs()}
    assert "run-keynote-en-listen" in names
    assert "verify-keynote-en-investigate" in names
    evs = player.load_events("verify-keynote-en-investigate")
    assert evs and any(e.get("type") == "claim.verdict" for e in evs)


def test_runs_dir_wins_over_shipped(tmp_path, monkeypatch):
    from live.replay import player
    d = tmp_path / "runs"
    d.mkdir()
    (d / "run-keynote-en-listen.jsonl").write_text(
        '{"type": "transcript.final", "bus_ts": 1.0, "text": "local"}\n')
    monkeypatch.setattr(player, "RUNS_DIR", d)
    got = player.resolve_run("run-keynote-en-listen")
    assert got is not None and str(got).startswith(str(d))
