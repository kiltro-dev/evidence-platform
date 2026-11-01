"""PDF intake: stdlib text-per-page -> anchored claims ({page}).

No third-party PDF dependency: a minimal parser reads content streams
(raw or FlateDecode via stdlib ``zlib``), maps them to pages through
the document's own ``/Page`` objects (with an honest sequential
fallback), and extracts ``Tj``/``TJ`` show-strings. It covers
text-based PDFs (including our code-generated fixtures); scanned pages
need OCR, which this module does NOT do (see docs/INGEST.md limits).

Claim drafting is LLM-first (:func:`structure_pdf_claims` with a
schema-constrained ``complete_json`` call per page) with a heuristic
fallback (sentence split, span = the sentence itself). Model spans are
kept only when verbatim in the page text (extract.py convention);
:func:`validate_anchors` then enforces the {page + span} anchor.
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass

from . import (
    MAX_CLAIMS_PER_PAGE,
    IngestedClaim,
    IngestError,
    IngestResult,
    validate_anchors,
)

log = logging.getLogger(__name__)

PDF_SYSTEM = """You extract factual claims from one PDF page for evidence analysis.
Return ONLY a JSON object with exactly this key:
{"claims": [{"text": "one self-contained factual claim",
             "span": "short phrase copied EXACTLY from the claim text"}]}
