"""M7: long-context speaker self-contradiction check (Atlas Live).

Pipeline for one full transcript::

    transcript segments [{t_sec, text}] + claims registry
      -> 1 long-context model call (reasoning "low", strict json_schema)
         proposes self-contradiction pairs
         {quote_a, t_a, quote_b, t_b, entity, indicator, why}
      -> CODE verifies every pair:
           V1 both quotes exist LITERALLY in the transcript (normalized)
           V2 claimed times match the segments where the quotes were found
           V3 the two segments differ by >30s
           V4 the same entity + indicator appear in both quotes
           V5 numbers re-extracted from BOTH quotes with
              ``src.atlas.core.units`` collide (same indicator/entity,
              compatible units, relative difference >2%),
              OR an explicit affirmation/negation clash
      -> verified pairs   => ``speaker.contradiction`` bus events
         rejected pairs   => discarded with a machine-readable reason
         zero verified    => honest empty report (never invented)

The model proposes; code disposes. ``run_longctx`` never raises on
model failure: it returns an empty report with a note.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim (same convention as live/verifier)
    from src.atlas.core.units import parse_quantities, units_compatible
except ImportError:  # pragma: no cover - import shim
    from atlas.core.units import parse_quantities, units_compatible

log = logging.getLogger(__name__)

# Floor: reasoning "low" burns hidden tokens before emitting (probe-verified
# class: vision/vista and falsify use 4000 for the same reason). M7's first
# live run truncated into empty content at 1500 (finish_reason=length).
MODEL_MAX_TOKENS = 4000
MODEL_REASONING_EFFORT = "low"
MIN_GAP_S = 30.0
MIN_REL_DIFF = 0.02
TIME_TOLERANCE_S = 25.0
MAX_PAIRS = 6
MIN_QUOTE_CHARS = 20

M7_SYSTEM = """You spot SELF-CONTRADICTIONS by ONE speaker in a keynote transcript. The transcript is given as numbered segments with start times like [t=13.3s]. A claim registry follows for context only.

Propose pairs where the SAME speaker asserts INCOMPATIBLE things about the SAME entity and the SAME indicator at DIFFERENT moments (example: "China is home to 1.41 billion people" early vs "China's population has fallen to just 141 million people" later).

Return ONLY a JSON object: {"pairs": [{"quote_a": "...", "t_a": 0.0, "quote_b": "...", "t_b": 0.0, "entity": "...", "indicator": "...", "why": "..."}]}
Your ENTIRE reply must be exactly that JSON object: no greeting, no preamble, no text before or after the braces.

