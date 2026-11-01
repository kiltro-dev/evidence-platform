"""M3 vista xcheck: deterministic said-vs-shown (Atlas Live, no model).

Compares speech numbers (``{"text", "numbers"?, "t_sec"}`` per turn;
``numbers`` optional — :func:`extract_numbers` fills them from the text)
against chart values read by ``live/vista/read.py`` within a ±``window_s``
time window:

- same unit class + relative difference <= ``tolerance`` -> ``match``;
- same unit class + larger difference -> ``mismatch`` + ``visual.mismatch``
  event carrying both values;
- ``truncated_axis`` chart + a trend/magnitude assertion in the window ->
  ``visual.misleading_axis`` flag;
- no compatible chart number in the window -> skipped (never a verdict).

Pure functions; the returned ``events`` are EventBus-ready dicts (each has
a ``type`` key) for the caller to publish.
"""

from __future__ import annotations

import re

WINDOW_S = 15.0
MATCH_TOL = 0.02  # relative difference at/under which said == shown

_SCALE_MULT = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "mn": 1e6, "millon": 1e6, "millones": 1e6,
    "billion": 1e9, "bn": 1e9,
    "trillion": 1e12, "tn": 1e12, "billon": 1e12, "billones": 1e12,
}
_SCALE_WORDS = "|".join(sorted(_SCALE_MULT, key=len, reverse=True))

_NUMBER_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s*(?:%|percent|por\s*ciento))\b"
    r"|(?P<num2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf2>\s*(?:" + _SCALE_WORDS + r"))?\b",
    re.IGNORECASE,
)
_MONEY_BEFORE_RE = re.compile(r"\$\s*$")
_MONEY_AFTER_RE = re.compile(r"^\s*(?:us\s*)?(?:dollars?|usd|d[oó]lares?)\b", re.IGNORECASE)
_PERCENT_WORD_RE = re.compile(r"(%|percent|por\s*ciento)", re.IGNORECASE)
_MONEY_WORD_RE = re.compile(r"(\$|dollar|usd|d[oó]lar|gdp|pib)", re.IGNORECASE)

# Trend / magnitude / ranking assertions (EN/ES, lowercase substrings).
TREND_CUES = frozenset({
    "doubled", "double", "tripled", "triple", "quadrupled",
    "surge", "surged", "soared", "soaring", "exploded", "explosion",
    "skyrocketed", "collapsed", "collapse", "plummeted", "halved",
    "multiplied", "grew", "grown", "growth", "grows",
    "shrink", "shrank", "shrunk", "declined", "decline",
    "larger", "smaller", "bigger", "greater", "biggest", "smallest",
    "exceeded", "surpassed", "outgrew", "overtaken", "larger than",
    "record", "boom", "every year", "each year", "every single year",
    "duplic", "triplic", "dispar", "explot", "explo", "creci",
    "crecido", "cay", "caida", "caída", "desplom", "mas grande",
    "más grande", "mayor que", "menor que", "super", "record",
    "cada a", "todos los a", "récord",
})


