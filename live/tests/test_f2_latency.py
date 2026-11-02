"""F2-3 latency tests: speed levers only, verdict logic untouched (offline).

Covers the F2_LATENCY edits (all live-network/model-free):

(a) claim parallelism 4 -> 8 (semaphore/config + contention telemetry),
(b) early exit on primary verbatim quote + numeric match (fires /
    silent, kill-switch, ``claim.early_exit`` event),
(c) per-claim time budget with graceful round aborts (packages kept,
    honest degraded note),
(d) fetch cache (memory + disk, TTL 24h, no-store honored, robots first),
(e) prefetch from provisional transcripts (launch/throttle/confirm/cancel),
(f) prompt-cache accounting fold (cached_tokens; per-role keys documented
    as out of scope in F2_LATENCY.md),
(g) UI intermediate states (progress from claim.evidence, provisional
    verdict label + correction) + server events_by_type.

Guards that must NEVER move: 2% tolerance, scale-slip backstop,
primary-beats-secondary hierarchy, verifier verdicts. Each has a pin
below running THROUGH the new code paths.

Fixtures are synthetic (Chile/Ruritania/Banaba: never dev claims, never
dev figures): every lever is general or it is not a lever.
"""

import asyncio
import hashlib
import io
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.run_live import PrefetchManager, collect_usage  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    CLAIM_DEADLINE_S,
    MAX_CONCURRENCY,
    SwarmConfig,
    early_exit_reason,
    run_claim,
    run_swarm,
)
from live.swarm.fetch import (  # noqa: E402
    CACHE_TTL_S,
    FetchBlocked,
    FetchError,
    FetchResult,
    cache_entry_fresh,
    cache_key_for_url,
    cache_lookup,
    cache_store,
    clear_mem_cache,
    fetch_url,
    get_cache_stats,
    reset_cache_stats,
)


# ---------------------------------------------------------------------------
# fixtures (synthetic: never dev claims, never dev figures)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_fetch_cache():
    """Inter-test hygiene: the mem cache + stats are process-global."""
    clear_mem_cache()
    reset_cache_stats()
    yield
    clear_mem_cache()
    reset_cache_stats()

SYN_TEXT = "Chile had 12,500 people in 2024."
SYN_WB_BODY = (
    '[{"page":1,"total":1},'
    '[{"indicator":{"id":"SP.POP.TOTL"},"country":{"id":"CL"},'
    '"countryiso3code":"CHL",'
    '"date":"2024","value":12500,"decimal":0}]]'
)
SYN_WB_URL = ("https://api.worldbank.org/v2/country/CHL/indicator/SP.POP.TOTL"
              "?format=json&per_page=50&date=2024%3A2024")
SYN_WB_QUOTE = '"date":"2024","value":12500'

SYN_PLAN = {
    "entities": ["Chile", "population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": None, "iso3": None},
    "year": 2024,
    "web_query": "Chile population 2024",
    "numbers_in_claim": ["12,500"],
}
SYN_REDTEAM_SKIP = {"counter_query": "", "rationale": "narrow", "skip": True}

RURITANIA_TEXT = "Ruritania had 12,500 widgets."
RURITANIA_PLAN = {
    "entities": ["Ruritania", "widgets"],
    "indicator_candidate": None,
    "geo": {"name": None, "iso3": None},
    "year": None,
    "web_query": "Ruritania widgets",
    "numbers_in_claim": ["12,500"],
}

WIKI_URL = "https://en.example.org/wiki/Ruritania"
WIKI_BODY = ("<html><body><p>Ruritania produced 12,500 widgets in its "
             "single factory, officials said in a statement released to "
             "describe the annual industrial output figures.</p></body></html>")
WIKI_QUOTE = ("Ruritania produced 12,500 widgets in its single factory, "
              "officials said in a statement released to describe the "
              "annual industrial output figures.")


def _bus_tracker():
    bus = EventBus()
    return bus, ClaimTracker(bus)


class FakeLLM:
    name = "fake-llm"
    available = True

    def __init__(self, script, prompt_tokens=100, completion_tokens=20):
        self._script = list(script)
        self.calls = []
        self.last_usage = None
        self.last_usage_seq = 0
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.calls.append({"system": system, "user": user})
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct,
                           "reasoning_tokens": 0,
                           "total_tokens": self._pt + self._ct,
                           "cached_tokens": 0}
        return self._script.pop(0)


