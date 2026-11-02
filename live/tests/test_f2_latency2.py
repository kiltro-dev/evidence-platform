"""F2-7 latency-2 tests: refutation fast-path + parallel grounding + fewer rounds.

Covers the F2_LAT2 edits (all live-network/model-free):

(a) code refutation fast-path (WB row mismatch closes rounds, same F1
    guards + verifier-mirrored units/scale/year checks),
(b) parallel grounding queries (cap 4, order-preserving, serial fallback),
(c) recuperador rounds stay 4 (the 4 -> 2 cut was REVERTED after a
    live B1 falso-contradicted; forced final + deferred redteam kept),
(d) per-claim budget 120 -> 70s (graceful aborts, packages kept),
(e) no-exit taxonomy (why each claim did not fast-path) + claim.no_exit.

Guards that must NEVER move: 2% tolerance, scale-slip backstop,
primary-beats-secondary hierarchy, arithmetic guard, verifier verdicts.
Each has a pin below running THROUGH the new code paths.

Fixtures are synthetic (Chile/Ruritania/Banaba: never dev claims, never
dev figures): every lever is general or it is not a lever.
"""

import asyncio
import hashlib
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    CLAIM_DEADLINE_S,
    GROUND_QUERY_WORKERS,
    MAX_TOOL_ROUNDS,
    SwarmConfig,
    build_package,
    code_refutation_reason,
    early_exit_reason,
    no_exit_reason,
    run_claim,
)
from live.swarm.fetch import FetchResult  # noqa: E402
from live.swarm.roles import RECUPERADOR_SYSTEM  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures (synthetic: never dev claims, never dev figures)
# ---------------------------------------------------------------------------

SYN_TEXT = "Chile had 12,500 people in 2024."
SYN_FALSE_TEXT = "Chile had 99,000 people in 2024."
SYN_WB_BODY = (
    '[{"page":1,"total":1},'
    '[{"indicator":{"id":"SP.POP.TOTL"},"country":{"id":"CL"},'
    '"countryiso3code":"CHL",'
    '"date":"2024","value":12500,"decimal":0}]]'
)
SYN_WB_URL = ("https://api.worldbank.org/v2/country/CHL/indicator/SP.POP.TOTL"
              "?format=json&per_page=50&date=2024%3A2024")
SYN_WB_QUOTE = '"date":"2024","value":12500'
SYN_META = {"indicator": "SP.POP.TOTL", "unit": "count", "scale": 1.0}