def _parse_num(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_numbers(text: str) -> list[dict]:
    """Speech numbers from text: ``[{value, base, unit, uclass, raw}]``.

    ``value`` is the number as said, ``base`` scaled to base units,
    ``unit`` the raw unit word (``percent``/``USD``/scale word/None),
    ``uclass`` one of ``percent``/``money``/``count``. Bare years in
    [1900, 2100] are excluded (labels, not quantities).
    """
    found: list[dict] = []
    for m in _NUMBER_RE.finditer(text or ""):
        raw_num = m.group("num") or m.group("num2") or ""
        try:
            value = _parse_num(raw_num)
        except ValueError:
            continue
        suf = (m.group("suf") or "").strip().lower()
        suf2 = (m.group("suf2") or "").strip().lower()
        before = text[:m.start()]
        after = text[m.end():]
        if suf or _PERCENT_WORD_RE.search(suf):
            unit, uclass, base = "percent", "percent", value
        elif _MONEY_BEFORE_RE.search(before) or _MONEY_AFTER_RE.search(after):
            unit, uclass = "USD", "money"
            base = value * (_SCALE_MULT.get(suf2, 1.0))
            if suf2:
                unit = f"USD {suf2}"
        elif suf2:
            unit, uclass = suf2, "count"
            base = value * _SCALE_MULT[suf2]
        else:
            if raw_num.isdigit() and 1900 <= int(raw_num) <= 2100:
                continue  # bare year, not a quantity
            unit, uclass, base = None, "count", value
        found.append({
            "value": value, "base": base, "unit": unit, "uclass": uclass,
            "raw": m.group(0).strip(),
        })
    return found


def _chart_unit(text: str) -> tuple[float, str]:
    """(scale_mult, uclass) parsed from chart unit/title/notes text."""
    low = (text or "").lower()
    if _PERCENT_WORD_RE.search(low):
        return 1.0, "percent"
    scale = 1.0
    for word, mult in _SCALE_MULT.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            scale = max(scale, mult)
    if _MONEY_WORD_RE.search(low):
        return scale, "money"
    return scale, "count"


def chart_numbers(chart: dict) -> list[dict]:
    """Chart values in base units: ``[{label, value, uclass}]``."""
    blob = " ".join(str(chart.get(k, "") or "") for k in ("unit", "title", "notes"))
    scale, uclass = _chart_unit(blob)
    out = []
    values = chart.get("values")
    if not isinstance(values, list):
        return out
    for v in values:
        if not isinstance(v, dict):
            continue
        num = v.get("value")
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            continue
        out.append({"label": str(v.get("label", "")),
                    "value": float(num) * scale, "uclass": uclass})
    return out


def rel_diff(a: float, b: float) -> float:
    """Relative difference in [0, +inf): |a-b|/max(|a|,|b|)."""
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 0.0
    return abs(a - b) / denom


def _speech_numbers(turn: dict) -> list[dict]:
    nums = turn.get("numbers")
    if nums is None:
        return extract_numbers(str(turn.get("text", "") or ""))
    out = []
    for n in nums:
        if isinstance(n, bool):
            continue
        if isinstance(n, (int, float)):
            out.append({"value": float(n), "base": float(n),
                        "unit": None, "uclass": None, "raw": str(n)})
        elif isinstance(n, dict) and isinstance(n.get("value"), (int, float)):
            unit = n.get("unit")
            blob = str(unit or "")
            _scale, uclass = _chart_unit(blob)
            out.append({"value": float(n["value"]),
                        "base": float(n.get("base", n["value"])),
                        "unit": unit, "uclass": n.get("uclass", uclass),
                        "raw": str(n.get("raw", n["value"]))})
    return out


def _has_trend_cue(text: str) -> str | None:
    low = (text or "").lower()
    for cue in sorted(TREND_CUES, key=len, reverse=True):
        if cue in low:
            return cue
    return None


def cross_check(
    speech: list[dict],
    visuals: list[dict],
    window_s: float = WINDOW_S,
    tolerance: float = MATCH_TOL,
) -> dict:
    """Deterministic said-vs-shown over ``speech`` turns and ``visuals``.

    ``speech``: ``[{text, t_sec, numbers?}]``; ``visuals``: ``read_frame``
    results (``{t_sec, charts, frame, slide}``). Returns ``{matches,
    mismatches, flags, events, stats}``.
    """
    matches: list[dict] = []
    mismatches: list[dict] = []
    flags: list[dict] = []
    events: list[dict] = []
    n_numbers = 0
    n_skipped = 0
    for turn in speech:
        text = str(turn.get("text", "") or "")
        t_said = float(turn.get("t_sec", 0.0) or 0.0)
        in_window = [v for v in visuals
                     if abs(float(v.get("t_sec", 0.0) or 0.0) - t_said) <= window_s]
        for said in _speech_numbers(turn):
            n_numbers += 1
            best = None
            for vis in in_window:
                for chart in (vis.get("charts") or []):
                    if not isinstance(chart, dict):
                        continue
                    for shown in chart_numbers(chart):
                        if (said["uclass"] is not None
                                and shown["uclass"] != said["uclass"]):
                            continue
                        diff = rel_diff(said["base"], shown["value"])
                        if best is None or diff < best[0]:
                            best = (diff, vis, chart, shown)
            if best is None:
                n_skipped += 1
                continue
            diff, vis, chart, shown = best
            record = {
                "speech_text": text,
                "speech_t_sec": t_said,
                "said": {"value": said["value"], "base": said["base"],
                         "unit": said["unit"], "raw": said["raw"]},
                "shown": {"value": round(shown["value"], 6),
                          "label": shown["label"],
                          "chart": str(chart.get("title", ""))},
                "rel_diff": round(diff, 6),
                "chart_t_sec": float(vis.get("t_sec", 0.0) or 0.0),
                "frame": str(vis.get("frame", "?")),
            }
            if diff <= tolerance:
                matches.append(record)
            else:
                mismatches.append(record)
                events.append({
                    "type": "visual.mismatch",
                    "speech_text": text,
                    "speech_t_sec": t_said,
                    "said": record["said"],
                    "shown": record["shown"],
                    "rel_diff": record["rel_diff"],
                    "chart_t_sec": record["chart_t_sec"],
                    "frame": record["frame"],
                })
        cue = _has_trend_cue(text)
        if cue:
            for vis in in_window:
                for chart in (vis.get("charts") or []):
                    if not isinstance(chart, dict) or not chart.get("truncated_axis"):
                        continue
                    flag = {
                        "type": "visual.misleading_axis",
                        "chart": str(chart.get("title", "")),
                        "frame": str(vis.get("frame", "?")),
                        "chart_t_sec": float(vis.get("t_sec", 0.0) or 0.0),
                        "speech_text": text,
                        "speech_t_sec": t_said,
                        "cue": cue,
                        "reason": "truncated_axis chart + trend/magnitude assertion",
                    }
                    if flag not in flags:
                        flags.append(flag)
                        events.append(dict(flag))
    return {
        "matches": matches,
        "mismatches": mismatches,
        "flags": flags,
        "events": events,
        "stats": {
            "n_speech_numbers": n_numbers,
            "n_match": len(matches),
            "n_mismatch": len(mismatches),
            "n_skipped_no_chart": n_skipped,
            "n_flags": len(flags),
        },
    }


async def publish_events(events: list[dict], bus) -> list[dict]:
    """Publish xcheck events to an ``EventBus`` (caller-owned bus)."""
    published = []
    for ev in events:
        published.append(await bus.publish(dict(ev)))
    return published