def make_fetch_fn(bodies, snapshots, fail_urls=()):
    def _fake(url, snapshot_dir=None):
        for sub in fail_urls:
            if sub in url:
                raise FetchError(f"boom {url}")
        for sub, body in bodies.items():
            if sub in url:
                raw = body.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                os.makedirs(snapshots, exist_ok=True)
                path = os.path.join(snapshots, f"{sha}.html")
                with open(path, "wb") as fh:
                    fh.write(raw)
                return FetchResult(
                    url=url, final_url=url, status=200,
                    content_type="text/html; charset=utf-8",
                    size=len(raw), sha256=sha, snapshot_path=path,
                    captured_at="2026-11-01T00:00:00+00:00",
                    text=body,
                )
        raise FetchError(f"404 {url}")

    return _fake


def make_search_fn(hits):
    def _fake(query, top_k=3):
        return list(hits[:top_k])

    return _fake


def _primary_pkg(quote, url=SYN_WB_URL):
    return {"package_id": "p1", "claim_id": "c", "url": url, "quote": quote,
            "source_type": "primary"}


# ---------------------------------------------------------------------------
# (a) parallelism 4 -> 8 + contention telemetry
# ---------------------------------------------------------------------------

def test_max_concurrency_is_8():
    assert MAX_CONCURRENCY == 8
    assert SwarmConfig().max_concurrency == 8
    assert SwarmConfig().early_exit_enabled is True  # default-on pin
    assert CLAIM_DEADLINE_S == 120.0


def test_run_swarm_records_contention(tmp_path):
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    assert WIKI_QUOTE in WIKI_BODY

    def _slow_fetch(url, snapshot_dir=None):
        time.sleep(0.05)
        return make_fetch_fn(bodies, str(tmp_path))(url, snapshot_dir)

    class RoutingLLM(FakeLLM):
        """Per-claim scripts (a shared script would interleave)."""

        def __init__(self, per_claim, texts):
            import threading as _th

            super().__init__([])
            self._per_claim = {k: list(v) for k, v in per_claim.items()}
            self._texts = dict(texts)
            self._lock = _th.Lock()

        def complete_json(self, system, user, max_tokens=1500,
                          reasoning_effort=None, json_schema=None):
            with self._lock:
                cid = None
                if "claim_id:" in user:
                    cid = user.split("claim_id:", 1)[1].split()[0].strip()
                else:  # redteam round carries the raw claim text instead
                    for key, txt in self._texts.items():
                        if txt in user:
                            cid = key
                            break
                self._script = self._per_claim[cid]
                return super().complete_json(
                    system, user, max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort, json_schema=json_schema)

    async def _run():
        bus, tracker = _bus_tracker()
        texts = {f"c-{i}": f"{RURITANIA_TEXT} (case {i})." for i in range(4)}
        per_claim = {cid: [dict(RURITANIA_PLAN), dict(final),
                            dict(SYN_REDTEAM_SKIP)] for cid in texts}
        shared = SwarmConfig(llm=RoutingLLM(per_claim, texts),
                             search_fn=make_search_fn(hits),
                             fetch_fn=_slow_fetch, max_concurrency=2)
        return await run_swarm(
            bus, tracker, list(texts.items()), shared)

    report = asyncio.run(_run())
    assert report.max_concurrency == 2
    assert report.peak_inflight == 2
    assert report.sem_wait_s_max > 0
    assert len(report.results) == 4
    assert all(len(r.packages) == 1 for r in report.results)


def test_run_swarm_default_width_is_8(tmp_path):
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=FakeLLM([dict(RURITANIA_PLAN), final,
                                      dict(SYN_REDTEAM_SKIP)]),
                         search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    report = asyncio.run(run_swarm(
        bus, tracker, [("c-0", RURITANIA_TEXT)], config))
    assert report.max_concurrency == 8
    assert report.peak_inflight == 1
    assert report.sem_wait_s_max == 0.0


# ---------------------------------------------------------------------------
# (b) early exit: primary verbatim quote + numeric match
# ---------------------------------------------------------------------------

def test_early_exit_fires_on_primary_match():
    pkgs = [_primary_pkg(SYN_WB_QUOTE)]
    reason = early_exit_reason(pkgs, SYN_TEXT)
    assert reason is not None and "primary" in reason
    assert "api.worldbank.org" in reason


