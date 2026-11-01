"""M3 vista read: vision read of one frame, strict schema (Atlas Live).

``read_frame`` sends the frame PNG to a Muse vision chat (image content
part) under a strict decoding schema and validates the reply in code.
Every chart/table carries its ``{"frame", "t_sec"}`` citation, so each
visual affirmation downstream is traceable to the exact frame.

The vision client is injected (``complete_vision(system, user_text,
image_data_urls, max_tokens, json_schema) -> dict``) — offline tests use
doubles. :func:`make_live_vision` builds the live transport (OpenAI SDK
against the Meta Model API, ``reasoning_effort="minimal"``); without a
key it returns an unavailable client and reads degrade to explicit
uncertainties — code never guesses pixels.

Token floor: vision calls use at least 2000 max_tokens (probe-verified
class: vision + reasoning truncates into empty content below that).
"""

from __future__ import annotations

import base64
import logging
import os

log = logging.getLogger(__name__)

READ_MAX_TOKENS = 2000
READ_REASONING_EFFORT = "minimal"
MAX_CHARTS = 4
MAX_VALUES = 24
MAX_TABLES = 4

READ_SYSTEM = """You read one presentation frame (slide + optional chart) for fact-checking.
Return ONLY a JSON object: {"on_screen_text": "...", "tables": [...], "charts": [...]}
Rules: "on_screen_text" is the visible slide text (title, bullets, footer), transcribed verbatim.
"tables" lists visible data tables: [{"caption": "...", "headers": [...], "rows": [[...]]}].
"charts" lists visible charts: [{"kind": "bar|line|pie|scatter|area|other", "title": "...",
"values": [{"label": "...", "value": number}], "unit": "..."|null,
"y_axis_min": number|null, "y_axis_max": number|null, "truncated_axis": true|false, "notes": "..."}].
"values" holds ONE entry per bar/point with the EXACT number rendered next to it (axis labels excluded);
"unit" copies the value-axis unit as shown (e.g. "percent", "trillion dollars", "quarterly growth %").
"truncated_axis" is true when the value axis does NOT start at zero OR the scale is broken, discontinuous,
split-panel, or otherwise distorted (axis breaks, zigzag glyphs, diverging panels); false for honest zero-based axes.
"notes" records anything odd: broken scale, cherry-picked dates, title-vs-data mismatch.
Describe ONLY what is visibly rendered; NEVER invent digits you cannot resolve — omit the value instead.
Emit [] for tables/charts when none is visible."""

READ_SCHEMA = {
    "name": "vista_frame_read",
    "schema": {
        "type": "object",
        "properties": {
            "on_screen_text": {"type": "string"},
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "caption": {"type": "string"},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "rows": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "required": ["caption", "headers", "rows"],
                    "additionalProperties": False,
                },
            },
            "charts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["bar", "line", "pie", "scatter", "area", "other"],
                        },
                        "title": {"type": "string"},
                        "values": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "value": {"type": "number"},
                                },
                                "required": ["label", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "unit": {"type": ["string", "null"]},
                        "y_axis_min": {"type": ["number", "null"]},
                        "y_axis_max": {"type": ["number", "null"]},
                        "truncated_axis": {"type": "boolean"},
                        "notes": {"type": "string"},
                    },
                    "required": ["kind", "title", "values", "unit",
                                 "y_axis_min", "y_axis_max",
                                 "truncated_axis", "notes"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["on_screen_text", "tables", "charts"],
        "additionalProperties": False,
    },
}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def sniff_mime(image_bytes: bytes) -> str:
    """Best-effort image MIME sniff (PNG/JPEG/GIF/WebP), else error."""
    if image_bytes.startswith(_PNG_SIGNATURE):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported image format (need PNG/JPEG/GIF/WebP)")


