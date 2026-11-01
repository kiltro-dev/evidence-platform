"""M4 infra quick wins: reasoning_effort + usage, parallel retrieve, model cache key.

Fully offline: the OpenAI client is faked, connectors are fakes (one
raising), and the API is driven via TestClient with a stubbed pipeline.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import atlas.api.app as appmod  # noqa: E402
import atlas.pipeline.retrieve as retrieve_mod  # noqa: E402
import atlas.pipeline.run as runmod  # noqa: E402
from atlas.connectors import InvalidGeoError, ProviderError  # noqa: E402
from atlas.core.types import (  # noqa: E402
    Conclusion,
    Evidence,
    Report,
    StructuredClaim,
)
from atlas.models.meta import MetaClient  # noqa: E402
from atlas.pipeline.retrieve import retrieve_all  # noqa: E402

# ---------------------------------------------------------------------------
# Task 1: reasoning_effort param + last_usage capture (mock OpenAI client).
# ---------------------------------------------------------------------------


def _usage(prompt=10, completion=20, reasoning=5, total=30):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
        total_tokens=total,
    )


class _FakeCompletions:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        msg = SimpleNamespace(content=self.content)
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=self.usage)


_DEFAULT_USAGE = object()


def _meta_with_fake(content='{"ok": true}', usage=_DEFAULT_USAGE):
    llm = MetaClient(api_key="test-key-not-a-secret")
    fake = _FakeCompletions(content, _usage() if usage is _DEFAULT_USAGE else usage)
    llm._get_client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return llm, fake


def test_reasoning_effort_omitted_by_default():
    llm, fake = _meta_with_fake()
    assert llm.complete_json("sys", "user") == {"ok": True}
    assert "reasoning_effort" not in fake.kwargs  # current behavior kept
    assert fake.kwargs["max_tokens"] == 1500
    assert fake.kwargs["model"] == llm.model


def test_reasoning_effort_sent_when_provided():
    llm, fake = _meta_with_fake()
    llm.complete_json("sys", "user", reasoning_effort="low")
    assert fake.kwargs["reasoning_effort"] == "low"


def test_last_usage_captures_reasoning_tokens():
    llm, _ = _meta_with_fake()
    assert llm.last_usage is None  # nothing recorded before the first call
    llm.complete_json("sys", "user")
    assert llm.last_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 30,
        "cached_tokens": 0,
    }


def test_last_usage_none_when_response_has_no_usage():
    llm, _ = _meta_with_fake(usage=None)
    llm.complete_json("sys", "user")
    assert llm.last_usage is None


def test_last_usage_recorded_even_on_empty_content():
    llm, _ = _meta_with_fake(content="   ")
    with pytest.raises(RuntimeError, match="empty content"):
        llm.complete_json("sys", "user")
    # Truncated calls still spent tokens (live probe: 48 reasoning tokens).
    assert llm.last_usage is not None
    assert llm.last_usage["reasoning_tokens"] == 5


def test_transport_failure_leaves_last_usage_untouched():
    llm, fake = _meta_with_fake()

    def boom(**kwargs):
        raise RuntimeError("connection reset")

    fake.create = boom
    with pytest.raises(RuntimeError, match="connection reset"):
        llm.complete_json("sys", "user")
    assert llm.last_usage is None


# ---------------------------------------------------------------------------
# Task 2: parallel connector fan-out.
# ---------------------------------------------------------------------------


def _ev(connector, locator):
    return Evidence(
        claim_id="clm_test",
        connector=connector,
        source_name="fake",
        source_kind="primary",
        evidence_kind="direct",
        locator=locator,
    )


class _FakeConn:
    def __init__(self, name, items=None, exc=None, delay=0.0):
        self.name = name
        self.domains = ("general",)
        self._items = items or []
        self._exc = exc
        self.delay = delay
        self.calls = 0

    def retrieve(self, claim, limit=5):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self._exc is not None:
            raise self._exc
        return [e for e in self._items[:limit]]


@pytest.fixture()
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(retrieve_mod, "RETRY_DELAY_S", 0)


def test_retrieve_all_merges_in_connector_order(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    # Slow connector FIRST: results must still follow input order.
    slow = _FakeConn("slow", [_ev("slow", "s1")], delay=0.3)
    fast = _FakeConn("fast", [_ev("fast", "f1"), _ev("fast", "f2")])
    evidence, errors = retrieve_all([slow, fast], claim, 5)
    assert errors == []
    assert [e.locator for e in evidence] == ["s1", "f1", "f2"]
    assert slow.calls == fast.calls == 1


def test_retrieve_all_runs_in_parallel(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    conns = [_FakeConn(f"c{i}", [_ev(f"c{i}", f"e{i}")], delay=0.3) for i in range(3)]
    t0 = time.perf_counter()
    evidence, errors = retrieve_all(conns, claim, 5)
    elapsed = time.perf_counter() - t0
    assert errors == []
    assert len(evidence) == 3
    assert elapsed < 0.9  # sequential would take >= 0.9s


def test_retrieve_all_reports_per_connector_timings(no_retry_sleep):
    # Perf MEDIUM (observability): each connector's wall time must be
    # measurable so the audit trail can show it (pre-fix: retrieve_all
    # returned no timings and audit retrieve steps read ~0.0ms).
    claim = StructuredClaim(raw_text="x")
    slow = _FakeConn("slow", [_ev("slow", "s1")], delay=0.3)
    fast = _FakeConn("fast", [_ev("fast", "f1")])
    timings: dict = {}
    evidence, errors = retrieve_all([slow, fast], claim, 5, timings=timings)
    assert errors == []
    assert set(timings) == {"slow", "fast"}
    assert timings["slow"] >= 0.25
    assert timings["fast"] < 0.25


def test_retrieve_all_isolates_failure(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    boom = RuntimeError("source exploded")
    bad = _FakeConn("bad", exc=boom)
    good = _FakeConn("good", [_ev("good", "g1")])
    evidence, errors = retrieve_all([bad, good], claim, 5)
    assert [e.locator for e in evidence] == ["g1"]  # survivor intact
    assert len(errors) == 1
    assert errors[0][0] == "bad"
    assert errors[0][1] is boom  # original exception object preserved


def test_retrieve_all_retries_transient_once_then_succeeds(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    flaky = _FakeConn("flaky", [_ev("flaky", "r1")])
    attempts = {"n": 0}
    inner = flaky.retrieve

    def sometimes_ok(claim, limit=5):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ProviderError("timeout", transient=True)
        return inner(claim, limit=limit)

    flaky.retrieve = sometimes_ok
    evidence, errors = retrieve_all([flaky], claim, 5)
    assert errors == []
    assert [e.locator for e in evidence] == ["r1"]
    assert attempts["n"] == 2


def test_retrieve_all_transient_still_failing_lands_in_errors(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    bad = _FakeConn("bad", exc=ProviderError("down", transient=True))
    evidence, errors = retrieve_all([bad], claim, 5)
    assert evidence == []
    assert bad.calls == 2  # initial + one retry, mirroring run.py
    assert errors[0][0] == "bad"
    assert isinstance(errors[0][1], ProviderError)


def test_retrieve_all_permanent_error_not_retried(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    bad = _FakeConn("bad", exc=ProviderError("400 bad", transient=False))
    evidence, errors = retrieve_all([bad], claim, 5)
    assert evidence == []
    assert bad.calls == 1  # no retry for permanent failures
    assert isinstance(errors[0][1], ProviderError)


def test_retrieve_all_preserves_invalid_geo_type(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    bad = _FakeConn("bad", exc=InvalidGeoError("XXX"))
    _, errors = retrieve_all([bad], claim, 5)
    assert bad.calls == 1  # no retry, mirroring run.py
    assert isinstance(errors[0][1], InvalidGeoError)


def test_retrieve_all_empty_connectors():
    claim = StructuredClaim(raw_text="x")
    assert retrieve_all([], claim, 5) == ([], [])


def test_retrieve_all_errors_follow_connector_order(no_retry_sleep):
    claim = StructuredClaim(raw_text="x")
    a = _FakeConn("a", exc=RuntimeError("A"))
    b = _FakeConn("b", [_ev("b", "ok")])
    c = _FakeConn("c", exc=RuntimeError("C"))
    evidence, errors = retrieve_all([a, b, c], claim, 5)
    assert [e.locator for e in evidence] == ["ok"]
    assert [name for name, _ in errors] == ["a", "c"]


# ---------------------------------------------------------------------------
# Task 3: model name in the identical-text cache key (regression).
# ---------------------------------------------------------------------------


def _stub_report(text: str) -> Report:
    return Report(
        claim=StructuredClaim(raw_text=text),
        conclusion=Conclusion(stance="unverifiable", summary="stub"),
    )


@pytest.fixture()
def api_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    appmod._rate_hits.clear()
    appmod._cache.clear()
    calls: list[dict] = []

    def fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        calls.append({"text": text, "limit": limit, "persist": persist})
        return _stub_report(text)

    monkeypatch.setattr(runmod, "analyze", fake)
    model = {"name": "muse-spark-1.3"}

    def fake_llm():
        return SimpleNamespace(model=model["name"])

    monkeypatch.setattr(appmod, "get_llm", fake_llm)
    with TestClient(appmod.app, raise_server_exceptions=False) as c:
        yield c, calls, model


def test_cache_key_includes_model_name(api_ctx):
    client, calls, model = api_ctx
    first = client.post("/api/analyze", json={"text": "model split"})
    assert first.status_code == 200
    assert len(calls) == 1
    # Same model -> cache hit, pipeline not re-run.
    second = client.post("/api/analyze", json={"text": "model split"})
    assert second.status_code == 200
    assert len(calls) == 1
    assert second.json()["report_id"] == first.json()["report_id"]
    # Different model -> cache MISS (stale-model fix), pipeline re-runs.
    model["name"] = "muse-spark-2.0"
    third = client.post("/api/analyze", json={"text": "model split"})
    assert third.status_code == 200
    assert len(calls) == 2
    assert third.json()["report_id"] != first.json()["report_id"]
    # Back to the first model -> original entry still cached.
    model["name"] = "muse-spark-1.3"
    fourth = client.post("/api/analyze", json={"text": "model split"})
    assert fourth.status_code == 200
    assert len(calls) == 2
    assert fourth.json()["report_id"] == first.json()["report_id"]
    # Key shape carries the model as its 5th element.
    assert all(len(k) == 5 for k in appmod._cache.keys())
    assert {k[4] for k in appmod._cache.keys()} == {
        "muse-spark-1.3",
        "muse-spark-2.0",
    }


def test_cache_none_model_is_own_key(api_ctx, monkeypatch):
    client, calls, _ = api_ctx
    monkeypatch.setattr(appmod, "get_llm", lambda: None)  # degraded path
    resp = client.post("/api/analyze", json={"text": "degraded split"})
    assert resp.status_code == 200
    assert len(calls) == 1
    assert list(appmod._cache.keys())[0][4] is None
    again = client.post("/api/analyze", json={"text": "degraded split"})
    assert again.status_code == 200
    assert len(calls) == 1  # degraded entry hits for degraded requests


def test_concurrent_identical_posts_run_pipeline_once(tmp_path, monkeypatch):
    # API LOW (cache-herd): 20 parallel identical POSTs each ran the full
    # pipeline (check-then-act race on _cache). Singleflight: the first
    # request owns the key, the rest wait and share its cached report.
    import threading

    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    appmod._inflight.clear()
    calls: list = []
    calls_lock = threading.Lock()

    def slow_fake(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        with calls_lock:
            calls.append(text)
        time.sleep(0.3)
        return _stub_report(text)

    monkeypatch.setattr(runmod, "analyze", slow_fake)
    n = 12
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i):
        barrier.wait(timeout=10)
        results[i] = client.post(
            "/api/analyze", json={"text": "herd me once", "persist": False}
        )

    with TestClient(appmod.app, raise_server_exceptions=False) as client:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert all(r is not None and r.status_code == 200 for r in results)
    assert len(calls) == 1
    ids = {r.json()["report_id"] for r in results}
    assert len(ids) == 1


def test_singleflight_owner_failure_fails_over_to_joiner(tmp_path, monkeypatch):
    # The owner reports its own genuine failure (500); a joiner that never
    # ran must not inherit it — it loops, owns the key, and runs clean.
    # _inflight must be empty afterwards (no leaked events).
    import threading

    monkeypatch.setenv("ATLAS_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "get_llm", lambda: None)
    appmod._rate_hits.clear()
    appmod._cache.clear()
    appmod._inflight.clear()
    state = {"n": 0}
    state_lock = threading.Lock()

    def flaky(text, llm=None, limit=5, persist=True, domain="auto", **kw):
        with state_lock:
            state["n"] += 1
            first = state["n"] == 1
        if first:
            time.sleep(0.3)  # hold the key so the sibling surely joins
            raise RuntimeError("boom-first")
        return _stub_report(text)

    monkeypatch.setattr(runmod, "analyze", flaky)
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(i):
        barrier.wait(timeout=10)
        results[i] = client.post(
            "/api/analyze", json={"text": "fail over once", "persist": False}
        )

    with TestClient(appmod.app, raise_server_exceptions=False) as client:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert {r.status_code for r in results} == {200, 500}
    assert state["n"] == 2
    assert appmod._inflight == {}
    ok = next(r for r in results if r.status_code == 200)
    assert ok.json()["report_id"]