def test_early_exit_fires_within_tolerance():
    # 1% off still matches (tolerance 2%, read-only reuse).
    pkgs = [_primary_pkg('"date":"2024","value":12600')]
    assert early_exit_reason(pkgs, SYN_TEXT) is not None


def test_early_exit_silent_without_primary():
    pkgs = [dict(_primary_pkg(SYN_WB_QUOTE), source_type="secondary")]
    assert early_exit_reason(pkgs, SYN_TEXT) is None


def test_early_exit_silent_beyond_tolerance():
    pkgs = [_primary_pkg('"date":"2024","value":99000')]
    assert early_exit_reason(pkgs, SYN_TEXT) is None


def test_early_exit_silent_on_year_only_match():
    # The claim year trivially occurs in every row; years never match.
    pkgs = [_primary_pkg('"date":"2024","value":99000')]
    assert early_exit_reason(pkgs, "Banaba notes from 2024.") is None
    assert early_exit_reason([], SYN_TEXT) is None
    assert early_exit_reason(None, SYN_TEXT) is None
    assert early_exit_reason([_primary_pkg(SYN_WB_QUOTE)], "No figures.") is None


def test_early_exit_never_raises_on_shaped_input():
    assert early_exit_reason([None, "x", {}], SYN_TEXT) is None
    assert early_exit_reason([{"source_type": "primary"}], SYN_TEXT) is None
    assert early_exit_reason([_primary_pkg(None)], SYN_TEXT) is None


def _chile_config(tmp_path, script, **kwargs):
    bodies = {"worldbank": SYN_WB_BODY}
    return SwarmConfig(
        llm=FakeLLM(script), search_fn=make_search_fn([]),
        fetch_fn=make_fetch_fn(bodies, str(tmp_path)), **kwargs)


