"""Meta Responses API client: live-web counter-evidence for falsify.

Minimal client for `POST {base_url}/responses` with
`tools=[{"type": "web_search"}]`. Used ONLY by the advisory web
counter-evidence pass in `pipeline/falsify.py`: the model searches the
live web for counter-evidence to the draft verdict, and DETERMINISTIC
CODE validates every `url_citation` annotation before anything is
surfaced. Model proposes, code disposes — extended to retrieval itself.

Conventions mirror `meta.py` (MetaClient): `MODEL_API_KEY` /
`MODEL_API_BASE_URL` / `MODEL_API_MODEL` env, `.env` loading, 120 s
timeout, up to 3 retries on transient failures, per-call `last_usage` +
`last_usage_seq`. The key is only ever sent in the `Authorization`
header: it is never logged, never echoed in errors, and excluded from
`repr`. The request uses low reasoning effort and a bounded output.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .meta import DEFAULT_BASE_URL, DEFAULT_MODEL, PROMPT_CACHE_KEY

log = logging.getLogger(__name__)

RESPONSES_PATH = "/responses"

# Safety cap: at most this many citations survive validation per call.
MAX_CITATIONS = 5

# Pause between transient-failure retries (tests monkeypatch this to 0).
RETRY_DELAY_S = 1.0

# Garbage hosts: citing one of these is never evidence. Search-result
# pages (a URL to a search is not a source), opaque shorteners, and
# RFC test domains. Matching is exact-or-subdomain, case-insensitive.
# Social/news hosts are deliberately NOT blocked: an official post or
# article can be legitimate counter-evidence; https + quoted-span
# validation still applies to every citation.
BLOCKED_DOMAINS = frozenset({
    "google.com",
    "bing.com",
    "yahoo.com",
    "baidu.com",
    "duckduckgo.com",
    "yandex.com",
    "ask.com",
    "bit.ly",
    "t.co",
    "tinyurl.com",
    "example.com",
    "example.org",
    "example.net",
})


@dataclass(frozen=True)
class WebCitation:
    """One code-validated live-web citation."""

    url: str
    title: str
    span: str  # quoted text the citation annotates (non-empty)
    domain: str


@dataclass(frozen=True)
class WebCandidate:
    """One raw web_search result the model retrieved (F2-A honesty).

    Candidates reuse URL hygiene but carry NO span requirement and are
    NEVER evidence: `cited` marks the ones the model also cited (also
    present in `parse_url_citations` output). The falsify stage lists
    uncited candidates as minor doubt so the report shows what was
    considered, not just what was cited.
    """

    url: str
    title: str
    cited: bool


@dataclass(frozen=True)
class CounterEvidence:
    """Parsed result of one web counter-evidence call."""

    text: str
    citations: list[WebCitation] = field(default_factory=list)
    candidates: list[WebCandidate] = field(default_factory=list)
    # V3 spike P4: status=="incomplete" (e.g. max_output_tokens) means a
    # zero-citation run is UNINFORMATIVE, not genuine no-counter-evidence.
    truncated: bool = False


def _iter_text_parts(response: object):
    """Yield (text, annotations) for each output_text part.

    Responses API shape: `output[]` items of `type: "message"` carry a
    `content[]` list whose `output_text` parts hold `text` plus
    `annotations[]` (incl. `url_citation`). Anything else (reasoning
    items, refusal parts, malformed entries) is skipped. Never raises
    on model-shaped input.
    """
    if not isinstance(response, dict):
        return
    output = response.get("output")
    if not isinstance(output, list):
        return
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "output_text":
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            annotations = part.get("annotations")
            yield text, annotations if isinstance(annotations, list) else []


def response_text(response: object) -> str:
    """Best-effort concatenation of all output text parts."""
    try:
        return "\n".join(t for t, _ in _iter_text_parts(response)).strip()
    except Exception:  # defensive: parsing must never crash the stage
        log.debug("response_text failed on malformed payload", exc_info=True)
        return ""


def _annotation_span(text: str, annotation: dict) -> str:
    """Recover the quoted span for one annotation.

    Primary source is the `start_index`/`end_index` slice into the
    part text; a few defensive alternates cover providers that echo
    the quote inline. Returns "" when nothing usable is found.
    """
    start = annotation.get("start_index")
    end = annotation.get("end_index")
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and not isinstance(start, bool)
        and not isinstance(end, bool)
        and 0 <= start < end <= len(text)
    ):
        return text[start:end]
    for key in ("quoted_text", "quote", "text"):
        candidate = annotation.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _host_blocked(host: str) -> bool:
    return any(host == blocked or host.endswith("." + blocked)
               for blocked in BLOCKED_DOMAINS)


# Minimum quoted-span length, unless the span carries a digit (short
# counter-figures like "7.4%" are the core case). Single chars and
# short digit-less words ("Mayotte") are noise, not quotes.
MIN_SPAN_CHARS = 8


def _validate_url(url: object) -> tuple[str, str] | None:
    """URL hygiene shared by citations and search candidates.

    Returns (clean_url, host) when the URL is an https URL with a real
    dotted non-IP host outside the garbage blocklist; None otherwise.
    Span rules live in `validate_citation`, NOT here: candidates reuse
    URL hygiene with no span requirement.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or " " in host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal: good
    else:
        return None  # bare IP literal: no attributable source
    if _host_blocked(host):
        return None
    return url.strip(), host


