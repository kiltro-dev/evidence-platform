"""M2b swarm tests: doubles (0 live) + one gated LIVE 3-claim run.

Offline tests use FakeLLM (complete_json interface), FakeSearch and
FakeFetch doubles. The LIVE test runs only with ATLAS_LIVE=1 and a
configured MODEL_API_KEY; it investigates 3 TRUE keynote EN claims
with known World Bank truth and checks literal quotes grep in their
snapshots. Live budget: <= $6 (measured, not estimated).
"""

import asyncio
import hashlib
import io
import json
import os
import urllib.error

import pytest

from live.orchestrator.bus import EventBus
from live.orchestrator.states import ClaimTracker
from live.swarm.coordinator import (
    SwarmConfig,
    build_package,
    numbers_from_quote,
    run_claim,
    run_swarm,
    source_type_for_url,
)
from live.swarm.fetch import (
    FetchBlocked,
    FetchError,
    FetchResult,
    fetch_url,
)
from live.swarm.roles import TOOL_NAMES, parse_tool_step

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------

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
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens})
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct,
                           "reasoning_tokens": 0,
                           "total_tokens": self._pt + self._ct,
                           "cached_tokens": 0}
        return self._script.pop(0)


class FailingLLM(FakeLLM):
    available = False

    def complete_json(self, *a, **k):
        raise RuntimeError("no model")


def make_fetch_fn(bodies, snapshots, fail_urls=()):
    """Fake fetch: url-substring -> body; writes real tmp snapshots."""

    def _fake(url, snapshot_dir=None):
        for sub in fail_urls:
            if sub in url:
                raise FetchError(f"boom {url}")
        for sub, body in bodies.items():
            if sub in url:
                raw = body.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                snap_dir = snapshots
                os.makedirs(snap_dir, exist_ok=True)
                path = os.path.join(snap_dir, f"{sha}.html")
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


def make_search_fn(hits_by_query, seen=None):
    def _fake(query, top_k=3):
        if seen is not None:
            seen.append(query)
        for sub, hits in hits_by_query.items():
            if sub.lower() in query.lower():
                return hits[:top_k]
        return hits_by_query.get("*", [])[:top_k]

    return _fake


WB_BODY = (
    '[{"page": 1, "lastupdated": "2025-09-01"}, '
    '[{"indicator": {"id": "SP.POP.TOTL"}, "country": {"id": "WLD"}, '
    '"date": "2024", "value": 8140897523, "decimal": "0"}]]'
)
WB_QUOTE = '"date": "2024", "value": 8140897523'

WIKI_BODY = (
    "<html><body><p>The world population reached 8.14 billion people "
    "in 2024 according to United Nations estimates.</p></body></html>"
)
WIKI_QUOTE = ("The world population reached 8.14 billion people in 2024 "
              "according to United Nations estimates.")

ESTRUCT_PLAN = {
    "entities": ["World Bank", "world population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": None, "iso3": None},
    "year": 2024,
    "web_query": "world population 2024",
    "numbers_in_claim": ["8.14 billion"],
}

REDTEAM_SKIP = {"counter_query": "", "rationale": "narrow", "skip": True}


def _wb_url(iso3, year=None):
    base = f"https://api.worldbank.org/v2/country/{iso3}/indicator/SP.POP.TOTL"
    if year is None:
        return base + "?format=json&per_page=50&mrnev=1"
    return base + f"?format=json&per_page=50&date={year}%3A{year}"


def _recup_final_wb(url):
    return {"thought": "wb row answers it",
            "final": {"findings": [{"url": url, "quote": WB_QUOTE}]}}


WIKI_URL = "https://en.wikipedia.org/wiki/World_population"


# ---------------------------------------------------------------------------
# fetch.py: responsible-fetch contract (doubles for urlopen/robots)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body, status=200, url="https://example.org/p",
                 ctype="text/html; charset=utf-8"):
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