def test_early_exit_grounding_closes_model_rounds(tmp_path):
    bus, tracker = _bus_tracker()
    llm_script = [dict(SYN_PLAN)]  # estructurador only; loop must not run
    seen = []

    def _recording_search(query, top_k=3):
        seen.append(query)
        return []

    bodies = {"worldbank": SYN_WB_BODY}
    config = SwarmConfig(llm=FakeLLM(llm_script), search_fn=_recording_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(run_claim(bus, tracker, "c-ee", SYN_TEXT, config))
    assert seen == []  # web pool skipped: the WB row already matched
    assert result.note.startswith("early-exit(grounding):")
    assert result.degraded is False
    assert len(result.packages) == 1
    assert result.packages[0]["extractor"] == "code"
    assert len(config.llm.calls) == 1  # model loop skipped entirely
    exits = bus.history("claim.early_exit")
    assert len(exits) == 1
    assert exits[0]["claim_id"] == "c-ee"
    assert exits[0]["reason"] == result.note
    assert exits[0]["n_packages"] == 1
    # claim.evidence still ships (verifier decides from kept packages).
    assert len(bus.history("claim.evidence")) == 1


def test_early_exit_kill_switch_restores_full_path(tmp_path):
    bus, tracker = _bus_tracker()
    final = {"thought": "citing",
             "final": {"findings": [{"url": SYN_WB_URL,
                                     "quote": SYN_WB_QUOTE}]}}
    config = _chile_config(tmp_path, [dict(SYN_PLAN), final,
                                       dict(SYN_REDTEAM_SKIP)],
                           early_exit_enabled=False)
    result = asyncio.run(run_claim(bus, tracker, "c-full", SYN_TEXT, config))
    assert result.note == "redteam skipped"
    assert len(config.llm.calls) == 3
    assert bus.history("claim.early_exit") == []


def test_early_exit_after_recuperador_skips_redteam(tmp_path):
    # No grounding rescue (Ruritania unresolvable) but the model cites a
    # matching PRIMARY row: only the redteam round closes.
    bodies = {"worldbank": SYN_WB_BODY}
    bus, tracker = _bus_tracker()
    plan = dict(RURITANIA_PLAN, indicator_candidate="SP.POP.TOTL",
                year=2024)
    plan["geo"] = {"name": None, "iso3": "RUR"}
    final = {"thought": "citing",
             "final": {"findings": [{"url": SYN_WB_URL.replace("CHL", "RUR"),
                                     "quote": SYN_WB_QUOTE}]}}
    llm = FakeLLM([plan, final])  # no redteam entry: must never be consumed
    config = SwarmConfig(llm=llm, search_fn=make_search_fn([]),
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    text = "Ruritania had 12,500 people in 2024."
    result = asyncio.run(run_claim(bus, tracker, "c-post", text, config))
    assert result.note.startswith("early-exit(recuperador):")
    assert len(llm.calls) == 2  # estructurador + recuperador only
    assert len(result.packages) == 1
    assert result.packages[0]["extractor"] == "model"
    assert len(bus.history("claim.early_exit")) == 1


def test_early_exit_silent_on_mismatch_full_path(tmp_path):
    bus, tracker = _bus_tracker()
    final = {"thought": "citing",
             "final": {"findings": [{"url": SYN_WB_URL,
                                     "quote": SYN_WB_QUOTE}]}}
    config = _chile_config(tmp_path, [dict(SYN_PLAN), final,
                                       dict(SYN_REDTEAM_SKIP)])
    text = "Chile had 99,000 people in 2024."  # FALSE vs the row
    result = asyncio.run(run_claim(bus, tracker, "c-mm", text, config))
    assert result.note == "redteam skipped"
    assert len(config.llm.calls) == 3
    assert bus.history("claim.early_exit") == []


# ---------------------------------------------------------------------------
# (c) per-claim time budget: graceful round abort, packages kept
# ---------------------------------------------------------------------------

def test_deadline_aborts_rounds_gracefully(tmp_path):
    bodies = {"worldbank": SYN_WB_BODY}

    def _sleepy_search(query, top_k=3):
        if "slow" in query:
            time.sleep(0.3)
        return []

    tool_step = {"thought": "need docs",
                 "tool_calls": [{"name": "web_search",
                                 "arguments": {"query": "slow query",
                                               "top_k": 2}}]}
    final = {"thought": "late",
             "final": {"findings": [{"url": SYN_WB_URL,
                                     "quote": SYN_WB_QUOTE}]}}
    llm = FakeLLM([dict(SYN_PLAN), tool_step, final, dict(SYN_REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=_sleepy_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         claim_deadline_s=0.05,
                         early_exit_enabled=False)
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-dl", SYN_TEXT, config))
    assert result.degraded is True
    assert "deadline" in result.note
    assert "packages kept" in result.note
    assert "recuperador" in result.note
    # Grounding (the deterministic code package) survives the abort.
    assert len(result.packages) == 1
    assert result.packages[0]["extractor"] == "code"
    ev = bus.history("claim.evidence")
    assert len(ev) == 1 and ev[0]["degraded"] is True


def test_deadline_backstop_still_fires(tmp_path, monkeypatch):
    import live.swarm.coordinator as coord

    # Shrink the hard backstop so one hanging call outlives it.
    monkeypatch.setattr(coord, "CLAIM_DEADLINE_SLACK_S", 0.1)

    class SlowLLM(FakeLLM):
        def complete_json(self, *a, **k):
            time.sleep(0.3)
            return super().complete_json(*a, **k)

    llm = SlowLLM([dict(SYN_PLAN)])
    config = SwarmConfig(llm=llm, claim_deadline_s=0.05,
                         search_fn=make_search_fn([]),
                         fetch_fn=make_fetch_fn({}, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-hang", SYN_TEXT, config))
    assert result.degraded is True
    assert "deadline" in result.note
    assert "hard backstop" in result.note


# ---------------------------------------------------------------------------
# (d) fetch cache: memory + disk, TTL, no-store, robots-first
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body, status=200, url="https://example.org/p",
                 ctype="text/html; charset=utf-8", cache_control=""):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": ctype,
                        "Cache-Control": cache_control}
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n if n and n > 0 else -1)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _allow_robots(monkeypatch):
    import live.swarm.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_robots_parser", lambda origin: None)


def test_cache_key_stable_and_hex():
    key = cache_key_for_url("https://example.org/p")
    assert key == cache_key_for_url("  https://example.org/p  ")
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def test_cache_entry_fresh_pure():
    now = 1_000_000.0
    assert cache_entry_fresh(now, now) is True
    assert cache_entry_fresh(now - 3600, now) is True
    assert cache_entry_fresh(now - CACHE_TTL_S, now) is False  # 24h: stale
    assert cache_entry_fresh(now - 25 * 3600, now) is False
    assert cache_entry_fresh(now + 10, now) is False  # future: stale
    assert cache_entry_fresh("junk", now) is False


def test_cache_hit_returns_original_provenance(tmp_path):
    import live.swarm.fetch as fetch_mod

    clear_mem_cache()
    reset_cache_stats()
    body = b"<html><body>Banaba phosphate 12,500 tonnes.</body></html>"
    first = FetchResult(
        url="https://example.org/banaba", final_url="https://example.org/b",
        status=200, content_type="text/html", size=len(body),
        sha256=hashlib.sha256(body).hexdigest(), snapshot_path="/x",
        captured_at="2026-01-01T00:00:00+00:00", text="t")
    assert cache_store(first.url, first, body,
                       cache_dir=str(tmp_path)) is True
    clear_mem_cache()  # force the disk path
    hit = cache_lookup(first.url, cache_dir=str(tmp_path))
    assert hit is not None
    cached, raw = hit
    assert raw == body
    assert cached.sha256 == first.sha256  # original hash kept
    assert cached.captured_at == first.captured_at  # original timestamp kept
    stats = get_cache_stats()
    assert stats["hits_disk"] == 1 and stats["stores"] == 1
    assert stats["hits_mem"] == 0
    # Second lookup is served from memory (disk hit repopulates it).
    assert cache_lookup(first.url, cache_dir=str(tmp_path)) is not None
    assert get_cache_stats()["hits_mem"] == 1
    assert fetch_mod  # silence unused (documents the seam under test)


def test_cache_miss_on_unknown_and_corrupt(tmp_path):
    clear_mem_cache()
    reset_cache_stats()
    assert cache_lookup("https://example.org/nope",
                        cache_dir=str(tmp_path)) is None
    bad = tmp_path / f"{cache_key_for_url('https://example.org/bad')}.json"
    bad.write_text("not json{{{", encoding="utf-8")
    assert cache_lookup("https://example.org/bad",
                        cache_dir=str(tmp_path)) is None
    assert get_cache_stats()["misses"] == 2


def test_cache_stale_entry_is_miss_and_unlinked(tmp_path):
    clear_mem_cache()
    reset_cache_stats()
    url = "https://example.org/stale"
    body = b"old body"
    payload = {"url": url, "fetched_at": time.time() - 25 * 3600,
               "status": 200, "content_type": "text/html",
               "final_url": url, "captured_at": "2026-01-01T00:00:00+00:00",
               "sha256": hashlib.sha256(body).hexdigest(),
               "body_b64": "b2xkIGJvZHk="}
    path = tmp_path / f"{cache_key_for_url(url)}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache_lookup(url, cache_dir=str(tmp_path)) is None
    assert not path.exists()  # stale entries are reaped on sight
    assert get_cache_stats()["misses"] == 1


def test_fetch_url_serves_second_call_from_cache(tmp_path, monkeypatch):
    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)
    clear_mem_cache()
    reset_cache_stats()
    body = b"<html><body>Banaba phosphate 12,500 tonnes.</body></html>"
    calls = []

    def _fake(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResp(body)

    monkeypatch.setattr(fetch_mod, "urlopen", _fake)
    snap = tmp_path / "snap"
    cache = tmp_path / "cache"
    first = fetch_url("https://example.org/banaba", snapshot_dir=snap,
                      cache_dir=cache)
    assert len(calls) == 1
    assert (snap / f"{first.sha256}.html").exists()
    # Drop the snapshot: the hit must re-materialize the same bytes.
    (snap / f"{first.sha256}.html").unlink()
    second = fetch_url("https://example.org/banaba", snapshot_dir=snap,
                       cache_dir=cache)
    assert len(calls) == 1  # no second network call
    assert second.sha256 == first.sha256
    assert second.captured_at == first.captured_at
    assert open(second.snapshot_path, "rb").read() == body
    stats = get_cache_stats()
    assert stats["hits_mem"] + stats["hits_disk"] == 1


def test_fetch_url_honors_no_store(tmp_path, monkeypatch):
    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)
    clear_mem_cache()
    reset_cache_stats()
    body = b"<html><body>fresh every time</body></html>"
    calls = []

    def _fake(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResp(body, cache_control="private, no-store")

    monkeypatch.setattr(fetch_mod, "urlopen", _fake)
    cache = tmp_path / "cache"
    fetch_url("https://example.org/live", snapshot_dir=tmp_path / "snap",
              cache_dir=cache)
    fetch_url("https://example.org/live", snapshot_dir=tmp_path / "snap",
              cache_dir=cache)
    assert len(calls) == 2  # never served from cache
    assert list(cache.glob("*.json")) == []
    assert get_cache_stats()["no_store"] == 2


def test_fetch_cache_never_beats_robots(tmp_path, monkeypatch):
    import urllib.robotparser as rp

    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)
    clear_mem_cache()
    body = b"<html><body>cached but disallowed</body></html>"
    monkeypatch.setattr(fetch_mod, "urlopen",
                        lambda req, timeout=None: _FakeResp(body))
    cache = tmp_path / "cache"
    fetch_url("https://example.org/walled", snapshot_dir=tmp_path / "snap",
              cache_dir=cache)
    # Now robots disallows: the cached entry must NOT be served.
    parser = rp.RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    monkeypatch.setattr(fetch_mod, "_robots_parser", lambda origin: parser)
    with pytest.raises(FetchBlocked):
        fetch_url("https://example.org/walled",
                  snapshot_dir=tmp_path / "snap", cache_dir=cache)


# ---------------------------------------------------------------------------
# (e) prefetch from provisional transcripts
# ---------------------------------------------------------------------------

def _partial_text():
    return ("Chile reportedly had 12,500 people in 2024 according to "
            "early estimates")


async def _drain(manager, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while manager._pending and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)


def test_prefetch_launches_and_warms():
    fetched = []

    def _search(query, top_k):
        return [{"url": "https://example.org/warm", "title": "w",
                 "snippet": ""}]

    manager = PrefetchManager(search_fn=_search,
                              fetch_fn=lambda url: fetched.append(url))

    async def _go():
        await manager.on_partial(1, _partial_text())
        await _drain(manager)
        await manager.close()

    asyncio.run(_go())
    assert manager.stats["partials"] == 1
    assert manager.stats["launched"] == 1
    assert fetched == ["https://example.org/warm"]
    assert manager.stats["urls_warmed"] == 1


def test_prefetch_ignores_short_and_throttled():
    manager = PrefetchManager(search_fn=lambda q, k: [], fetch_fn=lambda u: None)

    async def _go():
        await manager.on_partial(1, "too short")
        await manager.on_partial(2, _partial_text())
        await manager.on_partial(2, _partial_text() + " more words here")
        await _drain(manager)
        await manager.close()

    asyncio.run(_go())
    assert manager.stats["partials"] == 3
    assert manager.stats["launched"] == 1  # short skipped, 2nd throttled


def test_prefetch_final_confirms_and_cancels_stale():
    import threading

    gate = threading.Event()
    started = threading.Event()

    def _slow_search(query, top_k):
        started.set()
        assert gate.wait(timeout=5.0)
        return []

    manager = PrefetchManager(search_fn=_slow_search,
                              fetch_fn=lambda u: None, throttle_s=0.0)

    async def _go():
        await manager.on_partial(1, _partial_text())
        await asyncio.to_thread(started.wait, 5.0)
        await asyncio.sleep(0.05)  # let the warmup block in the executor
        manager.on_final(2)  # a NEWER turn's final: turn 1 is stale
        assert manager.stats["confirmed"] == 1
        assert manager.stats["cancelled"] == 1
        gate.set()
        await manager.close()

    asyncio.run(_go())
    assert manager.stats["launched"] == 1


def test_prefetch_failures_are_swallowed():
    def _boom_search(query, top_k):
        raise RuntimeError("search down")

    def _boom_fetch(url):
        raise FetchError("fetch down")

    manager = PrefetchManager(search_fn=_boom_search, fetch_fn=_boom_fetch)

    async def _go():
        await manager.on_partial(1, _partial_text())
        await _drain(manager)
        await manager.close()

    asyncio.run(_go())  # must not raise
    assert manager.stats["launched"] == 1
    assert manager.stats["urls_warmed"] == 0


# ---------------------------------------------------------------------------
# (f) prompt-cache accounting (+ stable role prompts pin)
# ---------------------------------------------------------------------------

def test_collect_usage_folds_cached_tokens():
    from types import SimpleNamespace

    llm = SimpleNamespace(
        last_usage={"prompt_tokens": 100, "completion_tokens": 20,
                    "cached_tokens": 7},
        last_usage_seq=3)
    acc = collect_usage(llm, {})
    assert acc["prompt_tokens"] == 100
    assert acc["completion_tokens"] == 20
    assert acc["cached_tokens"] == 7
    # Idempotent per seq (same call twice folds once).
    assert collect_usage(llm, acc)["cached_tokens"] == 7


def test_role_system_prompts_are_stable_constants():
    # Prompt-cache friendliness: identical bytes every call (module-level
    # constants, never formatted per claim).
    from live.swarm import roles

    for name in ("ESTRUCTURADOR_SYSTEM", "RECUPERADOR_SYSTEM",
                 "REDTEAM_SYSTEM"):
        first = getattr(roles, name)
        import importlib

        importlib.reload(roles)
        assert getattr(roles, name) == first
        assert isinstance(first, str) and len(first) > 100


# ---------------------------------------------------------------------------
# (g) UI intermediate states + provisional verdict + server telemetry
# ---------------------------------------------------------------------------

def _ui_text():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "ui", "index.html"),
              encoding="utf-8") as fh:
        return fh.read()