def validate_citation(url: object, span: object, title: object = "") -> WebCitation | None:
    """Validate one candidate citation. Returns None when rejected.

    Rules (deterministic, no model judgment): URL must be an https URL
    with a real dotted non-IP host outside the garbage blocklist; the
    quoted span must be non-empty and either carry a digit or reach
    MIN_SPAN_CHARS. Rejection is silent by design — callers count
    survivors.
    """
    if not isinstance(span, str) or not span.strip():
        return None
    clean = span.strip()
    if len(clean) < MIN_SPAN_CHARS and not any(ch.isdigit() for ch in clean):
        return None
    checked = _validate_url(url)
    if checked is None:
        return None
    clean_url, host = checked
    return WebCitation(
        url=clean_url,
        title=title if isinstance(title, str) else "",
        span=span.strip(),
        domain=host,
    )


# Obvious official sources (statistics agencies, central banks,
# multilaterals). Used ONLY for stable authoritative-first ordering
# before caps — never for blocking. A junk domain still surfaces when
# it is all there is; official sources win ties. This is disclosed
# selection-ordering, not an endorsement ranking.
AUTHORITATIVE_DOMAINS = frozenset({
    "worldbank.org",
    "data.worldbank.org",
    "bea.gov",
    "bls.gov",
    "census.gov",
    "insee.fr",
    "destatis.de",
    "ons.gov.uk",
    "stat.go.jp",
    "oecd.org",
    "imf.org",
    "europa.eu",
    "federalreserve.gov",
    "openalex.org",
    "api.openalex.org",
})


def is_authoritative_host(host: object) -> bool:
    """True for obvious official sources (exact or subdomain match)."""
    if not isinstance(host, str) or not host:
        return False
    host = host.lower().rstrip(".")
    return any(host == auth or host.endswith("." + auth)
               for auth in AUTHORITATIVE_DOMAINS)


def parse_url_citations(
    response: object,
    max_citations: int = MAX_CITATIONS,
) -> list[WebCitation]:
    """Extract + validate every `url_citation` annotation.

    Only `type: "url_citation"` annotations are considered; each is
    validated by `validate_citation` and exact duplicates are dropped.
    All survivors collect first, then stable-sort authoritative-first,
    THEN the cap applies — so junk early in model order can never crowd
    out an official source. Never raises on model-shaped input —
    malformed payloads yield [].
    """
    survivors: list[WebCitation] = []
    seen: set[tuple[str, str]] = set()
    try:
        parts = list(_iter_text_parts(response))
    except Exception:
        log.debug("parse_url_citations failed on malformed payload",
                  exc_info=True)
        return []
    for text, annotations in parts:
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            if annotation.get("type") != "url_citation":
                continue
            try:
                citation = validate_citation(
                    annotation.get("url"),
                    _annotation_span(text, annotation),
                    annotation.get("title", ""),
                )
            except Exception:  # one bad annotation never kills the pass
                log.debug("citation validation failed", exc_info=True)
                continue
            if citation is None:
                continue
            key = (citation.url, citation.span)
            if key in seen:
                continue
            seen.add(key)
            survivors.append(citation)
    survivors.sort(key=lambda c: not is_authoritative_host(c.domain))
    return survivors[:max_citations]


def parse_search_candidates(response: object) -> list[WebCandidate]:
    """Extract raw web_search results as candidates (F2-A honesty).

    Reads `output[]` items of `type: "web_search_call"` and their
    `results[]` entries ({title, url}); each entry passing URL hygiene
    becomes a WebCandidate whose `cited` flag is True when the URL is
    also in `parse_url_citations` output. There is NO span requirement
    and NO survivor cap here (the falsify stage bounds what it lists
    but audits the full considered count). Never raises on
    model-shaped input — missing/non-list results yield [].
    """
    try:
        cited_urls = {c.url for c in parse_url_citations(response)}
    except Exception:
        log.debug("parse_search_candidates cited-set failed",
                  exc_info=True)
        cited_urls = set()
    candidates: list[WebCandidate] = []
    seen: set[str] = set()
    try:
        if not isinstance(response, dict):
            return []
        output = response.get("output")
        if not isinstance(output, list):
            return []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "web_search_call":
                continue
            results = item.get("results")
            if not isinstance(results, list):
                continue
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                try:
                    checked = _validate_url(entry.get("url"))
                except Exception:  # one bad entry never kills the pass
                    log.debug("candidate URL check failed", exc_info=True)
                    continue
                if checked is None:
                    continue
                clean_url, _host = checked
                if clean_url in seen:
                    continue
                seen.add(clean_url)
                title = entry.get("title")
                candidates.append(WebCandidate(
                    url=clean_url,
                    title=title if isinstance(title, str) else "",
                    cited=clean_url in cited_urls,
                ))
    except Exception:
        log.debug("parse_search_candidates failed on malformed payload",
                  exc_info=True)
        return []
    return candidates