def _allow_robots(monkeypatch):
    import live.swarm.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_robots_parser", lambda origin: None)


def test_fetch_rejects_non_http(tmp_path):
    _allow = None
    with pytest.raises(FetchError):
        fetch_url("file:///etc/passwd", snapshot_dir=tmp_path)
    with pytest.raises(FetchError):
        fetch_url("ftp://example.org/x", snapshot_dir=tmp_path)
    with pytest.raises(FetchError):
        fetch_url("https://", snapshot_dir=tmp_path)


def test_fetch_honors_robots(tmp_path, monkeypatch):
    import urllib.robotparser as rp

    import live.swarm.fetch as fetch_mod

    parser = rp.RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    monkeypatch.setattr(fetch_mod, "_robots_parser", lambda origin: parser)
    with pytest.raises(FetchBlocked):
        fetch_url("https://example.org/page", snapshot_dir=tmp_path)


def test_fetch_snapshots_and_hashes(tmp_path, monkeypatch):
    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)
    body = b"<html><body>8.14 billion people in 2024.</body></html>"
    monkeypatch.setattr(fetch_mod, "urlopen",
                        lambda req, timeout=None: _FakeResp(body))
    res = fetch_url("https://example.org/p", snapshot_dir=tmp_path)
    assert res.sha256 == hashlib.sha256(body).hexdigest()
    assert res.snapshot_path == str(tmp_path / f"{res.sha256}.html")
    assert open(res.snapshot_path, "rb").read() == body
    assert res.status == 200 and res.size == len(body)
    assert "captured_at" in res.__dict__ and res.captured_at.startswith("20")


def test_fetch_size_cap(tmp_path, monkeypatch):
    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)
    monkeypatch.setattr(
        fetch_mod, "urlopen",
        lambda req, timeout=None: _FakeResp(b"x" * 100))
    with pytest.raises(FetchError, match="exceeds"):
        fetch_url("https://example.org/big", snapshot_dir=tmp_path,
                  max_bytes=10)


def test_fetch_refuses_login_status(tmp_path, monkeypatch):
    import live.swarm.fetch as fetch_mod

    _allow_robots(monkeypatch)

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(fetch_mod, "urlopen", _raise)
    with pytest.raises(FetchBlocked):
        fetch_url("https://example.org/walled", snapshot_dir=tmp_path)


# ---------------------------------------------------------------------------
# roles.py: tool specs + step parsing
# ---------------------------------------------------------------------------

def test_tool_specs_cover_four_tools():
    assert TOOL_NAMES == {"web_search", "fetch_url", "wb_lookup",
                          "extra_lookup"}


def test_parse_tool_step_drops_unknown_tools():
    calls, final = parse_tool_step({
        "thought": "t",
        "tool_calls": [
            {"name": "web_search", "arguments": {"query": "q"}},
            {"name": "rm_rf", "arguments": {}},
            {"name": "fetch_url", "arguments": "not-a-dict"},
        ],
    })
    assert calls == [{"name": "web_search", "arguments": {"query": "q"}}]
    assert final is None
    _calls, final = parse_tool_step(
        {"thought": "t", "final": {"findings": []}})
    assert final == {"findings": []}
    assert parse_tool_step("garbage") == ([], None)


# ---------------------------------------------------------------------------
# coordinator pure helpers
# ---------------------------------------------------------------------------

def test_numbers_from_quote_scales():
    nums = numbers_from_quote(
        "GDP was 29.3 trillion dollars in 2024.")
    assert nums[0]["value"] == pytest.approx(29.3e12)
    assert nums[0]["unit"] == "USD"
    assert nums[0]["scale_raw"] == "trillion"
    nums = numbers_from_quote("France had 68.6 million inhabitants.")
    assert nums[0]["value"] == pytest.approx(68.6e6)
    assert nums[0]["unit"] is None
    assert nums[0]["scale_raw"] == "million"


