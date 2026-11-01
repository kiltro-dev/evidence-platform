"""M1b claim detector: worthiness + coreference + window dedup.

Model-first under a strict decoding schema (mirrors the ``split.py`` /
``extract.py`` pattern): the model proposes verbatim spans with
``worth_checking`` + coreference-resolved text, and deterministic code
enforces everything else:

- a span is kept only when its normalized form appears verbatim in the
  turn text (no rephrasing, no invention);
- ``worth_checking == False`` spans are dropped;
- ``resolved`` text is carried APART from the verbatim span; a
  resolution is kept only when its antecedent is explicit in the
  context window, never invented;
- spans duplicating the recent window (exact normalized match or
  fuzzy >= threshold) are dropped as repeats, never re-emitted.

Without a model the heuristic fallback splits sentences and keeps
spans with checkable surface cues (numbers, years, indicator words);
it never resolves coreference (``resolved`` stays None).
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

log = logging.getLogger(__name__)

# ---- budgets -------------------------------------------------------------
# Transcript slice fed to the model per turn (chars, ~4 chars/token).
DETECT_INPUT_CHARS = 2000
# Completion cap: detection emits short spans, never prose.
DETECT_MAX_TOKENS = 800
# Detection is the cheapest judgment step: keep hidden reasoning minimal
# so end-phrase -> card latency stays low.
# LIVE PROBE 2026-11-01 (M1e U1): with the full DETECT_SYSTEM prompt,
# reasoning_effort="low" burns all 800 max_tokens in hidden reasoning
# (finish_reason=length, empty content, silent heuristic fallback).
# "minimal" completes (257 reasoning tokens on probe); "none" is rejected
# by the API (supported: minimal/low/medium/high/xhigh/max).
DETECT_REASONING_EFFORT = "minimal"
# Minimum headroom (tokens) the caller must keep under the model limit.
HEADROOM_TOKENS = 1500
# Conservative context assumption for the headroom check. The real
# muse-spark limit is higher; callers that know it pass `context_tokens`.
CONSERVATIVE_CONTEXT_TOKENS = 16000
# Recent claims kept for cross-turn dedup.
DEDUP_WINDOW = 20
# Normalized fuzzy similarity at/above which a span is a repeat.
DEDUP_FUZZY_THRESHOLD = 0.85
# Heuristic floor: shorter spans assert nothing checkable.
_MIN_SPAN_CHARS = 12

DETECT_SYSTEM = """You detect checkable factual claims in one live transcript turn (Spanish or English speech).
Return ONLY a JSON object: {"claims": [{"text": "...", "worth_checking": true|false, "resolved": "..."|null, "reason": "..."}]}
Rules: "text" is copied VERBATIM from the turn (exact words, no rephrasing, no new words, no invented numbers). One checkable fact per item.
"worth_checking" is true ONLY for factual assertions a fact-checker could verify (statistics, records, dates, quantities, named achievements); false for greetings, opinions, questions, advice, filler, and vague claims with no verifiable content.
"resolved" rewrites "text" with pronouns/demonstratives replaced by their EXPLICIT antecedent from the context turns (e.g. turn says "su PIB crecio" and context names "Espana" -> "el PIB de Espana crecio"); null when there is no explicit antecedent or no pronoun to resolve. Never guess an antecedent.
"reason" is a few words: why worth checking, or why not."""

# Strict decoding schema for the detect call (mirrors DETECT_SYSTEM).
DETECT_SCHEMA = {
    "name": "live_claim_detect",
    "schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "worth_checking": {"type": "boolean"},
                        "resolved": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["text", "worth_checking", "resolved", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
}

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+")

# Surface cues for the heuristic worthiness test (ES/EN indicator words).
_WORTH_WORDS = frozenset(
    {
        "pib", "gdp", "producto", "interior", "bruto", "desempleo",
        "unemployment", "paro", "inflacion", "inflation", "precios",
        "prices", "poblacion", "population", "deuda", "debt",
        "deficit", "superavit", "surplus", "exportaciones", "exports",
        "importaciones", "imports", "salario", "wage", "pension",
        "billones", "trillion", "millones", "million", "miles", "miles",
        "por", "ciento", "percent", "puntos", "points", "record",
        "historia", "history", "estudio", "study", "datos", "data",
        "crecio", "crecimiento", "growth", "cayo", "caida", "billion",
    }
)
_YEAR_RE = re.compile(r"\b(19|20|21)\d{2}\b")
_DIGIT_RE = re.compile(r"\d")


def normalize(text: str) -> str:
    """Canonical form for verbatim + dedup comparisons.

    Lowercase, accent-stripped, punctuation dropped, whitespace
    collapsed: "¡Su PIB creció 5%!" -> "su pib crecio 5".
    """
    lowered = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in lowered if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def fuzzy_similarity(a: str, b: str) -> float:
    """Normalized similarity in [0, 1] between two raw spans."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def estimate_tokens(chars: int) -> int:
    """Rough char-based token estimate (4 chars/token, rounded up)."""
    return (chars + 3) // 4