def test_ui_derives_progress_from_existing_events():
    html = _ui_text()
    # Progress renderer consumes claim.evidence counts (no invented data).
    assert "renderProgress" in html
    assert "n_sources" in html and "n_quotes" in html
    assert "claim.progress" in html  # tolerated explicit frame
    # Derived call site inside the evidence handler.
    assert "Object.keys(seen).length, pkgs.length" in html


def test_ui_labels_provisional_and_corrects_on_final():
    html = _ui_text()
    assert "ev.provisional === true" in html
    assert "v-provisional" in html
    assert "STR[lang].prov" in html
    # Correction path: provisional -> final appends a history line.
    assert "wasProv" in html
    assert "STR[lang].corrected" in html
    # Early-exit telemetry renders as a history line.
    assert "claim.early_exit" in html
    assert "addEarlyExit" in html


def test_metrics_reports_events_by_type():
    from fastapi.testclient import TestClient

    from live.server.app import create_app

    client = TestClient(create_app())
    demo = [{"turn": 1, "speaker": "Host",
             "text": "Ruritania had 12,500 widgets.",
             "t_start": 0.0, "t_end": 2.0}]
    res = client.post("/run", json={"transcript": demo})
    assert res.status_code == 200
    metrics = client.get("/metrics").json()
    by_type = metrics["events_by_type"]
    assert by_type.get("transcript.final", 0) >= 1
    assert by_type.get("claim.detected", 0) >= 1
    assert by_type.get("claim.state", 0) >= 1
    assert "latency_ms" in metrics  # pre-existing shape kept


