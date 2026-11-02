"""F2-9 p95 tests: early abort of sterile recuperador rounds.

Covers the F2_P95 edit (all live-network/model-free):

(a) ``early_abort_reason`` (pure): fires on 0 packages + 0 new docs
    since grounding, silent otherwise, never raises.
(b) pipeline: a sterile 1st round (re-search, already-known or
    unfetchable hits) aborts the remaining rounds with
    ``early-abort(recuperador)`` + ``claim.early_abort``; deciders
    (final in round 1, new doc in round 1, grounding package in hand)
    never abort; kill-switch ``early_abort_enabled=False`` restores
    the full 4-round loop.
(c) pins: every lever NOT taken stays pinned (deadline 70, 4 rounds,
    deferred redteam, 2% tolerance, scale backstop, primary-first,
    arithmetic guard, verbatim-only packages). See F2_P95.md § pins.

Fixtures are synthetic (Ruritania/Banaba/Chile-test: never dev claims,
never dev figures): the lever is general or it is not a lever.
"""

import asyncio
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    CLAIM_DEADLINE_S,
    MAX_TOOL_ROUNDS,
    SwarmConfig,
    build_package,
    code_refutation_reason,
    early_abort_reason,
    early_exit_reason,
    run_claim,
    source_type_for_url,
    wb_fallback_eligible,
)
from live.swarm.fetch import FetchResult  # noqa: E402
from live.swarm.roles import RECUPERADOR_SYSTEM  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures (synthetic: never dev claims, never dev figures)
# ---------------------------------------------------------------------------

RURI_TEXT = "Ruritania produced 12,500 widgets in 2024."
RURI_PLAN = {
    "entities": ["Ruritania", "widgets"],
    "indicator_candidate": None,
    "geo": {"name": None, "iso3": None},
    "year": None,
    "web_query": "Ruritania widgets 2024",
    "numbers_in_claim": ["12,500"],
}
RURI_URL = "https://en.example.org/wiki/Ruritania"
RURI_BODY = ("<html><body><p>Ruritania produced 12,500 widgets in its "
             "single factory, officials said in a statement released to "
             "describe the annual industrial output figures.</p></body></html>")
RURI_QUOTE = ("Ruritania produced 12,500 widgets in its single factory, "
              "officials said in a statement released to describe the "
              "annual industrial output figures.")
BANABA_URL = "https://en.example.org/wiki/Banaba"
BANABA_BODY = ("<html><body><p>Banaba harvested 7,700 yams in its communal "
               "groves, elders said in a proclamation issued to celebrate "
               "the seasonal agricultural yield totals.</p></body></html>")
BANABA_QUOTE = ("Banaba harvested 7,700 yams in its communal groves, elders "
                "said in a proclamation issued to celebrate the seasonal "
                "agricultural yield totals.")

CHILE_WB_BODY = (
    '[{"page":1,"total":1},'
    '[{"indicator":{"id":"SP.POP.TOTL"},"country":{"id":"CL"},'
    '"countryiso3code":"CHL",'
    '"date":"2024","value":12500,"decimal":0}]]'
)
# Exactly 1000x off with no explicit scale word: the verifier abstains
# with possible_scale_error, so NEITHER fast-path fires, yet the
# grounding code package exists (wb_fallback_eligible: curated total +
# exact year + non-arithmetic + resolver agrees).
SCALE_TEXT = "Chile had 12.5 people in 2024."
SCALE_PLAN = {
    "entities": ["Chile", "population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": "Chile", "iso3": "CHL"},
    "year": 2024,
    "web_query": "Chile population 2024",
    "numbers_in_claim": ["12.5"],
}


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
                from live.swarm.fetch import FetchError

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
        from live.swarm.fetch import FetchError

        raise FetchError(f"404 {url}")

    return _fake


def make_search_fn(hits):
    def _fake(query, top_k=3):
        return list(hits[:top_k])

    return _fake


def _search_step(query="Ruritania widgets 2024"):
    return {"thought": "need docs",
            "tool_calls": [{"name": "web_search",
                            "arguments": {"query": query, "top_k": 2}}]}


def _final_with(url, quote):
    return {"thought": "got it",
            "final": {"findings": [{"url": url, "quote": quote}]}}


def _ruri_hit(url=RURI_URL):
    return {"url": url, "title": "R", "snippet": ""}


