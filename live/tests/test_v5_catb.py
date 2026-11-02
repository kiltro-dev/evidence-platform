"""V5-CATB Atlas Live: category-b alternative routes (offline, $0).

Category b (IMF WEO Oct-2026, BLS Employment Situation Sep-2026) scores
0/42 on the corpus and 0/6 on dev: its primaries bot-wall automated
reads (Akamai 403) and every keyless structured fallback is key-gated,
robots-disallowed, or unreachable (probed 2026-11-02; evidence table in
``live/V5_CATB.md``). This suite pins the general machinery shipped in
``live/swarm/altroutes.py`` (+ minimal ``fetch.py`` / ``coordinator.py``
plumb), all offline:

- b-like topic matchers (IMF/BLS classes, EN+ES; never claim ids) with
  corpus-wide coverage pins (38/38 routed b fire; 15 misses + 4 non-b
  fires documented and pinned by id below).
- BLS API payload/parse/quote/meta builders over RECORDED keyless
  responses (``live/fixtures/v5_catb_bls_*.json`` — real Sep-2026 rows:
  rate 4.2, level 7109 thousand).
- Robots-first behavior: registry-dead routes send ZERO packets; the
  BLS route is attempted so policy refuses it (FetchBlocked) with no
  snapshot and no package; the per-claim budget caps attempts at 2.
- ``fetch_post``: same policy as GET (robots before send, refused
  statuses, size cap, content-addressed snapshot, no cache).
- PDF leg: ``pdf_quote_exists`` (deterministic literal check),
  content-addressed ``.pdftxt`` sidecar, ``ingest_open_pdf`` (walled
  PDFs raise before anything archives).
- Coordinator plumb: ``_Tools.alt_lookup`` + ``_maybe_add_altroutes``
  + ``build_package(source_meta_override=...)``.

All tests offline: recorded fixtures, test doubles, no network, no
model calls.
"""

import glob
import hashlib
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.swarm.altroutes import (  # noqa: E402
    BLS_API_URL,
    MAX_ALT_CALLS_PER_CLAIM,
    AltAttempt,
    archive_pdf_text,
    attempt_altroutes,
    bls_payload,
    bls_quote_for,
    bls_source_meta,
    bls_vintage,
    ingest_open_pdf,
    is_blike_claim,
    match_bls,
    match_bls_series,
    match_imf,
    month_from_text,
    parse_bls_observations,
    pdf_quote_exists,
    pick_observation,
    read_archived_pdf_text,
    route_status,
    year_from_text,
)
from live.swarm.coordinator import (  # noqa: E402
    Doc,
    SwarmConfig,
    _ClaimCtx,
    _DocStore,
    _maybe_add_altroutes,
    _Tools,
    build_package,
)
from live.swarm.fetch import (  # noqa: E402
    FetchBlocked,
    FetchError,
    FetchResult,
    extract_pdf_text,
    fetch_post,
)

LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX_DIR = os.path.join(LIVE_DIR, "fixtures")


def _fixture(name):
    with open(os.path.join(FIX_DIR, name), "rb") as fh:
        return fh.read()


def _bls_body(series):
    return _fixture(f"v5_catb_bls_{series}_2026.json").decode("utf-8")


BLS_RATE_TEXT = ("The United States unemployment rate was 4.2 percent "
                 "in September 2026, according to the Bureau of Labor "
                 "Statistics.")
BLS_LEVEL_TEXT = ("7.1 million people were unemployed in the United "
                  "States in September 2026, according to the Bureau of "
                  "Labor Statistics.")
BLS_AMBIGUOUS_TEXT = ("The unemployment rate was 4.2 percent and 7.1 "
                      "million people were unemployed in September 2026.")
IMF_TEXT = ("The IMF's October 2026 World Economic Outlook projects "
            "global growth of 3.1 percent in 2026.")
IMF_ES_TEXT = "El FMI proyecta un crecimiento mundial del 3,1 por ciento en 2026."
BLS_ES_TEXT = ("El desempleo en Estados Unidos fue del 4,2 por ciento "
               "en sep-2026.")
