"""S3 directo-simulado tests: HLS loop, serve routes, UI badges.

Offline, $0: short HLS is generated from a tiny synthetic mp4 in tmp
with the repo imageio-ffmpeg static binary (no network, no ASR).
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from live.simulive.hls import (
    CUSTOM_COMMENT,
    CUSTOM_TAG,
    build_loop_cmd,
    ensure_metadata,
    generate_short_hls,
    parse_playlist,
    write_live_window,
)
from live.simulive.serve import hls_status, mount_simulive

UI_PATH = Path(__file__).resolve().parent.parent / "ui" / "index.html"


@pytest.fixture(scope="module")
def hls_dir(tmp_path_factory):
    try:
        workdir = tmp_path_factory.mktemp("s3hls")
        generate_short_hls(workdir, duration_s=5.0, hls_time=1)
    except Exception as exc:
        pytest.skip(f"ffmpeg unavailable: {exc}")
    return workdir


def _read_playlist(hls_dir: Path) -> str:
    return (hls_dir / "stream.m3u8").read_text(encoding="utf-8")


# --- loop command -----------------------------------------------------------


def test_loop_cmd_paced_infinite_moving_window():
    argv = build_loop_cmd("/tmp/src.mp4", "/tmp/hls")
    assert "-re" in argv
    assert argv[argv.index("-stream_loop") + 1] == "-1"
    assert "delete_segments+append_list" in argv
    assert argv[argv.index("-hls_time") + 1] == "4"
    assert argv[argv.index("-hls_list_size") + 1] == "10"
    assert argv[-1].endswith("stream.m3u8")
    assert "/tmp/src.mp4" in argv


# --- manifest -----------------------------------------------------------------


def test_short_hls_manifest_metadata(hls_dir):
    text = _read_playlist(hls_dir)
    assert CUSTOM_TAG in text
    assert CUSTOM_COMMENT in text
    parsed = parse_playlist(text)
    assert parsed["has_custom"] and parsed["has_comment"]
    assert len(parsed["segments"]) >= 2
    for seg in parsed["segments"]:
        assert (hls_dir / seg).is_file()


def test_ensure_metadata_idempotent():
    once = ensure_metadata("#EXTM3U\n#EXT-X-VERSION:3\n")
    assert CUSTOM_TAG in once and CUSTOM_COMMENT in once
    assert ensure_metadata(once) == once
    # Non-playlists pass through untouched.
    assert ensure_metadata("junk") == "junk"


# --- moving window: mobile seq --------------------------------------------------


def test_media_sequence_moves(hls_dir):
    text = _read_playlist(hls_dir)
    segs = parse_playlist(text)["segments"]
    assert len(segs) >= 3
    w1 = write_live_window(
        hls_dir / "stream.m3u8", segs[:3], seq_start=41
    )
    w2 = write_live_window(
        hls_dir / "stream.m3u8", segs[1:4] if len(segs) >= 4 else segs,
        seq_start=42,
    )
    p1, p2 = parse_playlist(w1), parse_playlist(w2)
    assert p2["seq"] == p1["seq"] + 1
    assert not p1["endlist"] and not p2["endlist"]  # live: no ENDLIST
    assert p2["has_custom"] and p2["has_comment"]


def test_discontinuity_on_trim(hls_dir):
    text = _read_playlist(hls_dir)
    segs = parse_playlist(text)["segments"]
    out = write_live_window(
        hls_dir / "stream.m3u8",
        segs[:3],
        seq_start=100,
        discontinuity_before={1},  # trim/loop point before 2nd segment
    )
    assert "#EXT-X-DISCONTINUITY" in out
    assert parse_playlist(out)["discontinuity"] is True
    plain = write_live_window(
        hls_dir / "stream.m3u8", segs[:3], seq_start=101
    )
    assert parse_playlist(plain)["discontinuity"] is False


# --- mid-join + pause -----------------------------------------------------------


def test_mid_join_late_client_gets_valid_window(hls_dir):
    """A client fetching the playlist late still resolves every URI."""
    text = _read_playlist(hls_dir)
    segs = parse_playlist(text)["segments"]
    # Simulate the loop having advanced far past the client's join time.
    late = write_live_window(
        hls_dir / "stream.m3u8", segs[-3:], seq_start=500
    )
    parsed = parse_playlist(late)
    assert parsed["seq"] == 500
    assert parsed["segments"]
    for seg in parsed["segments"]:
        assert (hls_dir / seg).is_file(), f"missing segment {seg}"


def test_pause_freeze_same_seq(hls_dir):
    """Frozen loop (no ffmpeg advance) => identical seq on re-read."""
    text = _read_playlist(hls_dir)
    segs = parse_playlist(text)["segments"]
    write_live_window(hls_dir / "stream.m3u8", segs[:3], seq_start=77)
    first = parse_playlist(_read_playlist(hls_dir))
    second = parse_playlist(_read_playlist(hls_dir))
    assert first["seq"] == second["seq"] == 77
    assert first["segments"] == second["segments"]


# --- serve routes ---------------------------------------------------------------


def _simulive_client(hls_dir: Path):
    app = FastAPI()
    mount_simulive(app, hls_dir=hls_dir)
    return TestClient(app)


def test_hls_status_reports_seq_and_uptime(hls_dir):
    text = _read_playlist(hls_dir)
    segs = parse_playlist(text)["segments"]
    write_live_window(hls_dir / "stream.m3u8", segs[:3], seq_start=9)
    client = _simulive_client(hls_dir)
    res = client.get("/hls-status")
    assert res.status_code == 200
    body = res.json()
    assert body["live"] is True
    assert body["seq"] == 9
    assert body["segments"] == 3
    assert body["uptime_s"] >= 0.0
    assert body["playlist"] == "/hls/stream.m3u8"
    assert hls_status(hls_dir)["seq"] == 9


def test_hls_status_no_playlist(tmp_path):
    client = _simulive_client(tmp_path)
    body = client.get("/hls-status").json()
    assert body["live"] is False
    assert body["seq"] is None


def test_hls_serves_playlist_with_metadata(hls_dir):
    client = _simulive_client(hls_dir)
    res = client.get("/hls/stream.m3u8")
    assert res.status_code == 200
    assert "mpegurl" in res.headers["content-type"]
    assert CUSTOM_TAG in res.text
    assert CUSTOM_COMMENT in res.text


def test_hls_serves_segment_and_rejects_traversal(hls_dir):
    text = _read_playlist(hls_dir)
    seg = parse_playlist(text)["segments"][0]
    client = _simulive_client(hls_dir)
    res = client.get(f"/hls/{seg}")
    assert res.status_code == 200
    assert res.content  # real .ts bytes
    assert client.get("/hls/..%2Fsecret").status_code == 404
    assert client.get("/hls/x.mp4").status_code == 404
    assert client.get("/hls/nope.ts").status_code == 404


# --- server app wiring (M1b/M4 contract intact) ----------------------------------


def test_app_mounts_simulive_routes():
    from live.server.app import create_app

    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/replay").status_code == 200
    # Additive S3 routes exist (no loop running => live:false).
    res = client.get("/hls-status")
    assert res.status_code == 200
    assert res.json()["playlist"] == "/hls/stream.m3u8"


# --- UI badges --------------------------------------------------------------------


def test_ui_simulive_badges_and_source_selector():
    html = UI_PATH.read_text(encoding="utf-8")
    # "Directo simulado" badge (source=hls) + permanent corpus corner badge.
    assert 'id="simuliveBadge"' in html
    assert 'id="corpusBadge"' in html
    assert "Synthetic demo" in html
    assert "DIRECTO SIMULADO" in html  # ES label
    assert "SIMULATED LIVE" in html  # EN label
    # Source selector (archivo / directo-simulado).
    assert 'id="srcSel"' in html
    assert 'value="hls"' in html
    assert "Directo simulado" in html
    assert "Simulated live" in html
    # Wiring: query flags + HLS playlist URL.
    assert 'qs.get("source")' in html
    assert 'qs.get("corpus")' in html
    assert "/hls/stream.m3u8" in html