def test_source_type_for_url():
    assert source_type_for_url(
        "https://api.worldbank.org/v2/country/WLD/x") == "primary"
    assert source_type_for_url(
        "https://en.wikipedia.org/wiki/X") == "secondary"


def test_build_package_rejects_non_verbatim(tmp_path):
    fetch = FetchResult(
        url="https://example.org/p", final_url="https://example.org/p",
        status=200, content_type="text/html", size=10,
        sha256="a" * 64, snapshot_path=str(tmp_path / "x.html"),
        captured_at="2026-11-01T00:00:00+00:00", text="hello world")
    from live.swarm.coordinator import Doc

    doc = Doc(url=fetch.url, title="", fetch=fetch, excerpt="hello")
    assert build_package("c1", fetch.url, "invented quote", doc, 2024,
                         0) is None
    pkg = build_package("c1", fetch.url, "hello", doc, 2024, 0)
    assert pkg is not None
    assert pkg["offsets"] == [0, 5]
    assert pkg["extractor"] == "model"


# ---------------------------------------------------------------------------
# coordinator pipeline (doubles, 0 live)
# ---------------------------------------------------------------------------

def _bus_tracker():
    bus = EventBus()
    return bus, ClaimTracker(bus)


def test_happy_path_emits_m2a_valid_package(tmp_path):
    bodies = {"worldbank": WB_BODY,
              "wikipedia": WIKI_BODY}
    search = make_search_fn({
        "*": [{"url": "https://en.wikipedia.org/wiki/World_population",
               "title": "World population", "snippet": ""}],
    })
    # F2-2: the model cites a LONGER verbatim span than the deterministic
    # row rescue (identical spans dedup by design), so both paths are pinned.
    model_quote = ('{"indicator": {"id": "SP.POP.TOTL"}, '
                   '"country": {"id": "WLD"}, ' + WB_QUOTE)
    assert model_quote in WB_BODY
    llm = FakeLLM([ESTRUCT_PLAN,
                   {"thought": "wb row answers it",
                    "final": {"findings": [{"url": _wb_url("WLD", 2024),
                                            "quote": model_quote}]}},
                   REDTEAM_SKIP])
    # F2-3: this fixture's WB row matches the claim, so the default-on
    # early exit would close the model rounds; pin the full model path.
    # F2-7: redteam is deferred by default; pin the full 3-call path.
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         early_exit_enabled=False, redteam_enabled=True)
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(
        bus, tracker, "T1-en",
        "We are 8.14 billion people sharing one planet.", config))
    assert result.degraded is False
    # F2-2: grounding adds one deterministic code package (WB row rescue)
    # next to the model package; the pins below still hold on the MODEL one.
    model_pkgs = [p for p in result.packages if p["extractor"] == "model"]
    code_pkgs = [p for p in result.packages if p["extractor"] == "code"]
    assert len(model_pkgs) == 1
    assert len(code_pkgs) == 1
    assert code_pkgs[0]["source_meta"] is not None
    pkg = model_pkgs[0]
    assert pkg["claim_id"] == "T1-en"
    assert pkg["quote"] == model_quote
    assert pkg["source_type"] == "primary"
    assert pkg["extractor"] == "model"
    assert pkg["offsets"][1] - pkg["offsets"][0] == len(model_quote)
    # quote greps in its snapshot (fake fetch wrote it under tmp_path)
    snap = open(str(tmp_path / f"{pkg['sha256']}.html"),
                encoding="utf-8").read()
    assert model_quote in snap
    # M2a validates the final schema
    from live.evidence.package import EvidencePackage

    EvidencePackage.from_dict(pkg)
    # bus contract: investigando + one claim.evidence
    states = [e for e in bus.history("claim.state")
              if e["claim_id"] == "T1-en"]
    assert [s["state"] for s in states] == ["escuchada", "investigando"]
    ev = bus.history("claim.evidence")
    assert len(ev) == 1 and ev[0]["packages"] == result.packages
    assert result.cost_usd > 0 and result.usage["model_calls"] == 3