Rules: each "quote_a"/"quote_b" is a VERBATIM substring of ONE transcript segment (copy words exactly, do not paraphrase or merge segments); "t_a"/"t_b" are the START times of the segments the quotes come from (copy the [t=...] markers); the two quotes must come from DIFFERENT segments far apart in time; "entity" is the shared subject (short, lowercase, e.g. "china"); "indicator" is the shared measure (short, lowercase, e.g. "population"); "why" is one sentence naming the concrete incompatibility with both figures. Only propose numeric clashes (same indicator, incompatible values) or EXPLICIT affirmation-vs-negation clashes ("X is ..." vs "X is not ..."). At most 6 pairs, strongest first. If the speaker never contradicts themselves, return {"pairs": []}. Never invent quotes, times, or figures."""

M7_SCHEMA = {
    "name": "m7_longctx",
    "schema": {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "quote_a": {"type": "string"},
                        "t_a": {"type": "number"},
                        "quote_b": {"type": "string"},
                        "t_b": {"type": "number"},
                        "entity": {"type": "string"},
                        "indicator": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": [
                        "quote_a", "t_a", "quote_b", "t_b",
                        "entity", "indicator", "why",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["pairs"],
        "additionalProperties": False,
    },
}

_WS_RE = re.compile(r"\s+")
_SRT_BLOCK_RE = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)
_NEG_RE = re.compile(
    r"\b(not|never|no|n't|cannot|can't|won't|don't|doesn't|didn't|"
    r"isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|none|nothing|"
    r"neither|nor|impossible|nunca|jam[aá]s|tampoco|nadie|nada|"
    r"ning[uú]n|sin)\b"
)

# Indicator synonym groups (EN + ES). A model "indicator" matches a quote
# when the indicator itself — or any member of the group containing it —
# appears literally in the normalized quote. Documented lexicon, not a
# semantic judge: groups only cover measures seen in keynote-style talks.
_INDICATOR_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"population", "people", "inhabitants", "persons",
               "población", "poblacion", "personas", "habitantes"}),
    frozenset({"gdp", "economy", "economic", "output",
               "pib", "economía", "economia"}),
    frozenset({"growth", "grew", "grow", "grows", "growing", "doubled",
               "double", "crecimiento", "creció", "crecio", "crece"}),
    frozenset({"price", "prices", "cost", "costs", "precio", "precios"}),
    frozenset({"temperature", "degrees", "celsius", "fahrenheit",
               "temperatura", "grados"}),
    frozenset({"speed", "velocity", "meters per second", "km/s",
               "velocidad"}),
    frozenset({"distance", "meters", "kilometers", "km", "around",
               "distancia", "metros"}),
    frozenset({"area", "covers", "cover", "spans", "span",
               "superficie", "cubre"}),
    frozenset({"capital", "city", "cities", "ciudad"}),
    frozenset({"budget", "spending", "funding", "presupuesto", "gasto"}),
    frozenset({"rate", "percent", "percentage", "tasa", "por ciento"}),
)


# ---------------------------------------------------------------------------
# transcript model
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """One transcript segment: start/end seconds + verbatim text."""

    t_sec: float
    end_sec: float
    text: str


def _srt_ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text: str) -> list[Segment]:
    """Parse SubRip text into ``[{t_sec, end_sec, text}]`` segments."""
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _SRT_BLOCK_RE.search(lines[1] if len(lines) > 1 else "")
        if m is None and len(lines) > 1:
            m = _SRT_BLOCK_RE.search(lines[0])
            body = " ".join(lines[1:])
        else:
            body = " ".join(lines[2:])
        if m is None or not body:
            continue
        start = _srt_ts(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _srt_ts(m.group(5), m.group(6), m.group(7), m.group(8))
        segments.append(Segment(t_sec=start, end_sec=end, text=body))
    return segments


def load_srt(path: str | Path) -> list[Segment]:
    """Load and parse an ``.srt`` file."""
    with open(path, encoding="utf-8") as fh:
        return parse_srt(fh.read())


def normalize(text: str) -> str:
    """Normalize for literal matching: quotes unified, case folded, ws one."""
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    text = text.replace("‘", "'").replace("—", "-").replace("–", "-")
    return _WS_RE.sub(" ", text.casefold()).strip()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@dataclass
class LongCtxConfig:
    """Knobs for the M7 check (all verification thresholds in code)."""

    llm: Any = None  # complete_json(system, user, max_tokens, ...) client
    model_max_tokens: int = MODEL_MAX_TOKENS
    reasoning_effort: str | None = MODEL_REASONING_EFFORT
    min_gap_s: float = MIN_GAP_S
    min_rel_diff: float = MIN_REL_DIFF
    time_tolerance_s: float = TIME_TOLERANCE_S
    max_pairs: int = MAX_PAIRS
    min_quote_chars: int = MIN_QUOTE_CHARS


@dataclass
class _Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def record(self, llm: Any) -> None:
        self.calls += 1
        usage = getattr(llm, "last_usage", None) or {}
        try:
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pass

    def cost_usd(self) -> float:
        return estimate_cost_usd(self.prompt_tokens, self.completion_tokens)


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost via the agent-loop helper, else fallback Spark prices."""
    try:
        from src.atlas.agent.loop import estimate_cost as _fn  # type: ignore
    except Exception:
        try:
            from atlas.agent.loop import estimate_cost as _fn  # type: ignore
        except Exception:
            _fn = None
    if callable(_fn):
        try:
            return float(_fn(prompt_tokens, completion_tokens))
        except Exception:
            pass
    return (prompt_tokens * 1.25 + completion_tokens * 4.25) / 1e6


