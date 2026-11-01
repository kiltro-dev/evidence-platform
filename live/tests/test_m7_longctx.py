"""M7 long-context self-contradiction tests.

Offline (FakeLLM doubles, $0): a planted synthetic pair is detected;
an invented quote, a same-window pair and compatible values are
discarded; a clean transcript yields an honest empty report. One gated
LIVE test (``ATLAS_LIVE=1``) runs a single long-context call over the
full keynote EN transcript (budget <= $1).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from live.longctx.check import (
    LongCtxConfig,
    Segment,
    load_srt,
    parse_srt,
    run_longctx,
    verify_pairs,
)
from live.orchestrator.bus import EventBus

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake-llm"
    available = True

    def __init__(self, script, prompt_tokens=1200, completion_tokens=120):
        self._script = list(script)
        self.calls = []
        self.last_usage = None
        self.last_usage_seq = 0
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens,
                           "reasoning_effort": reasoning_effort,
                           "schema": json_schema})
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct,
                           "reasoning_tokens": 0,
                           "total_tokens": self._pt + self._ct,
                           "cached_tokens": 0}
        return self._script.pop(0)


def _segs(*texts_and_times):
    return [Segment(t_sec=t, end_sec=t + 10.0, text=tx)
            for tx, t in texts_and_times]


def _run(bus, segments, pairs, **cfg_kw):
    llm = FakeLLM([{"pairs": pairs}])
    cfg = LongCtxConfig(llm=llm, **cfg_kw)
    report = asyncio.run(run_longctx(bus, segments, [], cfg))
    return report, llm


# ---------------------------------------------------------------------------
# offline: code verification
# ---------------------------------------------------------------------------

A_TX = "The city budget is 100 million dollars this year, a historic figure."
B_TX = ("Later, our budget this year reached 250 million dollars, "
        "a new record for the city.")


def test_planted_pair_detected():
    """Two literal contradictory quotes, 120s apart -> 1 event."""
    bus = EventBus()
    segments = _segs((A_TX, 0.0), (B_TX, 120.0))
    pair = {"quote_a": A_TX, "t_a": 0.0, "quote_b": B_TX, "t_b": 120.0,
            "entity": "budget", "indicator": "dollars",
            "why": "100M vs 250M dollars for the same budget."}
    report, llm = _run(bus, segments, [pair])
    assert len(llm.calls) == 1  # exactly one long-context call
    assert report["proposed"] == 1
    assert len(report["verified"]) == 1
    assert report["discarded"] == []
    events = bus.history("speaker.contradiction")
    assert len(events) == 1
    ev = events[0]
    assert ev["value_a"] == pytest.approx(100e6)
    assert ev["value_b"] == pytest.approx(250e6)
    assert ev["rel_diff"] > 0.02
    assert ev["t_b"] - ev["t_a"] > 30


def test_invented_quote_discarded():
    """A proposed quote that is not literal in the transcript is dropped."""
    bus = EventBus()
    segments = _segs((A_TX, 0.0), (B_TX, 120.0))
    pair = {"quote_a": A_TX, "t_a": 0.0,
            "quote_b": "The budget was secretly cut to one dollar.",
            "t_b": 120.0, "entity": "budget", "indicator": "dollars",
            "why": "invented by the test, not in the transcript."}
    report, _ = _run(bus, segments, [pair])
    assert report["verified"] == []
    assert len(report["discarded"]) == 1
    assert report["discarded"][0]["reason"] == "quote_not_literal"
    assert bus.history("speaker.contradiction") == []


def test_same_minute_discarded():
    """Contradictory quotes 10s apart fail the >30s separation rule."""
    bus = EventBus()
    segments = _segs((A_TX, 0.0), (B_TX, 10.0))
    pair = {"quote_a": A_TX, "t_a": 0.0, "quote_b": B_TX, "t_b": 10.0,
            "entity": "budget", "indicator": "dollars",
            "why": "100M vs 250M but in the same minute."}
    report, _ = _run(bus, segments, [pair])
    assert report["verified"] == []
    assert report["discarded"][0]["reason"] == "same_window"
    assert bus.history("speaker.contradiction") == []


def test_clean_transcript_empty():
    """Model proposes nothing -> honest empty report, no events."""
    bus = EventBus()
    segments = _segs(("Welcome to the summit, thank you all.", 0.0),
                     ("Good night, and safe travels home.", 120.0))
    report, llm = _run(bus, segments, [])
    assert len(llm.calls) == 1
    assert report["proposed"] == 0
    assert report["verified"] == []
    assert report["discarded"] == []
    assert report["events"] == []
    assert bus.history("speaker.contradiction") == []


def test_call_contract_longctx_low_and_strict():
    """The single call uses reasoning low + strict structured output."""
    bus = EventBus()
    segments = _segs(("Hello world, this is a long enough segment.", 0.0))
    _, llm = _run(bus, segments, [])
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["reasoning_effort"] == "low"
    assert call["schema"]["name"] == "m7_longctx"
    assert call["schema"]["schema"]["additionalProperties"] is False
    # full transcript travels in the single call (long context)
    assert "Hello world" in call["user"]


def test_compatible_values_discarded():
    """Same figure repeated (<=2% apart) is agreement, not contradiction."""
    bus = EventBus()
    again = "To repeat: the city budget is 101 million dollars this year."
    segments = _segs((A_TX, 0.0), (again, 120.0))
    pair = {"quote_a": A_TX, "t_a": 0.0, "quote_b": again, "t_b": 120.0,
            "entity": "budget", "indicator": "dollars",
            "why": "100M vs 101M are compatible."}
    report, _ = _run(bus, segments, [pair])
    assert report["verified"] == []
    assert report["discarded"][0]["reason"] == "no_collision"
    assert bus.history("speaker.contradiction") == []


def test_no_model_empty_report_zero_calls():
    """Without a model the check reports empty honestly, spending nothing."""
    bus = EventBus()
    segments = _segs((A_TX, 0.0), (B_TX, 120.0))
    report = asyncio.run(
        run_longctx(bus, segments, [], LongCtxConfig(llm=None)))
    assert report["model_calls"] == 0
    assert report["verified"] == [] and report["events"] == []
    assert any("no model" in n for n in report["notes"])


def test_polarity_clash_detected_without_numbers():
    """Explicit affirmation-vs-negation on one entity verifies (no units)."""
    bus = EventBus()
    pos = "The new bridge will open in spring, I promise you that today."
    neg = "The new bridge will not open in spring, that plan is dead now."
    segments = _segs((pos, 0.0), (neg, 200.0))
    pair = {"quote_a": pos, "t_a": 0.0, "quote_b": neg, "t_b": 200.0,
            "entity": "bridge", "indicator": "bridge",
            "why": "will open vs will not open."}
    report, _ = _run(bus, segments, [pair])
    assert len(report["verified"]) == 1
    assert report["verified"][0]["clash"] == "polarity"
    assert len(bus.history("speaker.contradiction")) == 1


def test_srt_parser_reads_keynote_blocks():
    """The SRT parser recovers timed segments from the real keynote file."""
    segments = load_srt(
        os.path.join(REPO_ROOT, "data", "keynote", "keynote_en.srt"))
    assert len(segments) == 12
    assert segments[0].t_sec == pytest.approx(0.0)
    assert segments[1].t_sec == pytest.approx(13.29)
    assert "1.41 billion people" in segments[1].text
    assert all(s.end_sec > s.t_sec for s in segments)


def test_verify_pairs_pure_code_needs_no_model():
    """verify_pairs is callable directly: wrong entity is rejected in code."""
    segments = _segs((A_TX, 0.0), (B_TX, 120.0))
    pair = {"quote_a": A_TX, "t_a": 0.0, "quote_b": B_TX, "t_b": 120.0,
            "entity": "submarine", "indicator": "dollars",
            "why": "entity not present in either quote."}
    verified, discarded = verify_pairs([pair], segments)
    assert verified == []
    assert discarded[0]["reason"] == "entity_mismatch"


# ---------------------------------------------------------------------------
# LIVE: full keynote EN, one long-context call (<= $1)
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_full_keynote_longctx():
    """LIVE: M7 over the complete keynote EN transcript (<= $1)."""
    if os.environ.get("ATLAS_LIVE") != "1":
        pytest.skip("live test: set ATLAS_LIVE=1 to run")
    from src.atlas.models.meta import MetaClient

    llm = MetaClient()
    if not llm.available:
        pytest.skip("live test: MODEL_API_KEY not configured")
    segments = load_srt(
        os.path.join(REPO_ROOT, "data", "keynote", "keynote_en.srt"))
    with open(os.path.join(REPO_ROOT, "live", "material", "truth.json"),
              encoding="utf-8") as fh:
        claims = [row for row in json.load(fh) if row.get("lang") == "en"]
    assert len(segments) == 12 and len(claims) > 0

    bus = EventBus()
    report = asyncio.run(run_longctx(
        bus, segments, claims, LongCtxConfig(llm=llm.fork())))
    print(f"\n[LIVE m7] proposed={report['proposed']} "
          f"verified={len(report['verified'])} "
          f"discarded={len(report['discarded'])} "
          f"cost=${report['cost_usd']:.4f}")
    for v in report["verified"]:
        print(f"[LIVE m7] VERIFIED t={v['t_a']:.1f}/{v['t_b']:.1f} "
              f"entity={v['entity']!r} indicator={v['indicator']!r} "
              f"values={v['value_a']}/{v['value_b']} {v['unit']} "
              f"rel_diff={v['rel_diff']}")
    for d in report["discarded"]:
        print(f"[LIVE m7] discarded reason={d['reason']} "
              f"detail={d.get('detail')}")
    print(f"[LIVE m7] notes={report['notes']}")
    assert report["model_calls"] == 1
    assert report["cost_usd"] <= 1.0
    # every verified pair re-verifies in pure code (no trust in the report)
    re_verified, _ = verify_pairs(
        [{"quote_a": v["quote_a"], "t_a": v["t_a"],
          "quote_b": v["quote_b"], "t_b": v["t_b"],
          "entity": v["entity"], "indicator": v["indicator"],
          "why": v["why"]} for v in report["verified"]], segments)
    assert len(re_verified) == len(report["verified"])
    assert (len(parse_srt("")) == 0)  # parser edge: empty input
