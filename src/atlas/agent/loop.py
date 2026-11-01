"""Self-correcting analyst agent (Pilar A-backend).

The agent drafts a short economics/science analysis with >=4 concrete
figures, verifies each numeric claim through the `verify_claim` tool
(which runs `atlas.pipeline.run.analyze` in-process), then rewrites the
draft correcting contradicted / unverifiable figures.

Chat layer is duck-typed: `run_agent` needs any object with
`chat(messages, tools=None, tool_choice="auto", max_tokens=...)` returning
a `ChatTurn`. `MetaToolClient` is the live implementation (OpenAI-style
chat completions + tools against the Meta Model API); tests inject
scripted fakes. The analyzer is likewise injectable; the default calls
`analyze(text, llm=None, persist=False)` so tool runs are deterministic
and free of extra model cost.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MAX_TOKENS = 2000  # hard floor for this agent: >= 1500

PRICE_PROMPT_PER_1M = 1.25
PRICE_COMPLETION_PER_1M = 4.25

MIN_FIGURES = 4

DRAFT_SYSTEM = (
    "You are a concise economics/science analyst. Write a short paragraph "
    "(80-150 words) on the requested topic with at least 4 concrete numeric "
    "figures (amounts, percents, counts with units). Plain text, no lists, "
    "no citations."
)
DRAFT_RETRY = (
    "Your draft had fewer than 4 concrete figures. Rewrite it now with at "
    "least 4 concrete numeric figures (amounts, percents, counts with units)."
)
VERIFY_SYSTEM = (
    "You are a verification assistant. The user gives you a draft analysis. "
    "Call the verify_claim tool ONCE per numeric claim in the draft, passing "
    "the full claim sentence as `text`. Do not answer in text until every "
    "verification result is back."
)
FORCE_NUDGE = (
    "You MUST call verify_claim now, once per numeric claim in the draft "
    "above. Do not answer in text until every tool call completes."
)
REWRITE_PROMPT = (
    "Verification results (JSON):\n{results}\n\n"
    "Rewrite the draft: keep supported figures as-is, correct or drop "
    "contradicted figures, qualify or drop unverifiable ones. Output ONLY "
    "the corrected analysis text (80-150 words), no preamble, no tool calls."
)

VERIFY_CLAIM_TOOL = {
    "type": "function",
    "function": {
        "name": "verify_claim",
        "description": (
            "Verify one factual claim sentence against retrieved evidence. "
            "Returns stance (supported/partially_supported/contradicted/"
            "unverifiable/not_assessable), a short summary, and a report_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full claim sentence to verify.",
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}

FIGURE_RE = re.compile(
    r"""
      \$\s?\d[\d,]*(?:\.\d+)?\s*(?:trillion|billion|million|thousand)?  # $ amounts
    | \d[\d,]*\.\d+\s*(?:%|percent|million|billion|trillion)?           # decimals
    | \d[\d,]*\s*(?:%|percent|million|billion|trillion)(?![A-Za-z])  # ints w/ units
    """,
    re.X | re.IGNORECASE,
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
BAD_STANCES = frozenset({"contradicted", "unverifiable"})
EXC_TEXT_LIMIT = 200


def count_figures(text: str) -> int:
    """Concrete statistical figures in text. Bare years (2023) don't count."""
    return len(FIGURE_RE.findall(text or ""))


def extract_claims(text: str) -> list[str]:
    """Sentences containing >=1 figure — the verifiable units of a draft."""
    return [s for s in _SENT_SPLIT_RE.split(text or "") if count_figures(s) >= 1]


def needs_correction(stance: str) -> bool:
    return stance in BAD_STANCES


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * PRICE_PROMPT_PER_1M + completion_tokens * PRICE_COMPLETION_PER_1M) / 1e6


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""


