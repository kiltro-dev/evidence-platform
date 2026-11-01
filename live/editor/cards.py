"""M5 editor: one ES/EN card per verdict, citations confined by code.

The model drafts the card under a strict ``json_schema`` whose prompt
lists ONLY the verified ``package_ids`` from the verdict event. Code
then validates that every cited id is in that set: any out-of-set
citation (or any malformed reply / model failure) discards the draft
and a deterministic fallback card built by code is published instead.
A card can therefore never cite evidence the verifier did not use.

Corrections: when a repeated ``claim.verdict`` for an already-carded
claim carries a NEW package (or a changed verdict), the editor emits
``card.correction`` (with ``correction_of`` + ``new_packages``) instead
of ``card.published``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from live.evidence.package import EvidencePackage
from live.verifier.check import Verdict

log = logging.getLogger(__name__)

CARD_MAX_TOKENS = 600
CARD_REASONING_EFFORT = "minimal"

CARD_SYSTEM = """You write ONE bilingual (Spanish + English) fact-check card for a live newsroom display.
Return ONLY a JSON object: {"headline_es": "...", "headline_en": "...", "body_es": "...", "body_en": "...", "citations": [...]}
Your ENTIRE reply must be exactly that JSON object: no greeting, no preamble, no text before or after the braces.
Rules: headlines are one short line each (same news, Spanish / English); bodies are 1-2 plain sentences each, stating the verdict and the key figure with its source. "citations" lists ONLY package ids from the ALLOWED list in the prompt — never invent, guess, or rephrase an id; when the list is empty, use []. Never add facts, figures, or sources beyond the prompt."""

CARD_SCHEMA = {
    "name": "m5_card",
    "schema": {
        "type": "object",
        "properties": {
            "headline_es": {"type": "string"},
            "headline_en": {"type": "string"},
            "body_es": {"type": "string"},
            "body_en": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["headline_es", "headline_en", "body_es",
                     "body_en", "citations"],
        "additionalProperties": False,
    },
}

# Verdict labels per card language: es, en.
VERDICT_LABELS = {
    "supported": ("Respaldada", "Supported"),
    "contradicted": ("Refutada", "Contradicted"),
    "disputed": ("En disputa", "Disputed"),
    "unverifiable": ("No verificable", "Unverifiable"),
}

_FALLBACK_BODY = {
    "supported": (
        "La afirmación coincide con las fuentes verificadas citadas.",
        "The claim matches the verified sources cited.",
    ),
    "contradicted": (
        "Las fuentes verificadas citadas contradicen la afirmación.",
        "The verified sources cited contradict the claim.",
    ),
    "disputed": (
        "Las fuentes verificadas no se ponen de acuerdo sobre la afirmación.",
        "The verified sources disagree about the claim.",
    ),
    "unverifiable": (
        "No hay fuentes verificadas suficientes para comprobar la afirmación.",
        "There are not enough verified sources to check the claim.",
    ),
}


@dataclass
class CardConfig:
    llm: Any = None  # complete_json(system, user, max_tokens, ...) client
    model_max_tokens: int = CARD_MAX_TOKENS
    reasoning_effort: str | None = CARD_REASONING_EFFORT


@dataclass
class Card:
    card_id: str
    claim_id: str
    verdict: str
    headline_es: str
    headline_en: str
    body_es: str
    body_en: str
    citations: list[str] = field(default_factory=list)
    fallback: bool = False
    event_type: str = "card.published"
    correction_of: str | None = None
    new_packages: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "card_id": self.card_id,
            "claim_id": self.claim_id,
            "verdict": self.verdict,
            "headline_es": self.headline_es,
            "headline_en": self.headline_en,
            "body_es": self.body_es,
            "body_en": self.body_en,
            "citations": list(self.citations),
            "fallback": self.fallback,
            "correction_of": self.correction_of,
            "new_packages": list(self.new_packages),
            "actor": "m5-editor",
        }


class Editor:
    """Stateful card emitter (remembers cards per claim for corrections)."""

    def __init__(self, config: CardConfig | None = None) -> None:
        self._config = config or CardConfig()
        self._seen: dict[str, dict] = {}  # claim_id -> card state
        self._counts: dict[str, int] = {}

    async def emit_card(self, bus: Any, *,
                        claim_id: str,
                        claim_text: str = "",
                        verdict: Verdict | dict | None = None,
                        packages: list[dict | EvidencePackage] | None = None,
                        ) -> Card:
        """Draft, confine, and publish one card (published or correction).

        Never raises on model failure: the fallback card is always
        available. ``bus`` may be None (card built, not published).
        """
        label, reasons, verified_ids = _verdict_parts(verdict)
        pkg_dicts = [p.to_dict() if isinstance(p, EvidencePackage) else p
                     for p in (packages or [])
                     if isinstance(p, (dict, EvidencePackage))]
        by_id = {str(p.get("package_id", "")): p
                 for p in pkg_dicts if isinstance(p, dict)}
        usage: dict = {"model_calls": 0}
        cost_usd = 0.0
        draft = await self._draft(claim_text, label, reasons, verified_ids,
                                  by_id, usage)
        if draft is None:
            card_fields = _fallback_card(claim_text, label, verified_ids)
            fallback = True
        else:
            card_fields = draft
            fallback = False
        cost_usd = _cost_of(self._config.llm, usage)
        n = self._counts.get(claim_id, 0) + 1
        self._counts[claim_id] = n
        prev = self._seen.get(claim_id)
        new_ids = sorted(set(verified_ids) - (prev["package_ids"]
                                              if prev else set()))
        verdict_changed = bool(prev) and prev.get("verdict") != label
        if prev is not None and (new_ids or verdict_changed):
            event_type = "card.correction"
            correction_of: str | None = prev["card_id"]
        else:
            event_type = "card.published"
            correction_of = None
        card = Card(
            card_id=f"card-{claim_id}-{n}", claim_id=claim_id, verdict=label,
            headline_es=card_fields[0], headline_en=card_fields[1],
            body_es=card_fields[2], body_en=card_fields[3],
            citations=card_fields[4], fallback=fallback,
            event_type=event_type, correction_of=correction_of,
            new_packages=new_ids, cost_usd=cost_usd, usage=usage)
        self._seen[claim_id] = {
            "card_id": card.card_id,
            "package_ids": (prev["package_ids"] if prev else set())
            | set(verified_ids),
            "verdict": label,
        }
        if bus is not None:
            try:
                await bus.publish(card.to_event())
            except Exception as exc:
                log.warning("card publish failed: %r", exc)
        return card

    async def _draft(self, claim_text: str, label: str, reasons: list[str],
                     verified_ids: list[str], by_id: dict,
                     usage: dict) -> tuple | None:
        """Model draft, or None when confined/fallback must take over."""
        llm = self._config.llm
        if llm is None or not bool(getattr(llm, "available", True)):
            return None
        lines = [f"claim: {claim_text or '(no text)'}",
                 f"verdict: {label}",
                 f"reasons: {', '.join(reasons) or '(none)'}",
                 f"ALLOWED package ids: "
                 f"{', '.join(verified_ids) or '(none — use [])'}"]
        for pid in verified_ids:
            pkg = by_id.get(pid, {})
            if not isinstance(pkg, dict):
                continue
            quote = str(pkg.get("quote", ""))[:400]
            lines.append(f"- {pid}: quote={quote!r} "
                         f"url={str(pkg.get('url', ''))[:120]}")
        prompt = "\n".join(lines)
        try:
            out = await asyncio.to_thread(
                _call_card_json, llm, prompt, self._config)
        except Exception as exc:
            log.warning("card draft failed: %r", exc)
            return None
        _record_usage(llm, usage)
        return _confine(out, set(verified_ids))


def _call_card_json(llm: Any, prompt: str, config: CardConfig) -> dict:
    return llm.complete_json(
        CARD_SYSTEM, prompt, max_tokens=config.model_max_tokens,
        reasoning_effort=config.reasoning_effort,
        json_schema=CARD_SCHEMA)


def _verdict_parts(verdict: Verdict | dict | None
                   ) -> tuple[str, list[str], list[str]]:
    if isinstance(verdict, Verdict):
        return (verdict.verdict, list(verdict.reasons),
                [str(i) for i in verdict.package_ids])
    if isinstance(verdict, dict):
        label = str(verdict.get("verdict", "unverifiable"))
        reasons = [str(r) for r in (verdict.get("reasons") or [])]
        ids = [str(i) for i in (verdict.get("package_ids") or [])]
        return label, reasons, ids
    return "unverifiable", [], []


def _confine(out: Any, allowed: set[str]) -> tuple | None:
    """Validate a model draft: all citations must be in the allowed set.

    Returns (headline_es, headline_en, body_es, body_en, citations) or
    None when the draft must be discarded (fallback takes over).
    """
    if not isinstance(out, dict):
        return None
    fields = [out.get(k) for k in
              ("headline_es", "headline_en", "body_es", "body_en")]
    if any(not isinstance(f, str) or not f.strip() for f in fields):
        return None
    citations = out.get("citations")
    if not isinstance(citations, list):
        return None
    clean: list[str] = []
    for c in citations:
        if not isinstance(c, str):
            return None
        if c not in allowed:
            log.warning("card citation %r outside verified set; "
                        "discarding draft", c)
            return None
        if c not in clean:
            clean.append(c)
    return (fields[0].strip(), fields[1].strip(), fields[2].strip(),
            fields[3].strip(), clean)


def _fallback_card(claim_text: str, label: str,
                   verified_ids: list[str]) -> tuple:
    es_label, en_label = VERDICT_LABELS.get(label, (label, label))
    snippet = (claim_text or "").strip()
    snippet = (snippet[:77] + "…") if len(snippet) > 78 else snippet
    es_body, en_body = _FALLBACK_BODY.get(label, _FALLBACK_BODY["unverifiable"])
    head_es = f"{es_label}: {snippet}" if snippet else es_label
    head_en = f"{en_label}: {snippet}" if snippet else en_label
    return (head_es, head_en, es_body, en_body, list(verified_ids))


def _record_usage(llm: Any, usage: dict) -> None:
    last = getattr(llm, "last_usage", None) or {}
    try:
        usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + int(
            last.get("prompt_tokens") or 0)
        usage["completion_tokens"] = usage.get("completion_tokens", 0) + int(
            last.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        pass
    usage["model_calls"] = usage.get("model_calls", 0) + 1
    usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get(
        "completion_tokens", 0)


def _cost_of(llm: Any, usage: dict) -> float:
    try:
        from live.swarm.coordinator import estimate_cost_usd

        return float(estimate_cost_usd(usage.get("prompt_tokens", 0),
                                       usage.get("completion_tokens", 0)))
    except Exception:
        return 0.0


__all__ = [
    "CARD_MAX_TOKENS",
    "CARD_REASONING_EFFORT",
    "CARD_SCHEMA",
    "CARD_SYSTEM",
    "VERDICT_LABELS",
    "Card",
    "CardConfig",
    "Editor",
]