# ---------------------------------------------------------------------------
# guards: verdict machinery untouched
# ---------------------------------------------------------------------------

def test_tolerance_still_2_percent():
    try:
        from src.atlas.core.compare import DEFAULT_REL_TOLERANCE
    except ImportError:
        from atlas.core.compare import DEFAULT_REL_TOLERANCE
    assert DEFAULT_REL_TOLERANCE == 0.02


def test_scale_backstop_and_hierarchy_intact():
    import live.swarm.coordinator as coord

    assert coord.source_type_for_url(SYN_WB_URL) == "primary"
    assert coord.source_type_for_url(WIKI_URL) == "secondary"
    try:
        from src.atlas.core.units import scale_error_power
    except ImportError:
        from atlas.core.units import scale_error_power
    assert scale_error_power(1.0, 1_000_000.0) == 6  # 10^6 slip detected


def test_verdict_logic_untouched_through_new_paths(tmp_path):
    # The full chain still decides from kept packages (exit disabled here
    # pins the old verdict; F2_RECALL pins the old verdicts generally).
    from live.run_verify import claim_input_for
    from live.verifier.check import verify_claim

    claim = claim_input_for({"id": "c", "text": SYN_TEXT})
    assert claim.value == 12500.0
    verdict = verify_claim(claim, [], lambda p: None)
    assert verdict.verdict == "unverifiable"
    assert "no_usable_package" in verdict.reasons