def _responses_usage_to_dict(usage: object) -> dict[str, int | None] | None:
    """Flatten a Responses-API usage object to MetaClient-style keys.

    Responses shape: {"input_tokens", "output_tokens", "total_tokens",
    "output_tokens_details": {"reasoning_tokens"}}. Mapped onto the
    prompt/completion/reasoning/total keys so stage usage stays uniform.
    cached_tokens (F2-B) comes from input_tokens_details and defaults to
    0 when the provider omits it.
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    input_details = usage.get("input_tokens_details")
    cached = input_details.get("cached_tokens") if isinstance(input_details, dict) else None
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "reasoning_tokens": reasoning,
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": cached if isinstance(cached, int) else 0,
    }


def _is_transient(exc: Exception) -> bool:
    """Retry predicate: timeouts, connects, 429s and 5xx only."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


def _httpx_transport(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """Default transport: one POST, raise on HTTP errors. No logging here."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


class ResponsesClient:
    """Minimal Responses-API client with web_search + citation parsing.

    `transport(url, headers, payload, timeout) -> dict` is injectable
    for offline tests; the default posts with httpx. Only the transport
    ever sees the API key (in the Authorization header).
    """

    name = "meta-responses-web-search"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        transport=None,
    ) -> None:
        try:  # honor a local .env without requiring it (meta.py convention)
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        self.api_key = api_key or os.environ.get("MODEL_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("MODEL_API_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = model or os.environ.get("MODEL_API_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._transport = transport or _httpx_transport
        # Same convention as MetaClient: usage from the most recent call
        # that got a response + a monotonic counter for stage attribution.
        self.last_usage: dict[str, int | None] | None = None
        self.last_usage_seq: int = 0

    def __repr__(self) -> str:  # the key must never appear in logs/traces
        return (
            f"ResponsesClient(model={self.model!r}, "
            f"base_url={self.base_url!r}, available={self.available})"
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search_counter_evidence(
        self,
        user_text: str,
        *,
        instructions: str,
        max_output_tokens: int = 2000,
    ) -> CounterEvidence:
        """One web_search call hunting counter-evidence. Raises on failure.

        Single logical model call (transport retries on transient
        failures only), low reasoning effort, bounded output. HTTP /
        JSON / schema failures raise — the falsify stage degrades them
        all to skip. Usage is recorded whenever a response arrives.
        """
        if not self.available:
            raise RuntimeError(
                "MODEL_API_KEY not configured; ResponsesClient unavailable."
            )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": user_text,
            "tools": [{"type": "web_search"}],
            "include": ["web_search_call.results"],
            "prompt_cache_key": PROMPT_CACHE_KEY,  # F2-B: accounting only
            "reasoning": {"effort": "low"},
            "max_output_tokens": max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = self._post_with_retry(
            f"{self.base_url}{RESPONSES_PATH}", headers, payload
        )
        self.last_usage = _responses_usage_to_dict(data.get("usage"))
        self.last_usage_seq += 1
        return CounterEvidence(
            text=response_text(data),
            citations=parse_url_citations(data),
            candidates=parse_search_candidates(data),
            truncated=data.get("status") == "incomplete",
        )

    def _post_with_retry(
        self, url: str, headers: dict, payload: dict
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1 + max(0, self.max_retries)):
            try:
                data = self._transport(url, headers, payload, self.timeout_s)
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if _is_transient(exc) and attempt < self.max_retries:
                    # Type only: messages could echo URLs, never the key
                    # (it travels in headers, which httpx never renders
                    # into exceptions), but type-only is safest.
                    log.warning(
                        "Responses API transient failure (attempt %d): %s",
                        attempt + 1, type(exc).__name__,
                    )
                    time.sleep(RETRY_DELAY_S)
                    continue
                raise
            if not isinstance(data, dict):
                raise RuntimeError(
                    "Responses API returned non-object JSON: "
                    f"{type(data).__name__}"
                )
            return data
        assert last_exc is not None  # loop always runs at least once
        raise last_exc