def test_invented_quote_yields_zero_packages(tmp_path):
    bodies = {"wikipedia": WIKI_BODY}
    search = make_search_fn({
        "*": [{"url": "https://en.wikipedia.org/wiki/World_population",
               "title": "W", "snippet": ""}],
    })
    bad_final = {"thought": "lying",
                 "final": {"findings": [
                     {"url": "https://en.wikipedia.org/wiki/World_population",
                      "quote": "the moon is made of cheese"}]}}
    llm = FakeLLM([ESTRUCT_PLAN, bad_final, REDTEAM_SKIP])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-bad", "Some claim.",
                                   config))
    assert result.packages == []
    assert result.degraded is False  # attempted, honestly empty
    assert bus.history("claim.evidence")[0]["packages"] == []


def test_uncited_url_is_dropped(tmp_path):
    bodies = {"wikipedia": WIKI_BODY}
    search = make_search_fn({"*": []})
    evil = {"thought": "evil",
            "final": {"findings": [
                {"url": "https://evil.example.org/never-fetched",
                 "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([ESTRUCT_PLAN, evil, REDTEAM_SKIP])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-evil", "Some claim.",
                                   config))
    assert result.packages == []


def test_no_model_degrades_without_packages(tmp_path):
    config = SwarmConfig(llm=FailingLLM([]),
                         fetch_fn=make_fetch_fn({}, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-nm", "Some claim.",
                                   config))
    assert result.degraded is True
    assert result.packages == []
    assert result.note == "no model available"
    assert bus.history("claim.evidence")[0]["degraded"] is True


def test_recuperador_tool_loop_calls_web_search(tmp_path):
    bodies = {"wikipedia": WIKI_BODY}
    seen: list[str] = []
    search = make_search_fn({
        "*": [{"url": "https://en.wikipedia.org/wiki/World_population",
               "title": "W", "snippet": ""}],
    }, seen=seen)
    tool_step = {"thought": "need docs",
                 "tool_calls": [{"name": "web_search",
                                 "arguments": {"query": "world population",
                                               "top_k": 2}}]}
    final = {"thought": "got it",
             "final": {"findings": [
                 {"url": "https://en.wikipedia.org/wiki/World_population",
                  "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([ESTRUCT_PLAN, tool_step, final, REDTEAM_SKIP])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-tools",
                                   "We are 8.14 billion people.", config))
    assert any("world population" in q for q in seen)
    assert len(result.packages) == 1
    assert result.packages[0]["source_type"] == "secondary"


def test_redteam_counter_query_triggers_second_round(tmp_path):
    bodies = {"worldbank": WB_BODY, "wikipedia": WIKI_BODY}
    seen: list[str] = []
    search = make_search_fn({
        "revised": [{"url": "https://en.wikipedia.org/wiki/World_population",
                     "title": "W", "snippet": ""}],
        "*": [{"url": "https://en.wikipedia.org/wiki/World_population",
               "title": "W", "snippet": ""}],
    }, seen=seen)
    redteam = {"counter_query": "world population revised 2024",
               "rationale": "vintage risk", "skip": False}
    final2 = {"thought": "counter-check",
              "final": {"findings": [
                  {"url": "https://en.wikipedia.org/wiki/World_population",
                   "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([ESTRUCT_PLAN, _recup_final_wb(_wb_url("WLD", 2024)),
                   redteam, final2, REDTEAM_SKIP])
    # F2-3: pin the full redteam round (default-on exit would close it).
    # F2-7: redteam is deferred by default; opt back in to pin the round.
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         early_exit_enabled=False, redteam_enabled=True)
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-red",
                                   "We are 8.14 billion people on this planet.",
                                   config))
    assert any("revised" in q for q in seen)
    assert len(result.packages) == 2


def test_deadline_degrades(tmp_path):
    class SlowLLM(FakeLLM):
        def complete_json(self, *a, **k):
            import time as _t

            _t.sleep(0.3)
            return super().complete_json(*a, **k)

    llm = SlowLLM([ESTRUCT_PLAN])
    # F2-3: stub search (the soft/hard split lets grounding run past the
    # tiny budget; without the stub this offline test would hit network).
    config = SwarmConfig(llm=llm, claim_deadline_s=0.05,
                         search_fn=make_search_fn({}),
                         fetch_fn=make_fetch_fn({}, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-slow", "Slow claim.",
                                   config))
    assert result.degraded is True
    assert "deadline" in result.note


def test_malformed_reply_retried_once(tmp_path):
    class FlakyLLM(FakeLLM):
        def complete_json(self, *a, **k):
            if not getattr(self, "_flaked", False):
                self._flaked = True
                self.last_usage_seq += 1
                self.last_usage = {"prompt_tokens": 50,
                                   "completion_tokens": 10,
                                   "reasoning_tokens": 0,
                                   "total_tokens": 60,
                                   "cached_tokens": 0}
                raise json.JSONDecodeError("msg", "doc", 0)
            return super().complete_json(*a, **k)

    bodies = {"worldbank": WB_BODY}
    search = make_search_fn({"*": []})
    llm = FlakyLLM([dict(ESTRUCT_PLAN),
                    _recup_final_wb(_wb_url("WLD", 2024)),
                    dict(REDTEAM_SKIP)])
    # F2-3: pin the full retry path (default-on exit would close rounds).
    # F2-7: redteam deferred by default; opt in to pin the 3-call path.
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         early_exit_enabled=False, redteam_enabled=True)
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(
        bus, tracker, "c-flaky",
        "We are 8.14 billion people sharing one planet.", config))
    assert result.degraded is False
    assert len(result.packages) == 1
    assert len(llm.calls) == 3  # flake consumed no scripted reply

    class AlwaysBad(FakeLLM):
        def complete_json(self, *a, **k):
            self.last_usage_seq += 1
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1,
                               "reasoning_tokens": 0, "total_tokens": 2,
                               "cached_tokens": 0}
            raise json.JSONDecodeError("msg", "doc", 0)

    llm2 = AlwaysBad([])
    config2 = SwarmConfig(llm=llm2,
                          fetch_fn=make_fetch_fn({}, str(tmp_path)))
    result2 = asyncio.run(run_claim(bus, tracker, "c-alwaysbad", "Claim.",
                                    config2))
    assert result2.degraded is True
    assert "estructurador failed" in result2.note