def build_data_url(image_bytes: bytes) -> str:
    """Encode image bytes as a ``data:<mime>;base64,`` URL for vision."""
    mime = sniff_mime(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _as_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def validate_read(data, frame: dict) -> dict:
    """Coerce a raw vision reply into the strict read contract.

    Never raises on model-shaped input: malformed items are dropped and
    counted under ``uncertainties``. Every surviving chart/table cites
    its ``{"frame", "t_sec"}``.
    """
    cite = {"frame": str(frame.get("path", "?")),
            "t_sec": float(frame.get("t_sec", 0.0) or 0.0)}
    out = {
        "frame": cite["frame"],
        "t_sec": cite["t_sec"],
        "slide": frame.get("slide"),
        "on_screen_text": "",
        "tables": [],
        "charts": [],
        "model": None,
        "uncertainties": [],
    }
    if not isinstance(data, dict):
        out["uncertainties"].append("model returned non-object; empty read")
        return out
    text = data.get("on_screen_text")
    out["on_screen_text"] = " ".join(str(text).split()) if isinstance(text, str) else ""
    if not isinstance(text, str):
        out["uncertainties"].append("on_screen_text missing/non-string")

    tables = data.get("tables")
    if isinstance(tables, list):
        for item in tables[:MAX_TABLES]:
            if not isinstance(item, dict):
                continue
            headers = item.get("headers")
            rows = item.get("rows")
            out["tables"].append({
                "caption": str(item.get("caption", "")),
                "headers": [str(h) for h in headers] if isinstance(headers, list) else [],
                "rows": [[str(c) for c in r] if isinstance(r, list) else []
                         for r in rows] if isinstance(rows, list) else [],
                **cite,
            })
    else:
        out["uncertainties"].append("tables missing/non-list")

    charts = data.get("charts")
    dropped = 0
    if isinstance(charts, list):
        for item in charts[:MAX_CHARTS]:
            if not isinstance(item, dict):
                dropped += 1
                continue
            kind = item.get("kind")
            values = item.get("values")
            clean_values = []
            if isinstance(values, list):
                for v in values[:MAX_VALUES]:
                    if not isinstance(v, dict):
                        continue
                    num = _as_float(v.get("value"))
                    if num is None:
                        continue
                    clean_values.append({"label": str(v.get("label", "")), "value": num})
            unit = item.get("unit")
            out["charts"].append({
                "kind": kind if kind in (
                    "bar", "line", "pie", "scatter", "area", "other") else "other",
                "title": str(item.get("title", "")),
                "values": clean_values,
                "unit": unit if isinstance(unit, str) else None,
                "y_axis_min": _as_float(item.get("y_axis_min")),
                "y_axis_max": _as_float(item.get("y_axis_max")),
                "truncated_axis": bool(item.get("truncated_axis", False)),
                "notes": str(item.get("notes", "")),
                **cite,
            })
    else:
        out["uncertainties"].append("charts missing/non-list")
    if dropped:
        out["uncertainties"].append(f"dropped {dropped} non-object chart(s)")
    return out


def read_frame(
    frame: dict,
    vision=None,
    max_tokens: int = READ_MAX_TOKENS,
) -> dict:
    """Vision-read one ``{"path", "t_sec", "slide"?}`` frame.

    Without an available vision client the read is empty with an explicit
    uncertainty (no guessing). Model failures degrade the same way.
    """
    if vision is None or not getattr(vision, "available", False):
        empty = validate_read({}, frame)
        empty["uncertainties"].append("no vision client: frame not read (0 claims)")
        return empty
    try:
        with open(frame["path"], "rb") as f:
            image_bytes = f.read()
        data_url = build_data_url(image_bytes)
    except (OSError, ValueError) as exc:
        empty = validate_read({}, frame)
        empty["uncertainties"].append(f"unreadable frame: {exc}")
        return empty
    label = f"{frame.get('path', '?')} at t={float(frame.get('t_sec', 0.0)):0.2f}s"
    if frame.get("slide") is not None:
        label += f" (slide {frame['slide']})"
    try:
        data = vision.complete_vision(
            READ_SYSTEM,
            f"Frame {label} ({len(image_bytes)} bytes). Transcribe the on-screen "
            "text and every chart exactly as rendered.",
            [data_url],
            max_tokens=max(max_tokens, READ_MAX_TOKENS),
            json_schema=READ_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — vision failure degrades, never kills
        log.warning("Vision read failed for %s: %r", frame.get("path"), exc)
        empty = validate_read({}, frame)
        empty["uncertainties"].append(f"vision call failed: {type(exc).__name__}")
        return empty
    out = validate_read(data, frame)
    out["model"] = getattr(vision, "name", "vision")
    return out


def read_frames(
    frames: list[dict],
    vision=None,
    max_tokens: int = READ_MAX_TOKENS,
) -> list[dict]:
    """Read several frames with one injected vision client, in order."""
    return [read_frame(f, vision=vision, max_tokens=max_tokens) for f in frames]


class VistaVisionClient:
    """Live vision transport (OpenAI SDK -> Meta Model API).

    Mirrors the ``src/atlas/ingest/image.py`` adapter conventions
    (data-URL image part, strict json_schema, usage on ``last_usage``)
    with the M3 decoding policy: ``reasoning_effort="minimal"``.
    """

    name = "meta-vision-vista"

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
        self.base_url = base_url or os.environ.get(
            "MODEL_API_BASE_URL", "https://api.meta.ai/v1")
        self.model = model or os.environ.get("MODEL_API_MODEL", "muse-spark-1.3")
        self.timeout_s = timeout_s
        self._client = None
        self.last_usage: dict | None = None
        self.last_usage_seq: int = 0

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

    def complete_vision(
        self,
        system: str,
        user_text: str,
        image_data_urls: list[str],
        max_tokens: int = READ_MAX_TOKENS,
        json_schema: dict | None = None,
    ) -> dict:
        import json as _json

        if not self.available:
            raise RuntimeError("MODEL_API_KEY not configured; vision unavailable.")
        if json_schema is None:
            response_format: dict = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(json_schema.get("name", "output")),
                    "strict": True,
                    "schema": json_schema["schema"],
                },
            }
        content: list[dict] = [{"type": "text", "text": user_text}]
        content.extend(
            {"type": "image_url", "image_url": {"url": url}}
            for url in image_data_urls
        )
        resp = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            response_format=response_format,
            temperature=0,
            max_tokens=max(max_tokens, READ_MAX_TOKENS),
            reasoning_effort=READ_REASONING_EFFORT,
        )
        usage = getattr(resp, "usage", None)
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        self.last_usage_seq += 1
        content_out = resp.choices[0].message.content
        if not content_out or not content_out.strip():
            raise RuntimeError(
                "Vision model returned empty content "
                f"(finish_reason={resp.choices[0].finish_reason})."
            )
        return _json.loads(content_out)


def make_live_vision(**kwargs) -> VistaVisionClient:
    """Build the live vision client (unavailable when no key is set)."""
    return VistaVisionClient(**kwargs)


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost of one vision call (repo prices, fallback when unimportable)."""
    try:
        from src.atlas.agent.loop import estimate_cost

        return float(estimate_cost(prompt_tokens or 0, completion_tokens or 0))
    except Exception:
        return ((prompt_tokens or 0) * 1.25 + (completion_tokens or 0) * 4.25) / 1e6