WB_TEXT = "India had 1.45 billion people in 2024, according to the World Bank."
PHYSICS_TEXT = ("Sound travels at 343 meters per second in dry air at "
                "20 degrees Celsius.")


def _post_double(bodies, tmp_path, seen=None, fail_exc=None):
    """Fake post_fn: series-id -> body; writes real tmp snapshots."""

    def _fake(url, payload, snapshot_dir=None):
        if seen is not None:
            seen.append({"url": url, "payload": payload})
        if fail_exc is not None:
            raise fail_exc(f"boom {url}")
        wanted = ""
        try:
            ids = (payload or {}).get("seriesid") or []
            wanted = str(ids[0]) if ids else ""
        except Exception:
            wanted = ""
        body = bodies.get(wanted)
        if body is None:
            raise FetchError(f"404 {url}")
        raw = body.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        snap_dir = str(snapshot_dir or tmp_path)
        os.makedirs(snap_dir, exist_ok=True)
        path = os.path.join(snap_dir, f"{sha}.html")
        with open(path, "wb") as fh:
            fh.write(raw)
        return FetchResult(
            url=url, final_url=url, status=200,
            content_type="application/json", size=len(raw), sha256=sha,
            snapshot_path=path,
            captured_at="2026-11-02T00:00:00+00:00", text=body)
    return _fake


def _doc(url, body, content_type="application/json"):
    raw = body.encode("utf-8")
    fetch = FetchResult(
        url=url, final_url=url, status=200, content_type=content_type,
        size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        snapshot_path="/tmp/v5catb-test-snap.html",
        captured_at="2026-11-02T00:00:00+00:00", text=body)
    return Doc(url=url, title="t", fetch=fetch, excerpt=body[:80])


class _FakeResp:
    def __init__(self, body, status=200, url="https://example.org/api",
                 ctype="application/json"):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n if n and n > 0 else -1)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# matchers (synthetic + corpus-wide pins)
# ---------------------------------------------------------------------------

class TestBlikeMatcher:
    @pytest.mark.parametrize("text", [
        BLS_RATE_TEXT, BLS_LEVEL_TEXT, BLS_AMBIGUOUS_TEXT, BLS_ES_TEXT,
        "En sep-2026 EE. UU. tenía 8,1 millones de desempleados.",
        "El paro en EE. UU. bajó al 3,2 por ciento en sep-2026.",
        "Los parados en Estados Unidos sumaban 8,1 millones en sep-2026.",
        "The number of unemployed Americans fell to 5.1 million.",
    ])
    def test_bls_fires(self, text):
        assert match_bls(text) and is_blike_claim(text)

    @pytest.mark.parametrize("text", [
        IMF_TEXT, IMF_ES_TEXT,
        "The IMF October 2026 outlook projects 2.1 percent growth.",
        "The same outlook projects 2.1 percent growth for advanced economies.",
        "El FMI prevé que las avanzadas crezcan un 2,1 por ciento en 2026.",
    ])
    def test_imf_fires(self, text):
        assert match_imf(text) and is_blike_claim(text)

    @pytest.mark.parametrize("text", [
        WB_TEXT, PHYSICS_TEXT,
        "Spain's GDP was 2.73 trillion dollars in 2024, overtaking France.",
        "In 2024, the United States GDP was 779 percent of India's GDP.",
        "Mexico's GDP was 1.83 billion dollars in 2024.",
        "We guide full year 2026 revenue to 19.1 billion dollars.",
        "Carbon dioxide averaged 422.8 parts per million worldwide in 2024.",
        "2024 was the warmest year on record, about 1.55 degrees above.",
        "The United Nations projects world population will peak in 2085.",
    ])
    def test_non_blike_silent(self, text):
        assert not is_blike_claim(text)

    def test_outlook_needs_topic(self):
        assert not match_imf("The outlook for the picnic is sunny.")
        assert not is_blike_claim("Economic outlook remains uncertain.")

    def test_never_raises(self):
        assert is_blike_claim(None) is False
        assert match_bls("") is False
        assert match_imf("") is False


