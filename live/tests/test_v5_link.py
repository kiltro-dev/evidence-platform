"""V5-G1 link ingest + Turbo tests (fully offline: doubles only)."""

import pytest
from fastapi.testclient import TestClient

from live.ingest import resolve as R
from live.ingest import turbo as T
from live.ingest.resolve import LinkBlocked, LinkError, ResolvedMedia

def PUBLIC_DNS(host):
    """Offline DNS double: literals pass through, names go public.

    Mirrors ``socket.getaddrinfo`` for numeric IPs (no network needed)
    while keeping example.com-style hosts on a public TEST-NET IP.
    """
    import ipaddress

    if str(host).lower().rstrip(".") == "localhost":
        return ["127.0.0.1"]
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        return ["93.184.216.34"]


ALLOW_ROBOTS = lambda url: True  # noqa: E731


def _head_ok(kind="mp4"):
    ctype = {"mp4": "video/mp4", "hls": "application/vnd.apple.mpegurl",
             "dash": "application/dash+xml"}.get(kind, "video/mp4")

    def _check(url):
        R.check_url_public(url, dns_resolver=PUBLIC_DNS)
        return url, ctype, 1234

    return _check


# ---------------------------------------------------------------------------
# resolver: extraction
# ---------------------------------------------------------------------------

def test_resolve_og_video():
    html = ('<html><head><meta property="og:video" '
            'content="https://cdn.example.com/v/clip.mp4"></head></html>')

    def _page(url):
        R.check_url_public(url, dns_resolver=PUBLIC_DNS)
        return url, "text/html", html.encode()

    media = R.resolve_link("https://example.com/article",
                           dns_resolver=PUBLIC_DNS,
                           robots_checker=ALLOW_ROBOTS,
                           page_fetcher=_page, head_checker=_head_ok())
    assert media.url == "https://cdn.example.com/v/clip.mp4"
    assert media.kind == "mp4"
    assert media.page_url == "https://example.com/article"


def test_resolve_hls_link_and_relative_url():
    html = '<html><body><a href="/streams/live.m3u8">watch</a></body></html>'

    def _page(url):
        return url, "text/html", html.encode()

    media = R.resolve_link("https://example.com/p",
                           dns_resolver=PUBLIC_DNS,
                           robots_checker=ALLOW_ROBOTS,
                           page_fetcher=_page, head_checker=_head_ok("hls"))
    assert media.url == "https://example.com/streams/live.m3u8"
    assert media.kind == "hls"


def test_resolve_video_src_tag():
    html = '<html><body><video src="https://cdn.example.com/a/b.mp4"/></body></html>'

    def _page(url):
        return url, "text/html", html.encode()

    media = R.resolve_link("https://example.com/p",
                           dns_resolver=PUBLIC_DNS,
                           robots_checker=ALLOW_ROBOTS,
                           page_fetcher=_page, head_checker=_head_ok())
    assert media.url == "https://cdn.example.com/a/b.mp4"


def test_resolve_direct_mp4_skips_page_fetch():
    def _page(url):  # pragma: no cover - must never run
        raise AssertionError("page fetch must be skipped for direct media")

    media = R.resolve_link("https://cdn.example.com/clip.mp4?x=1",
                           dns_resolver=PUBLIC_DNS,
                           robots_checker=ALLOW_ROBOTS,
                           page_fetcher=_page, head_checker=_head_ok())
    assert media.url == "https://cdn.example.com/clip.mp4?x=1"
    assert media.kind == "mp4"


def test_resolve_no_media_raises():
    def _page(url):
        return url, "text/html", b"<html><body>no video here</body></html>"

    with pytest.raises(LinkError):
        R.resolve_link("https://example.com/empty",
                       dns_resolver=PUBLIC_DNS,
                       robots_checker=ALLOW_ROBOTS,
                       page_fetcher=_page, head_checker=_head_ok())


def test_resolve_bad_content_type_skipped_then_error():
    html = '<html><body><video src="https://cdn.example.com/x.mp4"/></body></html>'

    def _page(url):
        return url, "text/html", html.encode()

    def _head(url):
        return url, "text/html", 10

    with pytest.raises(LinkError):
        R.resolve_link("https://example.com/p", dns_resolver=PUBLIC_DNS,
                       robots_checker=ALLOW_ROBOTS,
                       page_fetcher=_page, head_checker=_head)


