"""Image intake: data-URL vision call -> anchored claims ({region}).

Mechanism verified live pre-V3 (data-URL accepted, schema-constrained
JSON out — see docs/LIVE_MODEL.md). Every surviving claim carries
``{region + resolution note + verbatim span}``; region is a qualitative
vocabulary (vision models cannot emit reliable bboxes, so free floats
are refused by construction).

``max_tokens`` is floored at 2000: vision + reasoning on small budgets
truncates into empty content (observed on probes). Reasoning effort is
"low" for the same reason.

The vision client is injected (``complete_vision(system, user_text,
image_data_urls, max_tokens, json_schema) -> dict``); offline tests use
doubles. :class:`OpenAIVisionAdapter` wraps a MetaClient for live calls
without touching models/.
"""

from __future__ import annotations

import base64
import logging
import struct

from . import (
    MAX_CLAIMS_PER_IMAGE,
    IngestedClaim,
    IngestError,
    IngestResult,
    validate_anchors,
)

log = logging.getLogger(__name__)

IMAGE_SYSTEM = """You read a chart, screenshot or photo for evidence analysis.
Return ONLY a JSON object with exactly this key:
{"claims": [{"text": "one self-contained factual claim about what the image shows",
             "span": "short phrase copied EXACTLY from the claim text",
             "region": "full|top|bottom|left|right|center|top-left|top-right|bottom-left|bottom-right"}]}
Rules: describe ONLY what is visibly rendered (values, labels, trends);
"region" names where in the image the evidence appears ("full" when it
spans the frame); "span" must be a verbatim substring of "text"; NEVER
read digits you cannot resolve — omit the claim instead of guessing;
emit [] when nothing checkable is visible."""

IMAGE_SCHEMA = {
    "name": "image_claims",
    "schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "span": {"type": "string"},
                        "region": {
                            "type": "string",
                            "enum": ["full", "top", "bottom", "left", "right",
                                     "center", "top-left", "top-right",
                                     "bottom-left", "bottom-right"],
                        },
                    },
                    "required": ["text", "span", "region"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
}

# Floor: vision + reasoning truncates below this (probe-verified class).
MIN_VISION_TOKENS = 2000
VISION_REASONING_EFFORT = "low"

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
    raise IngestError("unsupported image format (need PNG/JPEG/GIF/WebP)")


def png_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Read WxH from a PNG IHDR chunk (stdlib ``struct``)."""
    if not image_bytes.startswith(_PNG_SIGNATURE) or len(image_bytes) < 33:
        raise IngestError("not a PNG (resolution note needs PNG IHDR)")
    if image_bytes[12:16] != b"IHDR":
        raise IngestError("malformed PNG (first chunk is not IHDR)")
    width, height = struct.unpack(">II", image_bytes[16:24])
    if width <= 0 or height <= 0:
        raise IngestError("malformed PNG (non-positive dimensions)")
    return width, height


def resolution_note(image_bytes: bytes) -> str:
    """Human + machine resolution note, e.g. ``"640x480 px"``.

    PNG dimensions come from IHDR; other formats report their MIME with
    an explicit ``resolution-unparsed`` marker (never a guessed size).
    """
    mime = sniff_mime(image_bytes)
    if mime == "image/png":
        width, height = png_dimensions(image_bytes)
        return f"{width}x{height} px"
    return f"{mime} resolution-unparsed"


def build_data_url(image_bytes: bytes) -> str:
    """Encode image bytes as a ``data:<mime>;base64,`` URL for vision."""
    mime = sniff_mime(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class OpenAIVisionAdapter:
    """Live vision transport over a MetaClient's OpenAI-compatible SDK.

    Wraps — never modifies — the client: builds the ``image_url`` content
    parts, enforces the token floor, and records usage on the wrapped
    client's ``last_usage``/``last_usage_seq`` (pipeline convention).
    """

    name = "meta-vision-adapter"

    def __init__(self, meta_client) -> None:
        self._client = meta_client

    @property
    def available(self) -> bool:
        return bool(getattr(self._client, "available", False))

    def complete_vision(
        self,
        system: str,
        user_text: str,
        image_data_urls: list[str],
        max_tokens: int = MIN_VISION_TOKENS,
        json_schema: dict | None = None,
    ) -> dict:
        import json as _json

        from ..models.meta import PROMPT_CACHE_KEY, _usage_to_dict

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
        sdk = self._client._get_client()
        resp = sdk.chat.completions.create(
            model=self._client.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            response_format=response_format,
            temperature=0,
            max_tokens=max(max_tokens, MIN_VISION_TOKENS),
            prompt_cache_key=PROMPT_CACHE_KEY,
            reasoning_effort=VISION_REASONING_EFFORT,
        )
        self._client.last_usage = _usage_to_dict(getattr(resp, "usage", None))
        self._client.last_usage_seq += 1
        content_out = resp.choices[0].message.content
        if not content_out or not content_out.strip():
            raise RuntimeError(
                "Vision model returned empty content "
                f"(finish_reason={resp.choices[0].finish_reason}); "
                "likely truncated during reasoning."
            )
        return _json.loads(content_out)


def describe_image(
    image_bytes: bytes,
    source: str,
    vision=None,
    max_tokens: int = MIN_VISION_TOKENS,
) -> IngestResult:
    """Draft + validate anchored claims for one image.

    Requires a vision client (no heuristic fallback exists for pixels:
    code cannot see). Without one, returns zero claims with an explicit
    uncertainty — never guessed content. Surviving claims carry
    ``{region + resolution note + span}``.
    """
    uncertainties: list[str] = []
    try:
        note = resolution_note(image_bytes)
        data_url = build_data_url(image_bytes)
    except IngestError as exc:
        return IngestResult(uncertainties=[f"unreadable image: {exc}"])
    if "resolution-unparsed" in note:
        uncertainties.append(f"resolution note degraded: {note}")
    if vision is None or not getattr(vision, "available", False):
        uncertainties.append("no vision client: image not read (0 claims, "
                             "no guessing)")
        return IngestResult(uncertainties=uncertainties)
    if max_tokens < MIN_VISION_TOKENS:
        uncertainties.append(
            f"max_tokens raised {max_tokens}->{MIN_VISION_TOKENS} "
            "(vision floor)"
        )
    try:
        data = vision.complete_vision(
            IMAGE_SYSTEM,
            f"Image {source} ({note}). List every checkable factual claim "
            "it shows.",
            [data_url],
            max_tokens=max(max_tokens, MIN_VISION_TOKENS),
            json_schema=IMAGE_SCHEMA,
        )
    except Exception as exc:
        log.warning("Vision drafting failed for %s: %r", source, exc)
        uncertainties.append(f"vision call failed: {type(exc).__name__}")
        return IngestResult(uncertainties=uncertainties)
    items = data.get("claims", []) if isinstance(data, dict) else []
    draft: list[IngestedClaim] = []
    for item in items[:MAX_CLAIMS_PER_IMAGE]:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        text = " ".join(text.split())
        span = item.get("span")
        span = span.strip() if isinstance(span, str) else ""
        if span not in text:
            span = ""  # validator discards: non-verbatim span
        draft.append(IngestedClaim(
            text=text,
            anchor={"region": item.get("region"), "resolution": note,
                    "span": span},
            source=source,
            actor=f"model:{getattr(vision, 'name', 'vision')}",
        ))
    return validate_anchors(draft, uncertainties)
