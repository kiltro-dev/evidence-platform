"""M8 ablation baseline: model-only judge with live web search.

One model call per claim (Responses API + ``web_search`` tool, reasoning
low). The judge reads the claim plus the keynote EN transcript (SRT —
the same talk both arms judge) and returns a JSON verdict. CODE ONLY
parses that JSON, applies URL hygiene to the browsed citations, and
records usage/cost.

Deliberately ABSENT vs the Atlas arm: no deterministic verifier, no
quantity parser, no tolerance/scale/source-hierarchy checks, no
snapshots, no EvidenceStore. The verdict is NEVER touched by code:
unparseable output, transport failures, and timeouts all degrade to
``unverifiable`` (abstention), never to a guessed side.

Client is duck-typed: anything with
``search_counter_evidence(user_text, instructions=..., max_output_tokens=...)``
returning ``(text, citations, truncated)`` plus ``last_usage`` — the
live client is ``src.atlas.models.responses.ResponsesClient``, tests
inject doubles. This module imports NO client, NO store, NO verifier.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:  # repo-root-on-path convention (live runners)
    from src.atlas.agent.loop import estimate_cost
except ImportError:  # src/-on-path convention (tests)
    from atlas.agent.loop import estimate_cost

VERDICTS = frozenset({"supported", "contradicted", "unverifiable"})

MAX_OUTPUT_TOKENS = 2000

_ERR_TEXT_LIMIT = 200

INSTRUCTIONS_TEMPLATE = """\
You are a fact-check judge with live web access. Judge ONE claim from a \
keynote talk. Use web search to verify the claim's factual content \
against reliable sources (official statistics, primary sources, \
reputable references). The talk transcript below gives context only — \
it is NOT evidence for or against the claim.

Verdicts:
- "supported": the claim's factual content checks out (figures match \
reliable sources within ~2%, entities/years/plain facts match).
- "contradicted": reliable sources refute a material factual element \
(figure clearly off, wrong entity, wrong year, false plain fact).
- "unverifiable": vague/opinion/prediction, no reliable source found, \
the claim hinges on chart visuals not checkable from text, or you are \
unsure. When in doubt, answer "unverifiable" — do NOT guess.

Reply with ONLY this JSON object, no prose before or after:
{"verdict": "supported|contradicted|unverifiable", \
"urls": ["https://source-you-actually-browsed/..."], \
"quote": "short span from a browsed page backing your verdict, or null"}

Transcript of the keynote (context only):
---
{transcript}
---
"""

USER_TEMPLATE = 'Claim to judge: "{claim}"'


def load_transcript(path: str | Path) -> str:
    """Raw keynote SRT text (same file for every baseline call)."""
    return Path(path).read_text(encoding="utf-8")


def build_instructions(transcript: str) -> str:
    """Judge instructions with the transcript embedded as context."""
    # .replace, NOT .format: the template carries literal JSON braces.
    return INSTRUCTIONS_TEMPLATE.replace("{transcript}",
                                         transcript.strip())


def build_user_text(claim_text: str) -> str:
    """Per-claim user message for the Responses call."""
    return USER_TEMPLATE.format(claim=str(claim_text))


def extract_json_object(text: str) -> dict | None:
    """First balanced ``{...}`` span that parses to a dict, else None.

    String-aware scan (mirrors the M2d rescue idea): braces inside
    JSON strings and escape sequences do not affect depth. Pure
    parsing — says nothing about the verdict's truth.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:pos + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def normalize_verdict(value: Any) -> str | None:
    """Lowercased verdict when it names a known label, else None."""
    if isinstance(value, str) and value.strip().lower() in VERDICTS:
        return value.strip().lower()
    return None


def coerce_urls(value: Any) -> list[str]:
    """Model-stated URL list (raw, unvalidated — audit trail only)."""
    if not isinstance(value, list):
        return []
    return [u.strip() for u in value
            if isinstance(u, str) and u.strip()][:10]


def coerce_quote(value: Any) -> str | None:
    """Short quote string or None (no truth content is checked)."""
    if isinstance(value, str) and value.strip():
        return value.strip()[:500]
    return None


def _citation_url(citation: Any) -> str | None:
    url = getattr(citation, "url", None)
    if url is None and isinstance(citation, dict):
        url = citation.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


@dataclass
class BaselineResult:
    """One judged claim. ``verdict`` is the model's word, verbatim."""

    claim_id: str
    verdict: str  # supported | contradicted | unverifiable
    urls: list[str] = field(default_factory=list)  # browsed citations
    judge_urls: list[str] = field(default_factory=list)  # from JSON
    quote: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    run_id: str = ""
    truncated: bool = False
    parse_ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _usage_cost(usage: Any) -> tuple[dict, float]:
    if not isinstance(usage, dict):
        return {}, 0.0
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    try:
        cost = estimate_cost(int(prompt), int(completion))
    except (TypeError, ValueError):
        return usage, 0.0
    return usage, cost


def judge_claim(client: Any, claim_id: str, claim_text: str,
                transcript: str, run_id: str = "",
                max_output_tokens: int = MAX_OUTPUT_TOKENS,
                ) -> BaselineResult:
    """Judge one claim. Never raises: failures abstain (unverifiable).

    ``client`` is duck-typed (see module docstring). Every failure mode
    — transport error, unparseable JSON, unknown verdict label —
    returns ``unverifiable`` with ``parse_ok``/``error`` recording why.
    """
    t0 = time.monotonic()
    try:
        answered = client.search_counter_evidence(
            build_user_text(claim_text),
            instructions=build_instructions(transcript),
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — abstain, record, continue
        return BaselineResult(
            claim_id=claim_id, verdict="unverifiable",
            latency_s=time.monotonic() - t0, run_id=run_id,
            parse_ok=False,
            error=f"{type(exc).__name__}: {str(exc)[:_ERR_TEXT_LIMIT]}",
        )
    latency = time.monotonic() - t0
    usage, cost = _usage_cost(getattr(client, "last_usage", None))
    text = getattr(answered, "text", "") or ""
    citations = getattr(answered, "citations", None) or []
    truncated = bool(getattr(answered, "truncated", False))
    urls = [u for u in (_citation_url(c) for c in citations) if u]
    parsed = extract_json_object(text)
    if parsed is None:
        return BaselineResult(
            claim_id=claim_id, verdict="unverifiable", urls=urls,
            usage=usage, cost_usd=cost, latency_s=latency,
            run_id=run_id, truncated=truncated, parse_ok=False,
            error="unparseable judge output (no JSON object)",
        )
    verdict = normalize_verdict(parsed.get("verdict"))
    if verdict is None:
        return BaselineResult(
            claim_id=claim_id, verdict="unverifiable", urls=urls,
            judge_urls=coerce_urls(parsed.get("urls")),
            usage=usage, cost_usd=cost, latency_s=latency,
            run_id=run_id, truncated=truncated, parse_ok=False,
            error=f"unknown verdict label: {parsed.get('verdict')!r}",
        )
    return BaselineResult(
        claim_id=claim_id, verdict=verdict, urls=urls,
        judge_urls=coerce_urls(parsed.get("urls")),
        quote=coerce_quote(parsed.get("quote")),
        usage=usage, cost_usd=cost, latency_s=latency,
        run_id=run_id, truncated=truncated, parse_ok=True,
    )