def test_extract_candidates_order_and_dedupe():
    html = ('<meta property="og:video" content="https://c.example.com/a.mp4">'
            '<video src="https://c.example.com/a.mp4"></video>'
            '<a href="https://c.example.com/b.m3u8">x</a>')
    cands = R.extract_media_candidates(html, "https://example.com/p")
    assert cands == ["https://c.example.com/a.mp4",
                     "https://c.example.com/b.m3u8"]


@pytest.mark.parametrize("url,ctype,want", [
    ("https://x/s.m3u8", "application/vnd.apple.mpegurl", "hls"),
    ("https://x/s.mpd", "application/dash+xml", "dash"),
    ("https://x/c.mp4", "video/mp4", "mp4"),
    ("https://x/c.mp4", "", "mp4"),  # HEAD-405 path: extension decides
    ("https://x/a", "audio/mpeg", "audio"),
])
def test_kind_for_media(url, ctype, want):
    assert R.kind_for_media(url, ctype) == want


# ---------------------------------------------------------------------------
# SSRF matrix (offline: numeric IPs need no DNS; youtube refused pre-DNS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1/video.mp4",
    "http://localhost/video.mp4",
    "http://10.0.0.5/x.mp4",
    "http://192.168.1.1/x.mp4",
    "http://172.16.0.9/x.mp4",
    "http://[::1]/x.mp4",
    "file:///etc/passwd",
    "ftp://example.com/x.mp4",
    "gopher://example.com/x",
])
def test_ssrf_blocked(url):
    with pytest.raises(LinkBlocked):
        R.check_url_public(url, dns_resolver=PUBLIC_DNS)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube-nocookie.com/embed/dQw4w9WgXcQ",
])
def test_youtube_always_refused(url):
    with pytest.raises(LinkBlocked) as excinfo:
        R.check_url_public(url, dns_resolver=PUBLIC_DNS)
    assert excinfo.value.reason == "youtube"


def test_youtube_refused_inside_resolve():
    with pytest.raises(LinkBlocked) as excinfo:
        R.resolve_link("https://www.youtube.com/watch?v=x",
                       dns_resolver=PUBLIC_DNS)
    assert excinfo.value.reason == "youtube"


def test_hostname_resolving_private_is_blocked():
    fake_dns = lambda host: ["10.9.9.9"]  # noqa: E731
    with pytest.raises(LinkBlocked) as excinfo:
        R.check_url_public("https://evil.example.com/x.mp4",
                           dns_resolver=fake_dns)
    assert excinfo.value.reason == "ssrf"


def test_public_host_passes():
    assert R.check_url_public("https://example.com/x.mp4",
                              dns_resolver=PUBLIC_DNS)


class _FakeResp:
    def __init__(self, status=200, headers=None, body=b"", url=""):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body
        self._url = url
        self._pos = 0

    def geturl(self):
        return self._url

    def read(self, n=-1):
        if self._pos >= len(self._body):
            return b""
        chunk = self._body[self._pos:] if n is None or n < 0 else \
            self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_redirect_to_private_is_blocked():
    def _opener(req, timeout):
        url = req.get_full_url()
        if "example.com" in url:
            return _FakeResp(302, {"Location": "http://169.254.169.254/x"},
                             url=url)
        raise AssertionError("must never fetch the private hop")

    with pytest.raises(LinkBlocked) as excinfo:
        R.fetch_page("https://example.com/start", min_interval_s=0,
                     dns_resolver=PUBLIC_DNS, robots_checker=ALLOW_ROBOTS,
                     opener=_opener)
    assert excinfo.value.reason == "ssrf"


def test_head_redirect_to_private_is_blocked():
    def _opener(req, timeout):
        return _FakeResp(302, {"Location": "http://10.1.2.3/c.mp4"},
                         url=req.get_full_url())

    with pytest.raises(LinkBlocked) as excinfo:
        R.head_media("https://cdn.example.com/c.mp4",
                     dns_resolver=PUBLIC_DNS, opener=_opener)
    assert excinfo.value.reason == "ssrf"


def test_head_too_large_blocked():
    big = str(R.MEDIA_MAX_BYTES + 1)

    def _opener(req, timeout):
        return _FakeResp(200, {"Content-Type": "video/mp4",
                               "Content-Length": big},
                         url=req.get_full_url())

    with pytest.raises(LinkBlocked) as excinfo:
        R.head_media("https://cdn.example.com/huge.mp4",
                     dns_resolver=PUBLIC_DNS, opener=_opener)
    assert excinfo.value.reason == "too-large"