# ---------------------------------------------------------------------------
# code verification (no model here)
# ---------------------------------------------------------------------------

def _locate(quote_norm: str, seg_norms: list[str]) -> int | None:
    """Index of the first segment containing the normalized quote."""
    if not quote_norm:
        return None
    for i, seg in enumerate(seg_norms):
        if quote_norm in seg:
            return i
    return None


def _indicator_hit(indicator: str, quote_norm: str) -> bool:
    """True when the indicator (or a synonym-group member) is literal."""
    ind = normalize(indicator)
    if not ind:
        return False
    if ind in quote_norm:
        return True
    words = [w for w in ind.split() if len(w) >= 4]
    for group in _INDICATOR_GROUPS:
        if ind in group or any(w in group for w in words):
            if any(member in quote_norm for member in group):
                return True
    return any(w in quote_norm for w in words)


def _rel_diff(a: float, b: float) -> float | None:
    if not a or not b:
        return None
    try:
        return abs(float(a) - float(b)) / max(abs(float(a)), abs(float(b)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _numeric_collision(quote_a: str, quote_b: str,
                       min_rel_diff: float) -> dict | None:
    """Re-extract numbers from BOTH quotes; collide on same-unit >2% diff.

    Returns the strongest colliding ``{value_a, value_b, unit, rel_diff}``
    or None. Units must be KNOWN on both sides (``units_compatible``:
    unknown units never match) — a bare year pair (2022 vs 2020) is
    therefore NOT a numeric collision.
    """
    qa = parse_quantities(quote_a, label="a")
    qb = parse_quantities(quote_b, label="b")
    best: dict | None = None
    for x in qa:
        for y in qb:
            if not units_compatible(x.unit, y.unit):
                continue
            diff = _rel_diff(x.value, y.value)
            if diff is None or diff <= min_rel_diff:
                continue
            if best is None or diff > best["rel_diff"]:
                best = {
                    "value_a": x.value,
                    "value_b": y.value,
                    "unit": x.normalized_unit or x.unit,
                    "raw_a": x.raw_text,
                    "raw_b": y.raw_text,
                    "rel_diff": diff,
                }
    return best


def _polarity_clash(quote_a: str, quote_b: str) -> bool:
    """True on EXPLICIT affirmation-vs-negation with shared numbers.

    Exactly one quote carries a negation cue, and both quotes either
    share a normalized number token or are both numberless (so "X is
    100" vs "X is not 100" clashes, but "X is 100" vs "Y is never 5"
    does not — that pair needs the numeric-collision path instead).
    """
    neg_a = bool(_NEG_RE.search(quote_a.casefold()))
    neg_b = bool(_NEG_RE.search(quote_b.casefold()))
    if neg_a == neg_b:
        return False
    nums_a = set(re.findall(r"\d[\d.,]*", quote_a))
    nums_b = set(re.findall(r"\d[\d.,]*", quote_b))
    if not nums_a and not nums_b:
        return True
    return bool(nums_a & nums_b)


def verify_pairs(pairs: list[dict], segments: list[Segment],
                 config: LongCtxConfig | None = None
                 ) -> tuple[list[dict], list[dict]]:
    """Verify proposed pairs against the transcript (pure code).

    Returns ``(verified, discarded)``. Each verified item carries the
    collision detail; each discarded item carries ``reason`` +
    machine-readable ``detail``. Reasons: ``malformed``,
    ``quote_too_short``, ``quote_not_literal``, ``time_mismatch``,
    ``same_window``, ``entity_mismatch``, ``indicator_mismatch``,
    ``no_collision``.
    """
    cfg = config or LongCtxConfig()
    seg_norms = [normalize(s.text) for s in segments]
    verified: list[dict] = []
    discarded: list[dict] = []

    def _drop(pair: dict, reason: str, **detail: Any) -> None:
        discarded.append({"pair": pair, "reason": reason, "detail": detail})

    for raw in pairs[: cfg.max_pairs]:
        if not isinstance(raw, dict):
            _drop(raw, "malformed")
            continue
        try:
            quote_a = str(raw.get("quote_a", ""))
            quote_b = str(raw.get("quote_b", ""))
            t_a = float(raw.get("t_a", "nan"))
            t_b = float(raw.get("t_b", "nan"))
            entity = str(raw.get("entity", ""))
            indicator = str(raw.get("indicator", ""))
            why = str(raw.get("why", ""))
        except (TypeError, ValueError):
            _drop(raw, "malformed")
            continue

        qa_norm, qb_norm = normalize(quote_a), normalize(quote_b)
        if len(qa_norm) < cfg.min_quote_chars or \
                len(qb_norm) < cfg.min_quote_chars:
            _drop(raw, "quote_too_short")
            continue

        # V1: both quotes literal in the transcript.
        ia, ib = _locate(qa_norm, seg_norms), _locate(qb_norm, seg_norms)
        if ia is None or ib is None:
            _drop(raw, "quote_not_literal",
                  found_a=ia is not None, found_b=ib is not None)
            continue
        seg_a, seg_b = segments[ia], segments[ib]

        # V2: claimed times match where the quotes were actually found.
        if abs(t_a - seg_a.t_sec) > cfg.time_tolerance_s or \
                abs(t_b - seg_b.t_sec) > cfg.time_tolerance_s:
            _drop(raw, "time_mismatch",
                  actual_a=seg_a.t_sec, actual_b=seg_b.t_sec)
            continue

        # V3: segments differ by >30s (uses ACTUAL segment times).
        gap = abs(seg_a.t_sec - seg_b.t_sec)
        if gap <= cfg.min_gap_s:
            _drop(raw, "same_window", gap_s=gap)
            continue

        # V4: same entity + indicator literal in both quotes.
        ent = normalize(entity)
        if not ent or ent not in qa_norm or ent not in qb_norm:
            _drop(raw, "entity_mismatch", entity=entity)
            continue
        if not _indicator_hit(indicator, qa_norm) or \
                not _indicator_hit(indicator, qb_norm):
            _drop(raw, "indicator_mismatch", indicator=indicator)
            continue

        # V5: numeric collision (re-extracted) or explicit polarity clash.
        collision = _numeric_collision(quote_a, quote_b, cfg.min_rel_diff)
        clash_kind = "numeric"
        if collision is None:
            if not _polarity_clash(quote_a, quote_b):
                _drop(raw, "no_collision")
                continue
            clash_kind = "polarity"
            collision = {"value_a": None, "value_b": None, "unit": None,
                         "raw_a": None, "raw_b": None, "rel_diff": None}

        verified.append({
            "quote_a": quote_a, "t_a": seg_a.t_sec,
            "quote_b": quote_b, "t_b": seg_b.t_sec,
            "gap_s": gap, "entity": entity, "indicator": indicator,
            "why": why, "clash": clash_kind, **collision,
        })
    return verified, discarded


# ---------------------------------------------------------------------------
# model call (one, long-context)
# ---------------------------------------------------------------------------

def _is_malformed_reply(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "json" in name or "decode" in name or "malformed" in str(exc).lower()


def _prompt_user(segments: list[Segment], claims: list[dict]) -> str:
    lines = ["TRANSCRIPT (full keynote, verbatim segments):"]
    for i, seg in enumerate(segments):
        lines.append(f"[{i} t={seg.t_sec:.1f}s] {seg.text}")
    lines.append("")
    lines.append("CLAIM REGISTRY (context only — quotes must come from "
                 "the transcript above):")
    for row in claims:
        cid = row.get("id", "?")
        lines.append(f"- {cid}: {row.get('text', '')}")
    return "\n".join(lines)


async def _complete_once(config: LongCtxConfig, system: str, user: str,
                         usage: _Usage) -> dict:
    """One structured call with a single malformed-reply retry (M2b rule)."""
    llm = config.llm

    def _call(sys: str) -> dict:
        return llm.complete_json(
            sys, user, max_tokens=config.model_max_tokens,
            reasoning_effort=config.reasoning_effort,
            json_schema=M7_SCHEMA,
        )

    try:
        out = await asyncio.to_thread(_call, system)
    except Exception as exc:
        usage.record(llm)
        if not _is_malformed_reply(exc):
            raise
        log.warning("malformed m7 reply, retrying once: %r", exc)
        out = await asyncio.to_thread(
            _call, system + "\n\nSTRICT: your reply was not pure JSON. "
            "Reply again with ONLY the JSON object.")
        usage.record(llm)
    else:
        usage.record(llm)
    if not isinstance(out, dict):
        raise RuntimeError("model returned non-dict JSON")
    return out


async def run_longctx(bus: Any, segments: list[Segment],
                      claims: list[dict] | None,
                      config: LongCtxConfig | None = None) -> dict:
    """Run the M7 check: propose pairs, verify in code, emit events.

    ``segments`` is the full parsed transcript (``{t_sec, text}``),
    ``claims`` the claims registry (context for the model). Returns an
    honest report dict; with zero verified pairs ``verified``/``events``
    are empty and nothing is invented. Never raises on model failure.
    """
    cfg = config or LongCtxConfig()
    usage = _Usage()
    claims = list(claims or [])
    report: dict = {
        "check": "m7_longctx",
        "segments": len(segments),
        "proposed": 0,
        "verified": [],
        "discarded": [],
        "events": [],
        "model_calls": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "cost_usd": 0.0,
        "notes": [],
    }

    llm = cfg.llm
    if llm is None or not getattr(llm, "available", True):
        report["notes"].append("no model available: zero calls, empty report")
        return report
    if not segments:
        report["notes"].append("empty transcript: nothing to check")
        return report

    try:
        out = await _complete_once(
            cfg, M7_SYSTEM, _prompt_user(segments, claims), usage)
    except Exception as exc:  # noqa: BLE001 - honest empty, never raise
        log.warning("m7 model call failed: %r", exc)
        report["notes"].append(f"model call failed: {exc}")
        report["model_calls"] = usage.calls
        report["usage"] = {"prompt_tokens": usage.prompt_tokens,
                           "completion_tokens": usage.completion_tokens}
        report["cost_usd"] = usage.cost_usd()
        return report

    pairs = out.get("pairs", [])
    if not isinstance(pairs, list):
        report["notes"].append("model reply had no pairs list")
        pairs = []
    report["proposed"] = len(pairs)

    verified, discarded = verify_pairs(pairs, segments, cfg)
    report["verified"] = verified
    report["discarded"] = discarded
    if not verified:
        report["notes"].append(
            f"0 of {len(pairs)} proposed pair(s) verified: "
            "no speaker.contradiction emitted")

    for v in verified:
        event = {
            "type": "speaker.contradiction",
            "check": "m7_longctx",
            "quote_a": v["quote_a"], "t_a": v["t_a"],
            "quote_b": v["quote_b"], "t_b": v["t_b"],
            "gap_s": v["gap_s"], "entity": v["entity"],
            "indicator": v["indicator"], "why": v["why"],
            "clash": v["clash"], "value_a": v["value_a"],
            "value_b": v["value_b"], "unit": v["unit"],
            "rel_diff": v["rel_diff"],
        }
        if bus is not None:
            try:
                await bus.publish(event)
            except Exception as exc:  # noqa: BLE001 - bus must not kill m7
                log.warning("m7 bus publish failed: %r", exc)
                report["notes"].append(f"bus publish failed: {exc}")
                continue
        report["events"].append(event)

    report["model_calls"] = usage.calls
    report["usage"] = {"prompt_tokens": usage.prompt_tokens,
                       "completion_tokens": usage.completion_tokens}
    report["cost_usd"] = usage.cost_usd()
    return report