# ---------------------------------------------------------------------------
# (a) early_abort_reason: pure
# ---------------------------------------------------------------------------

def test_abort_fires_on_zero_packages_zero_new_docs():
    reason = early_abort_reason(0, 0)
    assert reason is not None
    assert "no packages" in reason and "no new documents" in reason


def test_abort_silent_with_packages():
    assert early_abort_reason(1, 0) is None
    assert early_abort_reason(2, 0) is None


def test_abort_silent_with_new_docs():
    assert early_abort_reason(0, 1) is None
    assert early_abort_reason(0, 3) is None
    assert early_abort_reason(1, 2) is None


def test_abort_never_raises_on_shaped_input():
    assert early_abort_reason(None, 0) is None
    assert early_abort_reason(0, None) is None
    assert early_abort_reason("x", "y") is None
    assert early_abort_reason(-1, 0) is None


# ---------------------------------------------------------------------------
# (a) pipeline: sterile round aborts, deciders never abort
# ---------------------------------------------------------------------------

def test_sterile_first_round_aborts_rest(tmp_path):
    bodies = {"Ruritania": RURI_BODY}
    search = make_search_fn([_ruri_hit()])  # grounding fetches it...
    sentinel = _final_with(RURI_URL, RURI_QUOTE)  # ...must NOT be consumed
    llm = FakeLLM([dict(RURI_PLAN), _search_step(), dict(sentinel)])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-sterile", RURI_TEXT,
                                   config))
    assert result.note.startswith("early-abort(recuperador)")
    assert result.degraded is True
    assert result.packages == []
    assert len(llm.calls) == 2  # estructurador + 1 sterile round only
    aborts = bus.history("claim.early_abort")
    assert len(aborts) == 1
    assert aborts[0]["claim_id"] == "c-sterile"
    assert aborts[0]["n_packages"] == 0
    assert "no packages" in aborts[0]["reason"]
    # The grounding taxonomy still fires (why no fast-path).
    no_exits = bus.history("claim.no_exit")
    assert len(no_exits) == 1
    assert no_exits[0]["reason"].startswith("no_exit_")


def test_abort_kill_switch_restores_full_loop(tmp_path):
    bodies = {"Ruritania": RURI_BODY}
    search = make_search_fn([_ruri_hit()])
    llm = FakeLLM([dict(RURI_PLAN), _search_step(),
                   _final_with(RURI_URL, RURI_QUOTE)])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         early_abort_enabled=False)
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-switch", RURI_TEXT,
                                   config))
    assert len(llm.calls) == 3  # the 2nd round runs with the switch off
    assert len(result.packages) == 1
    assert result.note == "done (redteam deferred)"
    assert result.degraded is False
    assert bus.history("claim.early_abort") == []


def test_final_in_first_round_never_aborts(tmp_path):
    # Decider shape C: the grounded docs suffice; final closes round 1.
    bodies = {"Ruritania": RURI_BODY}
    search = make_search_fn([_ruri_hit()])
    llm = FakeLLM([dict(RURI_PLAN), _final_with(RURI_URL, RURI_QUOTE)])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-final1", RURI_TEXT,
                                   config))
    assert len(result.packages) == 1
    assert result.note == "done (redteam deferred)"
    assert bus.history("claim.early_abort") == []


def test_new_doc_in_first_round_never_aborts(tmp_path):
    # Decider shape D3: round 1 fetches a NEW doc, round 2 cites it.
    bodies = {"Ruritania": RURI_BODY, "Banaba": BANABA_BODY}
    search = make_search_fn([_ruri_hit()])  # grounding: Ruritania only
    fetch_step = {"thought": "fetch more",
                  "tool_calls": [{"name": "fetch_url",
                                  "arguments": {"url": BANABA_URL}}]}
    llm = FakeLLM([dict(RURI_PLAN), fetch_step,
                   _final_with(BANABA_URL, BANABA_QUOTE)])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-progress", RURI_TEXT,
                                   config))
    assert len(llm.calls) == 3  # no abort: round 1 made progress
    assert len(result.packages) == 1
    assert result.packages[0]["url"] == BANABA_URL
    assert bus.history("claim.early_abort") == []