def test_token_budget_stops_model_calls(tmp_path):
    llm = FakeLLM([ESTRUCT_PLAN], prompt_tokens=100, completion_tokens=20)
    config = SwarmConfig(llm=llm, token_budget_per_claim=1,
                         fetch_fn=make_fetch_fn({}, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-budget", "Claim.",
                                   config))
    assert result.degraded is True
    assert "budget" in result.note
    assert len(llm.calls) == 1


def test_run_swarm_parallel_cost_adds_up(tmp_path):
    bodies = {"worldbank": WB_BODY}
    search = make_search_fn({"*": []})

    def _one(iso3):
        # ESTRUCT_PLAN carries year 2024, so grounding uses date= URLs.
        return FakeLLM([dict(ESTRUCT_PLAN),
                        _recup_final_wb(_wb_url(iso3, 2024)),
                        dict(REDTEAM_SKIP)])

    bus, tracker = _bus_tracker()

    async def _go():
        # one client per claim (no shared mutable usage state)
        configs = [SwarmConfig(llm=_one("WLD"), search_fn=search,
                               fetch_fn=make_fetch_fn(bodies, str(tmp_path))),
                   SwarmConfig(llm=_one("CHN"), search_fn=search,
                               fetch_fn=make_fetch_fn(bodies, str(tmp_path)))]
        sem_results = await asyncio.gather(*[
            run_claim(bus, tracker, cid, text, cfg)
            for cid, text, cfg in [
                ("c-a", "We are 8.14 billion people sharing one planet.",
                 configs[0]),
                ("c-b", "China is home to 1.41 billion people.",
                 configs[1]),
            ]
        ])
        return sem_results

    results = asyncio.run(_go())
    assert [len(r.packages) for r in results] == [1, 1]
    assert all(r.cost_usd > 0 for r in results)
    assert len(bus.history("claim.evidence")) == 2


