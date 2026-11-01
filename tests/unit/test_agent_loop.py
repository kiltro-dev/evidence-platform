"""Unit tests for the agent self-correction loop (Pilar A-backend).

OFFLINE ONLY: every test uses scripted chat doubles and a stub analyzer.
Zero live model calls, zero network. The single live run lives in
eval/agent_demo.py (never under pytest).
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.agent.loop import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    VERIFY_CLAIM_TOOL,
    ChatTurn,
    ToolCall,
    count_figures,
    estimate_cost,
    extract_claims,
    needs_correction,
    run_agent,
    turn_from_response,
    verify_claim,
)
from atlas.core.types import Conclusion, Report, StructuredClaim  # noqa: E402

DRAFT_4FIG = (
    "The US economy reached $27.36 trillion in GDP in 2023, growing 2.5%. "
    "Germany's unemployment stood at 3.1% while Japan's population was 125 million."
)

DRAFT_1FIG = "The economy did fine in 2023 with growth of 2.5%."


def _turn(content="", tool_calls=None, finish_reason="stop", usage=None):
    return ChatTurn(
        content=content,
        tool_calls=list(tool_calls or []),
        finish_reason=finish_reason,
        usage=dict(usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    )


def _tc(call_id, text):
    return ToolCall(id=call_id, name="verify_claim", arguments=json.dumps({"text": text}))


class FakeChat:
    """Scripted chat-completions double. Pops one turn per call."""

    def __init__(self, turns, model="fake-model"):
        self.turns = list(turns)
        self.requests = []
        self.model = model

    def chat(self, messages, tools=None, tool_choice="auto", max_tokens=2000):
        self.requests.append(
            {
                "n_messages": len(messages),
                "tools": tools,
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
                "last_text": json.dumps(messages[-1].get("content"))[:400],
            }
        )
        if not self.turns:
            raise AssertionError("FakeChat out of scripted turns")
        return self.turns.pop(0)


def _stub_analyzer(mapping):
    """mapping: substring -> stance. Returns (analyzer_fn, calls)."""

    calls = []

    def analyze(text, **kwargs):
        calls.append(text)
        stance = "supported"
        for key, value in mapping.items():
            if key in text:
                stance = value
                break
        return Report(
            claim=StructuredClaim(raw_text=text),
            conclusion=Conclusion(stance=stance, summary=f"stubbed {stance}"),
        )

    return analyze, calls


# --- figure helpers -------------------------------------------------------


def test_count_figures_requires_units_or_decimals():
    assert count_figures(DRAFT_4FIG) == 4
    assert count_figures("nothing here in 2023") == 0  # bare year is not a figure
    assert count_figures("grew 2.5 percent and fell 10%") == 2
    assert count_figures("500 million people, $27,810 billion GDP") == 2


def test_extract_claims_keeps_only_sentences_with_figures():
    claims = extract_claims(DRAFT_4FIG + " This sentence has no numbers.")
    assert len(claims) == 2
    assert all(count_figures(c) >= 1 for c in claims)


def test_needs_correction_only_for_bad_stances():
    assert needs_correction("contradicted") is True
    assert needs_correction("unverifiable") is True
    assert needs_correction("supported") is False
    assert needs_correction("partially_supported") is False
    assert needs_correction("not_assessable") is False


def test_estimate_cost_matches_rate_card():
    assert estimate_cost(1_000_000, 1_000_000) == 5.50
    assert estimate_cost(1000, 2000) == (1000 * 1.25 + 2000 * 4.25) / 1e6


# --- tool spec ------------------------------------------------------------


def test_tool_spec_is_openai_function_shape():
    assert VERIFY_CLAIM_TOOL["type"] == "function"
    fn = VERIFY_CLAIM_TOOL["function"]
    assert fn["name"] == "verify_claim"
    assert fn["parameters"]["type"] == "object"
    assert "text" in fn["parameters"]["properties"]
    assert "text" in fn["parameters"]["required"]


def test_default_max_tokens_floor():
    assert DEFAULT_MAX_TOKENS >= 1500


# --- happy path -----------------------------------------------------------


def test_happy_path_model_tool_calls_end_to_end():
    analyzer, calls = _stub_analyzer({"27.36": "supported", "125 million": "contradicted"})
    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(
                content="",
                tool_calls=[
                    _tc("call_1", "The US GDP was $27.36 trillion in 2023."),
                    _tc("call_2", "Japan's population was 125 million in 2023."),
                ],
                finish_reason="tool_calls",
            ),
            _turn(content="results received", finish_reason="stop"),
            _turn(content="corrected final text here", finish_reason="stop"),
        ]
    )
    result = run_agent("G7 outlook 2023", client=chat, analyzer=analyzer)

    assert result["draft"] == DRAFT_4FIG
    assert result["final"] == "corrected final text here"
    assert len(result["tool_calls"]) == 2
    assert "tool_calls" in result["finish_reasons"]
    assert [r["stance"] for r in result["receipts"]] == ["supported", "contradicted"]
    assert all(r["report_id"].startswith("rep_") for r in result["receipts"])
    assert len(calls) == 2  # analyzer ran in-process, once per tool call
    # usage accumulates across the 4 chat turns; cost follows the rate card
    assert result["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 20,
        "total_tokens": 60,
        "n_calls": 4,
    }
    assert result["cost_usd"] == estimate_cost(40, 20)
    # every chat call respects the token floor
    assert all(r["max_tokens"] >= 1500 for r in chat.requests)
    # contract is JSON-serializable for the UI
    json.dumps(result)


def test_verify_results_are_fed_back_to_rewrite_call():
    analyzer, _ = _stub_analyzer({"X": "contradicted"})
    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="", tool_calls=[_tc("c1", "claim X 5%")], finish_reason="tool_calls"),
            _turn(content="noted", finish_reason="stop"),
            _turn(content="final", finish_reason="stop"),
        ]
    )
    run_agent("topic", client=chat, analyzer=analyzer)
    rewrite_req = chat.requests[-1]
    assert "contradicted" in rewrite_req["last_text"]


def test_forced_round_when_model_skips_tools():
    analyzer, _ = _stub_analyzer({})
    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="looks fine, no need to check", finish_reason="stop"),
            _turn(
                content="",
                tool_calls=[_tc("c1", "US GDP was $27.36 trillion in 2023.")],
                finish_reason="tool_calls",
            ),
            _turn(content="verified, ready", finish_reason="stop"),
            _turn(content="final after forced verify", finish_reason="stop"),
        ]
    )
    result = run_agent("topic", client=chat, analyzer=analyzer)

    choices = [r["tool_choice"] for r in chat.requests]
    assert choices[1] == "auto"
    assert "required" in choices  # forced round guarantees tool_calls
    assert len(result["tool_calls"]) == 1
    assert "tool_calls" in result["finish_reasons"]


def test_no_forced_round_when_disabled():
    analyzer, _ = _stub_analyzer({})
    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="looks fine", finish_reason="stop"),
            _turn(content="final", finish_reason="stop"),
        ]
    )
    result = run_agent("topic", client=chat, analyzer=analyzer, force_verify=False)
    assert result["tool_calls"] == []
    assert result["receipts"] == []
    assert "tool_calls" not in result["finish_reasons"]


def test_draft_retried_until_four_figures():
    analyzer, _ = _stub_analyzer({})
    chat = FakeChat(
        [
            _turn(content=DRAFT_1FIG, finish_reason="stop"),
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="looks fine", finish_reason="stop"),
            _turn(content="final", finish_reason="stop"),
        ]
    )
    result = run_agent("topic", client=chat, analyzer=analyzer, force_verify=False)
    assert result["draft"] == DRAFT_4FIG
    assert result["figures"]["draft"] == 4
    assert len(chat.requests) == 4  # 2 draft attempts + verify + rewrite


# --- robustness -----------------------------------------------------------


def test_malformed_tool_arguments_do_not_kill_loop():
    analyzer, calls = _stub_analyzer({})
    bad = ToolCall(id="bad1", name="verify_claim", arguments="not-json{{")
    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="", tool_calls=[bad], finish_reason="tool_calls"),
            _turn(content="noted", finish_reason="stop"),
            _turn(content="final", finish_reason="stop"),
        ]
    )
    result = run_agent("topic", client=chat, analyzer=analyzer)
    assert calls == []  # analyzer never ran for the malformed call
    assert result["receipts"][0]["stance"] == "not_assessable"
    assert "error" in result["receipts"][0]
    assert result["final"] == "final"


def test_analyzer_exception_becomes_error_receipt():
    def boom(text, **kwargs):
        raise RuntimeError("source down")

    chat = FakeChat(
        [
            _turn(content=DRAFT_4FIG, finish_reason="stop"),
            _turn(content="", tool_calls=[_tc("c1", "some claim 5%")], finish_reason="tool_calls"),
            _turn(content="noted", finish_reason="stop"),
            _turn(content="final", finish_reason="stop"),
        ]
    )
    result = run_agent("topic", client=chat, analyzer=boom)
    assert result["receipts"][0]["stance"] == "not_assessable"
    assert "source down" in result["receipts"][0]["error"]


def test_verify_claim_wrapper_returns_stance_receipt():
    analyzer, _ = _stub_analyzer({"27.36": "partially_supported"})
    receipt = verify_claim("GDP was $27.36 trillion.", analyzer=analyzer)
    assert receipt["stance"] == "partially_supported"
    assert receipt["claim_text"] == "GDP was $27.36 trillion."
    assert receipt["report_id"].startswith("rep_")


def test_multi_round_tool_loop_bounded():
    analyzer, _ = _stub_analyzer({})
    chat = FakeChat(
        [_turn(content=DRAFT_4FIG, finish_reason="stop")]
        + [
            _turn(content="", tool_calls=[_tc(f"c{i}", f"claim {i} 5%")], finish_reason="tool_calls")
            for i in range(4)
        ]
        + [_turn(content="final", finish_reason="stop")]
    )
    result = run_agent("topic", client=chat, analyzer=analyzer, max_tool_rounds=2)
    assert len(result["tool_calls"]) == 3  # initial + 2 extra rounds, then stop
    assert result["final"] == "final"


# --- recorded transcript fixture ------------------------------------------

RECORDED_TOOL_TRANSCRIPT = {
    "id": "chatcmpl-recorded001",
    "model": "muse-spark-1.3",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_ab12",
                        "type": "function",
                        "function": {
                            "name": "verify_claim",
                            "arguments": '{"text": "The GDP of the United States was $27.36 trillion in 2023."}',
                        },
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 812, "completion_tokens": 64, "total_tokens": 876},
}


def _to_ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_ns(v) for v in obj]
    return obj


def test_turn_parser_handles_sdk_object_shape():
    resp = _to_ns(RECORDED_TOOL_TRANSCRIPT)
    turn = turn_from_response(resp)
    assert turn.finish_reason == "tool_calls"
    assert turn.content == ""
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "verify_claim"
    assert "27.36" in turn.tool_calls[0].arguments
    assert turn.usage["prompt_tokens"] == 812
    assert turn.usage["completion_tokens"] == 64


def test_turn_parser_handles_plain_dict_shape():
    turn = turn_from_response(RECORDED_TOOL_TRANSCRIPT)
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].id == "call_ab12"
    assert turn.usage["total_tokens"] == 876


def test_turn_parser_stop_with_content():
    resp = {"choices": [{"finish_reason": "stop", "message": {"content": "hello"}}]}
    turn = turn_from_response(resp)
    assert turn.finish_reason == "stop"
    assert turn.content == "hello"
    assert turn.tool_calls == []