SYN_PLAN = {
    "entities": ["Chile", "population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": None, "iso3": None},
    "year": 2024,
    "web_query": "Chile population 2024",
    "numbers_in_claim": ["12,500"],
}
MERGED_SYN_PLAN = {
    "entities": ["Chile", "population"],
    "indicator": "SP.POP.TOTL",
    "iso3": "CHL",
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

ARITH_TEXT = ("The average population of Chile and Banaba in 2024 was "
              "48.5 million people, according to the World Bank.")


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


def _code_pkg(quote=SYN_WB_QUOTE, url=SYN_WB_URL, meta=None):
    return {"package_id": "p-code", "claim_id": "c", "url": url,
            "quote": quote, "source_type": "primary", "extractor": "code",
            "source_meta": dict(SYN_META if meta is None else meta)}


def _model_primary_pkg(quote=SYN_WB_QUOTE, url=SYN_WB_URL):
    return {"package_id": "p-model", "claim_id": "c", "url": url,
            "quote": quote, "source_type": "primary", "extractor": "model",
            "source_meta": dict(SYN_META)}


# ---------------------------------------------------------------------------
# (a) code refutation fast-path: pure reason
# ---------------------------------------------------------------------------

def test_refutation_fires_on_beyond_tolerance_mismatch():
    reason = code_refutation_reason(
        [_code_pkg()], SYN_FALSE_TEXT, dict(MERGED_SYN_PLAN))
    assert reason is not None and "refutes" in reason
    assert "api.worldbank.org" in reason
    assert "99" in reason or "99000" in reason  # claim figure named


def test_refutation_silent_on_match():
    # TRUE Chile: the support path owns it, never the refutation path.
    assert code_refutation_reason(
        [_code_pkg()], SYN_TEXT, dict(MERGED_SYN_PLAN)) is None
    assert early_exit_reason([_code_pkg()], SYN_TEXT) is not None


def test_refutation_tolerance_boundary():
    # 1% off: support match, no refutation (tolerance 2%, read-only).
    close = _code_pkg(quote='"date":"2024","value":12600')
    assert code_refutation_reason(
        [close], SYN_TEXT, dict(MERGED_SYN_PLAN)) is None
    assert early_exit_reason([close], SYN_TEXT) is not None
    # 4% off: refutation, no support match.
    far = _code_pkg(quote='"date":"2024","value":13000')
    assert code_refutation_reason(
        [far], SYN_TEXT, dict(MERGED_SYN_PLAN)) is not None
    assert early_exit_reason([far], SYN_TEXT) is None


def test_refutation_needs_code_primary_with_meta():
    plan = dict(MERGED_SYN_PLAN)
    # Model primary (even mismatching) never refute-exits: the fast-path
    # is deterministic code rows only.
    assert code_refutation_reason(
        [_model_primary_pkg()], SYN_FALSE_TEXT, plan) is None
    # Secondary code-shaped rows never fire either.
    sec = _code_pkg()
    sec["source_type"] = "secondary"
    assert code_refutation_reason([sec], SYN_FALSE_TEXT, plan) is None
    # Missing source_meta: units cannot be proven; stay silent.
    nometa = _code_pkg()
    del nometa["source_meta"]
    assert code_refutation_reason([nometa], SYN_FALSE_TEXT, plan) is None
    assert code_refutation_reason([], SYN_FALSE_TEXT, plan) is None
    assert code_refutation_reason(None, SYN_FALSE_TEXT, plan) is None


def test_refutation_silent_on_ineligible_plans():
    pkg = [_code_pkg()]
    # Wrong indicator family (per-capita/growth stay out by design).
    bad_ind = dict(MERGED_SYN_PLAN, indicator="NY.GDP.PCAP.CD")
    assert code_refutation_reason(pkg, SYN_FALSE_TEXT, bad_ind) is None
    # No exact year.
    bad_year = dict(MERGED_SYN_PLAN, year=None)
    assert code_refutation_reason(pkg, SYN_FALSE_TEXT, bad_year) is None
    # Arithmetic claims: component rows cannot refute averages/ratios.
    arith_plan = {"indicator": "SP.POP.TOTL", "iso3": "CHL", "year": 2024}
    assert code_refutation_reason(pkg, ARITH_TEXT, arith_plan) is None
    # Resolver disagreement (wrong-country rows never refute).
    bad_geo = dict(MERGED_SYN_PLAN, iso3="ARG")
    assert code_refutation_reason(pkg, SYN_FALSE_TEXT, bad_geo) is None


def test_refutation_requires_claim_year_match():
    # Plan year 2023 vs claim year 2024: a different vintage cannot
    # refute, even with a numeric mismatch.
    pkg = [_code_pkg(quote='"date":"2023","value":99999')]
    plan = dict(MERGED_SYN_PLAN, year=2023)
    assert code_refutation_reason(pkg, SYN_FALSE_TEXT, plan) is None
    # Year-less claims never refute (no exact year to pin the row).
    assert code_refutation_reason(
        pkg, "Chile had 99,000 people.", plan) is None


def test_refutation_respects_unit_compatibility():
    # EUR claim vs USD row with the SAME digits: numeric match fires the
    # support path (F2-3 behavior, verifier then abstains on units), but
    # a mismatching EUR claim must NOT refute from a USD row.
    eur_url = ("https://api.worldbank.org/v2/country/CHL/indicator/"
               "NY.GDP.MKTP.CD?format=json")
    eur_meta = {"indicator": "NY.GDP.MKTP.CD", "unit": "USD", "scale": 1.0}
    row = _code_pkg(quote='"date":"2024","value":2270000000000',
                    url=eur_url, meta=eur_meta)
    plan = {"indicator": "NY.GDP.MKTP.CD", "iso3": "CHL", "year": 2024}
    # Same digits: support path (existing behavior), no refutation.
    same = "Chile GDP was 2.27 trillion euros in 2024."
    assert code_refutation_reason([row], same, plan) is None
    # Different digits, incompatible units: no refutation either (the
    # verifier would say unit_mismatch, not contradicted).
    diff = "Chile GDP was 9.99 trillion euros in 2024."
    assert code_refutation_reason([row], diff, plan) is None


def test_refutation_respects_scale_backstop():
    # 1000x gap with no explicit scale word: the verifier abstains with
    # possible_scale_error, so the fast-path must stay silent.
    pkg = [_code_pkg(quote='"date":"2024","value":8000000')]
    plan = dict(MERGED_SYN_PLAN)
    text = "Chile had 8000 people in 2024."
    assert code_refutation_reason(pkg, text, plan) is None
    # Explicit scale words keep real contradictions: 1.83B vs 1.83T.
    mex_url = ("https://api.worldbank.org/v2/country/MEX/indicator/"
               "NY.GDP.MKTP.CD?format=json")
    mex_meta = {"indicator": "NY.GDP.MKTP.CD", "unit": "USD", "scale": 1.0}
    mex_row = _code_pkg(quote='"date":"2024","value":1830000000000',
                        url=mex_url, meta=mex_meta)
    mex_plan = {"indicator": "NY.GDP.MKTP.CD", "iso3": "MEX", "year": 2024}
    mex_text = "Mexico GDP was 1.83 billion dollars in 2024."
    assert code_refutation_reason([mex_row], mex_text, mex_plan) is not None


def test_refutation_never_raises_on_shaped_input():
    plan = dict(MERGED_SYN_PLAN)
    assert code_refutation_reason([None, "x", {}], SYN_FALSE_TEXT, plan) is None
    assert code_refutation_reason([{"source_type": "primary"}],
                                  SYN_FALSE_TEXT, plan) is None
    assert code_refutation_reason([_code_pkg()], SYN_FALSE_TEXT, None) is None
    assert code_refutation_reason([_code_pkg()], "", plan) is None
    bad_meta = _code_pkg()
    bad_meta["source_meta"] = {"indicator": "X", "unit": "USD",
                               "scale": "junk"}
    assert code_refutation_reason([bad_meta], SYN_FALSE_TEXT, plan) is None


# ---------------------------------------------------------------------------
# (a) code refutation fast-path: pipeline integration
# ---------------------------------------------------------------------------

def _chile_config(tmp_path, script, **kwargs):
    bodies = {"worldbank": SYN_WB_BODY}
    return SwarmConfig(
        llm=FakeLLM(script), search_fn=make_search_fn([]),
        fetch_fn=make_fetch_fn(bodies, str(tmp_path)), **kwargs)


def test_refutation_grounding_closes_model_rounds(tmp_path):
    bus, tracker = _bus_tracker()
    seen: list[str] = []

    def _recording_search(query, top_k=3):
        seen.append(query)
        return []

    bodies = {"worldbank": SYN_WB_BODY}
    config = SwarmConfig(llm=FakeLLM([dict(SYN_PLAN)]),
                         search_fn=_recording_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-rf", SYN_FALSE_TEXT, config))
    assert seen == []  # web pool skipped: the WB row already refutes
    assert result.note.startswith("early-exit(grounding):")
    assert "refutes" in result.note
    assert result.degraded is False
    assert len(result.packages) == 1
    assert result.packages[0]["extractor"] == "code"
    assert len(config.llm.calls) == 1  # model loop skipped entirely
    exits = bus.history("claim.early_exit")
    assert len(exits) == 1 and exits[0]["claim_id"] == "c-rf"
    assert bus.history("claim.no_exit") == []  # fast: no no-exit event
    assert len(bus.history("claim.evidence")) == 1


def test_refutation_kill_switch_restores_full_path(tmp_path):
    bus, tracker = _bus_tracker()
    final = {"thought": "citing",
             "final": {"findings": [{"url": SYN_WB_URL,
                                     "quote": SYN_WB_QUOTE}]}}
    config = _chile_config(tmp_path, [dict(SYN_PLAN), final],
                           code_refutation_enabled=False)
    result = asyncio.run(
        run_claim(bus, tracker, "c-rf-off", SYN_FALSE_TEXT, config))
    assert result.note == "done (redteam deferred)"
    assert len(config.llm.calls) == 2  # estructurador + recuperador
    assert bus.history("claim.early_exit") == []
    noexits = bus.history("claim.no_exit")
    assert len(noexits) == 1  # slow path explains itself
    assert noexits[0]["reason"] == "no_exit_refutation_disabled"


def test_refutation_package_sustains_contradicted(tmp_path):
    # The kept code package alone must verify contradicted (no model
    # rounds needed to decide the FALSE fixture).
    import hashlib as _hl

    from live.evidence.package import EvidencePackage
    from live.run_verify import claim_input_for
    from live.swarm.coordinator import _DocStore
    from live.verifier.check import verify_claim

    raw = SYN_WB_BODY.encode("utf-8")
    sha = _hl.sha256(raw).hexdigest()
    fetch = FetchResult(
        url=SYN_WB_URL, final_url=SYN_WB_URL, status=200,
        content_type="application/json", size=len(raw), sha256=sha,
        snapshot_path=str(tmp_path / f"{sha}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=SYN_WB_BODY)
    docs = _DocStore()
    doc = docs.add(fetch, title="World Bank SP.POP.TOTL CHL")
    for text, cid, want in ((SYN_TEXT, "c-T", "supported"),
                            (SYN_FALSE_TEXT, "c-F", "contradicted")):
        pkgd = build_package(cid, SYN_WB_URL, SYN_WB_QUOTE, doc, 2024, 0,
                             extractor="code")
        assert pkgd is not None
        pkg = EvidencePackage.from_dict(pkgd)
        claim = claim_input_for({"id": cid, "text": text})
        verdict = verify_claim(claim, [pkg], lambda _p: raw)
        assert verdict.verdict == want, (text, verdict)


def test_true_claims_never_refute(tmp_path):
    # TRUE fixtures across both curated indicators: support owns them.
    bus, tracker = _bus_tracker()
    config = _chile_config(tmp_path, [dict(SYN_PLAN)])
    result = asyncio.run(run_claim(bus, tracker, "c-T", SYN_TEXT, config))
    assert result.note.startswith("early-exit(grounding):")
    assert "refutes" not in result.note
    assert "within" in result.note  # support match, not refutation


# ---------------------------------------------------------------------------
# (b) parallel grounding queries (cap 4, order-preserving)
# ---------------------------------------------------------------------------

def test_ground_query_workers_is_4():
    assert GROUND_QUERY_WORKERS == 4
    assert SwarmConfig().ground_parallel is True  # default-on pin


def _counting_search(hits, delay_s=0.08):
    state = {"inflight": 0, "peak": 0, "calls": []}
    lock = threading.Lock()

    def _fake(query, top_k=3):
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            state["calls"].append(query)
        try:
            time.sleep(delay_s)
        finally:
            with lock:
                state["inflight"] -= 1
        return list(hits[:top_k])

    return _fake, state


def test_grounding_queries_run_in_parallel(tmp_path):
    # Ruritania: no WB row, so the 3-query web pool runs; with the
    # default parallel grounding the 3 searches overlap (peak 3).
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    search, state = _counting_search(hits)
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=FakeLLM([dict(RURITANIA_PLAN), final]),
                         search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-par", RURITANIA_TEXT, config))
    assert len(result.packages) == 1
    assert len(state["calls"]) == 3  # multi-query pool intact
    assert state["peak"] == 3  # all 3 overlapped (cap 4, polite)


def test_grounding_serial_fallback_same_results(tmp_path):
    # Kill-switch serializes the pool: same packages, peak 1.
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    bus, tracker = _bus_tracker()
    search, state = _counting_search(hits)
    config = SwarmConfig(llm=FakeLLM([dict(RURITANIA_PLAN), dict(final)]),
                         search_fn=search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         ground_parallel=False)
    result = asyncio.run(
        run_claim(bus, tracker, "c-ser", RURITANIA_TEXT, config))
    assert len(result.packages) == 1
    assert result.packages[0]["quote"] == WIKI_QUOTE
    assert state["peak"] == 1
    assert len(state["calls"]) == 3


def test_grounding_parallel_dedups_and_orders_deterministically(tmp_path):
    # Same hit across queries dedups to one fetch; the package is
    # identical under parallel and serial grounding.
    bodies = {"example": WIKI_BODY}
    dup_hits = [{"url": WIKI_URL, "title": "R", "snippet": ""},
                {"url": WIKI_URL, "title": "R-dup", "snippet": "x"}]
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    pkgs = []
    for parallel in (True, False):
        bus, tracker = _bus_tracker()
        config = SwarmConfig(
            llm=FakeLLM([dict(RURITANIA_PLAN), dict(final)]),
            search_fn=make_search_fn(dup_hits),
            fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
            ground_parallel=parallel)
        result = asyncio.run(
            run_claim(bus, tracker, "c-dd", RURITANIA_TEXT, config))
        pkgs.append(result.packages)
    assert pkgs[0] == pkgs[1]
    assert len(pkgs[0]) == 1


def test_grounding_search_exception_degrades_to_fewer_hits(tmp_path):
    # A raising search_fn never breaks grounding (empty pool + fetch of
    # nothing); the model loop still runs over the (empty) docs.
    def _boom(query, top_k=3):
        raise RuntimeError("search down")

    bodies = {"example": WIKI_BODY}
    final = {"thought": "empty",
             "final": {"findings": []}}
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=FakeLLM([dict(RURITANIA_PLAN), final]),
                         search_fn=_boom,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-boom", RURITANIA_TEXT, config))
    assert result.degraded is False
    assert result.packages == []
    assert result.note == "done (redteam deferred)"


# ---------------------------------------------------------------------------
# (c) recuperador rounds stay 4 (2 reverted) + forced final + deferred redteam
# ---------------------------------------------------------------------------

def test_max_tool_rounds_stays_4_and_prompt_agrees():
    # The 4 -> 2 cut was REVERTED after a live B1 falso-contradicted
    # (premature citation of a rounded secondary); 4 rounds + deferred
    # redteam is the shipped (c) lever.
    assert MAX_TOOL_ROUNDS == 4
    assert SwarmConfig().max_tool_rounds == 4
    assert "at most 4 tool rounds" in RECUPERADOR_SYSTEM
    assert "at most 2 tool rounds" not in RECUPERADOR_SYSTEM
    assert SwarmConfig().redteam_enabled is False  # deferred by default


def test_forced_final_after_four_tool_rounds(tmp_path):
    # Four tool rounds with no final, then the forced-final call decides.
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    tool_step = {"thought": "need docs",
                 "tool_calls": [{"name": "web_search",
                                 "arguments": {"query": "Ruritania widgets",
                                               "top_k": 2}}],
                 "final": None}
    forced = {"thought": "citing late",
              "tool_calls": [],
              "final": {"findings": [{"url": WIKI_URL,
                                      "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([dict(RURITANIA_PLAN)] + [dict(tool_step)] * 4
                  + [dict(forced)])
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-ff", RURITANIA_TEXT, config))
    assert len(result.packages) == 1
    assert result.packages[0]["quote"] == WIKI_QUOTE
    # estructurador + 4 tool rounds + forced final (no redteam).
    assert len(llm.calls) == 6
    assert result.note == "done (redteam deferred)"


def test_redteam_deferred_by_default(tmp_path):
    # Even with a counter-query waiting in the script, the deferred
    # default never consumes it: verdict latency excludes redteam.
    bodies = {"example": WIKI_BODY}
    seen: list[str] = []

    def _seeing_search(query, top_k=3):
        seen.append(query)
        return [{"url": WIKI_URL, "title": "R", "snippet": ""}][:top_k]

    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    counter = {"counter_query": "Ruritania widgets revised figures",
               "rationale": "vintage risk", "skip": False}
    final2 = {"thought": "counter",
              "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([dict(RURITANIA_PLAN), dict(final), dict(counter),
                   dict(final2), dict(SYN_REDTEAM_SKIP)])
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=llm, search_fn=_seeing_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-def", RURITANIA_TEXT, config))
    assert result.note == "done (redteam deferred)"
    assert len(llm.calls) == 2  # estructurador + recuperador only
    assert not any("revised" in q for q in seen)
    assert len(result.packages) == 1


def test_redteam_opt_in_restores_second_round(tmp_path):
    bodies = {"example": WIKI_BODY}
    seen: list[str] = []

    def _seeing_search(query, top_k=3):
        seen.append(query)
        return [{"url": WIKI_URL, "title": "R", "snippet": ""}][:top_k]

    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    counter = {"counter_query": "Ruritania widgets revised figures",
               "rationale": "vintage risk", "skip": False}
    final2 = {"thought": "counter",
              "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    llm = FakeLLM([dict(RURITANIA_PLAN), dict(final), dict(counter),
                   dict(final2), dict(SYN_REDTEAM_SKIP)])
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=llm, search_fn=_seeing_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         redteam_enabled=True)
    result = asyncio.run(
        run_claim(bus, tracker, "c-rt", RURITANIA_TEXT, config))
    assert result.note == "done"
    assert any("revised" in q for q in seen)
    assert len(llm.calls) == 4  # estruct + recup + redteam + round-2
    assert len(result.packages) == 1  # dedup: same quote twice


# ---------------------------------------------------------------------------
# (d) per-claim budget 120 -> 70s
# ---------------------------------------------------------------------------

def test_claim_deadline_is_70():
    assert CLAIM_DEADLINE_S == 70.0
    assert SwarmConfig().claim_deadline_s == 70.0


def test_deadline_aborts_with_new_defaults(tmp_path):
    # Ruritania (no fast-path) with a tiny budget: graceful abort keeps
    # grounding docs, reports honestly, stays degraded.
    bodies = {"worldbank": SYN_WB_BODY}

    def _sleepy_search(query, top_k=3):
        if "slow" in query:
            time.sleep(0.3)
        return []

    tool_step = {"thought": "need docs",
                 "tool_calls": [{"name": "web_search",
                                 "arguments": {"query": "slow query",
                                               "top_k": 2}}],
                 "final": None}
    final = {"thought": "late", "tool_calls": [],
             "final": {"findings": []}}
    llm = FakeLLM([dict(RURITANIA_PLAN), dict(tool_step), dict(final)])
    config = SwarmConfig(llm=llm, search_fn=_sleepy_search,
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)),
                         claim_deadline_s=0.05)
    bus, tracker = _bus_tracker()
    result = asyncio.run(
        run_claim(bus, tracker, "c-dl2", RURITANIA_TEXT, config))
    assert result.degraded is True
    assert "deadline" in result.note
    assert "packages kept" in result.note
    ev = bus.history("claim.evidence")
    assert len(ev) == 1 and ev[0]["degraded"] is True


# ---------------------------------------------------------------------------
# (e) no-exit taxonomy
# ---------------------------------------------------------------------------

def test_no_exit_taxonomy_exit_kinds():
    assert no_exit_reason([_code_pkg()], SYN_TEXT,
                          dict(MERGED_SYN_PLAN)) == "exit_support_fast"
    assert no_exit_reason([_code_pkg()], SYN_FALSE_TEXT,
                          dict(MERGED_SYN_PLAN)) == "exit_refute_fast"


def test_no_exit_taxonomy_ineligibility_causes():
    # Indicator family outside the curated totals.
    plan = {"indicator": "NY.GDP.PCAP.CD", "iso3": "CHL", "year": 2024}
    assert no_exit_reason([], SYN_TEXT, plan) == \
        "no_exit_ineligible_indicator"
    # Unusable geo.
    plan = {"indicator": "SP.POP.TOTL", "iso3": "XX", "year": 2024}
    assert no_exit_reason([], SYN_TEXT, plan) == "no_exit_ineligible_geo"
    # Missing year.
    plan = {"indicator": "SP.POP.TOTL", "iso3": "CHL", "year": None}
    assert no_exit_reason([], SYN_TEXT, plan) == "no_exit_ineligible_year"
    # Computed figure (average): component rows cannot decide it.
    plan = {"indicator": "SP.POP.TOTL", "iso3": "CHL", "year": 2024}
    assert no_exit_reason([], ARITH_TEXT, plan) == \
        "no_exit_ineligible_arithmetic"
    # Resolver disagreement (wrong-country row would corrupt verdicts).
    plan = {"indicator": "SP.POP.TOTL", "iso3": "ARG", "year": 2024}
    assert no_exit_reason([], SYN_FALSE_TEXT, plan) == \
        "no_exit_ineligible_country"


def test_no_exit_taxonomy_eligible_edge_causes():
    plan = dict(MERGED_SYN_PLAN)
    # Eligible but the row never materialized (fetch/row miss).
    assert no_exit_reason([], SYN_FALSE_TEXT, plan) == \
        "no_exit_no_code_package"
    # Eligible row for a different year than the claim (figures far
    # enough to avoid the support match; the year guard blocks refute).
    code = [_code_pkg(quote='"date":"2023","value":50000')]
    plan23 = dict(MERGED_SYN_PLAN, year=2023)
    assert no_exit_reason(code, SYN_FALSE_TEXT, plan23) == \
        "no_exit_year_mismatch"
    # Eligible row present but units/scale edge (1000x, no scale word):
    # the verifier would abstain, so model rounds are needed.
    scale_code = [_code_pkg(quote='"date":"2024","value":8000000')]
    assert no_exit_reason(scale_code, "Chile had 8000 people in 2024.",
                          plan) == "no_exit_numeric_undecided"


def test_no_exit_taxonomy_planless_fallback_and_safety():
    assert no_exit_reason([], SYN_TEXT, None) == "no_exit_no_packages"
    sec = [dict(_code_pkg(), source_type="secondary")]
    assert no_exit_reason(sec, SYN_TEXT, {}) == "no_exit_no_primary"
    assert no_exit_reason([None], SYN_TEXT, None) in (
        "no_exit_no_packages", "no_exit_no_primary",
        "no_exit_numeric_undecided", "no_exit_unknown")
    # Never raises.
    assert no_exit_reason(None, "", None).startswith("no_exit")


def test_no_exit_event_emitted_on_slow_path(tmp_path):
    bodies = {"example": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "R", "snippet": ""}]
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    bus, tracker = _bus_tracker()
    config = SwarmConfig(llm=FakeLLM([dict(RURITANIA_PLAN), final]),
                         search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
    result = asyncio.run(
        run_claim(bus, tracker, "c-ne", RURITANIA_TEXT, config))
    assert result.note == "done (redteam deferred)"
    noexits = bus.history("claim.no_exit")
    assert len(noexits) == 1
    assert noexits[0]["claim_id"] == "c-ne"
    assert noexits[0]["reason"] == "no_exit_ineligible_indicator"
    assert bus.history("claim.early_exit") == []


def test_no_exit_event_absent_on_fast_paths(tmp_path):
    for text, cid in ((SYN_TEXT, "c-f1"), (SYN_FALSE_TEXT, "c-f2")):
        bus, tracker = _bus_tracker()
        bodies = {"worldbank": SYN_WB_BODY}
        config = SwarmConfig(llm=FakeLLM([dict(SYN_PLAN)]),
                             search_fn=make_search_fn([]),
                             fetch_fn=make_fetch_fn(bodies, str(tmp_path)))
        result = asyncio.run(run_claim(bus, tracker, cid, text, config))
        assert result.note.startswith("early-exit(grounding):")
        assert bus.history("claim.no_exit") == []
        assert len(bus.history("claim.early_exit")) == 1


# ---------------------------------------------------------------------------
# guards that must NEVER move (through the new paths)
# ---------------------------------------------------------------------------

def test_guard_tolerance_still_2pct():
    from live.swarm.coordinator import _default_rel_tolerance

    assert _default_rel_tolerance() == 0.02
    # 2% boundary through both paths (max-denominator for support,
    # evidence-denominator for refutation; both read the same 2%).
    # 12,750 is within 2% (250/12,750 = 1.96%); 12,756 is over
    # (256/12,756 = 2.01%).
    plan = dict(MERGED_SYN_PLAN)
    at_edge = _code_pkg(quote='"date":"2024","value":12750')
    assert early_exit_reason([at_edge], SYN_TEXT) is not None
    assert code_refutation_reason([at_edge], SYN_TEXT, plan) is None
    over_edge = _code_pkg(quote='"date":"2024","value":12756')
    assert early_exit_reason([over_edge], SYN_TEXT) is None
    assert code_refutation_reason([over_edge], SYN_TEXT, plan) is not None


def test_guard_scale_backstop_survives_fast_path(tmp_path):
    # 1000x gap, no scale word: verifier abstains AND the fast-path
    # stays silent (no invented refutation from a scale slip).
    import hashlib as _hl

    from live.evidence.package import EvidencePackage
    from live.run_verify import claim_input_for
    from live.swarm.coordinator import _DocStore
    from live.verifier.check import verify_claim

    body = ('[{"page":1},[{"indicator":{"id":"SP.POP.TOTL"},'
            '"countryiso3code":"CHL","date":"2024","value":8000000}]]')
    url = ("https://api.worldbank.org/v2/country/CHL/indicator/"
           "SP.POP.TOTL?format=json")
    quote = '"date":"2024","value":8000000'
    raw = body.encode("utf-8")
    sha = _hl.sha256(raw).hexdigest()
    fetch = FetchResult(
        url=url, final_url=url, status=200, content_type="application/json",
        size=len(raw), sha256=sha,
        snapshot_path=str(tmp_path / f"{sha}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=body)
    docs = _DocStore()
    doc = docs.add(fetch, title="WB")
    text = "Chile had 8000 people in 2024."
    pkgd = build_package("c-s", url, quote, doc, 2024, 0, extractor="code")
    assert pkgd is not None
    pkg = EvidencePackage.from_dict(pkgd)
    claim = claim_input_for({"id": "c-s", "text": text})
    verdict = verify_claim(claim, [pkg], lambda _p: raw)
    assert verdict.verdict == "unverifiable"
    assert "possible_scale_error" in verdict.reasons
    plan = {"indicator": "SP.POP.TOTL", "iso3": "CHL", "year": 2024}
    assert code_refutation_reason([pkgd], text, plan) is None


def test_guard_hierarchy_primary_beats_secondary(tmp_path):
    # Primary support + secondary contradiction: verifier still says
    # supported (hierarchy untouched); the fast-path support exit keeps
    # the same verdict with fewer packages.
    import hashlib as _hl

    from live.evidence.package import EvidencePackage
    from live.run_verify import claim_input_for
    from live.swarm.coordinator import _DocStore
    from live.verifier.check import verify_claim

    raw_p = SYN_WB_BODY.encode("utf-8")
    sha_p = _hl.sha256(raw_p).hexdigest()
    fetch_p = FetchResult(
        url=SYN_WB_URL, final_url=SYN_WB_URL, status=200,
        content_type="application/json", size=len(raw_p), sha256=sha_p,
        snapshot_path=str(tmp_path / f"{sha_p}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=SYN_WB_BODY)
    raw_s = WIKI_BODY.encode("utf-8")
    sha_s = _hl.sha256(raw_s).hexdigest()
    fetch_s = FetchResult(
        url=WIKI_URL, final_url=WIKI_URL, status=200,
        content_type="text/html", size=len(raw_s), sha256=sha_s,
        snapshot_path=str(tmp_path / f"{sha_s}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=WIKI_BODY)
    docs = _DocStore()
    doc_p = docs.add(fetch_p, title="WB")
    doc_s = docs.add(fetch_s, title="W")
    # Chile TRUE: primary code supports; a secondary prose quote with the
    # same unit (people/count) but a wrong figure contradicts. Hierarchy
    # must still decide supported from the primary alone.
    bad_wiki_body = ("<html><body><p>Chile had 99,000 people in 2024, "
                     "according to apress roundup of demographic estimates "
                     "published for general readers worldwide.</p></body></html>")
    bad_wiki_quote = ("Chile had 99,000 people in 2024, according to apress "
                      "roundup of demographic estimates published for "
                      "general readers worldwide.")
    raw_b = bad_wiki_body.encode("utf-8")
    sha_b = _hl.sha256(raw_b).hexdigest()
    fetch_b = FetchResult(
        url=WIKI_URL, final_url=WIKI_URL, status=200,
        content_type="text/html", size=len(raw_b), sha256=sha_b,
        snapshot_path=str(tmp_path / f"{sha_b}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=bad_wiki_body)
    docs_b = _DocStore()
    doc_b = docs_b.add(fetch_b, title="W")
    assert bad_wiki_quote in bad_wiki_body
    pkg_p = EvidencePackage.from_dict(
        build_package("c-h", SYN_WB_URL, SYN_WB_QUOTE, doc_p, 2024, 0,
                      extractor="code"))
    pkg_s = EvidencePackage.from_dict(
        build_package("c-h", WIKI_URL, bad_wiki_quote, doc_b, 2024, 1))
    assert pkg_p.source_type == "primary"
    assert pkg_s.source_type == "secondary"
    claim = claim_input_for({"id": "c-h", "text": SYN_TEXT})
    snaps = {pkg_p.sha256: raw_p, pkg_s.sha256: raw_b}
    verdict = verify_claim(claim, [pkg_p, pkg_s],
                           lambda p: snaps[p.sha256])
    assert verdict.verdict == "supported"
    assert verdict.details.get("hierarchy") == "primary_beats_secondary"


def test_guard_arithmetic_never_refutes(tmp_path):
    # Computed figures abstain even against a mismatching component row:
    # the verifier guard holds and the fast-path never fires.
    from live.run_verify import claim_input_for
    from live.verifier.check import verify_claim
    from live.evidence.package import EvidencePackage
    from live.swarm.coordinator import _DocStore
    import hashlib as _hl

    body = ('[{"page":1},[{"indicator":{"id":"SP.POP.TOTL"},'
            '"countryiso3code":"CHL","date":"2024","value":12500}]]')
    url = ("https://api.worldbank.org/v2/country/CHL/indicator/"
           "SP.POP.TOTL?format=json")
    quote = '"date":"2024","value":12500'
    raw = body.encode("utf-8")
    sha = _hl.sha256(raw).hexdigest()
    fetch = FetchResult(
        url=url, final_url=url, status=200, content_type="application/json",
        size=len(raw), sha256=sha,
        snapshot_path=str(tmp_path / f"{sha}.html"),
        captured_at="2026-11-01T00:00:00+00:00", text=body)
    docs = _DocStore()
    doc = docs.add(fetch, title="WB")
    pkgd = build_package("c-a", url, quote, doc, 2024, 0, extractor="code")
    assert pkgd is not None
    pkg = EvidencePackage.from_dict(pkgd)
    claim = claim_input_for({"id": "c-a", "text": ARITH_TEXT})
    verdict = verify_claim(claim, [pkg], lambda _p: raw)
    assert verdict.verdict == "unverifiable"
    assert "arithmetic_needs_computation" in verdict.reasons
    plan = {"indicator": "SP.POP.TOTL", "iso3": "CHL", "year": 2024}
    assert code_refutation_reason([pkgd], ARITH_TEXT, plan) is None
    assert no_exit_reason([pkgd], ARITH_TEXT, plan) == \
        "no_exit_ineligible_arithmetic"
