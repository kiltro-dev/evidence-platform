"""M1e E2E tests: offline pipeline + event contract. 0 live calls.

The pipeline stages under test are the REAL ``live/run_live.py`` pieces
(``flatten_final`` + ``process_final``: detector -> bus -> tracker); the
ASR side is the recorded ``live/fixtures/asr_sample.jsonl`` session
replayed through ``TurnTracker`` (no network, no key), and the model
detector side uses ``FakeLLM`` doubles (same ``complete_json`` interface
as ``live/tests/test_detector.py``).
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "live"))

import run_live  # noqa: E402
from live.detector.claims import Detector  # noqa: E402
from live.oido.asr_ws import TurnTracker  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "asr_sample.jsonl")


class FakeLLM:
    """Test double for the model client (complete_json interface)."""

    name = "fake-llm"
    available = True

    def __init__(self, script):
        self._script = list(script)
        self.last_usage = None
        self.last_usage_seq = 0

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 20,
                           "reasoning_tokens": 5, "total_tokens": 120,
                           "cached_tokens": 0}
        return self._script.pop(0)


def fixture_finals():
    """Recorded s2c server messages -> flat final turns (offline replay)."""
    tracker = TurnTracker()
    turns = []
    with open(FIXTURE, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("dir") != "s2c":
                continue
            msg = row["msg"]
            msg = json.loads(msg) if isinstance(msg, str) else msg
            for ev in tracker.feed(msg):
                turn = run_live.flatten_final(ev)
                if turn is not None:
                    turns.append(turn)
    return turns


def run_pipeline(turns, detector, run_log, llm=None):
    """Drive turns through process_final; return (bus, detected, usage)."""
    async def go():
        bus = EventBus(run_log=run_log)
        tracker = ClaimTracker(bus)
        context: list[dict] = []
        usage: dict = {}
        detected = []
        try:
            for turn in turns:
                detected.extend(await run_live.process_final(
                    turn, detector=detector, bus=bus, tracker=tracker,
                    context=context, llm=llm, usage=usage))
            return bus, detected, usage, tracker
        finally:
            await bus.close()

    return asyncio.run(go())


def test_offline_pipeline_heuristic_fixture():
    """Recorded session -> 3 finals -> 2 heuristic claims + escuchada."""
    turns = fixture_finals()
    assert [t["turn"] for t in turns] == [0, 1, 2]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "run.jsonl")
        bus, detected, _usage, tracker = run_pipeline(
            turns, Detector(llm=None), log)
        assert len(detected) == 2
        assert [d["claim_id"] for d in detected] == [
            "clm_dcb0b1c91f05", "clm_611342f26826"]
        assert detected[0]["text"] == (
            "El producto interno bruto creció un dos coma cuatro por ciento.")
        assert detected[1]["text"] == (
            "Inflation fell below three percent in June.")
        assert all(d["resolved"] is None for d in detected)  # never resolved
        states = bus.history("claim.state")
        assert len(states) == 2
        assert all(s["state"] == "escuchada" for s in states)
        assert all(tracker.state(d["claim_id"]) == "escuchada"
                   for d in detected)
        assert len(bus.history("transcript.final")) == 3
        # Run log is the replayable JSONL base (M4): one line per bus event.
        rows = [json.loads(line) for line in
                open(log, encoding="utf-8").read().splitlines()]
        assert len(rows) == len(bus.history()) == 3 + 2 + 2
        assert [r["seq"] for r in rows] == [1, 2, 3, 4, 5, 6, 7]


def test_offline_pipeline_model_double():
    """FakeLLM script: worthiness + grounded resolution + usage folded."""
    turns = fixture_finals()
    script = [
        {"claims": []},  # turn 0 greeting: nothing worth checking
        {"claims": [{"text": ("El producto interno bruto creció un dos coma "
                              "cuatro por ciento."),
                     "worth_checking": True,
                     # No new content words -> grounded, carried through.
                     "resolved": ("El producto interno bruto creció un dos "
                                  "coma cuatro por ciento."),
                     "reason": "statistic"}]},
        {"claims": [{"text": "Inflation fell below three percent in June.",
                     "worth_checking": True,
                     # "france" never mentioned -> resolution dropped,
                     # verbatim span survives.
                     "resolved": "Inflation in France fell below three "
                                 "percent in June.",
                     "reason": "statistic"}]},
    ]
    fake = FakeLLM(script)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "run.jsonl")
        bus, detected, usage, _tracker = run_pipeline(
            turns, Detector(llm=fake), log, llm=fake)
        assert len(detected) == 2
        assert detected[0]["resolved"] == detected[0]["text"]
        assert detected[1]["resolved"] is None
        # F2-3: collect_usage also folds cached_tokens (0 here).
        assert usage == {"seq": 3, "prompt_tokens": 300,
                         "completion_tokens": 60, "cached_tokens": 0}
        assert len(bus.history("claim.state")) == 2


def test_event_contract_all_bus_events():
    """Every bus event: JSON-only, enveloped, contract-shaped, joinable."""
    turns = fixture_finals()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "run.jsonl")
        bus, _detected, _usage, _tracker = run_pipeline(
            turns, Detector(llm=None), log)
        events = bus.history()
        assert events, "pipeline must emit bus events"
        for ev in events:
            json.dumps(ev)  # JSON-only contract
            assert isinstance(ev["seq"], int) and ev["seq"] >= 1
            assert isinstance(ev["bus_ts"], float)
        finals = [e for e in events if e["type"] == "transcript.final"]
        assert finals and all(
            {"turn", "text", "speaker", "t_start", "t_end"} <= set(e)
            and e["text"].strip() for e in finals)
        detected = [e for e in events if e["type"] == "claim.detected"]
        assert detected and all(
            {"claim_id", "text", "resolved", "t_start", "t_end"} <= set(e)
            and e["claim_id"].startswith("clm_") for e in detected)
        states = [e for e in events if e["type"] == "claim.state"]
        assert len(states) == len(detected)
        ids = {e["claim_id"] for e in detected}
        assert all(s["state"] == "escuchada" and s["prev"] is None
                   and s["claim_id"] in ids for s in states)
        # Envelope seqs are dense; every log line reparses to the event.
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        rows = [json.loads(line) for line in
                open(log, encoding="utf-8").read().splitlines()]
        assert rows == events


def test_flatten_final_shapes():
    """Oido envelope -> flat turn; flat passes through; non-finals -> None."""
    oido = {"type": "transcript.final", "t_wall": 1.0, "t_media": 2.0,
            "payload": {"turn": 5, "text": " Hola. ", "speaker": "C",
                        "t_start": 0.1, "t_end": 2.0}}
    assert run_live.flatten_final(oido) == {
        "turn": 5, "text": " Hola. ", "speaker": "C",
        "t_start": 0.1, "t_end": 2.0}
    flat = {"type": "transcript.final", "turn": 1, "text": "Hi.",
            "speaker": None, "t_start": None, "t_end": 1.2}
    assert run_live.flatten_final(flat) == {
        "turn": 1, "text": "Hi.", "speaker": None,
        "t_start": None, "t_end": 1.2}
    assert run_live.flatten_final({"type": "transcript.partial",
                                   "payload": {"turn": 0, "text": "x"}}) is None
    assert run_live.flatten_final({"type": "transcript.final",
                                   "payload": {"turn": 0, "text": "  "}}) is None
    assert run_live.flatten_final(None) is None
    assert run_live.flatten_final({"no-type": True}) is None


def test_cost_and_latency_helpers():
    """ASR floors whole seconds; latency summary is p50/p95 ms."""
    assert run_live.estimate_asr_usd(209.9) == 209 / 3600 * 0.18
    assert run_live.estimate_asr_usd(0.9) == 0.0
    assert run_live.estimate_asr_usd(-5) == 0.0
    summary = run_live.summarize_ms([10.0, 20.0, 30.0, 40.0])
    assert summary["count"] == 4
    assert summary["p50_ms"] == 30.0  # nearest-rank, per server.percentile
    assert summary["p95_ms"] == 40.0
    assert run_live.summarize_ms([]) == {
        "count": 0, "p50_ms": None, "p95_ms": None}


def test_fifostream_wiring_shape():
    """FifoStream: simulated-stream label + -re restream + PCM24k demux."""
    stream = run_live.FifoStream("clip.mp4", chunk_ms=80, duration=60)
    assert stream.source_label == "simulated-stream"
    assert stream.chunk_bytes == 24000 * 2 * 80 // 1000 == 3840
    extra = stream.restream_extra
    assert extra[extra.index("-f") + 1] == "matroska"  # not segfaulty mpegts
    assert extra[extra.index("-t") + 1] == "60"
    assert "-y" in extra  # fifos already exist at open time
    argv = run_live.FifoStream.demux_argv_for("/tmp/live.mkv")
    assert argv[argv.index("-i") + 1] == "/tmp/live.mkv"
    assert argv[-2:] == ["s16le", "-"]
    assert argv[argv.index("-ar") + 1] == "24000"
    assert argv[argv.index("-ac") + 1] == "1"
