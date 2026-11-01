"""Prompt-cache instrumentation (F2-B) tests. Offline: fake clients and
transports only. No live calls — every test here must pass with the
network unplugged.

Contract pinned here: (1) both model clients send
prompt_cache_key="shipcheck-v0"; (2) usage dicts carry `cached_tokens`
(default 0 when the provider omits it); (3) pure accounting — return
values and behavior are unchanged.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.models.meta import PROMPT_CACHE_KEY, MetaClient  # noqa: E402
from atlas.models.responses import ResponsesClient  # noqa: E402

EXPECTED_KEY = "shipcheck-v0"
FAKE_KEY = "SECRETKEY123-NOT-A-REAL-KEY"


def test_prompt_cache_key_constant():
    assert PROMPT_CACHE_KEY == EXPECTED_KEY


# --- MetaClient (chat completions path) ---


def _chat_usage(prompt=10, completion=20, reasoning=5, total=30,
                cached=None):
    details = SimpleNamespace(reasoning_tokens=reasoning)
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        completion_tokens_details=details,
        total_tokens=total,
    )
    if cached is not None:
        usage.prompt_tokens_details = SimpleNamespace(
            cached_tokens=cached)
    return usage


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


def _meta_with_fake(content='{"ok": true}', usage=None):
    llm = MetaClient(api_key="test-key-not-a-secret")
    fake = _FakeCompletions(
        content, _chat_usage() if usage is None else usage)
    llm._get_client = lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=fake))
    return llm, fake


def test_meta_client_sends_prompt_cache_key():
    llm, fake = _meta_with_fake()
    assert llm.complete_json("sys", "user") == {"ok": True}  # unchanged
    assert fake.kwargs["prompt_cache_key"] == EXPECTED_KEY


def test_meta_usage_captures_cached_tokens():
    llm, _ = _meta_with_fake(usage=_chat_usage(cached=42))
    llm.complete_json("sys", "user")
    assert llm.last_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 30,
        "cached_tokens": 42,
    }


def test_meta_usage_cached_tokens_defaults_zero():
    llm, _ = _meta_with_fake()  # no prompt_tokens_details at all
    llm.complete_json("sys", "user")
    assert llm.last_usage is not None
    assert llm.last_usage["cached_tokens"] == 0
    # Explicit null details are equally absent.
    usage = _chat_usage()
    usage.prompt_tokens_details = None
    llm2, _ = _meta_with_fake(usage=usage)
    llm2.complete_json("sys", "user")
    assert llm2.last_usage is not None
    assert llm2.last_usage["cached_tokens"] == 0


# --- ResponsesClient (web_search path) ---


class _RecordingTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "payload": payload, "timeout": timeout})
        return self.payload


def _responses_payload(usage):
    return {"id": "resp_cache_1", "output": [], "usage": usage}


def _web_client(payload):
    return ResponsesClient(
        api_key=FAKE_KEY, base_url="https://api.meta.ai/v1",
        model="muse-spark-1.3",
        transport=_RecordingTransport(payload))


def test_responses_payload_sends_prompt_cache_key():
    client = _web_client(_responses_payload({"input_tokens": 5}))
    client.search_counter_evidence("Claim: x", instructions="i")
    transport = client._transport
    assert transport.calls[0]["payload"]["prompt_cache_key"] == EXPECTED_KEY
    # The honesty `include` rides along untouched (F2-A rollback line).
    assert transport.calls[0]["payload"]["include"] == [
        "web_search_call.results"]


def test_responses_usage_captures_cached_tokens():
    client = _web_client(_responses_payload({
        "input_tokens": 120, "output_tokens": 60, "total_tokens": 180,
        "input_tokens_details": {"cached_tokens": 7},
    }))
    client.search_counter_evidence("Claim: x", instructions="i")
    assert client.last_usage == {
        "prompt_tokens": 120,
        "completion_tokens": 60,
        "reasoning_tokens": None,
        "total_tokens": 180,
        "cached_tokens": 7,
    }


def test_responses_usage_cached_tokens_defaults_zero():
    client = _web_client(_responses_payload({
        "input_tokens": 120, "output_tokens": 60, "total_tokens": 180,
    }))
    result = client.search_counter_evidence("Claim: x", instructions="i")
    assert result.citations == []  # behavior unchanged
    assert result.candidates == []
    assert client.last_usage is not None
    assert client.last_usage["cached_tokens"] == 0
