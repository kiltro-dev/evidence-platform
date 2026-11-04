"""V6-2 link-live completion: labels, duration, replay, media, report.

Fully offline (doubles + tmp dirs + shipped replay logs).
"""

import json

import pytest
from fastapi.testclient import TestClient

import live.server.app as appmod
from live.ingest import turbo as T
from live.ingest.simstream import SOURCE_LABEL


def _client():
    return TestClient(appmod.create_app())


# ---------------------------------------------------------------------------
# run labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode,source,want", [
    ("vivo", "link-hls", "Vivo por enlace"),
    ("turbo", "link-mp4", "Turbo por enlace"),
    ("vivo", "link-audio", "Vivo por enlace"),
    ("vivo", SOURCE_LABEL, "Directo simulado"),
    ("turbo", SOURCE_LABEL, "Turbo simulado"),
    ("vivo", "live-microphone", "Micrófono en vivo"),
    ("vivo", "file", "Archivo en tiempo real"),
    ("turbo", "file", "Turbo de archivo"),
])
def test_run_label(mode, source, want):
    assert T.run_label(mode, source) == want


def test_mode_event_carries_label_and_source():
    ev = T.mode_event("vivo", "link-hls")
    assert ev["type"] == "run.mode" and ev["source"] == "link-hls"
    assert ev["label"] == "Vivo por enlace"


def test_fifostream_source_default_and_custom():
    from live.run_live import FifoStream

    assert FifoStream("v.mp4").source_label == SOURCE_LABEL
    assert FifoStream("v.mp4", source_label="link-hls").source_label == \
        "link-hls"


# ---------------------------------------------------------------------------
# /ingest duration + source passthrough
# ---------------------------------------------------------------------------

def test_ingest_rejects_bad_duration():
    c = _client()
    r = c.post("/ingest", json={"url": "https://cdn.example.com/a.mp4",
                                "mode": "vivo", "launch": False,
                                "duration": "soon"})
    assert r.status_code == 400
    r = c.post("/ingest", json={"url": "https://cdn.example.com/a.mp4",
                                "mode": "vivo", "launch": False,
                                "duration": 99999})
    assert r.status_code == 400


def test_link_pipeline_passes_duration_and_source(monkeypatch, tmp_path):
    from live import run_live
    from live.ingest import hlsfetch as H

    calls: dict = {}
    monkeypatch.setattr(
        H, "download_media",
        lambda url, dest, **kw: {"path": str(tmp_path / "l.media"),
                                 "url": url, "size": 9,
                                 "content_type": "video/mp4"})

    async def _fake_amain(args):
        calls["duration"] = args.duration
        calls["source"] = args.source
        return {"summary_path": "s.json"}

    monkeypatch.setattr(run_live, "amain", _fake_amain)
    monkeypatch.chdir(tmp_path)
    runs = {"r1": {"status": "started"}}
    appmod._run_link_pipeline("r1", "https://cdn.example.com/c.mp4",
                              "mp4", "vivo", runs, "link-r1.jsonl",
                              duration=30.0)
    assert runs["r1"]["status"] == "done"
    assert calls == {"duration": 30.0, "source": "link-mp4"}


# ---------------------------------------------------------------------------
# link runs are replayable; their media is served
# ---------------------------------------------------------------------------

def test_link_runs_listed_for_replay(monkeypatch, tmp_path):
    from live.replay import player as P

    log = tmp_path / "link-abc123.jsonl"
    log.write_text(json.dumps({"type": "run.mode", "mode": "vivo",
                               "source": "link-hls",
                               "label": "Vivo por enlace"}) + "\n")
    monkeypatch.setattr(P, "RUNS_DIR", tmp_path)
    runs = {r["run"]: r for r in P.list_runs()}
    assert runs["link-abc123"]["events"] == 1
    assert P.resolve_run("link-abc123") == log


def test_media_serves_link_cache_and_refuses_traversal(monkeypatch, tmp_path):
    (tmp_path / "link-abc123.mkv").write_bytes(b"\x1a\x45\xdf\xa3zzz")
    monkeypatch.setattr(appmod, "CACHE_DIR", tmp_path)
    c = _client()
    ok = c.get("/media/link-abc123.mkv")
    assert ok.status_code == 200
    assert c.get("/media/link-..%2F..%2Fx.mkv").status_code == 404
    assert c.get("/media/evil.mp4").status_code == 404
    assert c.get("/media/link-abc123.exe").status_code == 404


def test_shipped_runs_pin_shipped_video():
    assert appmod._video_for_run("run-es-native") == \
        "/media/keynote_es_native.mp4"
    assert appmod._video_for_run("run-keynote-en-listen") == \
        "/media/keynote_en.mp4"
    assert appmod._video_for_run("verify-keynote-en-investigate") == \
        "/media/keynote_en.mp4"
    assert appmod._video_for_run("run-m1e-heuristic") == \
        "/media/keynote_en.mp4"


def test_media_serves_shipped_fallback():
    c = _client()
    r = c.get("/media/keynote_en.mp4")
    assert r.status_code == 200 and len(r.content) > 1_000_000
    r = c.get("/media/keynote_es_native.mp4")
    assert r.status_code == 200 and len(r.content) > 1_000_000


def test_video_for_run_maps_link_cache(monkeypatch, tmp_path):
    (tmp_path / "link-abc123.mkv").write_bytes(b"mkv")
    monkeypatch.setattr(appmod, "CACHE_DIR", tmp_path)
    assert appmod._video_for_run("link-abc123") == "/media/link-abc123.mkv"
    assert appmod._video_for_run("link-../../../etc") is None
    assert appmod._video_for_run("link-abc123") != "/media/link-abc123.media"


# ---------------------------------------------------------------------------
# /report endpoint
# ---------------------------------------------------------------------------

def test_report_json_html_and_errors():
    c = _client()
    runs = c.get("/replay").json()["runs"]
    assert runs, "shipped replay logs must exist for the report test"
    run = runs[0]["run"]
    js = c.get(f"/report/{run}?format=json")
    assert js.status_code == 200 and js.json()["tool"]
    html = c.get(f"/report/{run}?format=html")
    assert html.status_code == 200 and "<html" in html.text.lower()
    assert c.get(f"/report/{run}?format=pdf").status_code == 400
    assert c.get("/report/no-such-run-xyz?format=json").status_code == 404


def test_report_link_run_badged_real(monkeypatch, tmp_path):
    from live.replay import player as P

    (tmp_path / "link-zz99.jsonl").write_text(
        json.dumps({"type": "run.mode", "mode": "vivo",
                    "source": "link-hls"}) + "\n")
    monkeypatch.setattr(P, "RUNS_DIR", tmp_path)
    payload = _client().get("/report/link-zz99?format=json").json()
    assert payload["source"] == "real"


# ---------------------------------------------------------------------------
# UI controls
# ---------------------------------------------------------------------------

def test_ui_has_mode_badge_and_link_replay():
    html = _client().get("/").text
    for marker in ("id=\"modeBadge\"", "id=\"ingestReplay\"",
                   "id=\"ingestReport\"", "run.mode", "/report/"):
        assert marker in html