def test_grounding_package_blocks_abort(tmp_path):
    # Scale-edge Chile: grounding keeps a code package but no fast-path
    # fires (both stay silent); a sterile round 1 must NOT abort.
    assert wb_fallback_eligible(SCALE_TEXT, {"indicator": "SP.POP.TOTL",
                                             "iso3": "CHL", "year": 2024})
    bodies = {"worldbank": CHILE_WB_BODY}
    search = make_search_fn([])
    quote = '"date":"2024","value":12500'
    wb_url = ("https://api.worldbank.org/v2/country/CHL/indicator/SP.POP.TOTL"
              "?format=json&per_page=50&date=2024%3A2024")
    llm = FakeLLM([dict(SCALE_PLAN), _search_step("Chile population 2024"),
                   _final_with(wb_url, quote)])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-pkgd", SCALE_TEXT,
                                   config))
    assert len(llm.calls) == 3  # packages in hand: no abort
    assert len(result.packages) >= 1
    assert bus.history("claim.early_abort") == []


def test_abort_without_bus_never_raises(tmp_path):
    bodies = {"Ruritania": RURI_BODY}
    search = make_search_fn([_ruri_hit()])
    llm = FakeLLM([dict(RURI_PLAN), _search_step()])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(run_claim(None, None, "c-nobus", RURI_TEXT,
                                   config))
    assert result.note.startswith("early-abort")
    assert result.degraded is True


def test_abort_evidence_event_marks_degraded(tmp_path):
    bodies = {"Ruritania": RURI_BODY}
    search = make_search_fn([_ruri_hit()])
    llm = FakeLLM([dict(RURI_PLAN), _search_step()])
    config = SwarmConfig(llm=llm, search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    bus, tracker = _bus_tracker()
    result = asyncio.run(run_claim(bus, tracker, "c-ev", RURI_TEXT, config))
    ev = bus.history("claim.evidence")
    assert len(ev) == 1
    assert ev[0]["degraded"] is True
    assert ev[0]["packages"] == []
    assert result.cost_usd >= 0.0


# ---------------------------------------------------------------------------
# (c) pins: levers NOT taken + invariants (justified in F2_P95.md, pins §)
# ---------------------------------------------------------------------------

def test_early_abort_default_on_and_deadline_stays_70():
    # Lever (b) REJECTED by measurement: deadline 70 cannot fix the tail
    # (abort-at-70 still measures ~70s+), and 60 would still MISS while
    # risking slow deciders. The abort fixes the tail instead.
    assert SwarmConfig().early_abort_enabled is True
    assert SwarmConfig().claim_deadline_s == 70.0
    assert CLAIM_DEADLINE_S == 70.0


def test_rounds_stay_4_and_prompt_agrees():
    # No round cut (the F2-7 4->2 cut caused a B1 falso-C): the abort
    # only skips rounds that produced nothing.
    assert SwarmConfig().max_tool_rounds == 4
    assert MAX_TOOL_ROUNDS == 4
    assert "at most 4 tool rounds" in RECUPERADOR_SYSTEM


def test_other_latency_defaults_unchanged():
    cfg = SwarmConfig()
    assert cfg.redteam_enabled is False  # deferred (F2-7), untouched
    assert cfg.code_refutation_enabled is True  # fast-path, untouched
    assert cfg.ground_parallel is True  # parallel grounding, untouched
    assert cfg.early_exit_enabled is True  # support fast-path, untouched


def test_tolerance_scale_hierarchy_arithmetic_pins():
    # Guards that must NEVER move: the abort only closes ROUNDS.
    from src.atlas.core.compare import DEFAULT_REL_TOLERANCE
    assert DEFAULT_REL_TOLERANCE == 0.02
    assert source_type_for_url(
        "https://api.worldbank.org/v2/country/CHL/indicator/SP.POP.TOTL"
    ) == "primary"
    assert source_type_for_url(RURI_URL) == "secondary"
    arith = ("The average population of Chile and Banaba in 2024 was "
             "48.5 million people, according to the World Bank.")
    assert wb_fallback_eligible(
        arith, {"indicator": "SP.POP.TOTL", "iso3": "CHL",
                "year": 2024}) is False
    # Verbatim-only packages: invented quotes still dropped (verdicts
    # still decide from kept packages only).
    assert code_refutation_reason([], RURI_TEXT, None) is None
    assert early_exit_reason([], RURI_TEXT) is None