def check_headroom(
    prompt_chars: int,
    max_tokens: int = DETECT_MAX_TOKENS,
    context_tokens: int = CONSERVATIVE_CONTEXT_TOKENS,
    headroom: int = HEADROOM_TOKENS,
) -> tuple[bool, int]:
    """(ok, remaining) — True when prompt + completion keeps `headroom`.

    `remaining` is the slack left under `context_tokens` after prompt,
    completion budget and required headroom. Callers with a known model
    limit pass it as `context_tokens`.
    """
    remaining = context_tokens - estimate_tokens(prompt_chars) - max_tokens - headroom
    return remaining >= 0, remaining


def claim_id_for(normalized_span: str) -> str:
    """Stable claim id from the normalized span (dedup-safe)."""
    digest = hashlib.sha1(normalized_span.encode("utf-8")).hexdigest()[:12]
    return f"clm_{digest}"


@dataclass
class ClaimCandidate:
    claim_id: str
    text: str  # verbatim span from the turn
    resolved: str | None  # coreference-resolved form, or None
    t_start: float | None = None
    t_end: float | None = None
    turn: int | None = None
    speaker: str | None = None
    actor: str = "heuristic"
    model: str | None = None
    notes: list[str] = field(default_factory=list)


class Detector:
    """Turn-level claim detector with a cross-turn dedup window."""

    def __init__(self, llm=None, window: int = DEDUP_WINDOW) -> None:
        self._llm = llm
        self._window = window
        # Recent normalized spans (+ claim ids) for dedup, oldest first.
        self._recent: deque[tuple[str, str]] = deque(maxlen=window)

    @property
    def recent(self) -> list[tuple[str, str]]:
        """Snapshot of the dedup window (normalized span, claim id)."""
        return list(self._recent)

    def process_turn(
        self, turn: dict, context_turns: list[dict] | None = None
    ) -> tuple[list[ClaimCandidate], list[str]]:
        """Detect new checkable claims in one transcript turn.

        `turn`: {turn, text, speaker, t_start, t_end}.
        `context_turns`: preceding turns (for model coreference only).
        Returns (new_claims, notes): repeats are dropped, never re-emitted.
        """
        notes: list[str] = []
        text = str(turn.get("text", "") or "")
        if not text.strip():
            return [], ["empty turn; nothing to detect"]
        if self._llm is not None and getattr(self._llm, "available", False):
            try:
                return self._from_model(turn, text, context_turns or [], notes)
            except Exception as exc:
                log.warning("Claim detection failed, using heuristic: %r", exc)
                notes.append("model detect failed; heuristic used")
        else:
            notes.append("no model available; heuristic used")
        claims, hnotes = self._from_heuristic(turn, text)
        notes.extend(hnotes)
        return claims, notes

    # -- model path --------------------------------------------------------
    def _from_model(
        self, turn: dict, text: str, context_turns: list[dict], notes: list[str]
    ) -> tuple[list[ClaimCandidate], list[str]]:
        clean = text.strip()
        if len(clean) > DETECT_INPUT_CHARS:
            clean = clean[:DETECT_INPUT_CHARS]
            notes.append(f"turn truncated to {DETECT_INPUT_CHARS} chars")
        ctx_lines = []
        for ctx in context_turns[-3:]:
            ctx_lines.append(
                f"[{ctx.get('speaker', '?')}] {str(ctx.get('text', ''))[:300]}"
            )
        prompt = (
                ("Context:\n" + "\n".join(ctx_lines) + "\n\n" if ctx_lines else "")
                + f"Turn [{turn.get('speaker', '?')}]: {clean}"
        )
        ok, remaining = check_headroom(len(prompt))
        if not ok:
            # Truncate context first (the turn itself is the payload).
            prompt = f"Turn [{turn.get('speaker', '?')}]: {clean}"
            ok, remaining = check_headroom(len(prompt))
        if not ok:
            raise ValueError(
                f"detect prompt exceeds budget even without context "
                f"(short {abs(remaining)} tokens)"
            )
        data = self._llm.complete_json(
            DETECT_SYSTEM,
            prompt,
            max_tokens=DETECT_MAX_TOKENS,
            reasoning_effort=DETECT_REASONING_EFFORT,
            json_schema=DETECT_SCHEMA,
        )
        model_name = getattr(self._llm, "name", "model")
        items = data.get("claims") if isinstance(data, dict) else None
        items = items if isinstance(items, list) else []
        haystack = normalize(clean)
        context_norm = normalize(" ".join(ctx_lines))
        claims: list[ClaimCandidate] = []
        dropped = 0
        for item in items:
            if not isinstance(item, dict):
                dropped += 1
                continue
            span = item.get("text")
            if (
                not isinstance(span, str)
                or not span.strip()
                or item.get("worth_checking") is not True
            ):
                dropped += 1
                continue
            norm = normalize(span)
            if (
                not norm
                or len(norm) < _MIN_SPAN_CHARS
                or norm not in haystack
            ):
                dropped += 1
                continue
            resolved = item.get("resolved")
            if not isinstance(resolved, str) or not resolved.strip():
                resolved = None
            elif not self._resolution_grounded(norm, resolved, context_norm):
                notes.append(f"unguarded resolution dropped for {span[:40]!r}")
                resolved = None
            dup_of = self._find_duplicate(norm)
            if dup_of is not None:
                dropped += 1
                continue
            claim_id = claim_id_for(norm)
            self._recent.append((norm, claim_id))
            claims.append(
                ClaimCandidate(
                    claim_id=claim_id,
                    text=span.strip(),
                    resolved=resolved.strip() if resolved else None,
                    t_start=turn.get("t_start"),
                    t_end=turn.get("t_end"),
                    turn=turn.get("turn"),
                    speaker=turn.get("speaker"),
                    actor="model",
                    model=model_name,
                )
            )
        if dropped:
            notes.append(f"dropped {dropped} not-worthy/non-verbatim/repeat span(s)")
        return claims, notes

    @staticmethod
    def _resolution_grounded(
        span_norm: str, resolved: str, context_norm: str
    ) -> bool:
        """True when every new content word in `resolved` is grounded.

        New = present in resolved but not in the span: each such word
        (length >= 4, so glue words pass) must appear in the context
        turns — otherwise the model guessed the antecedent ("su PIB"
        with no country named) and the resolution is dropped while the
        verbatim span survives.
        """
        new_words = set(normalize(resolved).split()) - set(span_norm.split())
        return all(
            len(w) < 4 or w in context_norm for w in new_words
        )

    # -- heuristic path ----------------------------------------------------
    def _from_heuristic(
        self, turn: dict, text: str
    ) -> tuple[list[ClaimCandidate], list[str]]:
        segments = [s.strip() for s in _SENTENCE_BOUNDARY.split(text.strip())]
        claims: list[ClaimCandidate] = []
        kept, dropped = 0, 0
        for seg in segments:
            norm = normalize(seg)
            if len(norm) < _MIN_SPAN_CHARS or not self._looks_checkable(norm):
                dropped += 1
                continue
            if self._find_duplicate(norm) is not None:
                dropped += 1
                continue
            claim_id = claim_id_for(norm)
            self._recent.append((norm, claim_id))
            claims.append(
                ClaimCandidate(
                    claim_id=claim_id,
                    text=seg,
                    resolved=None,  # heuristic never resolves coreference
                    t_start=turn.get("t_start"),
                    t_end=turn.get("t_end"),
                    turn=turn.get("turn"),
                    speaker=turn.get("speaker"),
                    actor="heuristic",
                    model=None,
                )
            )
            kept += 1
        notes = [f"heuristic kept {kept}, dropped {dropped} segment(s)"]
        return claims, notes

    @staticmethod
    def _looks_checkable(norm: str) -> bool:
        """Surface worthiness: numbers/years/indicator words (ES/EN)."""
        if _DIGIT_RE.search(norm) or _YEAR_RE.search(norm):
            return True
        return any(w in _WORTH_WORDS for w in norm.split())

    # -- dedup ---------------------------------------------------------------
    def _find_duplicate(self, norm: str) -> str | None:
        """Claim id of the window entry this span repeats, else None."""
        for prev_norm, prev_id in self._recent:
            if norm == prev_norm:
                return prev_id
            if SequenceMatcher(None, norm, prev_norm).ratio() >= DEDUP_FUZZY_THRESHOLD:
                return prev_id
        return None