@dataclass
class ChatTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def turn_from_response(resp) -> ChatTurn:
    """Parse an OpenAI-style chat completion into a ChatTurn.

    Accepts both SDK objects (attribute access) and plain dicts (recorded
    JSON transcripts), so live payloads and fixtures share one parser.
    """
    choices = _get(resp, "choices", []) or []
    first = choices[0] if choices else {}
    finish_reason = _get(first, "finish_reason", "stop") or "stop"
    message = _get(first, "message", {}) or {}
    content = _get(message, "content", "") or ""
    raw_calls = _get(message, "tool_calls", []) or []
    tool_calls = []
    for rc in raw_calls:
        fn = _get(rc, "function", {}) or {}
        tool_calls.append(
            ToolCall(
                id=str(_get(rc, "id", "")),
                name=str(_get(fn, "name", "")),
                arguments=str(_get(fn, "arguments", "") or ""),
            )
        )
    raw_usage = _get(resp, "usage", {}) or {}
    usage = {
        "prompt_tokens": _get(raw_usage, "prompt_tokens", 0) or 0,
        "completion_tokens": _get(raw_usage, "completion_tokens", 0) or 0,
        "total_tokens": _get(raw_usage, "total_tokens", 0) or 0,
    }
    return ChatTurn(content=content, tool_calls=tool_calls, finish_reason=finish_reason, usage=usage)


class MetaToolClient:
    """Live chat-completions + tools client (muse-spark-1.3).

    Mirrors meta.MetaClient conventions (env config, lazy transport, 120s
    timeout) but speaks tools instead of response_format JSON.
    """

    name = "meta-tool-api"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        self.api_key = api_key or os.environ.get("MODEL_API_KEY", "")
        self.base_url = base_url or os.environ.get("MODEL_API_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.environ.get("MODEL_API_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=3,
            )
        return self._client

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0,
    ) -> ChatTurn:
        if not self.available:
            raise RuntimeError("MODEL_API_KEY not configured; MetaToolClient unavailable.")
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        resp = self._get_client().chat.completions.create(**kwargs)
        turn = turn_from_response(resp)
        if not turn.content and not turn.tool_calls:
            raise RuntimeError(
                f"Model returned empty content (finish_reason={turn.finish_reason}); "
                "retry with more max_tokens."
            )
        return turn


def _default_analyzer(text: str):
    """In-process verification: full pipeline, heuristic extract, no writes."""
    from atlas.pipeline.run import analyze

    return analyze(text, llm=None, persist=False)


def verify_claim(text: str, analyzer=None) -> dict:
    """Run one claim through the pipeline; return a JSON-ready receipt."""
    run = analyzer if analyzer is not None else _default_analyzer
    report = run(text)
    conclusion = getattr(report, "conclusion", None)
    return {
        "claim_text": text,
        "stance": getattr(conclusion, "stance", "not_assessable"),
        "summary": getattr(conclusion, "summary", ""),
        "report_id": getattr(report, "report_id", ""),
        "n_comparisons": len(getattr(report, "comparisons", []) or []),
        "n_evidences": len(getattr(report, "evidences", []) or []),
    }


def _echo_assistant(turn: ChatTurn) -> dict:
    return {
        "role": "assistant",
        "content": turn.content or None,
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in turn.tool_calls
        ],
    }


def _execute_tool_call(tc: ToolCall, analyzer) -> tuple[dict, dict]:
    """Run one verify_claim call. Returns (tool_result_message, receipt)."""
    try:
        args = json.loads(tc.arguments or "{}")
        text = args.get("text", "")
    except (json.JSONDecodeError, AttributeError) as exc:
        receipt = {
            "claim_text": "",
            "stance": "not_assessable",
            "summary": "",
            "report_id": "",
            "error": f"malformed tool arguments: {str(exc)[:EXC_TEXT_LIMIT]}",
        }
        return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(receipt)}, receipt
    if not isinstance(text, str) or not text.strip():
        receipt = {
            "claim_text": "",
            "stance": "not_assessable",
            "summary": "",
            "report_id": "",
            "error": "missing required argument: text",
        }
        return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(receipt)}, receipt
    try:
        receipt = verify_claim(text, analyzer=analyzer)
    except Exception as exc:  # noqa: BLE001 — a dead source degrades, never kills
        log.warning("verify_claim failed for %r: %s", text[:80], exc)
        receipt = {
            "claim_text": text,
            "stance": "not_assessable",
            "summary": "",
            "report_id": "",
            "error": str(exc)[:EXC_TEXT_LIMIT],
        }
    return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(receipt)}, receipt


