"""Live-web counter-evidence pass (P1-A) tests. Offline: mocked transport
plus injected web clients only. No live calls — every test here must
pass with the network unplugged.

Contract pinned here: (1) deterministic citation validation accepts
good citations and rejects bad ones (no URL, empty span, non-https,
garbage hosts); (2) any transport/parse failure degrades to skip;
(3) the web pass is advisory-only — stances never move; (4) at most
one model call per falsify run; (5) ATLAS_WEB_FALSIFY=0 disables;
(6) the API key never appears in logs or audit text.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402

import atlas.pipeline.falsify as falsify_mod  # noqa: E402
from atlas.core.types import Comparison, StructuredClaim  # noqa: E402
from atlas.models.meta import MetaClient  # noqa: E402
from atlas.models.responses import (  # noqa: E402
    CounterEvidence,
    ResponsesClient,
    WebCandidate,
    WebCitation,
    parse_search_candidates,
    parse_url_citations,
    validate_citation,
)
from atlas.models.scripted import ScriptedClient  # noqa: E402
from atlas.pipeline.falsify import (  # noqa: E402
    FALSIFY_SCHEMA,
    _merge_usage,
    falsify_draft,
)
from atlas.pipeline.synthesize import decide_stance, synthesize  # noqa: E402

FAKE_KEY = "SECRETKEY123-NOT-A-REAL-KEY"


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="GDP grew 5% in 2023", domain="economy",
                claim_type="quantitative")
    base.update(kw)
    return StructuredClaim(**base)


def _cmp(verdict: str = "consistent", cid: str = "cmp_real",
         metric: str = "quantity_match") -> Comparison:
    return Comparison(  # type: ignore[arg-type]
        claim_id="c", comparison_id=cid, metric=metric, verdict=verdict,
        notes="value matches source",
    )


def _annotation(url, start, end, title="Source page",
                atype="url_citation"):
    ann: dict = {"type": atype, "title": title,
                 "start_index": start, "end_index": end}
    if url is not None:
        ann["url"] = url
    return ann


def _responses_payload(text="The statistics office reports growth was 1.2%.",
                       annotations=(), usage=None):
    return {
        "id": "resp_test_1",
        "object": "response",
        "model": "muse-spark-1.3",
        "output": [
            {"type": "reasoning", "summary": []},  # ignored, tolerated
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": text,
                 "annotations": list(annotations)},
            ]},
        ],
        "usage": usage or {"input_tokens": 120, "output_tokens": 60,
                           "total_tokens": 180},
    }


class MockTransport:
    """Recording fake transport: canned payloads or scripted failures."""

    def __init__(self, payloads=None, failures=None):
        self.payloads = list(payloads or [])
        self.failures = list(failures or [])
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "payload": payload, "timeout": timeout})
        if self.failures:
            raise self.failures.pop(0)
        assert self.payloads, "transport called with no payload scripted"
        result = self.payloads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _web_client(transport, **kw) -> ResponsesClient:
    kw.setdefault("api_key", FAKE_KEY)
    # Pinned: default base_url/model come from the environment, which
    # would make request-shape assertions env-fragile.
    kw.setdefault("base_url", "https://api.meta.ai/v1")
    kw.setdefault("model", "muse-spark-1.3")
    return ResponsesClient(transport=transport, **kw)


def _good_span(text):
    """Span covering 'growth was 1.2%' in the default payload text."""
    start = text.index("growth was 1.2%")
    return start, start + len("growth was 1.2%")


# --- request shape: one web_search call, key in header only ---


def test_request_shape_single_web_search_call():
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    transport = MockTransport(payloads=[_responses_payload(
        text, [_annotation("https://stats.example.gov/report", start, end)])])
    client = _web_client(transport)
    result = client.search_counter_evidence(
        "Claim: x", instructions=" Hunt counter-evidence. ")
    assert len(transport.calls) == 1  # <=1 extra call budget
    call = transport.calls[0]
    assert call["url"] == "https://api.meta.ai/v1/responses"
    assert call["payload"]["model"] == "muse-spark-1.3"
    assert call["payload"]["tools"] == [{"type": "web_search"}]
    assert call["payload"]["reasoning"] == {"effort": "low"}
    assert call["payload"]["instructions"] == " Hunt counter-evidence. "
    assert isinstance(result, CounterEvidence)
    assert len(result.citations) == 1
    assert client.last_usage_seq == 1
    assert client.last_usage == {"prompt_tokens": 120,
                                 "completion_tokens": 60,
                                 "reasoning_tokens": None,
                                 "total_tokens": 180,
                                 "cached_tokens": 0}


def test_api_key_only_in_authorization_header():
    transport = MockTransport(payloads=[_responses_payload()])
    _web_client(transport).search_counter_evidence(
        "Claim: x", instructions="i")
    headers = transport.calls[0]["headers"]
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in repr(transport.calls[0]["payload"])
    assert "api_key" not in transport.calls[0]["payload"]


# --- citation validation: accepts good, rejects bad ---


def test_validate_citation_accepts_good():
    cit = validate_citation("https://data.worldbank.org/indicator/x",
                            "GDP grew 1.2%", "World Bank")
    assert cit is not None
    assert cit.domain == "data.worldbank.org"
    assert cit.span == "GDP grew 1.2%"


def test_validate_citation_rejects_weak_spans():
    url = "https://data.worldbank.org/indicator/x"
    # Single chars and short digit-less words are noise, not quotes.
    assert validate_citation(url, "x") is None
    assert validate_citation(url, "Mayotte") is None
    # Short spans WITH digits are the core case (counter-figures).
    assert validate_citation(url, "7.4%") is not None
    assert validate_citation(url, "124,352,000") is not None
    # Exact boundary: 8 digit-less chars accept, 7 reject.
    assert validate_citation(url, "12345678") is not None
    assert validate_citation(url, "1234567") is not None  # digits win
    assert validate_citation(url, "abcdefgh") is not None
    assert validate_citation(url, "abcdefg") is None


def test_validate_citation_rejects_bad():
    cases = [
        (None, "some span"),                       # no URL
        ("", "some span"),                         # empty URL
        (123, "some span"),                        # non-string URL
        ("https://a.example.com/x", ""),           # empty span
        ("https://a.example.com/x", "   "),        # blank span
        ("https://a.example.com/x", None),         # no span
        ("http://a.example.com/x", "span"),        # non-https
        ("ftp://a.example.com/x", "span"),         # non-https
        ("notaurl", "span"),                       # unparsable
        ("https://localhost/x", "span"),           # single-label host
        ("https://127.0.0.1/x", "span"),           # IP literal
        ("https://www.google.com/search?q=gdp", "span"),  # garbage host
        ("https://maps.google.com/x", "span"),     # garbage subdomain
        ("https://bit.ly/abc", "span"),            # shortener
        ("https://example.com/x", "span"),         # RFC test domain
    ]
    for url, span in cases:
        assert validate_citation(url, span) is None, (url, span)


def test_parse_filters_mixed_annotations():
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    payload = _responses_payload(text, [
        _annotation("https://stats.example.gov/report", start, end),
        _annotation(None, start, end),                       # no URL
        _annotation("https://stats.example.gov/ok", end, end),  # empty span
        _annotation("http://stats.example.gov/x", start, end),  # http
        _annotation("https://www.google.com/search?q=x", start, end),  # garbage
        _annotation("https://stats.example.gov/report", start, end),  # dup
        {"type": "file_citation", "file_id": "f1"},           # other type
        "not-a-dict",                                        # malformed
    ])
    survivors = parse_url_citations(payload)
    assert len(survivors) == 1
    assert survivors[0].url == "https://stats.example.gov/report"
    assert survivors[0].span == "growth was 1.2%"


def test_parse_caps_survivors_and_tolerates_malformed():
    text = "word " * 20
    # Slice (0, 9) = "word word": a span strong enough to validate, so
    # this test exercises the CAP, not span rejection.
    anns = [_annotation(f"https://src{i}.example.gov/x", 0, 9)
            for i in range(10)]
    assert len(parse_url_citations(_responses_payload(text, anns))) == 5
    for bad in (None, {}, {"output": "nope"}, {"output": [{"nope": 1}]},
                {"output": [{"type": "message", "content": "nope"}]},
                {"output": [{"type": "message",
                             "content": [{"type": "output_text"}]}]},
                ["not", "a", "dict"]):
        assert parse_url_citations(bad) == [], bad


def test_parse_out_of_bounds_indices_fall_back_or_drop():
    text = "short text here"
    payload = _responses_payload(text, [
        _annotation("https://a.example.gov/1", 999, 1999),   # OOB, no quote
        _annotation("https://b.example.gov/2", -5, 3),       # negative
        _annotation("https://c.example.gov/3", 6, 2),        # inverted
    ])
    assert parse_url_citations(payload) == []
    quoted = _annotation("https://d.example.gov/4", 999, 1999)
    quoted["quoted_text"] = "inline quote"
    assert len(parse_url_citations(_responses_payload(text, [quoted]))) == 1


# --- wiring: survivors surface as attributed doubt ---


def test_web_citations_surface_as_attributed_doubt(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    transport = MockTransport(payloads=[_responses_payload(
        text, [_annotation("https://stats.example.gov/report", start, end,
                                     title="Stats office")])])
    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(
        claim, cmps, [], "supported", llm,
        web_client=_web_client(transport))
    assert len(transport.calls) == 1
    assert len(new_c) == 1 and len(new_u) == 1
    assert new_c[0].metric == "web_counter_evidence"
    assert new_c[0].actor == "model"
    assert new_c[0].verdict == "inconclusive"
    assert "stats.example.gov" in new_c[0].notes
    assert "growth was 1.2%" in new_c[0].notes
    assert "https://stats.example.gov/report" in new_c[0].notes
    assert new_u[0].code == "web_counter_evidence"
    assert new_u[0].severity == "minor"  # never blocking
    assert "stats.example.gov" in new_u[0].description
    # UT-A4: quotes are model-reported, pages never fetched by code.
    assert "model-reported quote" in new_c[0].notes
    assert "not fetched by code" in new_u[0].description
    assert "web_citations=1" in audit.detail
    assert "attacks=0" in audit.detail and "advisory_only" in audit.detail


def test_web_pass_alone_with_llm_none_and_injected_client(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    transport = MockTransport(payloads=[_responses_payload(
        text, [_annotation("https://stats.example.gov/report", start, end)])])
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", None,
        web_client=_web_client(transport))
    assert len(new_c) == 1 and len(new_u) == 1
    assert audit.actor.startswith("model:")
    assert "web_citations=1" in audit.detail


def test_no_citations_validated_is_clean_skip(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(payloads=[_responses_payload(
        "no citations here", [])])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert (new_c, new_u) == ([], [])
    assert "web_citations=0" in audit.detail


# --- degrade-to-skip: transport errors, timeouts, malformed payloads ---


def test_degrade_to_skip_on_transport_error(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(
        failures=[RuntimeError("connection reset by peer")])
    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(
        claim, cmps, [], "supported", llm,
        web_client=_web_client(transport, max_retries=0))
    assert (new_c, new_u) == ([], [])
    assert "web=skipped(model call failed RuntimeError)" in audit.detail
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"


def test_degrade_to_skip_on_timeout(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(
        failures=[httpx.ConnectTimeout("connect timed out")])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport, max_retries=0))
    assert (new_c, new_u) == ([], [])
    assert "ConnectTimeout" in audit.detail


def test_transient_retry_then_success(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    import atlas.models.responses as responses_mod
    monkeypatch.setattr(responses_mod, "RETRY_DELAY_S", 0)
    transport = MockTransport(
        payloads=[_responses_payload("ok", [])],
        failures=[httpx.ConnectError("dns blip")],
    )
    client = _web_client(transport, max_retries=1)
    result = client.search_counter_evidence("Claim: x", instructions="i")
    assert len(transport.calls) == 2
    assert result.citations == []


def test_non_transient_http_error_not_retried():
    request = httpx.Request("POST", "https://api.meta.ai/v1/responses")
    resp = httpx.Response(400, request=request)
    err = httpx.HTTPStatusError("bad request", request=request,
                                response=resp)
    transport = MockTransport(failures=[err])
    client = _web_client(transport, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        client.search_counter_evidence("Claim: x", instructions="i")
    assert len(transport.calls) == 1  # no retry on permanent 4xx


def test_malformed_response_object_degrades_to_skip(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    for bad in ({"bogus": True}, {"output": None}, {"output": []},
                {"output": [{"type": "message",
                             "content": [{"type": "refusal",
                                          "refusal": "no"}]}]}):
        transport = MockTransport(payloads=[bad])
        llm = ScriptedClient({}, default={"attacks": []})
        new_c, new_u, audit = falsify_draft(
            _claim(), [_cmp()], [], "supported", llm,
            web_client=_web_client(transport))
        assert (new_c, new_u) == ([], []), bad
        assert "web_citations=0" in audit.detail, bad


def test_non_object_json_raises_then_skips(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(payloads=[["not", "a", "dict"]])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert (new_c, new_u) == ([], [])
    assert "web=skipped(model call failed RuntimeError)" in audit.detail


def test_unavailable_web_client_skips(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(payloads=[_responses_payload()])
    llm = ScriptedClient({}, default={"attacks": []})
    # Force keylessness directly: the client honors .env, so env-only
    # deletion cannot prove unavailability on a keyed machine.
    web_client = _web_client(transport)
    web_client.api_key = ""
    assert web_client.available is False
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=web_client)
    assert (new_c, new_u) == ([], [])
    assert "web=skipped(no MODEL_API_KEY)" in audit.detail
    assert transport.calls == []


# --- advisory-only: stances never move ---


@pytest.mark.parametrize("verdict,draft", [
    ("consistent", "supported"),
    ("contradicts", "contradicted"),
    ("inconclusive", "unverifiable"),
    ("not_comparable", "unverifiable"),
])
def test_web_artifacts_leave_every_stance_untouched(
        monkeypatch, verdict, draft):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    transport = MockTransport(payloads=[_responses_payload(
        text, [_annotation("https://stats.example.gov/report", start, end)])])
    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp(verdict=verdict)]
    before = decide_stance(cmps, [], claim)
    assert before == draft
    new_c, new_u, _ = falsify_draft(
        claim, cmps, [], draft, llm,
        web_client=_web_client(transport))
    assert len(new_c) == 1  # doubt surfaced ...
    assert decide_stance(cmps + new_c, new_u, claim) == draft  # ... stance kept
    assert synthesize(claim, cmps + new_c, new_u, llm=None).stance == draft


def test_web_uncertainties_never_blocking_nor_stance_coded(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "word " * 20
    # Slice (0, 9) = "word word": validates, so the cap is exercised.
    anns = [_annotation(f"https://src{i}.example.gov/x", 0, 9)
            for i in range(7)]
    transport = MockTransport(payloads=[_responses_payload(text, anns)])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert len(new_c) == 5 and len(new_u) == 5  # capped, not flooding
    assert {u.severity for u in new_u} == {"minor"}
    assert {u.code for u in new_u} == {"web_counter_evidence"}
    assert "web_citations=5" in audit.detail


# --- kill-switch + offline gating ---


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " 0 "])
def test_env_flag_disables_web_pass(monkeypatch, value):
    monkeypatch.setenv("ATLAS_WEB_FALSIFY", value)
    transport = MockTransport(payloads=[_responses_payload()])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert (new_c, new_u) == ([], [])
    assert transport.calls == []  # not even attempted
    assert "web=disabled(ATLAS_WEB_FALSIFY=0)" in audit.detail


def test_test_doubles_never_trigger_live_client(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "dummy-key-present")
    def _boom(*a, **k):
        raise AssertionError("ResponsesClient must not be constructed")
    monkeypatch.setattr(falsify_mod, "ResponsesClient", _boom)
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert (new_c, new_u) == ([], [])
    assert "web=skipped(no real client" in audit.detail


def test_real_meta_client_auto_constructs_web_pass(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    constructed = []
    made = []

    class FakeWeb:
        name = "fake-web"
        available = True
        last_usage = {"prompt_tokens": 1, "completion_tokens": 2,
                      "reasoning_tokens": None, "total_tokens": 3}

        def __init__(self, *a, **k):
            constructed.append((a, k))

        def search_counter_evidence(self, user_text, **kw):
            made.append((user_text, kw))
            return CounterEvidence(text="counter", citations=[
                WebCitation(url="https://audit.example.gov/n",
                            title="t", span="growth was 1.2%",
                            domain="audit.example.gov"),
            ])

    monkeypatch.setattr(falsify_mod, "ResponsesClient", FakeWeb)

    def _fail(self, *a, **k):
        raise RuntimeError("offline test: main pass stays local")

    monkeypatch.setattr(MetaClient, "complete_json", _fail)
    llm = MetaClient(api_key="dummy-offline-key")  # lazy: no network
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(claim, cmps, [], "supported", llm)
    # Main pass failed locally -> skipped; web pass auto-constructed
    # exactly once and surfaced doubt. No live calls anywhere.
    assert len(constructed) == 1
    assert len(made) == 1
    assert "Claim:" in made[0][0] and "supported" in made[0][0]
    assert made[0][1]["instructions"]  # instructions always provided
    assert len(new_c) == 1 and len(new_u) == 1
    assert "web_citations=1" in audit.detail
    assert llm.last_usage is not None  # web spend attributed to the stage
    assert llm.last_usage_seq == 1
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"


def test_usage_merges_with_main_pass_spend(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)

    class FakeWeb:
        name = "fake-web"
        available = True
        last_usage = {"prompt_tokens": 10, "completion_tokens": 20,
                      "reasoning_tokens": 5, "total_tokens": 30}

        def __init__(self):
            self.calls = 0

        def search_counter_evidence(self, user_text, **kw):
            self.calls += 1
            return CounterEvidence(text="t", citations=[])

    class RichDouble(ScriptedClient):
        name = "rich-double"

        def __init__(self):
            super().__init__({}, default={"attacks": []})
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2,
                               "reasoning_tokens": None, "total_tokens": 3}
            self.last_usage_seq = 1

    llm = RichDouble()
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm, web_client=FakeWeb())
    assert (new_c, new_u) == ([], [])
    assert "web_citations=0" in audit.detail
    assert llm.last_usage == {"prompt_tokens": 11, "completion_tokens": 22,
                              "reasoning_tokens": 5, "total_tokens": 33,
                              "cached_tokens": None}
    assert llm.last_usage_seq == 2


def test_parse_orders_authoritative_before_cap():
    # Junk early in model order must not crowd out an official source:
    # collect-all, authoritative-first, THEN cap.
    text = "Unemployment was 7.4% in 2024."
    anns = (
        [_annotation(f"https://junk{i}.dailynewsdesk.info/x", 0, 24) for i in range(4)]
        + [_annotation("https://www.bea.gov/news/gdp", 0, 24)]
        + [_annotation(f"https://junk{i}.dailynewsdesk.info/x", 0, 24) for i in range(4, 8)]
    )
    out = parse_url_citations(_responses_payload(text, anns), max_citations=5)
    assert len(out) == 5
    assert out[0].domain == "www.bea.gov"
    assert [c.domain for c in out[1:]] == [
        "junk0.dailynewsdesk.info", "junk1.dailynewsdesk.info",
        "junk2.dailynewsdesk.info", "junk3.dailynewsdesk.info",
    ]


def test_merge_usage_sums_cached_tokens():
    first = {"prompt_tokens": 10, "completion_tokens": 5,
             "reasoning_tokens": 0, "total_tokens": 15, "cached_tokens": 4}
    second = {"prompt_tokens": 3, "completion_tokens": 1,
              "reasoning_tokens": 0, "total_tokens": 4, "cached_tokens": 2}
    assert _merge_usage(first, second)["cached_tokens"] == 6
    assert _merge_usage(first, None)["cached_tokens"] == 4
    assert _merge_usage({}, {})["cached_tokens"] is None


# --- secrets hygiene ---


def test_no_secret_in_audit_or_logs(monkeypatch, caplog):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    transport = MockTransport(
        failures=[RuntimeError("connection reset by peer")])
    llm = ScriptedClient({}, default={"attacks": []})
    with caplog.at_level("WARNING"):
        _, _, audit = falsify_draft(
            _claim(), [_cmp()], [], "supported", llm,
            web_client=_web_client(transport, max_retries=0))
    assert FAKE_KEY not in audit.detail
    assert FAKE_KEY not in caplog.text
    assert "SECRETKEY" not in caplog.text
    assert FAKE_KEY not in repr(_web_client(transport))


# --- strict schema on the main falsify call ---


def test_falsify_schema_mirrors_parsed_shape():
    assert FALSIFY_SCHEMA["name"] == "falsify_attacks"
    schema = FALSIFY_SCHEMA["schema"]
    assert schema["required"] == ["attacks"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["attacks"]["items"]
    assert set(item["required"]) == {
        "target_comparison_id", "attack_type", "argument",
        "severity", "confidence"}
    assert item["additionalProperties"] is False
    assert set(item["properties"]["attack_type"]["enum"]) == {
        "wrong_unit", "wrong_period", "wrong_geo",
        "non_verifying_overclaim", "missing_uncertainty", "citation_error"}
    assert set(item["properties"]["severity"]["enum"]) == {
        "fatal", "serious", "nit"}
    assert set(item["properties"]["confidence"]["enum"]) == {
        "low", "med", "high"}


def test_schema_passed_only_when_client_supports_it(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    seen: dict = {}

    class SchemaAware(ScriptedClient):
        def complete_json(self, system, user, max_tokens=1500,
                          reasoning_effort=None, json_schema=None):
            seen["json_schema"] = json_schema
            return {"attacks": []}

    falsify_draft(_claim(), [_cmp()], [], "supported", SchemaAware({}))
    assert seen["json_schema"] == FALSIFY_SCHEMA

    # Plain doubles without the parameter keep working (no TypeError).
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported",
        ScriptedClient({}, default={"attacks": []}))
    assert (new_c, new_u) == ([], [])
    assert "attacks=0" in audit.detail


# --- raw search results: considered-not-cited honesty (F2-A) ---


def _search_result(url, title="Result page"):
    entry: dict = {"title": title}
    if url is not None:
        entry["url"] = url
    return entry


def _with_search_call(payload, results):
    payload["output"].append(
        {"type": "web_search_call", "id": "ws_test_1",
         "results": results})
    return payload


def test_request_includes_raw_search_results():
    transport = MockTransport(payloads=[_responses_payload("ok", [])])
    _web_client(transport).search_counter_evidence(
        "Claim: x", instructions="i")
    # Rollback is this one line: drop `include`, candidates go empty,
    # and every pre-existing assertion still holds.
    assert transport.calls[0]["payload"]["include"] == [
        "web_search_call.results"]


def test_parse_search_candidates_marks_cited():
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    payload = _with_search_call(_responses_payload(text, [
        _annotation("https://stats.example.gov/report", start, end),
    ]), [
        _search_result("https://stats.example.gov/report", "Stats office"),
        _search_result("https://other.example.gov/data", "Other data"),
    ])
    candidates = parse_search_candidates(payload)
    assert all(isinstance(c, WebCandidate) for c in candidates)
    assert [(c.url, c.title, c.cited) for c in candidates] == [
        ("https://stats.example.gov/report", "Stats office", True),
        ("https://other.example.gov/data", "Other data", False),
    ]


def test_parse_search_candidates_hygiene_without_span():
    payload = _with_search_call(_responses_payload("ok", []), [
        _search_result("https://good.example.gov/x", "Good"),  # kept
        _search_result("https://good.example.gov/x", "Good"),  # dup dropped
        _search_result("http://plain.example.gov/x", "Plain"),  # non-https
        _search_result("https://www.google.com/search?q=x",
                       "Search"),  # garbage host
        _search_result("https://sub.bit.ly/x", "Short"),  # shortener
        _search_result("https://127.0.0.1/x", "IP"),  # IP literal
        _search_result("https://localhost/x", "Local"),  # single-label
        _search_result(None, "No URL"),  # missing URL
        {"title": "No URL key at all"},
        "not-a-dict",  # malformed entry
    ])
    candidates = parse_search_candidates(payload)
    # No span requirement: URL hygiene alone decides.
    assert [(c.url, c.cited) for c in candidates] == [
        ("https://good.example.gov/x", False)]
    assert candidates[0].title == "Good"


def test_parse_search_candidates_malformed_tolerant():
    assert parse_search_candidates(None) == []
    assert parse_search_candidates({}) == []
    assert parse_search_candidates({"output": "nope"}) == []
    # A web_search_call without usable results -> [] (never raises).
    for item in ({"type": "web_search_call"},
                 {"type": "web_search_call", "results": None},
                 {"type": "web_search_call", "results": "nope"},
                 {"type": "web_search_call", "results": [None, 42, "x"]},
                 {"type": "other_call", "results": [
                     {"url": "https://a.example.gov/x"}]},
                 "not-a-dict"):
        assert parse_search_candidates({"output": [item]}) == [], item


def test_uncited_candidates_surface_as_minor_doubt_only(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    payload = _with_search_call(_responses_payload(text, [
        _annotation("https://stats.example.gov/report", start, end),
    ]), [
        _search_result("https://stats.example.gov/report", "Stats office"),
        _search_result("https://a.example.gov/1", "Alpha"),
        _search_result("https://b.example.gov/2", "Beta"),
        _search_result("http://c.example.gov/3", "Rejected"),  # hygiene
    ])
    transport = MockTransport(payloads=[payload])
    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(
        claim, cmps, [], "supported", llm,
        web_client=_web_client(transport))
    assert len(new_c) == 1  # only the citation is evidence-adjacent
    assert new_c[0].metric == "web_counter_evidence"
    assert len(new_u) == 3  # 1 citation doubt + 2 considered-not-cited
    considered = [u for u in new_u if u.code == "web_considered_not_cited"]
    assert len(considered) == 2
    assert {u.severity for u in considered} == {"minor"}  # never blocking
    assert "Alpha" in considered[0].description
    assert "https://a.example.gov/1" in considered[0].description
    assert "not evidence" in considered[0].description
    assert "cited=1/considered=3" in audit.detail
    assert "web_citations=1" in audit.detail
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"


def test_uncited_candidates_capped_at_five(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    results = [_search_result(f"https://src{i}.example.gov/x", f"Src {i}")
               for i in range(8)]
    transport = MockTransport(payloads=[
        _with_search_call(_responses_payload("ok", []), results)])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert new_c == []  # NEVER evidence: no comparisons at all
    assert len(new_u) == 5  # attached list bounded ...
    assert {u.code for u in new_u} == {"web_considered_not_cited"}
    assert "cited=0/considered=8" in audit.detail  # ... audit counts all


@pytest.mark.parametrize("verdict,draft", [
    ("consistent", "supported"),
    ("contradicts", "contradicted"),
    ("inconclusive", "unverifiable"),
    ("not_comparable", "unverifiable"),
])
def test_considered_not_cited_leaves_every_stance_untouched(
        monkeypatch, verdict, draft):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    payload = _with_search_call(_responses_payload("ok", []), [
        _search_result("https://a.example.gov/1", "Alpha"),
    ])
    transport = MockTransport(payloads=[payload])
    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp(verdict=verdict)]
    assert decide_stance(cmps, [], claim) == draft
    new_c, new_u, _ = falsify_draft(
        claim, cmps, [], draft, llm,
        web_client=_web_client(transport))
    assert new_c == []  # doubt surfaced as uncertainties only ...
    assert len(new_u) == 1
    assert decide_stance(cmps + new_c, new_u, claim) == draft  # ... stance kept
    assert synthesize(claim, cmps + new_c, new_u, llm=None).stance == draft


def test_no_search_results_is_clean_zero_considered(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    text = "The statistics office reports growth was 1.2%."
    start, end = _good_span(text)
    transport = MockTransport(payloads=[_responses_payload(
        text, [_annotation("https://stats.example.gov/report", start, end)])])
    llm = ScriptedClient({}, default={"attacks": []})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm,
        web_client=_web_client(transport))
    assert len(new_c) == 1 and len(new_u) == 1  # citation doubt only
    assert "cited=1/considered=0" in audit.detail


# --- authoritative-first ordering under cap pressure (F11) ---


def test_authoritative_first_under_cap_pressure(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    span = "growth was 1.2 percent"
    junk_citations = [WebCitation(
        url=f"https://n{i}.whatisgdp.com/x", title="junk",
        span=span, domain=f"n{i}.whatisgdp.com") for i in range(6)]
    auth_citation = WebCitation(
        url="https://data.worldbank.org/indicator/x", title="World Bank",
        span=span, domain="data.worldbank.org")
    junk_candidates = [WebCandidate(
        url=f"https://fibre2fashion.com/textile-news-{i}", title="textile",
        cited=False) for i in range(6)]
    auth_candidate = WebCandidate(
        url="https://www.bea.gov/news/x", title="BEA", cited=False)

    class CapWeb:
        name = "cap-web"
        available = True
        last_usage = None

        def search_counter_evidence(self, user_text, **kw):
            return CounterEvidence(
                text="t",
                citations=junk_citations + [auth_citation],
                candidates=junk_candidates + [auth_candidate],
            )

    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(
        claim, cmps, [], "supported", llm, web_client=CapWeb())
    # Citations: authoritative wins the cap instead of being crowded out.
    assert len(new_c) == 5
    assert new_c[0].claim_value == "https://data.worldbank.org/indicator/x"
    assert {c.metric for c in new_c} == {"web_counter_evidence"}
    # Considered-not-cited: same ordering, bounded the same way.
    considered = [u for u in new_u if u.code == "web_considered_not_cited"]
    assert len(considered) == 5
    assert "bea.gov" in considered[0].description
    # Advisory-only preserved: inconclusive + minor, stance untouched.
    assert all(c.verdict == "inconclusive" for c in new_c)
    assert all(u.severity == "minor" for u in new_u)
    assert "cited=5/considered=7" in audit.detail
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"


def test_junk_only_still_surfaces(monkeypatch):
    monkeypatch.delenv("ATLAS_WEB_FALSIFY", raising=False)
    # Ordering is selection, not blocking: with no authoritative source
    # anywhere, junk is honestly listed (never silently dropped).
    span = "growth was 1.2 percent"

    class JunkWeb:
        name = "junk-web"
        available = True
        last_usage = None

        def search_counter_evidence(self, user_text, **kw):
            return CounterEvidence(
                text="t",
                citations=[WebCitation(
                    url="https://n1.whatisgdp.com/x", title="junk",
                    span=span, domain="n1.whatisgdp.com")],
                candidates=[WebCandidate(
                    url="https://fibre2fashion.com/textile-news-0",
                    title="textile", cited=False)],
            )

    llm = ScriptedClient({}, default={"attacks": []})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(
        claim, cmps, [], "supported", llm, web_client=JunkWeb())
    assert len(new_c) == 1
    assert "whatisgdp.com" in new_c[0].notes
    considered = [u for u in new_u if u.code == "web_considered_not_cited"]
    assert len(considered) == 1
    assert "fibre2fashion.com" in considered[0].description
    assert "cited=1/considered=1" in audit.detail
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"
