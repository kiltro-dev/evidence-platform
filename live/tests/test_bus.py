"""EventBus tests (M1b). No network, no model — pure asyncio + tmp files."""

import asyncio
import json

from live.orchestrator.bus import EventBus


def test_publish_envelopes_and_historizes():
    async def go():
        bus = EventBus()
        ev = await bus.publish({"type": "transcript.final", "text": "hola"})
        assert ev["seq"] == 1
        assert isinstance(ev["bus_ts"], float)
        assert ev["text"] == "hola"
        ev2 = await bus.publish({"type": "claim.state", "state": "escuchada"})
        assert ev2["seq"] == 2
        assert len(bus.history()) == 2
        assert len(bus.history("claim.state")) == 1
        await bus.close()

    asyncio.run(go())


def test_subscribers_typed_and_wildcard_sync_and_async():
    async def go():
        bus = EventBus()
        seen_typed, seen_all = [], []
        bus.subscribe("claim.detected", lambda e: seen_typed.append(e["claim_id"]))
        bus.subscribe("transcript.final", lambda e: seen_typed.append("WRONG"))

        async def wild(e):
            seen_all.append(e["type"])

        bus.subscribe(None, wild)
        await bus.publish({"type": "claim.detected", "claim_id": "clm_1"})
        assert seen_typed == ["clm_1"]
        assert seen_all == ["claim.detected"]
        bus.unsubscribe("claim.detected", seen_typed.append)  # absent: no-op
        await bus.close()

    asyncio.run(go())


def test_run_log_is_replayable_jsonl(tmp_path):
    async def go():
        log = tmp_path / "run.jsonl"
        bus = EventBus(run_log=log)
        await bus.publish({"type": "transcript.final", "turn": 1})
        await bus.publish({"type": "claim.detected", "claim_id": "clm_a"})
        await bus.close()
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert [r["type"] for r in rows] == ["transcript.final", "claim.detected"]
        assert rows[0]["seq"] == 1 and rows[1]["seq"] == 2

    asyncio.run(go())


def test_non_json_event_rejected():
    async def go():
        bus = EventBus()
        try:
            await bus.publish({"type": "x", "bad": object()})
        except TypeError:
            pass
        else:
            raise AssertionError("expected TypeError for non-JSON payload")
        try:
            await bus.publish({"no-type": True})
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for missing type")
        assert bus.history() == []
        await bus.close()

    asyncio.run(go())


def test_failing_subscriber_does_not_block_bus():
    async def go():
        bus = EventBus()
        good = []
        bus.subscribe(None, lambda e: 1 / 0)
        bus.subscribe(None, lambda e: good.append(e["seq"]))
        ev = await bus.publish({"type": "claim.state", "state": "escuchada"})
        assert ev["seq"] == 1 and good == [1]
        await bus.close()

    asyncio.run(go())
