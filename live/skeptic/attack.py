"""M5 skeptic attack: try to break ``supported`` verdicts with new evidence.

Pipeline for one claim whose verdict is ``supported``::

    counter-queries (1 model call, reasoning minimal)
      -> grounding web_search + responsible fetch (fetch.py contract)
      -> contradicting-quote extraction (1 model call per fetched doc)
      -> candidate package (verbatim validation by CODE)
      -> GATE A: EvidencePackage.from_dict
      -> GATE B: existing verifier literal check (verify_claim usability)
      -> usable + contradict => ``skeptic.challenge``,
         anything else      => advisory ``skeptic.note``.

The model proposes; code disposes. A ``skeptic.challenge`` is published
ONLY when the candidate dict passes M2a structural validation AND the
existing M2a verifier confirms it is literally grounded
(quote verbatim + hash match). An invented/non-verbatim quote, an
uncorroborated fetch failure, or corroborating evidence all degrade to
``skeptic.note`` — never a challenge, never an exception.

Non-``supported`` verdicts are never attacked (nothing to break): they
get a note without spending a single model call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from live.evidence.package import EvidencePackage
from live.swarm.coordinator import (
    build_package,
    doc_excerpts,
    estimate_cost_usd,
    web_search,
)
from live.swarm.fetch import FetchResult, fetch_url
from live.verifier.check import ClaimInput, Verdict, verify_claim

log = logging.getLogger(__name__)

ATTACK_MAX_TOKENS = 800
ATTACK_REASONING_EFFORT = "minimal"
MAX_COUNTER_QUERIES = 2
FETCH_TOP_K = 1
SEARCH_TOP_K = 3

SKEPTIC_SYSTEM = """You attack ONE fact-checked claim whose verdict is SUPPORTED. Propose web-search counter-queries most likely to surface CONTRADICTING or qualifying evidence (revised figures, different year/vintage, different definition, per-capita vs total, nominal vs real, a reputable dispute).
Return ONLY a JSON object: {"counter_queries": [{"query": "...", "rationale": "..."}], "skip": true|false, "skip_reason": "..."}
Your ENTIRE reply must be exactly that JSON object: no greeting, no preamble, no text before or after the braces.
Rules: at most 2 counter-queries, each ONE short English search query; each "rationale" is one sentence naming the concrete risk. "skip" is true ONLY when the verdict rests on an exact indicator + year + primary source match no plausible query could shake; then "counter_queries" is [] and "skip_reason" says why. Never invent counter-facts — you propose QUERIES, not answers."""

SKEPTIC_SCHEMA = {
    "name": "m5_skeptic",
    "schema": {
        "type": "object",
        "properties": {
            "counter_queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["query", "rationale"],
                    "additionalProperties": False,
                },
            },
            "skip": {"type": "boolean"},
            "skip_reason": {"type": "string"},
        },
        "required": ["counter_queries", "skip", "skip_reason"],
        "additionalProperties": False,
    },
}

SKEPTIC_EXTRACT_SYSTEM = """You try to CONTRADICT one supported factual claim from fetched web documents.
Return ONLY a JSON object: {"quote": "...", "targets_claim": true|false, "rationale": "..."}
Your ENTIRE reply must be exactly that JSON object: no greeting, no preamble, no text before or after the braces.
Rules: "quote" MUST be copied CHARACTER-FOR-CHARACTER from ONE fetched document (no rephrasing, no rounding, no new words, no invented numbers) and MUST assert a figure that disagrees with the claim (outside a 2% tolerance) or a direct textual contradiction. "targets_claim" is true only when the quote really contradicts the claim. When no document contradicts the claim, return {"quote": "", "targets_claim": false, "rationale": "..."} — an empty quote is correct, an invented or merely-related quote is a failure."""

SKEPTIC_EXTRACT_SCHEMA = {
    "name": "m5_skeptic_extract",
    "schema": {
        "type": "object",
        "properties": {
            "quote": {"type": "string"},
            "targets_claim": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["quote", "targets_claim", "rationale"],
        "additionalProperties": False,
    },
}


@dataclass
class AttackConfig:
    """Knobs + seams (doubles inject llm/search_fn/fetch_fn)."""

    llm: Any = None  # complete_json(system, user, max_tokens, ...) client
    search_fn: Callable[[str, int], list[dict]] | None = None
    fetch_fn: Callable[..., FetchResult] | None = None
    snapshot_dir: str | Path | None = None
    max_counter_queries: int = MAX_COUNTER_QUERIES
    fetch_top_k: int = FETCH_TOP_K
    model_max_tokens: int = ATTACK_MAX_TOKENS
    reasoning_effort: str | None = ATTACK_REASONING_EFFORT


@dataclass
class AttackResult:
    claim_id: str
    challenged: bool
    package: dict | None = None  # candidate package dict (challenge only)
    counter_query: str = ""
    rationale: str = ""
    note: str = ""
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    docs_fetched: int = 0
    extracts: int = 0


class _Usage:
    """Token accounting from llm.last_usage (+seq), M2b-style."""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        self._seq = 0

    def record(self, llm: Any) -> None:
        try:
            seq = int(getattr(llm, "last_usage_seq", 0) or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq <= self._seq:
            return
        self._seq = seq
        usage = getattr(llm, "last_usage", None) or {}
        try:
            self.prompt += int(usage.get("prompt_tokens") or 0)
            self.completion += int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pass
        self.calls += 1

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "total_tokens": self.prompt + self.completion,
            "model_calls": self.calls,
        }

    def cost_usd(self) -> float:
        return estimate_cost_usd(self.prompt, self.completion)


def _is_malformed_reply(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        return True
    return isinstance(exc, RuntimeError) and "empty content" in str(exc)


def _claim_input(claim: ClaimInput | dict) -> ClaimInput:
    if isinstance(claim, ClaimInput):
        return claim
    if not isinstance(claim, dict):
        raise ValueError("claim must be ClaimInput or dict")
    return ClaimInput(
        claim_id=str(claim.get("claim_id", "")),
        text=str(claim.get("text", "") or ""),
        value=float(claim.get("value", 0.0)),
        unit=claim.get("unit"),
        raw=str(claim.get("raw", "") or ""),
        claim_year=claim.get("claim_year"),
        tolerance=float(claim.get("tolerance", 0.02)),
    )


def _verdict_label(verdict: Verdict | dict) -> str:
    if isinstance(verdict, Verdict):
        return verdict.verdict
    if isinstance(verdict, dict):
        return str(verdict.get("verdict", ""))
    return ""


async def _complete(config: AttackConfig, usage: _Usage,
                    system: str, user: str, schema: dict) -> dict:
    """One structured call with a single malformed-reply retry (M2b rule)."""
    llm = config.llm

    def _call(sys: str) -> dict:
        return llm.complete_json(
            sys, user, max_tokens=config.model_max_tokens,
            reasoning_effort=config.reasoning_effort,
            json_schema=schema,
        )

    try:
        out = await asyncio.to_thread(_call, system)
    except Exception as exc:
        usage.record(llm)
        if not _is_malformed_reply(exc):
            raise
        log.warning("malformed skeptic reply, retrying once: %r", exc)
        out = await asyncio.to_thread(
            _call, system + "\n\nSTRICT: your reply was not pure JSON. "
            "Reply again with ONLY the JSON object.")
        usage.record(llm)
    else:
        usage.record(llm)
    if not isinstance(out, dict):
        raise RuntimeError("model returned non-dict JSON")
    return out


def _search(config: AttackConfig, query: str) -> list[dict]:
    fn = config.search_fn or web_search
    try:
        hits = fn(query, SEARCH_TOP_K)
    except Exception as exc:
        log.warning("skeptic search failed for %r: %r", query, exc)
        return []
    return [h for h in (hits or []) if isinstance(h, dict)
            and str(h.get("url", "")).startswith(("http://", "https://"))]


def _fetch(config: AttackConfig, url: str) -> FetchResult:
    fn = config.fetch_fn or fetch_url
    return fn(url, snapshot_dir=config.snapshot_dir)


async def run_attack(bus: Any,
                     claim: ClaimInput | dict,
                     verdict: Verdict | dict,
                     packages: list[dict | EvidencePackage] | None,
                     loader: Any,
                     config: AttackConfig | None = None) -> AttackResult:
    """Attack one ``supported`` verdict; publish challenge-or-note.

    Publishes exactly one event: ``skeptic.challenge`` (gates passed and
    the candidate contradicts) or ``skeptic.note`` (anything else).
    Never raises on model/network failures.
    """
    config = config or AttackConfig()
    usage = _Usage()
    try:
        claim_in = _claim_input(claim)
    except Exception as exc:
        return await _finish_note(
            bus, "", usage, f"bad claim input: {type(exc).__name__}")
    label = _verdict_label(verdict)
    if label != "supported":
        return await _finish_note(
            bus, claim_in.claim_id, usage,
            f"only supported verdicts are attacked (got {label or 'none'})")

    llm = config.llm
    if llm is None or not bool(getattr(llm, "available", True)):
        return await _finish_note(
            bus, claim_in.claim_id, usage, "no model available")

    existing = [p.to_dict() if isinstance(p, EvidencePackage) else p
                for p in (packages or []) if isinstance(p, (dict, EvidencePackage))]
    known_urls = {str(p.get("url", "")) for p in existing
                  if isinstance(p, dict) and p.get("url")}

    # 1. counter-queries (one structured call).
    try:
        plan = await _complete(
            config, usage, SKEPTIC_SYSTEM,
            f"claim_id: {claim_in.claim_id}\nclaim: {claim_in.text}\n"
            f"supporting quotes: "
            f"{json.dumps([p.get('quote', '') for p in existing if isinstance(p, dict)], ensure_ascii=False)[:1500]}",
            SKEPTIC_SCHEMA)
    except Exception as exc:
        return await _finish_note(
            bus, claim_in.claim_id, usage,
            f"counter-query failed: {type(exc).__name__}")
    if not isinstance(plan, dict) or plan.get("skip"):
        reason = plan.get("skip_reason", "") if isinstance(plan, dict) else ""
        return await _finish_note(
            bus, claim_in.claim_id, usage,
            f"skeptic skipped: {reason or 'no plausible counter-query'}")
    queries = [q for q in (plan.get("counter_queries") or [])
               if isinstance(q, dict) and str(q.get("query", "")).strip()]
    queries = queries[:max(1, config.max_counter_queries)]
    if not queries:
        return await _finish_note(
            bus, claim_in.claim_id, usage, "empty counter-query list")

    # 2-4. per counter-query: search -> fetch -> extract -> gates.
    corroborated = 0
    attempted: list[str] = []
    docs_fetched = 0
    extracts = 0
    failures: list[str] = []
    for item in queries:
        query = str(item["query"]).strip()[:300]
        rationale = str(item.get("rationale", "")).strip()[:300]
        attempted.append(query)
        hits = await asyncio.to_thread(_search, config, query)
        fresh = [h for h in hits
                 if str(h.get("url", "")) not in known_urls]
        for hit in fresh[:max(1, config.fetch_top_k)]:
            url = str(hit["url"])
            try:
                fetched = await asyncio.to_thread(_fetch, config, url)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}")
                continue
            docs_fetched += 1
            known_urls.add(fetched.final_url or url)
            excerpt = doc_excerpts(fetched.text or "")
            if not excerpt.strip():
                failures.append("empty-excerpt")
                continue
            try:
                found = await _complete(
                    config, usage, SKEPTIC_EXTRACT_SYSTEM,
                    f"claim: {claim_in.text}\n"
                    f"counter-query was: {query}\n\n"
                    f"fetched document ({fetched.final_url or url}):\n"
                    f"{excerpt[:6000]}",
                    SKEPTIC_EXTRACT_SCHEMA)
                extracts += 1
            except Exception as exc:
                failures.append(f"extract:{type(exc).__name__}")
                continue
            quote = found.get("quote", "") if isinstance(found, dict) else ""
            if not isinstance(quote, str) or not quote.strip():
                continue  # model abstained honestly: try next doc
            pkg = _candidate_package(
                claim_in.claim_id, fetched, quote,
                claim_in.claim_year, len(existing))
            if pkg is None:
                failures.append("non-verbatim-quote")
                continue
            # GATE A: M2a structural validation of the exact published dict.
            try:
                candidate = EvidencePackage.from_dict(pkg)
            except Exception as exc:
                failures.append(f"from_dict:{type(exc).__name__}")
                continue
            # GATE B: the EXISTING verifier literal check (hash + verbatim
            # quote). Only a usable package may challenge; usability is
            # read from the real verify_claim details (outcome support or
            # contradict — never invented here).
            try:
                check = verify_claim(claim_in, [candidate], loader)
            except Exception as exc:
                failures.append(f"verify:{type(exc).__name__}")
                continue
            detail = (check.details or {}).get(candidate.package_id, {})
            outcome = detail.get("outcome")
            if outcome not in ("support", "contradict"):
                failures.append(str(detail.get("reason", "unusable")))
                continue
            if outcome == "contradict":
                result = AttackResult(
                    claim_id=claim_in.claim_id, challenged=True,
                    package=pkg, counter_query=query, rationale=rationale,
                    note="counter-evidence verified", cost_usd=usage.cost_usd(),
                    usage=usage.as_dict(), queries=attempted,
                    docs_fetched=docs_fetched, extracts=extracts)
                if bus is not None:
                    await bus.publish({
                        "type": "skeptic.challenge",
                        "claim_id": claim_in.claim_id,
                        "package": pkg,
                        "counter_query": query,
                        "rationale": rationale,
                        "verdict_detail": detail,
                        "cost_usd": result.cost_usd,
                        "usage": result.usage,
                        "queries": attempted,
                        "docs_fetched": docs_fetched,
                        "actor": "m5-skeptic",
                    })
                return result
            corroborated += 1
    accounting = (f"queries={len(attempted)}, fetched={docs_fetched}, "
                  f"extracts={extracts}")
    if corroborated:
        note = (f"attack corroborated the verdict "
                f"({corroborated} usable supporting package(s); "
                f"{accounting})")
    elif failures:
        uniq = sorted(set(failures))
        note = (f"no usable counter-evidence "
                f"({', '.join(uniq[:4])}; {accounting})")
    else:
        note = f"no usable counter-evidence (no fresh documents; {accounting})"
    return await _finish_note(
        bus, claim_in.claim_id, usage, note,
        counter_query=attempted[-1] if attempted else "",
        queries=attempted, docs_fetched=docs_fetched, extracts=extracts)


def _candidate_package(claim_id: str, fetched: FetchResult, quote: str,
                       source_year: int | None, index: int) -> dict | None:
    """Verbatim-validated candidate via the M2b builder (M5 package id)."""
    from live.swarm.coordinator import Doc
    from live.swarm.coordinator import snapshot_relpath

    doc = Doc(url=fetched.final_url or fetched.url, title="",
              fetch=fetched, excerpt="")
    pkg = build_package(claim_id, doc.url, quote, doc, source_year, index)
    if pkg is None:
        return None
    pkg["package_id"] = f"m5s-{claim_id}-{fetched.sha256[:12]}-{index}"
    pkg["snapshot_path"] = snapshot_relpath(fetched.snapshot_path,
                                            fetched.sha256)
    try:
        json.dumps(pkg)
    except (TypeError, ValueError):
        return None
    return pkg


async def _finish_note(bus: Any, claim_id: str, usage: _Usage,
                       note: str, counter_query: str = "",
                       queries: list[str] | None = None,
                       docs_fetched: int = 0,
                       extracts: int = 0) -> AttackResult:
    result = AttackResult(
        claim_id=claim_id, challenged=False, note=note,
        counter_query=counter_query, cost_usd=usage.cost_usd(),
        usage=usage.as_dict(), queries=list(queries or []),
        docs_fetched=docs_fetched, extracts=extracts)
    if bus is not None:
        try:
            await bus.publish({
                "type": "skeptic.note",
                "claim_id": claim_id,
                "challenged": False,
                "note": note,
                "counter_query": counter_query,
                "queries": result.queries,
                "docs_fetched": docs_fetched,
                "extracts": extracts,
                "cost_usd": result.cost_usd,
                "usage": result.usage,
                "actor": "m5-skeptic",
            })
        except Exception as exc:
            log.warning("skeptic.note publish failed: %r", exc)
    return result


__all__ = [
    "ATTACK_MAX_TOKENS",
    "ATTACK_REASONING_EFFORT",
    "FETCH_TOP_K",
    "MAX_COUNTER_QUERIES",
    "SKEPTIC_EXTRACT_SCHEMA",
    "SKEPTIC_EXTRACT_SYSTEM",
    "SKEPTIC_SCHEMA",
    "SKEPTIC_SYSTEM",
    "AttackConfig",
    "AttackResult",
    "run_attack",
]