Rules: one claim per distinct fact; keep numbers/units exactly as written;
"span" must be a verbatim substring of "text" (a quoted fragment of it);
skip headers, page numbers and boilerplate; emit [] when the page states
no factual claim."""

PDF_SCHEMA = {
    "name": "pdf_page_claims",
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
                    },
                    "required": ["text", "span"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
}

# One structuring call per page; pages are short, 1500 is ample and
# matches the abstracts budget (LIVE_MODEL: never below ~1500).
PDF_MAX_TOKENS = 1500
PDF_REASONING_EFFORT = "low"


@dataclass(frozen=True)
class PageText:
    """Extracted text of one page (1-based `page`)."""

    page: int
    text: str


# ---------------------------------------------------------------------------
# Minimal stdlib PDF text extraction
# ---------------------------------------------------------------------------

_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.DOTALL)
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
_CONTENTS_REF_RE = re.compile(rb"/Contents\s*(\[.*?\]|(\d+)\s+(\d+)\s+R)", re.DOTALL)
_REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R")
_TJ_OP_RE = re.compile(
    rb"(?P<str>\((?:\\.|\\\r?\n|[^\\()])*\)|<[0-9A-Fa-f\s]*>)\s*Tj"
    rb"|(?P<arr>\[.*?)\]\s*TJ",
    re.DOTALL,
)
_TJ_ARR_STR_RE = re.compile(rb"\((?:\\.|\\\r?\n|[^\\()])*\)|<[0-9A-Fa-f\s]*>")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
            "(": "(", ")": ")", "\\": "\\"}


def _pdf_string_to_text(raw: bytes) -> str:
    """Decode one PDF literal `(..)` or hex `<..>` string (WinAnsi-ish)."""
    if raw.startswith(b"<"):
        hexpart = re.sub(rb"\s+", b"", raw[1:-1])
        if len(hexpart) % 2:
            hexpart += b"0"
        try:
            data = bytes.fromhex(hexpart.decode("ascii"))
        except ValueError:
            return ""
        return data.decode("latin-1")
    out: list[str] = []
    i = 1  # skip opening paren
    body = raw[:-1] if raw.endswith(b")") else raw[1:]
    while i < len(body):
        ch = body[i:i + 1]
        if ch != b"\\":
            out.append(ch.decode("latin-1"))
            i += 1
            continue
        nxt = body[i + 1:i + 2]
        if not nxt:
            break
        c = nxt.decode("latin-1")
        if c in _ESCAPES:
            out.append(_ESCAPES[c])
            i += 2
        elif c in "\r\n":  # line continuation: skip EOL
            i += 3 if body[i + 1:i + 3] == b"\r\n" else 2
        elif c.isdigit():  # octal escape, up to 3 digits
            octal = body[i + 1:i + 4].decode("latin-1")
            m = re.match(r"[0-7]{1,3}", octal)
            digits = m.group(0) if m else ""
            out.append(chr(int(digits, 8)) if digits else "")
            i += 1 + len(digits)
        else:
            out.append(c)
            i += 2
    return "".join(out)


def _stream_show_text(stream: bytes) -> str:
    """Concatenate every Tj/TJ shown string in one content stream."""
    shown: list[str] = []
    for match in _TJ_OP_RE.finditer(stream):
        if match.group("str"):
            shown.append(_pdf_string_to_text(match.group("str")))
        else:
            arr = match.group("arr") or b""
            for part in _TJ_ARR_STR_RE.findall(arr):
                shown.append(_pdf_string_to_text(part))
    return " ".join(s for s in shown if s).strip()


def _decode_stream(obj_body: bytes) -> bytes | None:
    """Return the decoded stream payload, or None when not a stream."""
    match = _STREAM_RE.search(obj_body)
    if not match:
        return None
    payload = match.group(1)
    header = obj_body[:match.start()]
    if b"/FlateDecode" in header:
        try:
            return zlib.decompress(payload)
        except zlib.error:
            log.warning("FlateDecode stream failed to decompress; using raw bytes")
            return payload
    return payload


def extract_pages(pdf_bytes: bytes) -> tuple[list[PageText], list[str]]:
    """Extract per-page text from PDF bytes (stdlib only).

    Returns (pages, notes): pages in document order with 1-based numbers,
    notes describing parser fallbacks for the audit trail. Raises
    :class:`IngestError` on non-PDF input.
    """
    notes: list[str] = []
    if not pdf_bytes.startswith(b"%PDF-"):
        raise IngestError("not a PDF (missing %PDF- header)")
    objects: dict[tuple[int, int], bytes] = {}
    for match in _OBJ_RE.finditer(pdf_bytes):
        objects[(int(match.group(1)), int(match.group(2)))] = match.group(3)
    decoded: dict[tuple[int, int], bytes] = {}
    for key, body in objects.items():
        payload = _decode_stream(body)
        if payload is not None:
            decoded[key] = payload
    # Page objects in document order (/Type /Page but not /Pages).
    page_keys: list[tuple[int, int]] = [
        key for key, body in objects.items()
        if re.search(rb"/Type\s*/Page(?!s)", body)
    ]
    if not page_keys:
        raise IngestError("no /Page objects found")
    pages: list[PageText] = []
    sequential_fallback = False
    stream_pool = [decoded[k] for k in sorted(decoded)]
    for number, key in enumerate(page_keys, start=1):
        body = objects[key]
        refs: list[tuple[int, int]] = []
        contents = _CONTENTS_REF_RE.search(body)
        if contents:
            if contents.group(2):  # single indirect reference
                refs.append((int(contents.group(2)), int(contents.group(3))))
            else:  # array of references
                refs.extend(
                    (int(n), int(g)) for n, g in _REF_RE.findall(contents.group(1))
                )
        texts: list[str] = []
        for ref in refs:
            payload = decoded.get(ref)
            if payload is not None:
                texts.append(_stream_show_text(payload))
        if not refs or not any(texts):
            # Honest fallback: positional assignment stream[i] -> page[i].
            sequential_fallback = True
            idx = number - 1
            texts = [_stream_show_text(stream_pool[idx])] if idx < len(stream_pool) else []
        text = re.sub(r"[ \t]+", " ", " ".join(texts)).strip()
        pages.append(PageText(page=number, text=text))
    if sequential_fallback:
        notes.append("page_split_unverified: /Contents refs unresolvable, "
                     "streams assigned to pages positionally")
    if all(not p.text for p in pages):
        notes.append("no extractable text: scanned/image-only PDF needs OCR "
                     "(not supported)")
    return pages, notes


# ---------------------------------------------------------------------------
# Claim drafting: model first, heuristic fallback
# ---------------------------------------------------------------------------

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\(])")


def _heuristic_page_claims(page: PageText, source: str) -> list[IngestedClaim]:
    """Sentence-split fallback: every substantial sentence is a claim,
    anchored to its page with the sentence itself as the literal span."""
    claims: list[IngestedClaim] = []
    # Boundary needs trailing space + capital/digit: decimals ("1.8")
    # and abbreviations mid-sentence never split.
    for sentence in _SENTENCE_BOUNDARY_RE.split(page.text):
        text = " ".join(sentence.split())
        if len(text) < 12:  # skip fragments, page numbers, headers
            continue
        claims.append(IngestedClaim(
            text=text,
            anchor={"page": page.page, "span": text},
            source=source,
        ))
        if len(claims) >= MAX_CLAIMS_PER_PAGE:
            break
    return claims


def _model_span(span: object, page_text: str) -> str:
    """Model-proposed span, kept only when verbatim in the page text."""
    if not isinstance(span, str) or not span.strip():
        return ""
    candidate = span.strip()
    return candidate if candidate in page_text else ""


def structure_pdf_claims(
    pdf_bytes: bytes,
    source: str,
    llm=None,
) -> IngestResult:
    """Extract text per page, then draft + validate anchored claims.

    With an available LLM: one schema-constrained ``complete_json`` call
    per page (model drafts, code keeps only spans verbatim in the page).
    Without: heuristic sentence split. Both paths end in
    :func:`validate_anchors` ({page + span} required).
    """
    pages, notes = extract_pages(pdf_bytes)
    uncertainties = list(notes)
    draft: list[IngestedClaim] = []
    use_model = llm is not None and getattr(llm, "available", False)
    for page in pages:
        if not page.text:
            continue
        if use_model:
            try:
                data = llm.complete_json(
                    PDF_SYSTEM,
                    f"Page {page.page} text:\n{page.text}",
                    max_tokens=PDF_MAX_TOKENS,
                    reasoning_effort=PDF_REASONING_EFFORT,
                    json_schema=PDF_SCHEMA,
                )
                items = data.get("claims", []) if isinstance(data, dict) else []
                kept = 0
                for item in items[:MAX_CLAIMS_PER_PAGE]:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    text = " ".join(text.split())
                    span = _model_span(item.get("span"), page.text)
                    if not span or span not in text:
                        # Span must ALSO be verbatim in the claim text;
                        # the page-verbatim fragment the model quoted is
                        # the honest fallback only when the claim repeats it.
                        span = ""
                    draft.append(IngestedClaim(
                        text=text,
                        anchor={"page": page.page, "span": span},
                        source=source,
                        actor=f"model:{getattr(llm, 'name', 'model')}",
                    ))
                    kept += 1
                continue  # model path handled this page (even if [])
            except Exception as exc:
                log.warning("PDF model drafting failed on page %d: %r",
                            page.page, exc)
                uncertainties.append(
                    f"page {page.page}: model drafting failed, "
                    "heuristic fallback used"
                )
        draft.extend(_heuristic_page_claims(page, source))
    return validate_anchors(draft, uncertainties)