def test_head_405_passes_through_unknown():
    def _opener(req, timeout):
        return _FakeResp(405, {}, url=req.get_full_url())

    final, ctype, size = R.head_media("https://cdn.example.com/c.mp4",
                                      dns_resolver=PUBLIC_DNS, opener=_opener)
    assert size is None and ctype == ""


def test_fetch_page_honors_robots():
    def _opener(req, timeout):  # pragma: no cover - must never run
        raise AssertionError("robots refusal must precede any GET")

    with pytest.raises(LinkBlocked) as excinfo:
        R.fetch_page("https://example.com/p", min_interval_s=0,
                     dns_resolver=PUBLIC_DNS,
                     robots_checker=lambda u: False, opener=_opener)
    assert excinfo.value.reason == "robots"


def test_real_opener_surfaces_redirects_locally():
    """The production opener (urllib) surfaces 3xx without following.

    Local loopback server: offline-safe, exercises the real
    ``_NoRedirectHandler`` wiring (SSRF layer not involved here).
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.request import Request

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/final.mp4")
                self.end_headers()
            else:
                body = b"hello"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with R._open_no_redirect(Request(base + "/redir"), 5) as resp:
            assert resp.status == 302
            assert resp.headers.get("Location") == "/final.mp4"
        with R._open_no_redirect(Request(base + "/final.mp4"), 5) as resp:
            assert resp.status == 200
            assert resp.read() == b"hello"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_throttle_spaces_host_hits():
    R.clear_throttle()
    slept: list[float] = []
    R.throttle_host("h.example.com", 1.0, _now=100.0,
                    _sleep=slept.append)
    R.throttle_host("h.example.com", 1.0, _now=100.2,
                    _sleep=slept.append)
    assert slept and abs(slept[0] - 0.8) < 1e-9
    R.clear_throttle()


# ---------------------------------------------------------------------------
# turbo: pacing governor
# ---------------------------------------------------------------------------

def test_turbo_pacing_args():
    assert T.pacing_args("vivo") == ("-re",)
    assert T.pacing_args("turbo") == ()
    with pytest.raises(ValueError):
        T.check_mode("ludicrous")


def test_turbo_argv_vivo_has_re_turbo_drops_it():
    vivo = T.restream_argv_for_mode("in.mp4", "out", "vivo")
    turbo = T.restream_argv_for_mode("in.mp4", "out", "turbo")
    assert "-re" in vivo
    assert "-re" not in turbo
    # identical shape otherwise
    assert [a for a in vivo if a != "-re"] == turbo


def test_turbo_restream_spawns_without_re_mock(monkeypatch):
    spawned: list[list[str]] = []

    class _FakeProc:
        pass

    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: (spawned.append(list(argv)),
                                           _FakeProc())[1])
    proc, label = T.restream_for_mode("in.mp4", "out", "turbo")
    assert "-re" not in spawned[0]
    assert label == "simulated-stream"
    proc2, _ = T.restream_for_mode("in.mp4", "out", "vivo")
    assert "-re" in spawned[1]


def test_turbo_mode_event_and_concurrency():
    ev = T.mode_event("turbo")
    assert ev["type"] == "run.mode"
    assert ev["mode"] == "turbo"
    assert ev["pacing"] == "turbo"
    assert ev["concurrency"] >= 1
    assert T.concurrency_for_mode("vivo") == 1
    assert T.concurrency_for_mode("turbo") >= 1
    fast = T.fast_paths_for_mode("turbo")
    assert fast["no_realtime_pacing"] is True
    slow = T.fast_paths_for_mode("vivo")
    assert slow["no_realtime_pacing"] is False


def test_run_live_mode_flag_defaults_vivo():
    from live import run_live

    assert run_live.build_parser().parse_args([]).mode == "vivo"
    assert run_live.build_parser().parse_args(["--mode", "turbo"]).mode == \
        "turbo"


def test_fifostream_passes_mode_to_restream(monkeypatch, tmp_path):
    from live import run_live

    calls: dict = {}
    monkeypatch.setattr(run_live, "probe_streams",
                        lambda v: {"audio": True, "video": False})

    class _FakeProc:
        stdout = None

        class _Err:
            def read(self, n=-1):
                return b""

        stderr = _Err()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    def _fake_restream(src, dst, mode, extra_args=()):
        calls["mode"] = mode
        return _FakeProc(), "simulated-stream"

    def _fake_popen(argv, **kw):
        calls["demux_argv"] = list(argv)
        return _FakeProc()

    monkeypatch.setattr(run_live, "restream_for_mode", _fake_restream)
    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr("os.mkfifo", lambda path: None)

    stream = run_live.FifoStream("clip.mp4", mode="turbo")
    stream._tmpdir = str(tmp_path)
    stream._fifo = str(tmp_path / "live.mkv")
    stream.start()
    try:
        assert calls["mode"] == "turbo"
        argv = T.restream_argv_for_mode("clip.mp4", "fifo", calls["mode"])
        assert "-re" not in argv  # turbo governor => no realtime pacing
    finally:
        stream.close()


# ---------------------------------------------------------------------------
# server + UI contracts (offline: resolve/launcher monkeypatched)
# ---------------------------------------------------------------------------

def _client():
    from live.server.app import create_app

    return TestClient(create_app())


def _fake_media():
    return ResolvedMedia(url="https://cdn.example.com/c.mp4", kind="mp4",
                         content_type="video/mp4", size=1234,
                         page_url="https://example.com/p")


def test_ingest_dry_run_contract(monkeypatch):
    monkeypatch.setattr(R, "resolve_link", lambda url, **kw: _fake_media())
    client = _client()
    res = client.post("/ingest", json={"url": "https://example.com/p",
                                       "mode": "turbo", "launch": False})
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "turbo"
    assert body["media"]["url"] == "https://cdn.example.com/c.mp4"
    assert body["media"]["kind"] == "mp4"
    assert body["run_id"]
    assert body["status"] == "resolved"
    # status roundtrip
    got = client.get(f"/ingest/{body['run_id']}")
    assert got.status_code == 200
    assert got.json()["mode"] == "turbo"


def test_ingest_launches_pipeline_thread(monkeypatch):
    import live.server.app as appmod

    monkeypatch.setattr(R, "resolve_link", lambda url, **kw: _fake_media())
    launched: dict = {}
    monkeypatch.setattr(appmod, "_launch_link_run",
                        lambda rid, url, kind, mode, runs, log, **kw:
                        launched.update(
                            run_id=rid, url=url, kind=kind, mode=mode))
    client = _client()
    res = client.post("/ingest", json={"url": "https://example.com/p",
                                       "mode": "vivo"})
    assert res.status_code == 200
    assert launched["mode"] == "vivo"
    assert launched["url"] == "https://cdn.example.com/c.mp4"
    assert launched["run_id"] == res.json()["run_id"]


def test_ingest_rejects_youtube_and_ssrf():
    client = _client()  # real resolver: both refuse before any network
    yt = client.post("/ingest", json={"url": "https://youtu.be/x"})
    assert yt.status_code == 403
    assert yt.json()["reason"] == "youtube"
    ssrf = client.post("/ingest",
                       json={"url": "http://169.254.169.254/x.mp4"})
    assert ssrf.status_code == 403
    assert ssrf.json()["reason"] == "ssrf"


def test_ingest_validation_errors(monkeypatch):
    client = _client()
    assert client.post("/ingest", json={}).status_code == 400
    bad = client.post("/ingest", json={"url": "https://example.com/p",
                                       "mode": "ludicrous"})
    assert bad.status_code == 400

    def _boom(url, **kw):
        raise LinkError("sin medio directo")

    monkeypatch.setattr(R, "resolve_link", _boom)
    res = client.post("/ingest", json={"url": "https://example.com/empty"})
    assert res.status_code == 400
    assert "sin medio" in res.json()["error"]
    assert client.get("/ingest/nope").status_code == 404


def test_download_media_success(tmp_path):
    body = b"\x00\x01mp4-bytes" * 100

    def _opener(req, timeout):
        return _FakeResp(200, {"Content-Type": "video/mp4",
                               "Content-Length": str(len(body))},
                         body=body, url=req.get_full_url())

    dest = str(tmp_path / "clip.media")
    info = R.download_media("https://cdn.example.com/c.mp4", dest,
                            dns_resolver=PUBLIC_DNS, opener=_opener)
    assert info["size"] == len(body)
    assert open(dest, "rb").read() == body


def test_download_media_guards(tmp_path):
    def _opener_for(status=200, headers=None, body=b""):
        def _opener(req, timeout):
            return _FakeResp(status, headers or {}, body=body,
                             url=req.get_full_url())
        return _opener

    dest = str(tmp_path / "x.media")
    # SSRF redirect
    with pytest.raises(LinkBlocked):
        R.download_media("https://cdn.example.com/c.mp4", dest,
                         dns_resolver=PUBLIC_DNS,
                         opener=_opener_for(
                             302, {"Location": "http://10.0.0.1/c.mp4"}))
    # non-media content-type
    with pytest.raises(LinkError):
        R.download_media("https://cdn.example.com/c.mp4", dest,
                         dns_resolver=PUBLIC_DNS,
                         opener=_opener_for(
                             200, {"Content-Type": "text/html"}, b"<html>"))
    # upfront cap
    with pytest.raises(LinkBlocked):
        R.download_media("https://cdn.example.com/c.mp4", dest,
                         dns_resolver=PUBLIC_DNS, max_bytes=10,
                         opener=_opener_for(
                             200, {"Content-Type": "video/mp4",
                                   "Content-Length": "999"}, b"0123456789"))
    # streaming cap (no Content-Length) + .part cleanup
    with pytest.raises(LinkBlocked):
        R.download_media("https://cdn.example.com/c.mp4", dest,
                         dns_resolver=PUBLIC_DNS, max_bytes=10,
                         opener=_opener_for(
                             200, {"Content-Type": "video/mp4"},
                             b"0123456789abcdef"))
    assert not (tmp_path / "x.media.part").exists()


def test_link_pipeline_materializes_then_reuses_run_live(monkeypatch,
                                                         tmp_path):
    import live.server.app as appmod
    from live import run_live
    from live.ingest import hlsfetch as H

    calls: dict = {}
    # V6-1: dispatch moved to hlsfetch.materialize_media; patch its seam.
    monkeypatch.setattr(H, "download_media",
                        lambda url, dest, **kw: calls.update(
                            downloaded=url, dest=dest) or
                        {"path": str(tmp_path / "local.media"),
                         "url": url, "size": 99,
                         "content_type": "video/mp4"})

    async def _fake_amain(args):
        calls["video"] = args.video
        calls["mode"] = args.mode
        return {"summary_path": "s.json"}

    monkeypatch.setattr(run_live, "amain", _fake_amain)
    runs = {"r1": {"status": "started"}}
    monkeypatch.chdir(tmp_path)
    appmod._run_link_pipeline("r1", "https://cdn.example.com/c.mp4",
                              "mp4", "turbo", runs, "link-r1.jsonl")
    assert runs["r1"]["status"] == "done"
    assert calls["downloaded"] == "https://cdn.example.com/c.mp4"
    assert calls["video"] == str(tmp_path / "local.media")
    assert calls["mode"] == "turbo"
    assert runs["r1"]["local_path"] == str(tmp_path / "local.media")


def test_link_pipeline_hls_materializes_dash_refused(monkeypatch, tmp_path):
    # V6-1: hls is materialized in Python (no https to ffmpeg); only
    # dash stays refused.
    import live.server.app as appmod
    from live import run_live
    from live.ingest import hlsfetch as H

    monkeypatch.setattr(
        H, "fetch_hls_local",
        lambda url, dest, **kw: {"path": str(tmp_path / "win.media"),
                                 "bytes": 10, "segments": 1,
                                 "variant": url, "live": True,
                                 "truncated": False})

    async def _fake_amain(args):
        return {"summary_path": "s.json"}

    monkeypatch.setattr(run_live, "amain", _fake_amain)
    monkeypatch.chdir(tmp_path)
    runs = {"r1": {"status": "started"}}
    appmod._run_link_pipeline("r1", "https://cdn.example.com/s.m3u8",
                              "hls", "vivo", runs, "link-r1.jsonl")
    assert runs["r1"]["status"] == "done"
    assert runs["r1"]["local_path"] == str(tmp_path / "win.media")

    runs = {"r2": {"status": "started"}}
    appmod._run_link_pipeline("r2", "https://cdn.example.com/s.mpd",
                              "dash", "vivo", runs, "link-r2.jsonl")
    assert runs["r2"]["status"] == "error"
    assert "dash" in runs["r2"]["error"].lower()


def test_ui_has_ingest_controls():
    html = _client().get("/").text
    for marker in ("id=\"ingestUrl\"", "id=\"modeVivo\"", "id=\"modeTurbo\"",
                   "id=\"ingestGo\"", "id=\"ingestError\"",
                   "id=\"ingestStatus\"", "/ingest"):
        assert marker in html