def run_agent(
    topic: str,
    client=None,
    analyzer=None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_tool_rounds: int = 4,
    force_verify: bool = True,
) -> dict:
    """Draft -> verify (tools) -> rewrite. Returns the UI-ready contract."""
    chat = client if client is not None else MetaToolClient()
    if max_tokens < 1500:
        raise ValueError(f"max_tokens must be >= 1500, got {max_tokens}")

    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0}
    finish_reasons: list[str] = []

    def _track(turn: ChatTurn) -> None:
        usage_total["prompt_tokens"] += int(turn.usage.get("prompt_tokens", 0) or 0)
        usage_total["completion_tokens"] += int(turn.usage.get("completion_tokens", 0) or 0)
        usage_total["total_tokens"] += int(turn.usage.get("total_tokens", 0) or 0)
        usage_total["n_calls"] += 1
        finish_reasons.append(turn.finish_reason)

    # Phase A: draft with >= MIN_FIGURES figures (one retry).
    draft_messages = [
        {"role": "system", "content": DRAFT_SYSTEM},
        {"role": "user", "content": f"Topic: {topic}\nWrite the analysis now. Include at least 4 concrete figures."},
    ]
    turn = chat.chat(draft_messages, tools=None, max_tokens=max_tokens)
    _track(turn)
    draft = turn.content or ""
    if count_figures(draft) < MIN_FIGURES:
        draft_messages.append({"role": "assistant", "content": draft})
        draft_messages.append({"role": "user", "content": DRAFT_RETRY})
        turn = chat.chat(draft_messages, tools=None, max_tokens=max_tokens)
        _track(turn)
        draft = turn.content or draft

    # Phase B: model-driven verification loop.
    verify_messages: list[dict] = [
        {"role": "system", "content": VERIFY_SYSTEM},
        {
            "role": "user",
            "content": f"Draft to verify:\n{draft}\n\nCall verify_claim once per numeric claim.",
        },
    ]
    tool_calls_log: list[dict] = []
    receipts: list[dict] = []

    def _drain(turn_: ChatTurn, budget: int) -> ChatTurn:
        """Execute tool rounds until the model stops calling (bounded)."""
        while turn_.tool_calls and budget > 0:
            budget -= 1
            verify_messages.append(_echo_assistant(turn_))
            for tc in turn_.tool_calls:
                msg, receipt = _execute_tool_call(tc, analyzer)
                verify_messages.append(msg)
                tool_calls_log.append({"id": tc.id, "name": tc.name, "arguments": tc.arguments})
                receipts.append(receipt)
            turn_ = chat.chat(
                verify_messages, tools=[VERIFY_CLAIM_TOOL], tool_choice="auto", max_tokens=max_tokens
            )
            _track(turn_)
        return turn_

    turn = chat.chat(
        verify_messages, tools=[VERIFY_CLAIM_TOOL], tool_choice="auto", max_tokens=max_tokens
    )
    _track(turn)
    turn = _drain(turn, max_tool_rounds + 1)

    # Forced round: guarantees at least one confirmed tool_calls turn.
    if not tool_calls_log and force_verify:
        verify_messages.append({"role": "user", "content": FORCE_NUDGE})
        turn = chat.chat(
            verify_messages, tools=[VERIFY_CLAIM_TOOL], tool_choice="required", max_tokens=max_tokens
        )
        _track(turn)
        turn = _drain(turn, max_tool_rounds + 1)

    # Phase C: rewrite with receipts in context.
    verify_messages.append(
        {"role": "user", "content": REWRITE_PROMPT.format(results=json.dumps(receipts))}
    )
    turn = chat.chat(verify_messages, tools=None, max_tokens=max_tokens)
    _track(turn)
    final = (turn.content or "").strip() or draft

    return {
        "draft": draft,
        "tool_calls": tool_calls_log,
        "final": final,
        "receipts": receipts,
        "finish_reasons": finish_reasons,
        "usage": usage_total,
        "cost_usd": estimate_cost(usage_total["prompt_tokens"], usage_total["completion_tokens"]),
        "figures": {"draft": count_figures(draft), "final": count_figures(final)},
        "model": getattr(chat, "model", None),
    }
