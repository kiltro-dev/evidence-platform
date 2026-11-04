"""V6-1 HLS-over-https tests: parse, SSRF-guarded materialize, attack cases.

Offline part uses doubles only. Live part (``test_live_*``) hits real
NASA / Internet Archive URLs and runs only with ``ATLAS_LIVE_NET=1``,
so the default offline gate never touches the network.
"""

import os

import pytest

from live.ingest import hlsfetch as H
from live.ingest.resolve import LinkBlocked, LinkError

LIVE = os.environ.get("ATLAS_LIVE_NET") == "1"
needs_live = pytest.mark.skipif(not LIVE, reason="needs ATLAS_LIVE_NET=1")

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
hi.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=320x180
lo.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
seg0.m4s
#EXTINF:6.0,
seg1.m4s
#EXT-X-ENDLIST
"""

LIVE_MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:6.0,
a0.ts
#EXTINF:6.0,
a1.ts
"""

ENCRYPTED = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXTINF:6.0,
s0.ts
#EXT-X-ENDLIST
"""


def PUBLIC_DNS(host):
    import ipaddress

    if str(host).lower().rstrip(".") == "localhost":
        return ["127.0.0.1"]
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        return ["93.184.216.34"]


ALLOW_ROBOTS = lambda url: True  # noqa: E731


def _pages(mapping):
    def _fetch(url):
        body = mapping[url]
        return url, "application/vnd.apple.mpegurl", body.encode()
    return _fetch


def _dl(store):
    def _download(url, dest, **kw):
        data = f"<{url}>".encode()
        with open(dest, "wb") as fh:
            fh.write(data)
        return {"path": dest, "url": url, "size": len(data),
                "content_type": "video/MP2T"}
    store.append(_download)
    return _download


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def test_parse_master_variants():
    parsed = H.parse_m3u8(MASTER)
    assert parsed["is_master"]
    assert [v["bandwidth"] for v in parsed["variants"]] == [2000000, 300000]


def test_pick_variant_lowest_bandwidth():
    parsed = H.parse_m3u8(MASTER)
    assert H.pick_variant(parsed) == "lo.m3u8"


def test_parse_media_segments_init_endlist():
    parsed = H.parse_m3u8(MEDIA)
    assert not parsed["is_master"]
    assert parsed["init"] == "init.mp4"
    assert parsed["segments"] == ["seg0.m4s", "seg1.m4s"]
    assert parsed["endlist"] and not parsed["live"]
    assert not parsed["encrypted"]


def test_parse_live_window_no_endlist():
    parsed = H.parse_m3u8(LIVE_MEDIA)
    assert parsed["live"] and not parsed["endlist"]


def test_parse_encrypted_flagged():
    assert H.parse_m3u8(ENCRYPTED)["encrypted"]


def test_parse_not_playlist_rejected():
    with pytest.raises(LinkError):
        H.parse_m3u8("<html>nope</html>")


# ---------------------------------------------------------------------------
# materialize (offline doubles)
# ---------------------------------------------------------------------------

def test_master_variant_concat_order(tmp_path):
    pages = {
        "https://cdn.example.com/live/master.m3u8": MASTER,
        "https://cdn.example.com/live/lo.m3u8": MEDIA,
    }
    dest = str(tmp_path / "win.media")
    out = H.fetch_hls_local(
        "https://cdn.example.com/live/master.m3u8", dest,
        page_fetcher=_pages(pages), downloader=_dl([]),
        dns_resolver=PUBLIC_DNS, robots_checker=ALLOW_ROBOTS)
    assert out["segments"] == 2 and out["variant"].endswith("lo.m3u8")
    raw = open(dest, "rb").read().decode()
    assert raw.index("init.mp4") < raw.index("seg0.m4s") < raw.index("seg1.m4s")


def test_variant_fallback_on_404(tmp_path):
    def _fetch(url):
        if url.endswith("lo.m3u8"):
            raise LinkError("HTTP 404")
        body = {"https://cdn.example.com/live/master.m3u8": MASTER,
                "https://cdn.example.com/live/hi.m3u8": MEDIA}[url]
        return url, "application/vnd.apple.mpegurl", body.encode()

    out = H.fetch_hls_local(
        "https://cdn.example.com/live/master.m3u8",
        str(tmp_path / "w.media"), page_fetcher=_fetch,
        downloader=_dl([]), dns_resolver=PUBLIC_DNS,
        robots_checker=ALLOW_ROBOTS)
    assert out["variant"].endswith("hi.m3u8") and out["segments"] == 2


def test_all_variants_dead_raises(tmp_path):
    def _fetch(url):
        if url.endswith(".m3u8") and "master" not in url:
            raise LinkError("HTTP 404")
        return url, "application/vnd.apple.mpegurl", MASTER.encode()

    with pytest.raises(LinkError, match="ninguna variante"):
        H.fetch_hls_local(
            "https://cdn.example.com/live/master.m3u8",
            str(tmp_path / "w.media"), page_fetcher=_fetch,
            downloader=_dl([]), dns_resolver=PUBLIC_DNS,
            robots_checker=ALLOW_ROBOTS)


def test_relative_urls_resolve_against_playlist(tmp_path):
    seen = []

    def _download(url, dest, **kw):
        seen.append(url)
        with open(dest, "wb") as fh:
            fh.write(b"x")
        return {"path": dest, "url": url, "size": 1, "content_type": "t"}

    pages = {"https://cdn.example.com/live/m.m3u8": LIVE_MEDIA}
    H.fetch_hls_local("https://cdn.example.com/live/m.m3u8",
                      str(tmp_path / "w.media"), page_fetcher=_pages(pages),
                      downloader=_download, dns_resolver=PUBLIC_DNS,
                      robots_checker=ALLOW_ROBOTS)
    assert seen == ["https://cdn.example.com/live/a0.ts",
                    "https://cdn.example.com/live/a1.ts"]


def test_encrypted_playlist_refused(tmp_path):
    pages = {"https://cdn.example.com/e/m.m3u8": ENCRYPTED}
    with pytest.raises(LinkError, match="cifrad"):
        H.fetch_hls_local("https://cdn.example.com/e/m.m3u8",
                          str(tmp_path / "w.media"), page_fetcher=_pages(pages),
                          downloader=_dl([]), dns_resolver=PUBLIC_DNS,
                          robots_checker=ALLOW_ROBOTS)


def test_max_segments_caps_live_window(tmp_path):
    segs = "".join(f"#EXTINF:6.0,\ns{i}.ts\n" for i in range(10))
    pages = {"https://cdn.example.com/l/m.m3u8": "#EXTM3U\n" + segs}
    out = H.fetch_hls_local(
        "https://cdn.example.com/l/m.m3u8", str(tmp_path / "w.media"),
        max_segments=3, page_fetcher=_pages(pages), downloader=_dl([]),
        dns_resolver=PUBLIC_DNS, robots_checker=ALLOW_ROBOTS)
    assert out["segments"] == 3 and out["truncated"]


def test_max_bytes_enforced(tmp_path):
    def _big(url, dest, **kw):
        raise LinkBlocked("too-large", "cap")

    pages = {"https://cdn.example.com/l/m.m3u8": LIVE_MEDIA}
    with pytest.raises(LinkBlocked):
        H.fetch_hls_local("https://cdn.example.com/l/m.m3u8",
                          str(tmp_path / "w.media"), page_fetcher=_pages(pages),
                          downloader=_big, dns_resolver=PUBLIC_DNS,
                          robots_checker=ALLOW_ROBOTS)


# ---------------------------------------------------------------------------
# SSRF attack cases: every segment URL is gated like the playlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evil", [
    "http://169.254.169.254/latest/meta-data",
    "http://localhost:8500/seg.ts",
    "http://127.0.0.1/seg.ts",
    "http://10.0.0.5/seg.ts",
    "file:///etc/passwd",
    "gopher://evil.example.com/x",
])
def test_evil_segment_url_blocked(tmp_path, evil):
    pages = {"https://cdn.example.com/l/m.m3u8":
             f"#EXTM3U\n#EXTINF:6.0,\n{evil}\n#EXT-X-ENDLIST\n"}
    with pytest.raises(LinkBlocked):
        H.fetch_hls_local("https://cdn.example.com/l/m.m3u8",
                          str(tmp_path / "w.media"), page_fetcher=_pages(pages),
                          downloader=_dl([]), dns_resolver=PUBLIC_DNS,
                          robots_checker=ALLOW_ROBOTS)


def test_evil_variant_url_blocked(tmp_path):
    evil_master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n"
                   "http://169.254.169.254/x.m3u8\n")
    pages = {"https://cdn.example.com/l/master.m3u8": evil_master}
    with pytest.raises(LinkBlocked):
        H.fetch_hls_local("https://cdn.example.com/l/master.m3u8",
                          str(tmp_path / "w.media"), page_fetcher=_pages(pages),
                          downloader=_dl([]), dns_resolver=PUBLIC_DNS,
                          robots_checker=ALLOW_ROBOTS)


def test_redirect_to_private_blocked(tmp_path):
    from urllib.parse import urlsplit  # noqa: F401  (documents the hop)

    def _redir_fetcher(url):
        # malicious playlist: segment redirects are resolved by the
        # downloader seam; here the playlist itself is fine but the
        # segment points at a name resolving to a private IP.
        assert url == "https://cdn.example.com/l/m.m3u8"
        body = "#EXTM3U\n#EXTINF:6.0,\nhttps://evil.example.com/s.ts\n"
        return url, "application/vnd.apple.mpegurl", body.encode()

    def _evil_dns(host):
        if host == "evil.example.com":
            return ["10.9.9.9"]
        return PUBLIC_DNS(host)

    with pytest.raises(LinkBlocked) as exc:
        H.fetch_hls_local("https://cdn.example.com/l/m.m3u8",
                          str(tmp_path / "w.media"),
                          page_fetcher=_redir_fetcher, downloader=_dl([]),
                          dns_resolver=_evil_dns,
                          robots_checker=ALLOW_ROBOTS)
    assert exc.value.reason == "ssrf"


# ---------------------------------------------------------------------------
# dispatch: hls is materializable, dash stays refused
# ---------------------------------------------------------------------------

def test_materialize_dispatch(tmp_path):
    pages = {"https://cdn.example.com/l/m.m3u8": LIVE_MEDIA}
    out = H.materialize_media(
        "https://cdn.example.com/l/m.m3u8", "hls", str(tmp_path),
        page_fetcher=_pages(pages), downloader=_dl([]),
        dns_resolver=PUBLIC_DNS, robots_checker=ALLOW_ROBOTS)
    assert out["path"].endswith(".media") and out["segments"] == 2


def test_materialize_dash_still_refused(tmp_path):
    with pytest.raises(LinkError, match="[Dd][Aa][Ss][Hh]"):
        H.materialize_media("https://cdn.example.com/l/m.mpd", "dash",
                            str(tmp_path))


# ---------------------------------------------------------------------------
# TS bridge (tsdemux): offline, lavfi-generated fixture
# ---------------------------------------------------------------------------

def _lavfi_ts(path):
    import subprocess

    from live.ingest.demux import ffmpeg_exe

    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostats", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "mpeg2video", "-c:a", "mp2",
         "-f", "mpegts", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        timeout=120)


def test_is_ts_sniffs_sync_pattern(tmp_path):
    from live.ingest.tsdemux import is_ts

    ts = str(tmp_path / "clip.ts")
    _lavfi_ts(ts)
    assert is_ts(ts)
    not_ts = tmp_path / "clip.mp4"
    not_ts.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500)
    assert not is_ts(str(not_ts))


def test_remux_ts_to_mkv_static_ffmpeg_reads_it(tmp_path):
    from live.ingest.demux import extract_pcm, probe_streams
    from live.ingest.tsdemux import probe_streams_ts, remux_to_mkv

    ts = str(tmp_path / "clip.ts")
    _lavfi_ts(ts)
    assert probe_streams_ts(ts) == {"audio": True, "video": True}
    mkv = remux_to_mkv(ts, str(tmp_path / "clip.mkv"))
    assert mkv["audio"] and mkv["video"] and mkv["bytes"] > 1000
    # the static binary (which segfaults on the .ts) reads the .mkv
    assert probe_streams(mkv["path"]) == {"audio": True, "video": True}
    pcm = extract_pcm(mkv["path"])
    assert len(pcm) > 24000 * 2  # >1 s of mono s16le 24 kHz


def test_probe_ts_behaves_loudly_or_reads(tmp_path):
    # Build-dependent reality: pre-0.7 static binaries segfault on TS
    # (probe_streams must raise loudly, never report "no streams");
    # fixed builds (>=0.7) read it. Either behavior passes.
    from live.ingest.demux import probe_streams

    ts = str(tmp_path / "clip.ts")
    _lavfi_ts(ts)
    try:
        info = probe_streams(ts)
    except RuntimeError as exc:
        assert "segfault" in str(exc)
    else:
        assert info == {"audio": True, "video": True}


def test_probe_segfault_branch_raises(monkeypatch):
    # Pin the rc=-11 branch regardless of the installed build.
    import subprocess as sp

    import live.ingest.demux as D

    class _Proc:
        returncode = -11
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="segfault"):
        D.probe_streams("whatever.ts")


# ---------------------------------------------------------------------------
# live network (ATLAS_LIVE_NET=1 only)
# ---------------------------------------------------------------------------

NASA_HLS = ("https://ntv1.akamaized.net/hls/live/2014075/"
            "NASA-NTV1-HLS/master.m3u8")
# Public deterministic VOD HLS fixture (NASA TV rotates/breaks its
# variant playlists; verified 2026-11-02: master lists variants that
# 404 — see V6_1 note).
VOD_HLS = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
NASA_MP4 = ("https://images-assets.nasa.gov/video/A1Launch/"
            "A1Launch~preview.mp4")
ARCHIVE_MP4 = ("https://archive.org/download/BigBuckBunny_124/"
               "Content/big_buck_bunny_720p_surround.mp4")


@needs_live
def test_live_hls_window_vod(tmp_path):
    out = H.fetch_hls_local(VOD_HLS, str(tmp_path / "vod.media"),
                            max_segments=2)
    assert out["segments"] == 2 and out["bytes"] > 10_000
    assert out["path"] == str(tmp_path / "vod.media")
    assert out["truncated"]  # VOD has more than 2 segments


@needs_live
def test_live_nasa_hls_best_effort(tmp_path):
    """NASA TV HLS when the event is up; xfail when Akamai rotates."""
    try:
        out = H.fetch_hls_local(NASA_HLS, str(tmp_path / "nasa.media"),
                                max_segments=2)
    except LinkError as exc:
        pytest.xfail(f"NASA TV HLS down/rotated: {exc}")
    assert out["segments"] >= 1 and out["bytes"] > 10_000


@needs_live
def test_live_hls_window_end_to_end_static_ffmpeg(tmp_path):
    """Real HLS -> materialize -> remux -> static ffmpeg demuxes it."""
    from live.ingest.demux import extract_pcm, probe_streams

    out = H.materialize_media(VOD_HLS, "hls", str(tmp_path),
                              prefix="vod")
    assert out["path"].endswith(".mkv")  # TS window was bridged
    assert probe_streams(out["path"]) == {"audio": True, "video": True}
    assert len(extract_pcm(out["path"])) > 24000 * 2


@needs_live
def test_live_nasa_mp4_resolve_and_head():
    from live.ingest import resolve as R

    media = R.resolve_link(NASA_MP4, min_interval_s=0.0)
    assert media.kind == "mp4" and media.size == 4493927


@needs_live
def test_live_archive_mp4_resolve():
    from live.ingest import resolve as R

    media = R.resolve_link(ARCHIVE_MP4, min_interval_s=0.0)
    assert media.kind == "mp4" and (media.size or 0) > 10_000_000