def test_run_swarm_entrypoint(tmp_path):
    bodies = {"worldbank": WB_BODY}
    search = make_search_fn({"*": []})
    llm = FakeLLM([dict(ESTRUCT_PLAN),
                   _recup_final_wb(_wb_url("WLD", 2024)),
                   dict(REDTEAM_SKIP)])
    # F2-3: pin the full 3-call path (default-on exit would close rounds).
    # F2-7: redteam deferred by default; opt in to pin 3 calls.
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         early_exit_enabled=False, redteam_enabled=True)
    bus, tracker = _bus_tracker()
    report = asyncio.run(run_swarm(
        bus, tracker,
        [("c-1", "We are 8.14 billion people sharing one planet.")],
        config))
    assert len(report.results) == 1
    assert report.total_cost_usd == pytest.approx(
        report.results[0].cost_usd)
    assert report.total_usage["model_calls"] == 3


# ---------------------------------------------------------------------------
# LIVE: real coordinator run over 3 TRUE keynote EN claims (gated)
# ---------------------------------------------------------------------------

LIVE_CLAIM_IDS = ("T1-en", "T2-en", "T9-en")
LIVE_BUDGET_USD = 6.0


def _load_live_claims():
    path = os.path.join(REPO_ROOT, "live", "material", "truth.json")
    with open(path, encoding="utf-8") as fh:
        truth = json.load(fh)
    by_id = {row["id"]: row for row in truth}
    return [(cid, by_id[cid]["text"]) for cid in LIVE_CLAIM_IDS]


@pytest.mark.live
def test_live_three_true_claims():
    """LIVE: >=2 of 3 TRUE claims bring a snapshot-greppable quote."""
    if os.environ.get("ATLAS_LIVE") != "1":
        pytest.skip("live test: set ATLAS_LIVE=1 to run")
    from src.atlas.models.meta import MetaClient

    llm = MetaClient()
    if not llm.available:
        pytest.skip("live test: MODEL_API_KEY not configured")
    claims = _load_live_claims()
    assert len(claims) == 3
    bus, tracker = _bus_tracker()
    # one forked client per claim: usage state is per-request mutable.
    configs = [SwarmConfig(llm=llm.fork()) for _ in claims]

    async def _go():
        return await asyncio.gather(*[
            run_claim(bus, tracker, cid, text, cfg)
            for (cid, text), cfg in zip(claims, configs)
        ])

    results = asyncio.run(_go())
    total_usd = sum(r.cost_usd for r in results)
    print(f"\n[LIVE m2b] total measured cost: ${total_usd:.4f}")
    grepping = 0
    for result in results:
        ok = False
        for pkg in result.packages:
            snap_path = os.path.join(REPO_ROOT, pkg["snapshot_path"])
            with open(snap_path, encoding="utf-8") as fh:
                snapshot = fh.read()
            if pkg["quote"] in snapshot:
                ok = True
                print(f"[LIVE m2b] {result.claim_id}: quote greps "
                      f"({pkg['url'][:80]})")
                break
        if ok:
            grepping += 1
        else:
            print(f"[LIVE m2b] {result.claim_id}: NO greppable quote "
                  f"(packages={len(result.packages)}, note={result.note})")
    print(f"[LIVE m2b] claims with greppable quote: {grepping}/3")
    assert total_usd <= LIVE_BUDGET_USD, f"live cost ${total_usd:.4f}"
    assert grepping >= 2, "need >=2 claims with literal snapshot quotes"
