"""Regression tests for integration fixes: status classification + dedupe."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402

import atlas.pipeline.run as runmod  # noqa: E402
from atlas.connectors import ProviderError, as_provider_error  # noqa: E402
from atlas.core.types import Evidence, StructuredClaim  # noqa: E402


def _status_error(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        f"{code} err",
        request=httpx.Request("GET", "http://example.invalid"),
        response=httpx.Response(code),
    )


def test_rate_limit_is_transient():
    assert as_provider_error(_status_error(429), "ctx").transient is True


def test_server_error_is_transient():
    assert as_provider_error(_status_error(503), "ctx").transient is True


def test_client_error_is_permanent():
    assert as_provider_error(_status_error(404), "ctx").transient is False


def test_timeout_is_transient():
    err = httpx.TimeoutException("timed out")
    assert as_provider_error(err, "ctx").transient is True


class _Flaky:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def retrieve(self, claim, limit=5):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return [
            Evidence(
                claim_id=claim.claim_id,
                connector="flaky",
                source_name="flaky",
                source_kind="secondary",
                evidence_kind="direct",
            )
        ]


def test_transient_failure_retried_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(runmod, "RETRY_DELAY_S", 0)
    conn = _Flaky([ProviderError("boom", transient=True)])
    claim = StructuredClaim(raw_text="x")
    out = runmod._retrieve_with_retry(conn, claim, 5)
    assert len(out) == 1 and conn.calls == 2


def test_permanent_failure_not_retried(monkeypatch):
    monkeypatch.setattr(runmod, "RETRY_DELAY_S", 0)
    conn = _Flaky([ProviderError("bad", transient=False)])
    claim = StructuredClaim(raw_text="x")
    try:
        runmod._retrieve_with_retry(conn, claim, 5)
        raise AssertionError("should have raised")
    except ProviderError:
        pass
    assert conn.calls == 1


def test_double_transient_raises_after_two_calls(monkeypatch):
    monkeypatch.setattr(runmod, "RETRY_DELAY_S", 0)
    conn = _Flaky(
        [ProviderError("t1", transient=True), ProviderError("t2", transient=True)]
    )
    claim = StructuredClaim(raw_text="x")
    try:
        runmod._retrieve_with_retry(conn, claim, 5)
        raise AssertionError("should have raised")
    except ProviderError as exc:
        assert exc.transient is True
    assert conn.calls == 2


def test_general_claim_uncertainties_deduplicated():
    orig = runmod.for_domain
    runmod.for_domain = lambda domain: []  # noqa: E731
    try:
        report = runmod.analyze(
            "Democracy is the best system of government.",
            llm=None,
            persist=False,
        )
    finally:
        runmod.for_domain = orig
    codes = [u.code for u in report.uncertainties]
    assert report.conclusion.stance == "not_assessable"
    assert len(codes) == len(set(codes)), f"duplicated codes: {codes}"