# Corpus-wide pins (dev.json + truth_en/truth_es, 516 claims). Misses:
# 5 E1 earnings (excluded from world scoring; non-IMF/BLS topic) + 10
# WMO/NOAA/UN claims (out of scope: no altroute; 9 are claim-side
# blocked and S2-04 alone needs URL discovery). Fires outside b: 4
# unemployment-2024 claims (documented: topic recall wins, policy +
# verifier keep precision; all refuse with 0 packages).
EXPECTED_MISS = frozenset({
    "E1-17-en", "E1-18-en", "E1-19-en", "E1-31-en", "E1-37-en",
    "S1-18-en", "S1-19-en", "S1-20-en", "S1-21-en",
    "S2-03-en", "S2-04-en", "S2-25-en", "S2-26-en", "S2-27-en", "S2-29-en",
})
EXPECTED_NONB_FIRES = frozenset({
    "K1-21-en", "K1-22-en", "K1-33-en", "K2-40-en",
})


def _all_claim_rows():
    rows = []
    for path in sorted(glob.glob(os.path.join(
            LIVE_DIR, "material", "truth_en", "*.json")) + glob.glob(
            os.path.join(LIVE_DIR, "material", "truth_es", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.load(fh))
    with open(os.path.join(LIVE_DIR, "eval2", "dev.json"),
              encoding="utf-8") as fh:
        rows.extend(json.load(fh))
    return rows


class TestMatcherCoverage:
    def test_corpus_pins(self):
        rows = _all_claim_rows()
        assert len(rows) == 516
        miss = {r.get("id") for r in rows
                if r.get("category") == "b"
                and not is_blike_claim(str(r.get("text")))}
        assert miss == EXPECTED_MISS
        fires = {r.get("id") for r in rows
                 if r.get("category") != "b"
                 and is_blike_claim(str(r.get("text")))}
        assert fires == EXPECTED_NONB_FIRES

    def test_dev_b_all_fire(self):
        with open(os.path.join(LIVE_DIR, "eval2", "dev.json"),
                  encoding="utf-8") as fh:
            dev = json.load(fh)
        blike = [e for e in dev if e.get("category") == "b"]
        assert len(blike) == 6
        assert all(is_blike_claim(str(e.get("text"))) for e in blike)

    def test_series_routing(self):
        assert match_bls_series(BLS_RATE_TEXT) == ["LNS14000000"]
        assert match_bls_series(BLS_LEVEL_TEXT) == ["LNS13000000"]
        assert match_bls_series(BLS_AMBIGUOUS_TEXT) == ["LNS14000000",
                                                       "LNS13000000"]
        assert match_bls_series(BLS_ES_TEXT) == ["LNS14000000"]
        # IMF percent claims must NOT try BLS (wrong source).
        assert match_bls_series(IMF_TEXT) == []
        assert match_bls_series(IMF_ES_TEXT) == []
        assert match_bls_series(WB_TEXT) == []


class TestClaimShaping:
    def test_year_month(self):
        assert year_from_text(BLS_RATE_TEXT) == 2026
        assert month_from_text(BLS_RATE_TEXT) == 9
        assert month_from_text(BLS_ES_TEXT) == 9
        assert month_from_text("no month here") is None
        assert year_from_text("no year here") is None
        assert year_from_text(None) is None
        assert month_from_text(None) is None

    def test_payload_exact(self):
        assert bls_payload(["LNS14000000"], 2026) == {
            "seriesid": ["LNS14000000"],
            "startyear": "2026", "endyear": "2026"}
        assert bls_payload(["lns13000000"], None) == {
            "seriesid": ["LNS13000000"]}
        assert bls_payload([], 2026)["seriesid"] == []

    def test_registry(self):
        assert MAX_ALT_CALLS_PER_CLAIM == 2
        viable, reason = route_status("bls-api")
        assert viable is False and "robots" in reason
        assert route_status("fred-api")[0] is False
        assert route_status("imf-weo")[0] is False
        assert route_status("nope") is None


# ---------------------------------------------------------------------------
# BLS response parsing over recorded fixtures
# ---------------------------------------------------------------------------

class TestBlsParsing:
    def test_rate_fixture_values(self):
        obs = parse_bls_observations(_bls_body("LNS14000000"))
        assert len(obs) == 9
        sep = pick_observation(obs, 2026, 9)
        assert sep["value"] == 4.2
        assert sep["period"] == "M09" and sep["latest"] is True
        assert bls_vintage(sep) == "2026-09"

    def test_level_fixture_values(self):
        obs = parse_bls_observations(_bls_body("LNS13000000"))
        sep = pick_observation(obs, 2026, 9)
        assert sep["value"] == 7109.0  # thousands -> 7.109M x1000
        assert bls_vintage(sep) == "2026-09"

    def test_pick_latest_without_year(self):
        obs = parse_bls_observations(_bls_body("LNS14000000"))
        latest = pick_observation(obs)
        assert (latest["year"], latest["period"]) == (2026, "M09")

    def test_pick_year_newest_period(self):
        obs = parse_bls_observations(_bls_body("LNS14000000"))
        assert pick_observation(obs, 2026)["period"] == "M09"
        assert pick_observation([], 2026) is None

    def test_quote_verbatim(self):
        for series, needle in (("LNS14000000", '"value":"4.2"'),
                               ("LNS13000000", '"value":"7109"')):
            body = _bls_body(series)
            obs = pick_observation(parse_bls_observations(body), 2026, 9)
            quote = bls_quote_for(body, obs)
            assert quote and quote in body
            assert needle in quote and '"period":"M09"' in quote

    def test_quote_absent(self):
        assert bls_quote_for("{}", {"year": 2026, "period": "M09",
                                    "value": 4.2}) is None
        assert bls_quote_for(_bls_body("LNS14000000"), {}) is None

    def test_source_meta_shapes(self):
        assert bls_source_meta("LNS14000000") == {
            "indicator": "LNS14000000", "unit": "percent", "scale": 1.0}
        assert bls_source_meta("lns13000000") == {
            "indicator": "LNS13000000", "unit": "count", "scale": 1000.0}
        assert bls_source_meta("NOPE") is None

    def test_malformed_bodies(self):
        assert parse_bls_observations("") == []
        assert parse_bls_observations("<html>nope") == []
        assert parse_bls_observations('{"status":"REQUEST_FAILED"}') == []
        assert parse_bls_observations('{"status":"REQUEST_SUCCEEDED"}') == []
        assert bls_vintage({}) is None
        assert bls_vintage({"year": 2026, "period": "M13"}) == "2026-13"


# ---------------------------------------------------------------------------
# attempt: budget + robots-first (no network in tests)
# ---------------------------------------------------------------------------

class TestAttempt:
    def test_non_blike_zero_calls(self, tmp_path):
        seen = []
        res = attempt_altroutes(
            WB_TEXT, _post_double({}, tmp_path, seen),
            snapshot_dir=tmp_path)
        assert isinstance(res, AltAttempt)
        assert res.calls_made == 0 and res.candidates == []
        assert res.routes_tried == [] and seen == []

    def test_imf_skipped_without_packets(self, tmp_path):
        seen = []
        res = attempt_altroutes(
            IMF_TEXT, _post_double({}, tmp_path, seen),
            snapshot_dir=tmp_path)
        assert res.calls_made == 0 and res.candidates == []
        assert [t["route"] for t in res.routes_tried] == ["imf-weo"]
        assert res.routes_tried[0]["action"] == "skipped"
        assert seen == []

    def test_bls_robots_refusal_no_snapshot(self, tmp_path):
        seen = []
        res = attempt_altroutes(
            BLS_RATE_TEXT,
            _post_double({}, tmp_path, seen, fail_exc=FetchBlocked),
            snapshot_dir=tmp_path)
        assert res.calls_made == 1 and res.candidates == []
        tried = res.routes_tried
        assert len(tried) == 1
        assert tried[0]["route"] == "bls-api/LNS14000000"
        assert tried[0]["action"] == "refused"
        assert tried[0]["reason"] == "FetchBlocked"
        assert seen[0]["url"] == BLS_API_URL
        assert seen[0]["payload"] == {"seriesid": ["LNS14000000"],
                                      "startyear": "2026",
                                      "endyear": "2026"}
        # Refusal archives nothing.
        assert list(tmp_path.iterdir()) == []

    def test_transport_error_recorded(self, tmp_path):
        res = attempt_altroutes(
            BLS_LEVEL_TEXT,
            _post_double({}, tmp_path, fail_exc=FetchError),
            snapshot_dir=tmp_path)
        assert res.calls_made == 1 and res.candidates == []
        assert res.routes_tried[0]["reason"] == "FetchError"

    def test_budget_caps_two_series(self, tmp_path):
        seen = []
        res = attempt_altroutes(
            BLS_AMBIGUOUS_TEXT,
            _post_double({}, tmp_path, seen, fail_exc=FetchBlocked),
            snapshot_dir=tmp_path)
        assert res.calls_made == 2
        assert len(seen) == 2
        assert res.candidates == []

    def test_budget_one_defers_second(self, tmp_path):
        res = attempt_altroutes(
            BLS_AMBIGUOUS_TEXT,
            _post_double({}, tmp_path, fail_exc=FetchBlocked),
            snapshot_dir=tmp_path, budget=1)
        assert res.calls_made == 1
        actions = [t["action"] for t in res.routes_tried]
        assert actions == ["refused", "skipped"]

    def test_success_candidate_rate(self, tmp_path):
        bodies = {"LNS14000000": _bls_body("LNS14000000")}
        res = attempt_altroutes(
            BLS_RATE_TEXT, _post_double(bodies, tmp_path),
            snapshot_dir=tmp_path)
        assert res.calls_made == 1
        assert len(res.candidates) == 1
        cand = res.candidates[0]
        assert cand.quote in bodies["LNS14000000"]
        assert '"value":"4.2"' in cand.quote
        assert cand.source_meta == {"indicator": "LNS14000000",
                                    "unit": "percent", "scale": 1.0}
        assert cand.source_year == 2026
        assert cand.vintage == "2026-09"
        assert res.routes_tried[0]["action"] == "candidate"

    def test_success_candidate_level(self, tmp_path):
        bodies = {"LNS13000000": _bls_body("LNS13000000")}
        res = attempt_altroutes(
            BLS_LEVEL_TEXT, _post_double(bodies, tmp_path),
            snapshot_dir=tmp_path)
        assert len(res.candidates) == 1
        cand = res.candidates[0]
        assert '"value":"7109"' in cand.quote
        assert cand.source_meta["scale"] == 1000.0
        assert cand.source_year == 2026

    def test_never_raises(self, tmp_path):
        def _boom(url, payload, snapshot_dir=None):
            raise RuntimeError("weird transport")
        res = attempt_altroutes(BLS_RATE_TEXT, _boom,
                                snapshot_dir=tmp_path)
        assert res.calls_made == 1 and res.candidates == []
        res = attempt_altroutes(None, _boom, snapshot_dir=tmp_path)
        assert res.calls_made == 0


# ---------------------------------------------------------------------------
# fetch_post: same policy as GET
# ---------------------------------------------------------------------------

def _allow_robots(monkeypatch):
    import live.swarm.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_robots_parser", lambda origin: None)


class TestFetchPost:
    def test_robots_refusal_sends_nothing(self, tmp_path, monkeypatch):
        import urllib.robotparser as rp

        import live.swarm.fetch as fetch_mod

        parser = rp.RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /"])
        monkeypatch.setattr(fetch_mod, "_robots_parser",
                            lambda origin: parser)
        sent = []

        def _spy(req, timeout=None):
            sent.append(req)
            return _FakeResp(b"{}")

        monkeypatch.setattr(fetch_mod, "urlopen", _spy)
        with pytest.raises(FetchBlocked):
            fetch_post("https://api.bls.gov/publicAPI/v2/timeseries/data/",
                       {"seriesid": ["LNS14000000"]},
                       snapshot_dir=tmp_path)
        assert sent == []
        assert list(tmp_path.iterdir()) == []

    def test_rejects_non_http(self, tmp_path):
        with pytest.raises(FetchError):
            fetch_post("file:///etc/passwd", {}, snapshot_dir=tmp_path)
        with pytest.raises(FetchError):
            fetch_post("https://", {}, snapshot_dir=tmp_path)

    def test_refuses_403(self, tmp_path, monkeypatch):
        import live.swarm.fetch as fetch_mod

        _allow_robots(monkeypatch)

        def _raise(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

        monkeypatch.setattr(fetch_mod, "urlopen", _raise)
        with pytest.raises(FetchBlocked):
            fetch_post("https://example.org/walled", {"a": 1},
                       snapshot_dir=tmp_path)

    def test_posts_json_and_snapshots(self, tmp_path, monkeypatch):
        import live.swarm.fetch as fetch_mod

        _allow_robots(monkeypatch)
        body = _bls_body("LNS14000000").encode("utf-8")
        seen = {}

        def _capture(req, timeout=None):
            seen["data"] = req.data
            seen["ctype"] = req.get_header("Content-type")
            seen["method"] = req.get_method()
            return _FakeResp(body, url="https://example.org/api")

        monkeypatch.setattr(fetch_mod, "urlopen", _capture)
        payload = {"seriesid": ["LNS14000000"],
                   "startyear": "2026", "endyear": "2026"}
        res = fetch_post("https://example.org/api", payload,
                         snapshot_dir=tmp_path)
        assert seen["method"] == "POST"
        assert seen["ctype"] == "application/json"
        assert json.loads(seen["data"].decode("utf-8")) == payload
        assert res.sha256 == hashlib.sha256(body).hexdigest()
        assert res.snapshot_path == str(tmp_path / f"{res.sha256}.html")
        with open(res.snapshot_path, "rb") as fh:
            assert fh.read() == body
        assert '"value":"4.2"' in res.text

    def test_size_cap(self, tmp_path, monkeypatch):
        import live.swarm.fetch as fetch_mod

        _allow_robots(monkeypatch)
        monkeypatch.setattr(fetch_mod, "urlopen",
                            lambda req, timeout=None: _FakeResp(b"x" * 100))
        with pytest.raises(FetchError, match="exceeds"):
            fetch_post("https://example.org/big", {"a": 1},
                       snapshot_dir=tmp_path, max_bytes=10)


# ---------------------------------------------------------------------------
# PDF leg: sidecar + deterministic quote check
# ---------------------------------------------------------------------------

PDF_SENTENCE = "The unemployment rate was 4.2 percent in September."


class TestPdfQuoteExists:
    def test_exact(self):
        assert pdf_quote_exists(PDF_SENTENCE, f"xx {PDF_SENTENCE} yy")

    def test_whitespace_normalized(self):
        archived = "The unemployment rate was 4.2\npercent\tin September."
        assert pdf_quote_exists(PDF_SENTENCE, archived)

    def test_negative(self):
        assert not pdf_quote_exists("3.2 percent", PDF_SENTENCE)
        assert not pdf_quote_exists(PDF_SENTENCE, "unrelated text")

    def test_empty_never_matches(self):
        assert pdf_quote_exists("", PDF_SENTENCE) is False
        assert pdf_quote_exists("   ", PDF_SENTENCE) is False
        assert pdf_quote_exists(None, PDF_SENTENCE) is False
        assert pdf_quote_exists(PDF_SENTENCE, None) is False


class TestPdfSidecar:
    def test_roundtrip(self, tmp_path):
        raw = _fixture("v5_catb_pdf_uncompressed.pdf")
        sha = hashlib.sha256(raw).hexdigest()
        path = archive_pdf_text(sha, PDF_SENTENCE, tmp_path)
        assert path == tmp_path / f"{sha}.pdftxt"
        assert read_archived_pdf_text(sha, tmp_path) == PDF_SENTENCE

    def test_missing_returns_none(self, tmp_path):
        assert read_archived_pdf_text("0" * 64, tmp_path) is None

    def test_bad_sha_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            archive_pdf_text("not-a-sha", "x", tmp_path)

    def test_stdlib_extraction_fixture(self):
        raw = _fixture("v5_catb_pdf_uncompressed.pdf")
        assert extract_pdf_text(raw) == PDF_SENTENCE
        # Uncompressed fixture: the sentence also occurs raw (the
        # verbatim-verifiable PDF class); compressed primaries do not.
        assert PDF_SENTENCE.encode("utf-8") in raw


def _pdf_fetch_double(url, snapshot_dir=None):
    raw = _fixture("v5_catb_pdf_uncompressed.pdf")
    sha = hashlib.sha256(raw).hexdigest()
    snap_dir = str(snapshot_dir or "/tmp")
    os.makedirs(snap_dir, exist_ok=True)
    path = os.path.join(snap_dir, f"{sha}.html")
    with open(path, "wb") as fh:
        fh.write(raw)
    return FetchResult(
        url=url, final_url=url, status=200,
        content_type="application/pdf", size=len(raw), sha256=sha,
        snapshot_path=path,
        captured_at="2026-11-02T00:00:00+00:00",
        text=extract_pdf_text(raw))


class TestIngestOpenPdf:
    def test_archives_and_checks(self, tmp_path):
        out = ingest_open_pdf("https://example.org/open.pdf",
                              _pdf_fetch_double, snapshot_dir=tmp_path)
        assert out.archived_text == PDF_SENTENCE
        assert out.sidecar_path is not None
        assert read_archived_pdf_text(
            out.fetch_result.sha256, tmp_path) == PDF_SENTENCE
        assert pdf_quote_exists(PDF_SENTENCE, out.archived_text)

    def test_reader_seam(self, tmp_path):
        out = ingest_open_pdf(
            "https://example.org/open.pdf", _pdf_fetch_double,
            snapshot_dir=tmp_path,
            reader=lambda fetched: f"VISION: {fetched.text}")
        assert out.archived_text == f"VISION: {PDF_SENTENCE}"
        assert pdf_quote_exists("VISION:", out.archived_text)

    def test_walled_pdf_raises_before_archiving(self, tmp_path):
        def _walled(url, snapshot_dir=None):
            raise FetchBlocked("robots.txt disallows")

        with pytest.raises(FetchBlocked):
            ingest_open_pdf("https://example.org/walled.pdf", _walled,
                            snapshot_dir=tmp_path)
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# coordinator plumb: override + alt_lookup + grounding fallback
# ---------------------------------------------------------------------------

class TestBuildPackageOverride:
    def test_override_attaches(self):
        body = _bls_body("LNS14000000")
        obs = pick_observation(parse_bls_observations(body), 2026, 9)
        quote = bls_quote_for(body, obs)
        doc = _doc(BLS_API_URL, body)
        pkg = build_package(
            "B3", BLS_API_URL, quote, doc, 2026, 0, extractor="code",
            source_meta_override=bls_source_meta("LNS14000000"))
        assert pkg is not None
        assert pkg["source_meta"] == {"indicator": "LNS14000000",
                                      "unit": "percent", "scale": 1.0}
        assert pkg["source_year"] == 2026
        assert pkg["extractor"] == "code"
        assert pkg["source_type"] == "secondary"  # api.bls.gov: not primary

    def test_garbage_override_rejected(self):
        body = _bls_body("LNS14000000")
        obs = pick_observation(parse_bls_observations(body), 2026, 9)
        quote = bls_quote_for(body, obs)
        doc = _doc(BLS_API_URL, body)
        assert build_package(
            "B3", BLS_API_URL, quote, doc, 2026, 0,
            source_meta_override={"indicator": "", "unit": "Nope",
                                  "scale": -1}) is None

    def test_default_unchanged(self):
        body = _bls_body("LNS14000000")
        obs = pick_observation(parse_bls_observations(body), 2026, 9)
        quote = bls_quote_for(body, obs)
        doc = _doc(BLS_API_URL, body)
        pkg = build_package("B3", BLS_API_URL, quote, doc, 2026, 0)
        assert pkg is not None and pkg["source_meta"] is None


class TestAltLookup:
    def test_refusing_post_zero_candidates(self, tmp_path):
        config = SwarmConfig(
            post_fn=_post_double({}, tmp_path, fail_exc=FetchBlocked),
            snapshot_dir=tmp_path)
        tools = _Tools(config, _DocStore())
        res = tools.alt_lookup(BLS_RATE_TEXT)
        assert res["ok"] is True
        assert res["candidates"] == [] and res["calls"] == 1

    def test_success_adds_doc(self, tmp_path):
        bodies = {"LNS14000000": _bls_body("LNS14000000")}
        docs = _DocStore()
        config = SwarmConfig(post_fn=_post_double(bodies, tmp_path),
                             snapshot_dir=tmp_path)
        res = _Tools(config, docs).alt_lookup(BLS_RATE_TEXT)
        assert res["ok"] is True and res["calls"] == 1
        assert len(res["candidates"]) == 1
        cand = res["candidates"][0]
        assert docs.find(cand["url"]) is not None
        assert "LNS14000000" in docs.find(cand["url"]).title
        assert cand["quote"] in bodies["LNS14000000"]

    def test_non_blike_no_calls(self, tmp_path):
        seen = []
        config = SwarmConfig(
            post_fn=_post_double({}, tmp_path, seen),
            snapshot_dir=tmp_path)
        res = _Tools(config, _DocStore()).alt_lookup(WB_TEXT)
        assert res["ok"] is True and res["calls"] == 0
        assert res["candidates"] == [] and seen == []


class TestMaybeAddAltroutes:
    def test_success_ships_code_package(self, tmp_path):
        bodies = {"LNS14000000": _bls_body("LNS14000000")}
        config = SwarmConfig(post_fn=_post_double(bodies, tmp_path),
                             snapshot_dir=tmp_path)
        ctx = _ClaimCtx("B3", BLS_RATE_TEXT, config)
        _maybe_add_altroutes(ctx)
        assert len(ctx.packages) == 1
        pkg = ctx.packages[0]
        assert pkg["extractor"] == "code"
        assert pkg["source_meta"]["indicator"] == "LNS14000000"
        assert pkg["quote"] in bodies["LNS14000000"]

    def test_refusal_ships_nothing(self, tmp_path):
        config = SwarmConfig(
            post_fn=_post_double({}, tmp_path, fail_exc=FetchBlocked),
            snapshot_dir=tmp_path)
        ctx = _ClaimCtx("B3", BLS_RATE_TEXT, config)
        _maybe_add_altroutes(ctx)
        assert ctx.packages == []

    def test_skipped_when_packages_exist(self, tmp_path):
        seen = []
        config = SwarmConfig(
            post_fn=_post_double({}, tmp_path, seen),
            snapshot_dir=tmp_path)
        ctx = _ClaimCtx("B3", BLS_RATE_TEXT, config)
        ctx.packages.append({"package_id": "pre-existing"})
        _maybe_add_altroutes(ctx)
        assert seen == [] and len(ctx.packages) == 1

    def test_never_raises(self, tmp_path):
        def _boom(url, payload, snapshot_dir=None):
            raise RuntimeError("weird transport")

        ctx = _ClaimCtx("B3", BLS_RATE_TEXT,
                        SwarmConfig(post_fn=_boom,
                                    snapshot_dir=tmp_path))
        _maybe_add_altroutes(ctx)  # must not raise
        assert ctx.packages == []
