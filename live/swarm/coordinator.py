"""M2b swarm coordinator: parallel asyncio claim investigation.

Per claim: ESTRUCTURADOR (plan) -> deterministic grounding (World Bank
API URL + web_search) -> responsible fetch + snapshots -> RECUPERADOR
(model tool loop) -> code-validated quotes -> ``claim.evidence`` with
EvidencePackage-compat dicts (M2a ``live/evidence/package.py`` validates
the final schema). REDTEAM is deferred by default (F2-7, advisory only).

Resilience: semaphore-limited parallelism (8), 70 s deadline per claim,
per-claim token budget, graceful degradation (no network/model ->
zero packages, never invented quotes). All blocking I/O (model, fetch,
search) runs in worker threads; the event loop only orchestrates.

F2-3 latency: the deadline is enforced per ROUND (graceful abort keeps
packages + an honest note; ``asyncio.wait_for`` stays as the backstop),
and a primary verbatim quote whose figure matches the claim within the
(untouched) verifier tolerance closes the extra rounds early
(``claim.early_exit``; kill-switch ``SwarmConfig.early_exit_enabled``).

F2-7 latency: a code-refuting WB row (same F1 guards + verifier-mirrored
units/scale/year checks) also closes rounds early; grounding queries run
in parallel (cap 4); redteam is deferred (the 4->2 round cut was
reverted after a B1 falso-C); claims that do not fast-path emit
``claim.no_exit`` with the general cause (see ``no_exit_reason``).

F2-9 p95: a recuperador round that adds no packages and no new documents
(sterile re-search, measured on the deadline-70 tail) aborts the
remaining rounds (``claim.early_abort``; kill-switch
``SwarmConfig.early_abort_enabled``). Deciders are untouched: a round
that returns final, adds a package, or fetches a new document never
aborts.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from .fetch import FetchResult, fetch_post, fetch_url
from .roles import (
    ESTRUCTURADOR_SCHEMA,
    ESTRUCTURADOR_SYSTEM,
    RECUPERADOR_SCHEMA,
    RECUPERADOR_SYSTEM,
    REDTEAM_SCHEMA,
    REDTEAM_SYSTEM,
    extract_balanced_json,
    parse_tool_step,
)

log = logging.getLogger(__name__)

CLAIM_DEADLINE_S = 70.0
# F2-3: hard backstop = soft budget + slack. Rounds abort gracefully at
# the soft budget; only a single hanging call can reach the backstop.
# F2-7: soft budget 120 -> 70 (the p95<=60 goal needs the tail under 60;
# the 70s cap plus the faster path is measured in F2_LAT2.md).
CLAIM_DEADLINE_SLACK_S = 30.0
MAX_CONCURRENCY = 8
# F2-7: recuperador rounds stay 4 (the 4->2 cut enabled a premature
# citation of a rounded secondary on B-class claims — B1 falso-C in
# run2 — so it was REVERTED; redteam stays deferred, forced final kept).
MAX_TOOL_ROUNDS = 4
# F2-7: parallel grounding queries (cap; polite for keyless search).
GROUND_QUERY_WORKERS = 4
MODEL_MAX_TOKENS = 1500
MODEL_REASONING_EFFORT = "low"
# M2d (cause N3): 12k -> 20k. The 12k cap degraded ~13% of claims
# (4/31: budget exhausted at recuperador, cutting the redteam round).
# Wall-cost impact is bounded: usage still accrues per call and the
# deadline still caps wall time; this only lets the final round finish.
TOKEN_BUDGET_PER_CLAIM = 20000

# Caps for model-read excerpts (quotes stay verbatim: see _segments()).
DOC_EXCERPT_CHARS = 6000
SEARCH_TOP_K = 3
FETCH_TOP_K = 2
# F2-2: grounding fetches across the multi-query hit pool (primary-first).
GROUND_FETCH_K = 4
# F2-2: nudge the model to cite grounded docs instead of re-searching.
_PREFER_FETCHED_NUDGE = (
    "Prefer citing the already-fetched documents below; call tools only "
    "when none contains the needed figure."
)

# Fallback Muse Spark prices (USD per 1M tokens) when the agent loop
# helper is not importable; same values as src/atlas/agent/loop.py.
_FALLBACK_PRICE_PROMPT = 1.25
_FALLBACK_PRICE_COMPLETION = 4.25

_WORLD_CUES_RE = re.compile(
    r"\b(world|worldwide|global|planet|earth|world's)\b", re.IGNORECASE
)
_YEAR_RE = re.compile(r"\b(19|20|21)\d{2}\b")
_INDICATOR_RE = re.compile(r"^[A-Z0-9][A-Z0-9._]{1,30}$")
_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_TAG_SPLIT_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")

# Fallback mirror of live.evidence.package.PRIMARY_HOSTS (M2c: the package
# module is the single source of truth; source_type_for_url() below
# delegates to it whenever M2a is importable).
_PRIMARY_HOSTS = (
    "api.worldbank.org",
    "data.worldbank.org",
    "worldbank.org",
    "fred.stlouisfed.org",
    "ourworldindata.org",
    "query.wikidata.org",
    "wikidata.org",
)

_NUMBER_RE = re.compile(
    r"(?P<prefix>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>\s*(?:trillion|billion|million|thousand|tn|bn|mn|k)\b|%)?",
    re.IGNORECASE,
)
_SCALE_MULT = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "mn": 1e6,
    "billion": 1e9, "bn": 1e9,
    "trillion": 1e12, "tn": 1e12,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# lazy repo imports (coordinator works standalone; features degrade)
# ---------------------------------------------------------------------------

def _try_import(path: str, name: str) -> Any | None:
    try:
        mod = __import__(path, fromlist=[name])
        return getattr(mod, name, None)
    except Exception:
        return None


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    fn = _try_import("src.atlas.agent.loop", "estimate_cost")
    if callable(fn):
        try:
            return float(fn(prompt_tokens, completion_tokens))
        except Exception:
            pass
    return (
        prompt_tokens * _FALLBACK_PRICE_PROMPT
        + completion_tokens * _FALLBACK_PRICE_COMPLETION
    ) / 1e6


def validate_package_dict(pkg: dict) -> bool:
    """True when M2a accepts the dict (or M2a is not importable)."""
    cls = _try_import("live.evidence.package", "EvidencePackage")
    if cls is None:
        return True
    try:
        cls.from_dict(pkg)
        return True
    except Exception as exc:
        log.warning("M2a rejected package dict: %r", exc)
        return False


# ---------------------------------------------------------------------------
# grounding web search (keyless: Wikipedia API + generic web)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


def _http_get_text(url: str, timeout_s: float = 15.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "atlas-live-research"}
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        return resp.read(512 * 1024).decode("utf-8", "replace")


def _wiki_search(query: str) -> list[SearchHit]:
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search",
        "srsearch": query, "srlimit": 3,
    })
    try:
        data = json.loads(
            _http_get_text(f"https://en.wikipedia.org/w/api.php?{params}")
        )
    except Exception:
        return []
    hits: list[SearchHit] = []
    items = (data.get("query", {}) or {}).get("search", []) or []
    for item in items:
        title = str(item.get("title", "") or "")
        if not title:
            continue
        slug = urllib.parse.quote(title.replace(" ", "_"), safe="/()")
        snippet = _TAG_SPLIT_RE.sub(
            " ", str(item.get("snippet", "") or "")
        )
        hits.append(SearchHit(
            url=f"https://en.wikipedia.org/wiki/{slug}",
            title=title,
            snippet=_WS_RE.sub(" ", snippet).strip()[:300],
        ))
    return hits


_DDG_LINK_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _ddg_search(query: str) -> list[SearchHit]:
    params = urllib.parse.urlencode({"q": query})
    try:
        html = _http_get_text(
            f"https://html.duckduckgo.com/html/?{params}"
        )
    except Exception:
        return []
    hits: list[SearchHit] = []
    for match in _DDG_LINK_RE.finditer(html):
        raw_href, raw_title = match.group(1), match.group(2)
        href = raw_href.strip()
        # DDG wraps outlinks as //duckduckgo.com/l/?uddg=<quoted-url>.
        if "uddg=" in href:
            href = urllib.parse.unquote(
                href.split("uddg=", 1)[1].split("&", 1)[0]
            )
        if not href.startswith(("http://", "https://")):
            continue
        title = _WS_RE.sub(
            " ", _TAG_SPLIT_RE.sub(" ", raw_title)
        ).strip()
        if title:
            hits.append(SearchHit(url=href, title=title[:200], snippet=""))
        if len(hits) >= 4:
            break
    return hits


def web_search(query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """Grounding search: Wikipedia first, generic web after, deduped.

    Never raises on network/model-shaped failures: degradation is an
    empty list (the claim then ships with fewer packages, never with
    invented URLs).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for hit in _wiki_search(query) + _ddg_search(query):
        if hit.url in seen:
            continue
        seen.add(hit.url)
        out.append(
            {"url": hit.url, "title": hit.title, "snippet": hit.snippet}
        )
        if len(out) >= max(1, min(top_k, 5)):
            break
    return out


def build_search_queries(plan: dict | None, text: str,
                         limit: int = 3) -> list[str]:
    """Diverse grounding queries from the merged plan (F2-2, pure).

    Always returns >= ``limit`` unique non-empty queries (<=300 chars):
    the plan web query, an entity+year authority query, an entity+figure
    query, an indicator-code query, then claim-text fallbacks. Facets come
    from plan fields only — never from any concrete claim.
    """
    plan = plan if isinstance(plan, dict) else {}
    queries: list[str] = []

    def _add(candidate: Any) -> None:
        clean = _WS_RE.sub(" ", str(candidate or "")).strip()[:300]
        if clean and clean not in queries:
            queries.append(clean)

    _add(plan.get("web_query"))
    ents = [str(e).strip() for e in (plan.get("entities") or [])
            if str(e).strip()][:2]
    year = plan.get("year")
    year_s = str(year) if isinstance(year, int) else ""
    nums = [str(n).strip() for n in (plan.get("numbers_in_claim") or [])
            if str(n).strip()][:1]
    ind = str(plan.get("indicator") or "").strip().upper()
    iso3 = str(plan.get("iso3") or "").strip().upper()
    base = _WS_RE.sub(" ", " ".join(ents + ([year_s] if year_s else [])))
    base = base.strip()
    if base:
        _add(f"{base} official statistics")
    if ents and nums:
        _add(f"{ents[0]} {nums[0]}")
    if ind and iso3:
        _add(f"{ind} {iso3} {year_s}".strip())
    if text:
        _add(text[:300])
    if base:
        _add(f"{base} data")
    # Degenerate-plan top-up: generic authority queries keep the >=3 pact.
    for fallback in ("official statistics figures",
                     "indicator data latest values",
                     "statistics office data"):
        _add(fallback)
    want = max(int(limit or 3), 3)
    return queries[:want] if len(queries) > want else queries


def rank_hits_primary_first(hits: list[dict]) -> list[dict]:
    """Stable hit ranking: official-statistics hosts first (F2-2, pure).

    Uses the canonical primary/secondary criterion; input order wins
    within each tier. Never raises on search-shaped input.
    """
    def _key(hit: Any) -> int:
        url = hit.get("url", "") if isinstance(hit, dict) else ""
        try:
            return 0 if source_type_for_url(str(url)) == "primary" else 1
        except Exception:
            return 1

    return sorted(list(hits or []), key=_key)


# ---------------------------------------------------------------------------
# model-read excerpts that keep quotes verbatim
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    """Stdlib HTML table reader (F2-2, no pandas).

    Collects tables -> rows -> cell texts (td/th, entities resolved).
    Never raises on markup-shaped input; truncated markup yields a
    best-effort partial table.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        name = (tag or "").lower()
        if name == "table":
            self._stack.append([])
            self._row = None
            self._cell = None
        elif name == "tr":
            if self._stack:
                self._row = []
                self._cell = None
        elif name in ("td", "th", "caption"):
            if self._stack and self._row is not None:
                self._cell = []
        elif name == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data or "")

    def handle_endtag(self, tag: str) -> None:
        name = (tag or "").lower()
        if name in ("td", "th", "caption"):
            if self._cell is not None and self._row is not None:
                cell = _WS_RE.sub(" ", "".join(self._cell)).strip()
                if cell:
                    self._row.append(cell)
            self._cell = None
        elif name == "tr":
            if self._stack and self._row:
                self._stack[-1].append(self._row)
            self._row = None
            self._cell = None
        elif name == "table":
            if self._stack:
                done = self._stack.pop()
                if done:
                    self.tables.append(done)
            self._row = None
            self._cell = None

    def close(self) -> None:
        try:
            super().close()
        except Exception:
            pass
        if self._cell is not None and self._row is not None:
            cell = _WS_RE.sub(" ", "".join(self._cell)).strip()
            if cell:
                self._row.append(cell)
        if self._row:
            if self._stack:
                self._stack[-1].append(self._row)
            else:
                self.tables.append([self._row])
        for pending in self._stack:
            if pending:
                self.tables.append(pending)
        self._stack = []
        self._row = None
        self._cell = None


def tables_from_html(html: str) -> list[list[list[str]]]:
    """Parse HTML tables into tables -> rows -> cells (F2-2, stdlib).

    Pure helper; returns [] for markup without tables. Never raises.
    """
    parser = _TableParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return list(parser.tables)
    return list(parser.tables)


def doc_excerpts(text: str, limit: int = DOC_EXCERPT_CHARS,
                 needles: "list[str] | tuple[str, ...] | None" = None,
                 ) -> str:
    """Readable excerpt where every line is a verbatim snapshot substring.

    HTML is split on tags (never reflowed): each surviving segment is a
    contiguous slice of the snapshot, so a quote copied from ONE segment
    greps exactly. Document order is kept (article leads come first);
    markup/data junk (chart JSON, template blobs, CSS) is dropped.

    F2-2: HTML data tables are appended as ``TABLE i row j: ...`` reading
    aids (same budget). These joined lines are NOT verbatim (cells are
    joined with " | "); single figures inside them still occur raw.

    V5-EN: ``needles`` (claim-derived tokens/figures, see
    :func:`excerpt_needles_for_claim`) reorders surviving segments so the
    ones mentioning the claim's figures/entities come first (stable:
    ties keep document order). Segments are never edited, so the
    verbatim pact holds exactly as before. ``None``/empty keeps the
    historical document order byte-for-byte.
    """
    clean = _SCRIPT_STYLE_RE.sub(" ", text or "")
    kept: list[str] = []
    for raw in _TAG_SPLIT_RE.split(clean):
        seg = _WS_RE.sub(" ", raw).strip()
        # Sentences carry quotable claims; tiny fragments are nav/TOC.
        # (Figure-dense API bodies are one big segment: unaffected.)
        if len(seg) < 60 and not (
                len(seg) >= 25 and " " in seg
                and any(ch.isdigit() for ch in seg)):
            continue
        if not seg or _is_junk_segment(seg):
            continue
        kept.append(seg)
    ranked = _rank_segments(kept, needles) if needles else kept
    out: list[str] = []
    budget = limit
    for seg in ranked:
        if budget <= 0:
            break
        out.append(seg[:budget])
        budget -= len(out[-1]) + 1
    if budget > 0:
        try:
            tables = tables_from_html(text or "")
        except Exception:
            tables = []
        for ti, rows in enumerate(tables[:5], 1):
            if budget <= 0:
                break
            for ri, row in enumerate(rows[:30], 1):
                line = f"TABLE {ti} row {ri}: {' | '.join(row)}"
                out.append(line[:budget])
                budget -= len(out[-1]) + 1
                if budget <= 0:
                    break
    return "\n".join(out)


# V5-EN: claim tokens worth ranking excerpt segments by. Digit runs
# (figures and years alike — ranking only decides reading order, never
# verdicts) plus alpha words of 4+ chars (entity/indicator/unit words;
# short words are stopword-grade noise in every language).
_NEEDLE_DIGIT_RE = re.compile(r"\d[\d.,]*")
_NEEDLE_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{4,}")
_MAX_NEEDLES = 32


def excerpt_needles_for_claim(text: str) -> list[str]:
    """Claim-derived excerpt needles (V5-EN, pure).

    Digit runs (with a commas-stripped variant so "299,792,458" also
    matches "299792458") plus lowercase alpha words of 4+ chars,
    de-duplicated, capped at 32. General token shaping over the claim
    TEXT only — never claim ids, entities, or languages.
    """
    needles: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        clean = (raw or "").strip().lower()
        if len(clean) >= 2 and clean not in seen:
            seen.add(clean)
            needles.append(clean)

    try:
        doc = text or ""
    except Exception:
        return []
    try:
        for match in _NEEDLE_DIGIT_RE.finditer(doc):
            token = match.group(0).strip(".,")
            if not token or not any(ch.isdigit() for ch in token):
                continue
            _add(token)
            _add(token.replace(",", ""))
        for match in _NEEDLE_WORD_RE.finditer(doc):
            _add(match.group(0))
    except Exception:
        return list(needles)
    return needles[:_MAX_NEEDLES]


def _rank_segments(segments: list[str],
                   needles: "list[str] | tuple[str, ...] | None",
                   ) -> list[str]:
    """Stable claim-first segment order (V5-EN, pure).

    Segments score distinct-needle hits (case-insensitive, commas
    ignored so grouped/ungrouped figures match); higher scores first,
    ties keep document order. Segments are never edited. Empty needles
    return the input order untouched. Never raises.
    """
    try:
        pins = [str(n).strip().lower().replace(",", "")
                for n in (needles or [])]
        pins = [p for p in pins if len(p) >= 2]
    except Exception:
        return list(segments)
    if not pins:
        return list(segments)
    scored: list[tuple[int, int, str]] = []
    for idx, seg in enumerate(segments or []):
        try:
            hay = str(seg).lower().replace(",", "")
        except Exception:
            hay = ""
        hits = sum(1 for p in pins if p in hay)
        scored.append((-hits, idx, seg))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [seg for _, _, seg in scored]


_JUNK_SEGMENT_MARKERS = (
    '{"template"', "{'template'", "&lt;", "{{", "mw-chart", "data-mw",
    "vector-feature", "client-js",
)


def _is_junk_segment(seg: str) -> bool:
    """True for markup/data blobs no quote should come from."""
    if seg[:1] in ("{", "<") or seg.startswith((",{", '"{')):
        return True
    lowered = seg[:120].lower()
    if any(marker in lowered for marker in _JUNK_SEGMENT_MARKERS):
        return True
    # Prose and figures are mostly alnum; dense symbol runs are data.
    alnum = sum(1 for ch in seg if ch.isalnum() or ch == " ")
    return alnum / max(1, len(seg)) < 0.5


def windows_around(
    text: str, needles: list[str], window: int = 300,
    limit: int = DOC_EXCERPT_CHARS,
) -> str:
    """Verbatim ±window slices around each needle (JSON/API bodies)."""
    out: list[str] = []
    budget = limit
    for needle in needles:
        if budget <= 0 or not needle:
            break
        idx = (text or "").find(needle)
        if idx < 0:
            continue
        frag = text[max(0, idx - window): idx + len(needle) + window]
        out.append(frag[:budget])
        budget -= len(out[-1]) + 1
    head = (text or "")[: max(0, min(1500, budget))]
    if head and not out:
        return head
    return "\n...\n".join(out) if out else head


# ---------------------------------------------------------------------------
# numbers + source typing (M2a-shaped)
# ---------------------------------------------------------------------------

def numbers_from_quote(quote: str) -> list[dict]:
    """Parse figure mentions into M2a NumberMention dicts.

    ``value`` is the full float (scale words applied), ``unit`` is set
    only for unambiguous markers ($/USD/percent), ``scale_raw`` keeps
    the raw scale word ("" when the digits stand alone).

    M2c: emitted units are guarded to the shared UNIT_VOCAB subset
    (USD/percent/None); anything outside degrades to None rather than
    shipping a unit M2a would reject.
    """
    out: list[dict] = []
    for match in _NUMBER_RE.finditer(quote or ""):
        raw_num = match.group("num").replace(",", "")
        try:
            value = float(raw_num)
        except ValueError:
            continue
        suffix = (match.group("suffix") or "").strip().lower()
        scale_raw = ""
        if suffix and suffix != "%":
            scale_raw = suffix
            value *= _SCALE_MULT.get(suffix, 1.0)
        unit: str | None = None
        if match.group("prefix") or re.search(
            r"\b(usd|dollars?|u\.s\. dollars?)\b", quote, re.IGNORECASE
        ):
            unit = "USD"
        elif suffix == "%" or re.search(
            r"\bpercent\b", quote, re.IGNORECASE
        ):
            unit = "percent"
        if unit not in ("USD", "percent", None):  # M2c: shared-vocab guard
            unit = None
        out.append({"value": value, "unit": unit, "scale_raw": scale_raw})
    # de-dup, keep order, cap at a sane bound
    seen: set[str] = set()
    uniq = [n for n in out if not (repr(n) in seen or seen.add(repr(n)))]
    return uniq[:10]


def source_type_for_url(url: str) -> str:
    """M2a source_type: official statistics hosts are primary.

    M2c: delegates to the canonical live.evidence.package criterion so
    producer and consumer agree by construction; the local table below
    is only a standalone fallback (same values).
    """
    shared = _try_import("live.evidence.package", "source_type_for_url")
    if callable(shared):
        try:
            typed = shared(url)
            if typed in ("primary", "secondary"):
                return typed
        except Exception:
            pass
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return "secondary"
    if any(host == h or host.endswith("." + h) for h in _PRIMARY_HOSTS):
        return "primary"
    return "secondary"


def repo_root() -> Path:
    """Repo root (parent of ``live/``)."""
    return Path(__file__).resolve().parents[2]


def snapshot_relpath(absolute_path: str, sha256: str) -> str:
    """M2a-style relative snapshot path (``data/snap/{sha}.html``)."""
    try:
        rel = Path(absolute_path).resolve().relative_to(repo_root().resolve())
        return str(rel)
    except (ValueError, OSError):
        return f"data/snap/{sha256}.html"


# ---------------------------------------------------------------------------
# config + accounting
# ---------------------------------------------------------------------------

@dataclass
class SwarmConfig:
    """Knobs + seams (doubles inject llm/search_fn/fetch_fn)."""

    llm: Any = None  # complete_json(system, user, max_tokens, ...) client
    search_fn: Callable[[str, int], list[dict]] | None = None
    fetch_fn: Callable[..., FetchResult] | None = None
    # V5-CATB: POST seam for altroutes (default: fetch_post). Doubles
    # take (url, payload_dict, snapshot_dir=...).
    post_fn: Callable[..., FetchResult] | None = None
    snapshot_dir: str | Path | None = None
    max_concurrency: int = MAX_CONCURRENCY
    claim_deadline_s: float = CLAIM_DEADLINE_S
    token_budget_per_claim: int = TOKEN_BUDGET_PER_CLAIM
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    model_max_tokens: int = MODEL_MAX_TOKENS
    reasoning_effort: str | None = MODEL_REASONING_EFFORT
    early_exit_enabled: bool = True  # F2-3 kill-switch (default: on)
    # F2-7 kill-switches (defaults: latency mode on).
    code_refutation_enabled: bool = True  # WB refute fast-path
    ground_parallel: bool = True  # parallel grounding queries (cap 4)
    redteam_enabled: bool = False  # True restores the blocking 2nd round
    # F2-9 kill-switch (default: on). False restores the full 4-round loop.
    early_abort_enabled: bool = True  # abort sterile no-progress rounds


@dataclass
class ClaimResult:
    claim_id: str
    packages: list[dict] = field(default_factory=list)
    degraded: bool = False
    note: str = ""
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)


@dataclass
class SwarmReport:
    results: list[ClaimResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_usage: dict = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    # F2-3 contention telemetry (additive; 0 when run_swarm was not used).
    max_concurrency: int = 0
    peak_inflight: int = 0  # max simultaneous run_claim bodies
    sem_wait_s_max: float = 0.0  # worst semaphore acquisition wait


class _Usage:
    """Per-claim token accounting from llm.last_usage (+seq)."""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        self._seq = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

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
            "total_tokens": self.total,
            "model_calls": self.calls,
        }

    def cost_usd(self) -> float:
        return estimate_cost_usd(self.prompt, self.completion)


class _BudgetOut(RuntimeError):
    pass


def _is_malformed_reply(exc: Exception) -> bool:
    """True for reply-shape failures worth one retry (not transport)."""
    if isinstance(exc, ValueError):  # incl. json.JSONDecodeError
        return True
    return isinstance(exc, RuntimeError) and "empty content" in str(exc)


def _rescue_reply(exc: Exception) -> dict | None:
    """Deterministic rescue of a preamble-wrapped JSON reply (M2d).

    json.JSONDecodeError carries the raw model text in ``.doc``; when it
    embeds one balanced object (preamble/trailer around valid JSON), parse
    just that span — 0 extra tokens, no new model call. Returns the dict
    on success, None when there is nothing rescuable (caller retries).
    """
    raw = getattr(exc, "doc", "")
    candidate = extract_balanced_json(raw) if raw else None
    if candidate is None:
        return None
    try:
        rescued = json.loads(candidate)
    except ValueError:
        return None
    return rescued if isinstance(rescued, dict) else None


# ---------------------------------------------------------------------------
# fetched-document store (url -> snapshot + model excerpt)
# ---------------------------------------------------------------------------

@dataclass
class Doc:
    url: str
    title: str
    fetch: FetchResult
    excerpt: str


class _DocStore:
    def __init__(self) -> None:
        self._docs: dict[str, Doc] = {}

    def add(self, fetch: FetchResult, title: str = "",
            excerpt_fn: Callable[[str], str] | None = None) -> Doc:
        excerpt = (excerpt_fn or doc_excerpts)(fetch.text or "")
        doc = Doc(url=fetch.final_url or fetch.url, title=title,
                  fetch=fetch, excerpt=excerpt)
        self._docs[doc.url] = doc
        self._docs.setdefault(fetch.url, doc)
        return doc

    def find(self, url: str) -> Doc | None:
        if url in self._docs:
            return self._docs[url]
        want = url.strip().rstrip("/")
        for key, doc in self._docs.items():
            if key.rstrip("/") == want:
                return doc
        return None

    def all(self) -> list[Doc]:
        seen: set[str] = set()
        out: list[Doc] = []
        for doc in self._docs.values():
            if id(doc) in seen:
                continue
            seen.add(id(doc))
            out.append(doc)
        return out

    def render(self) -> str:
        parts = []
        for i, doc in enumerate(self.all(), 1):
            parts.append(
                f"[doc {i}] url: {doc.url}\n"
                f"title: {doc.title or '(no title)'}\n"
                f"text:\n{doc.excerpt}"
            )
        return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# tool implementations (sync; executed by the RECUPERADOR loop)
# ---------------------------------------------------------------------------

class _Tools:
    """Deterministic tool backends behind the model tool loop."""

    def __init__(self, config: SwarmConfig, docs: _DocStore) -> None:
        self._config = config
        self._docs = docs
        # V5-EN: claim-derived excerpt needles (set by _ClaimCtx; None
        # keeps the historical document-order excerpts byte-for-byte).
        self.excerpt_needles: list[str] | None = None

    def _fetch(self, url: str) -> FetchResult:
        fn = self._config.fetch_fn or fetch_url
        return fn(url, snapshot_dir=self._config.snapshot_dir)

    def _search(self, query: str, top_k: int) -> list[dict]:
        fn = self._config.search_fn or web_search
        try:
            hits = fn(query, top_k)
        except Exception as exc:
            log.warning("web_search failed for %r: %r", query, exc)
            return []
        return [h for h in (hits or []) if isinstance(h, dict)
                and str(h.get("url", "")).startswith(("http://", "https://"))]

    # -- web_search ------------------------------------------------------
    def web_search(self, query: str, top_k: int = SEARCH_TOP_K) -> dict:
        hits = self._search(str(query or "")[:300], max(1, min(int(top_k or 3), 5)))
        return {"results": hits}

    # -- fetch_url -------------------------------------------------------
    def fetch_url(self, url: str) -> dict:
        try:
            fetched = self._fetch(str(url or ""))
        except Exception as exc:
            return {"ok": False, "url": url, "error": f"{type(exc).__name__}"}
        needles = self.excerpt_needles
        if needles:
            excerpt_fn = (lambda t, _n=tuple(needles):
                          doc_excerpts(t, needles=_n))
        else:
            excerpt_fn = None
        doc = self._docs.add(fetched, excerpt_fn=excerpt_fn)
        return {"ok": True, "url": doc.url, "title": doc.title,
                "text": doc.excerpt}

    # -- wb_lookup -------------------------------------------------------
    def wb_lookup(self, indicator: str, iso3: str,
                  year: int | None = None) -> dict:
        ind = str(indicator or "").strip().upper()
        geo = str(iso3 or "").strip().upper()
        if not _INDICATOR_RE.match(ind) or not _ISO3_RE.match(geo):
            return {"ok": False, "error": "bad indicator/iso3"}
        if isinstance(year, int) and 1960 <= year <= 2100:
            params = {"format": "json", "per_page": "50",
                      "date": f"{year}:{year}"}
        else:
            params = {"format": "json", "per_page": "50", "mrnev": "1"}
        url = (f"https://api.worldbank.org/v2/country/{geo}/indicator/{ind}"
               f"?{urllib.parse.urlencode(params)}")
        try:
            fetched = self._fetch(url)
        except Exception as exc:
            return {"ok": False, "url": url,
                    "error": f"{type(exc).__name__}"}
        needles: list[str] = []
        try:
            data = json.loads(fetched.text or "")
            rows = data[1] if isinstance(data, list) and len(data) > 1 else []
            for row in (rows or [])[:5]:
                if isinstance(row, dict) and row.get("value") is not None:
                    needles.append(f'"value": {row["value"]}')
                    needles.append(str(row.get("date", "")))
        except (ValueError, TypeError):
            pass
        doc = self._docs.add(
            fetched, title=f"World Bank {ind} {geo}",
            excerpt_fn=lambda t: windows_around(t, needles) if needles
            else doc_excerpts(t),
        )
        return {"ok": True, "url": doc.url, "title": doc.title,
                "text": doc.excerpt}

    # -- extra_lookup (src/atlas/connectors/extra.py as a tool) -----------
    def extra_lookup(self, source: str, dataset: str, iso3: str = "",
                     year: int | None = None) -> dict:
        extra = _try_import("src.atlas.connectors.extra", "fetch_owid")
        if extra is None:
            return {"ok": False, "error": "extra.py unavailable"}
        name = str(source or "").strip().lower()
        ds = str(dataset or "").strip()
        geo = str(iso3 or "").strip().upper()
        try:
            if name == "fred":
                return self._extra_fred(ds, year)
            if name == "owid":
                return self._extra_owid(ds, geo, year)
            if name == "wikidata":
                return self._extra_wikidata(ds, geo)
        except Exception as exc:
            log.warning("extra_lookup failed: %r", exc)
            return {"ok": False, "error": f"{type(exc).__name__}"}
        return {"ok": False, "error": f"unknown source {name!r}"}

    def _extra_fred(self, series: str, year: int | None) -> dict:
        import src.atlas.connectors.extra as extra

        series = series.strip().upper()
        if series not in extra.FRED_SERIES:
            return {"ok": False, "error": f"unknown FRED series {series!r}"}
        params = {"id": series}
        url = f"{extra.FRED_CSV_URL}?{urllib.parse.urlencode(params)}"
        try:
            fetched = self._fetch(url)
        except Exception as exc:
            return {"ok": False, "url": url,
                    "error": f"{type(exc).__name__}"}
        lines = (fetched.text or "").splitlines()
        header = lines[0] if lines else ""
        picked = [ln for ln in lines[1:]
                  if year is None or ln.startswith(f"{year}-")][-5:]
        excerpt = "\n".join([header, *picked])
        doc = self._docs.add(fetched, title=f"FRED {series}",
                             excerpt_fn=lambda _t: excerpt)
        return {"ok": True, "url": doc.url, "title": doc.title,
                "text": doc.excerpt}

    def _extra_owid(self, slug: str, iso3: str, year: int | None) -> dict:
        import src.atlas.connectors.extra as extra

        slug = slug.strip()
        if slug not in extra.OWID_DATASETS:
            return {"ok": False, "error": f"unknown OWID slug {slug!r}"}
        if not _ISO3_RE.match(iso3 or ""):
            return {"ok": False, "error": "iso3 required for owid"}
        url = f"{extra.OWID_GRAPHER_URL}/{slug}.csv"
        try:
            fetched = self._fetch(url)
        except Exception as exc:
            return {"ok": False, "url": url,
                    "error": f"{type(exc).__name__}"}
        lines = (fetched.text or "").splitlines()
        header = lines[0] if lines else ""
        picked: list[str] = []
        for line in lines[1:]:
            try:
                cells = next(csv.reader([line]))
            except (csv.Error, ValueError):
                continue
            if len(cells) < 4:
                continue
            code = cells[1].strip().upper()
            entity = cells[0].strip()
            match_geo = (entity == "World") if iso3 == "WLD" else (code == iso3)
            if not match_geo:
                continue
            try:
                row_year = int(float(cells[2].strip()))
            except ValueError:
                continue
            if year is not None and row_year != year:
                continue
            picked.append(line)  # raw line: verbatim in the snapshot
            if len(picked) >= 6:
                break
        excerpt = "\n".join([header, *picked])
        doc = self._docs.add(fetched, title=f"OWID {slug} {iso3}",
                             excerpt_fn=lambda _t: excerpt)
        return {"ok": True, "url": doc.url, "title": doc.title,
                "text": doc.excerpt}

    def _extra_wikidata(self, prop: str, iso3: str) -> dict:
        import src.atlas.connectors.extra as extra

        prop = prop.strip().upper()
        if prop not in extra.WD_PROPS or iso3 not in extra.WD_COUNTRIES:
            return {"ok": False,
                    "error": "need known WD prop + WD_COUNTRIES iso3"}
        qid = extra.WD_COUNTRIES[iso3][0]
        try:
            rows = extra.fetch_wikidata(qid, prop)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}"}
        from urllib.parse import quote as _quote

        sparql = (
            f"SELECT ?value ?time WHERE {{ wd:{qid} p:{prop} ?s. "
            f"?s ps:{prop} ?value. OPTIONAL {{ ?s pq:P585 ?time }} }} "
            f"ORDER BY DESC(?time) LIMIT 10"
        )
        url = (f"{extra.WIKIDATA_SPARQL_URL}?format=json&"
               f"query={_quote(sparql)}")
        try:
            fetched = self._fetch(url)
        except Exception as exc:
            return {"ok": False, "url": url,
                    "error": f"{type(exc).__name__}"}
        needles = [str(r.get("value", "")) for r in rows[:5]]
        doc = self._docs.add(
            fetched, title=f"Wikidata {qid} {prop}",
            excerpt_fn=lambda t: windows_around(t, needles),
        )
        return {"ok": True, "url": doc.url, "title": doc.title,
                "text": doc.excerpt}

    # -- alt_lookup (V5-CATB deterministic altroutes, grounding-called) -
    def alt_lookup(self, text: str) -> dict:
        """Deterministic b-like attempt via live.swarm.altroutes.

        Code-only (zero model cost), at most 2 network calls (the
        altroutes budget). NOT a RECUPERADOR tool: ``roles.py`` is
        untouched, so the model never calls this; grounding invokes it
        once per claim when no package exists yet. Candidates carry
        their snapshots as added docs; the caller builds packages.
        Never raises: import/transport failures return ok False.
        """
        try:
            attempt = _try_import("live.swarm.altroutes",
                                  "attempt_altroutes")
            if not callable(attempt):
                return {"ok": False, "error": "altroutes unavailable"}
            post = self._config.post_fn or fetch_post
            tried = attempt(
                str(text or ""), post,
                snapshot_dir=self._config.snapshot_dir)
        except Exception as exc:
            log.warning("alt_lookup failed: %r", exc)
            return {"ok": False, "error": f"{type(exc).__name__}"}
        out: list[dict] = []
        try:
            for cand in tried.candidates:
                doc = self._docs.add(
                    cand.fetch_result,
                    title=(f"altroutes "
                           f"{(cand.source_meta or {}).get('indicator', '?')}"
                           f" {cand.vintage or ''}".rstrip()),
                    excerpt_fn=(lambda q: (lambda t: windows_around(t, [q]))
                                )(cand.quote),
                )
                out.append({"url": doc.url, "quote": cand.quote,
                            "source_meta": cand.source_meta,
                            "source_year": cand.source_year,
                            "vintage": cand.vintage})
            return {"ok": True, "candidates": out,
                    "calls": tried.calls_made,
                    "routes_tried": tried.routes_tried}
        except Exception as exc:
            log.warning("alt_lookup packaging failed: %r", exc)
            return {"ok": False, "error": f"{type(exc).__name__}"}


# ---------------------------------------------------------------------------
# plan helpers (model plan first, deterministic code fallbacks)
# ---------------------------------------------------------------------------

def _code_plan(text: str) -> dict:
    """Deterministic plan from repo helpers (fills model-plan gaps)."""
    plan: dict[str, Any] = {
        "indicator": None, "iso3": None, "year": None, "query": text[:200],
    }
    match_indicator = _try_import(
        "src.atlas.connectors.worldbank", "match_indicator")
    if callable(match_indicator):
        try:
            hit = match_indicator(text)
            if hit:
                plan["indicator"] = hit[0]
        except Exception:
            pass
    resolve_country = _try_import(
        "src.atlas.connectors.countries", "resolve_country")
    if callable(resolve_country):
        try:
            _name, iso3 = resolve_country(text)
            plan["iso3"] = iso3
        except Exception:
            pass
    if plan["iso3"] is None and _WORLD_CUES_RE.search(text):
        plan["iso3"] = "WLD"
    years = [int(y.group(0)) for y in _YEAR_RE.finditer(text)]
    plan["year"] = next((y for y in years if 1960 <= y <= 2100), None)
    return plan


def _merge_plan(model_plan: dict | None, text: str) -> dict:
    code = _code_plan(text)
    merged = {
        "entities": [], "indicator": code["indicator"], "iso3": code["iso3"],
        "year": code["year"], "web_query": code["query"],
        "numbers_in_claim": [],
    }
    if not isinstance(model_plan, dict):
        return merged
    if isinstance(model_plan.get("entities"), list):
        merged["entities"] = [str(e)[:100] for e in model_plan["entities"][:8]]
    ind = model_plan.get("indicator_candidate")
    if isinstance(ind, str) and _INDICATOR_RE.match(ind.strip().upper()):
        merged["indicator"] = ind.strip().upper()
    geo = model_plan.get("geo") or {}
    if isinstance(geo, dict):
        iso = geo.get("iso3")
        if isinstance(iso, str) and _ISO3_RE.match(iso.strip().upper()):
            merged["iso3"] = iso.strip().upper()
    year = model_plan.get("year")
    if isinstance(year, int) and 1960 <= year <= 2100:
        merged["year"] = year
    query = model_plan.get("web_query")
    if isinstance(query, str) and query.strip():
        merged["web_query"] = query.strip()[:300]
    if isinstance(model_plan.get("numbers_in_claim"), list):
        merged["numbers_in_claim"] = [
            str(n)[:50] for n in model_plan["numbers_in_claim"][:8]]
    return merged


# ---------------------------------------------------------------------------
# F2-2 deterministic World Bank row rescue (structured-unit rescue)
# ---------------------------------------------------------------------------

_WB_ROW_RE = re.compile(
    r'"date"\s*:\s*"(?P<date>\d{4})"\s*,\s*"value"\s*:\s*'
    r"(?P<val>-?\d+(?:\.\d+)?)"
)

# Standalone mirror of live.evidence.package.WB_INDICATOR_UNITS keys (same
# delegation pattern as _PRIMARY_HOSTS): the canonical map wins whenever
# M2a is importable; totals-only by construction.
_CURATED_TOTALS_MIRROR = ("SP.POP.TOTL", "NY.GDP.MKTP.CD")

# Standalone mirror of live.evidence.package.is_multi_source_arithmetic
# (level-exception first, then computed-figure cues). Used only when the
# canonical helper is not importable.
_ARITH_LEVEL_MIRROR_RE = re.compile(
    r"\b(doubled|tripled|quadrupled|grew|grown|growing|rose|risen|fell"
    r"|fallen|dropped|declined|decline|reached|climbed|surged|jumped)\s+to\b",
    re.IGNORECASE,
)
_ARITH_COMPUTED_MIRROR_RES = (
    re.compile(r"\baverag(?:e|ed|es|ing)\b|\bmean\b|\bmedian\b",
               re.IGNORECASE),
    re.compile(r"\bpercent\s+of\b", re.IGNORECASE),
    re.compile(r"\b(grew|growth|increased|increase|decreased|decrease|rose"
               r"|fell|declined|decline|change[sd]?)\b[^.?!]{0,60}\bpercent\b",
               re.IGNORECASE),
    re.compile(r"\bfrom\s+(19|20|21)\d{2}\s+to\s+(19|20|21)\d{2}\b",
               re.IGNORECASE),
)


def _is_arithmetic_claim(text: str) -> bool:
    """Canonical multi-source-arithmetic test, mirrored standalone."""
    fn = _try_import("live.evidence.package", "is_multi_source_arithmetic")
    if callable(fn):
        try:
            return bool(fn(text))
        except Exception:
            pass
    doc = text or ""
    if _ARITH_LEVEL_MIRROR_RE.search(doc):
        return False
    return any(rx.search(doc) for rx in _ARITH_COMPUTED_MIRROR_RES)


def _curated_total_indicators() -> tuple[str, ...]:
    mod = _try_import("live.evidence.package", "WB_INDICATOR_UNITS")
    if isinstance(mod, dict) and mod:
        try:
            return tuple(sorted(str(k).upper() for k in mod))
        except Exception:
            pass
    return _CURATED_TOTALS_MIRROR


_WB_ROW_DATE_RE = re.compile(r'"date"\s*:\s*"((?:19|20|21)\d{2})"')


def row_year_for_quote(quote: str, url: str) -> int | None:
    """Year of the cited World Bank API row, or None (V5-EN, pure).

    A package's ``source_year`` must date the CITED row, not the plan:
    derivation claims (pct_change across 2023+2024) cite two rows from
    two years while the plan carries one, and stamping the plan year on
    both collapses the pair the V5-P0 derivation needs to order old/new
    (EN bench: K1-08/K1-11/P1-06/P1-11/P1-12 abstained on 2024/2024
    stamps over 2024+2023 rows). Returns the year only when the quote
    carries EXACTLY ONE distinct WB row date and the URL is a World
    Bank API snapshot; anything else (prose, CSV, multi-row spans)
    returns None so the caller keeps its year. Never raises.
    """
    try:
        host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    except Exception:
        return None
    if host != "api.worldbank.org":
        return None
    try:
        years = set(_WB_ROW_DATE_RE.findall(quote or ""))
    except Exception:
        return None
    if len(years) != 1:
        return None
    try:
        year = int(next(iter(years)))
    except (TypeError, ValueError):
        return None
    return year if 1960 <= year <= 2100 else None


def wb_row_quote_for(snapshot_text: str, year: int) -> str | None:
    """Verbatim ``"date":"YYYY","value":N`` row span, or None (F2-2, pure).

    The quote is a regex span of the real snapshot text (compact and
    pretty JSON separators both match), so it verifies verbatim by
    construction. None when the year row is missing, null, or the body
    is not WB-shaped. Never raises.
    """
    if not isinstance(year, int):
        return None
    try:
        body = snapshot_text or ""
    except Exception:
        return None
    want = str(year)
    try:
        for match in _WB_ROW_RE.finditer(body):
            if match.group("date") == want:
                return match.group(0)
    except Exception:
        return None
    return None


def wb_fallback_eligible(text: str, plan: dict | None) -> bool:
    """True when a deterministic WB row package is sound (F2-2, pure).

    Guards (all general): curated total indicator only (per-capita /
    growth rows would corrupt verdicts); exact claim year present;
    non-arithmetic claims only (component rows cannot decide averages /
    ratios / percent changes); merged iso3 agrees with the deterministic
    country resolver (a wrong-country row would contradict TRUE claims).
    """
    plan = plan if isinstance(plan, dict) else {}
    ind = str(plan.get("indicator") or "").strip().upper()
    iso3 = str(plan.get("iso3") or "").strip().upper()
    year = plan.get("year")
    if ind not in _curated_total_indicators():
        return False
    if not _ISO3_RE.match(iso3):
        return False
    if not (isinstance(year, int) and 1960 <= year <= 2100):
        return False
    if _is_arithmetic_claim(text or ""):
        return False
    try:
        code_iso3 = _code_plan(text or "").get("iso3")
    except Exception:
        return False
    return code_iso3 == iso3


def _maybe_add_wb_fallback(ctx: "_ClaimCtx", plan: dict) -> None:
    """Grounding-time deterministic primary package (F2-2).

    When eligible, quotes the exact ``"date","value"`` row span from the
    already-fetched World Bank API doc (verbatim by construction) and
    ships it as an ``extractor="code"`` package — including when the
    model loop later returns [] or exhausts budget/deadline (grounding
    packages persist: the FALSE-claim recall class). First matching doc
    only; never raises.
    """
    try:
        if not wb_fallback_eligible(ctx.text, plan):
            return
        ind = str(plan.get("indicator")).strip().upper()
        iso3 = str(plan.get("iso3")).strip().upper()
        year = plan.get("year")
        needle = f"/{iso3}/indicator/{ind}".lower()
        for doc in ctx.docs.all():
            url = doc.url or ""
            if "api.worldbank.org" not in url:
                continue
            if needle not in url.lower():
                continue
            quote = wb_row_quote_for(doc.fetch.text or "", year)
            if quote is None:
                return
            pkg = build_package(ctx.claim_id, url, quote, doc, year,
                                len(ctx.packages), extractor="code")
            if pkg is None:
                return
            key = (doc.fetch.sha256, quote)
            if key not in ctx._seen_quotes:
                ctx._seen_quotes.add(key)
                ctx.packages.append(pkg)
            return
    except Exception as exc:
        log.warning("wb fallback failed for %s: %r",
                    getattr(ctx, "claim_id", "?"), exc)


def _maybe_add_altroutes(ctx: "_ClaimCtx") -> None:
    """Grounding-time deterministic altroute packages (V5-CATB).

    Last grounding step before the model loop: when the claim is
    b-like (IMF/BLS-topic, matched on TEXT, never ids) and grounding
    shipped zero packages, try the altroutes registry (max 2 network
    calls, code-only, zero model cost) and ship verbatim candidates
    as ``extractor="code"`` packages. Non-b-like claims return
    immediately (0 calls, byte-identical behavior). Never raises.
    """
    try:
        if ctx.packages:
            return
        res = ctx.tools.alt_lookup(ctx.text)
        if not isinstance(res, dict) or not res.get("ok"):
            return
        for cand in res.get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            url = cand.get("url")
            quote = cand.get("quote")
            if not isinstance(url, str) or not isinstance(quote, str):
                continue
            doc = ctx.docs.find(url)
            if doc is None:
                continue
            pkg = build_package(
                ctx.claim_id, url, quote, doc, cand.get("source_year"),
                len(ctx.packages), extractor="code",
                source_meta_override=cand.get("source_meta")
                if isinstance(cand.get("source_meta"), dict) else None)
            if pkg is None:
                continue
            key = (doc.fetch.sha256, quote)
            if key not in ctx._seen_quotes:
                ctx._seen_quotes.add(key)
                ctx.packages.append(pkg)
    except Exception as exc:
        log.warning("altroutes fallback failed for %s: %r",
                    getattr(ctx, "claim_id", "?"), exc)


# ---------------------------------------------------------------------------
# package builder (strict verbatim validation)
# ---------------------------------------------------------------------------

def build_package(claim_id: str, url: str, quote: str, doc: Doc,
                  source_year: int | None, index: int,
                  extractor: str = "model",
                  source_meta_override: dict | None = None,
                  ) -> dict | None:
    """Validate a finding and build an M2a-compatible package dict.

    Returns None (never raises on model-shaped input) when the quote
    is not a verbatim substring of the snapshot text, the URL does not
    match the fetched doc, or M2a validation rejects the dict.

    F2-2: ``extractor="code"`` marks deterministic grounding packages
    (WB row rescue); the default "model" preserves every old call site.

    V5-EN: ``source_year`` resolves to the CITED WB row's date when the
    quote carries exactly one (see :func:`row_year_for_quote`); the
    caller-passed plan year stays the fallback for prose/CSV/multi-row
    quotes. Code-built WB packages quote the plan year's own row, so
    they resolve identically.

    V5-CATB: ``source_meta_override`` (``{indicator, unit, scale}``)
    replaces the snapshot-derived meta for non-WB structured routes
    (BLS API rows); None (default) keeps the historical derivation
    byte-for-byte. M2a still validates the final dict, so a
    malformed override degrades to None (no package), never a crash.
    """
    if not isinstance(quote, str) or not quote.strip():
        return None
    text = doc.fetch.text or ""
    start = text.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    sha = doc.fetch.sha256
    try:
        row_year = row_year_for_quote(quote, doc.url)
    except Exception:
        row_year = None
    resolved_year = row_year if row_year is not None else source_year
    # M2d: structured-source metadata (WB API JSON -> indicator/unit) so
    # the verifier can resolve unitless JSON quotes. Derived from the
    # snapshot bytes, never from the model; None for prose/CSV/unknown.
    meta_fn = _try_import("live.evidence.package", "source_meta_for_snapshot")
    meta = None
    if callable(meta_fn):
        try:
            found = meta_fn(doc.url, doc.fetch.text or "")
            meta = ({"indicator": found.indicator, "unit": found.unit,
                     "scale": found.scale} if found is not None else None)
        except Exception:
            meta = None
    if isinstance(source_meta_override, dict):  # V5-CATB (default None)
        meta = source_meta_override
    pkg = {
        "package_id": f"m2b-{claim_id}-{sha[:12]}-{index}",
        "claim_id": claim_id,
        "url": doc.url,
        "captured_at": doc.fetch.captured_at,
        "sha256": sha,
        "snapshot_path": snapshot_relpath(doc.fetch.snapshot_path, sha),
        "quote": quote,
        "offsets": [start, end],
        "source_type": source_type_for_url(doc.url),
        "numbers": numbers_from_quote(quote),
        "extractor": extractor if extractor in ("model", "code") else "model",
        "source_year": resolved_year,
        "source_meta": meta,
    }
    try:
        json.dumps(pkg)  # bus contract: JSON-only events
    except (TypeError, ValueError):
        return None
    if not validate_package_dict(pkg):
        return None
    return pkg


# ---------------------------------------------------------------------------
# F2-3 early exit (primary verbatim quote + numeric match closes rounds)
# ---------------------------------------------------------------------------

def _default_rel_tolerance() -> float:
    """Verifier tolerance, READ-ONLY (F2-3 reuses, never changes it)."""
    for path in ("src.atlas.core.compare", "atlas.core.compare"):
        value = _try_import(path, "DEFAULT_REL_TOLERANCE")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 0.02


def _is_year_value(num: dict) -> bool:
    """True for bare 4-digit years (never a figure match)."""
    try:
        value = float(num.get("value", 0.0))
        scale_raw = str(num.get("scale_raw") or "")
    except (TypeError, ValueError):
        return True
    return (
        not scale_raw
        and value == int(value)
        and 1960 <= int(value) <= 2100
    )


def early_exit_reason(packages: list[dict] | None, text: str) -> str | None:
    """Reason to close extra rounds, or None (F2-3, pure).

    Fires when some PRIMARY package carries a verbatim quote (every
    shipped package is verbatim by construction) with a figure within
    the verifier tolerance of a claim figure. Years never match.
    The verifier still decides the verdict from the kept packages;
    this only skips further ROUNDS (model loop / redteam).
    """
    try:
        claim_nums = [n for n in numbers_from_quote(text or "")
                      if not _is_year_value(n)]
    except Exception:
        return None
    if not claim_nums:
        return None
    tol = _default_rel_tolerance()
    for pkg in packages or []:
        if not isinstance(pkg, dict):
            continue
        if pkg.get("source_type") != "primary":
            continue
        quote = pkg.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        try:
            quote_nums = [n for n in numbers_from_quote(quote)
                          if not _is_year_value(n)]
        except Exception:
            continue
        for qn in quote_nums:
            for cn in claim_nums:
                try:
                    denom = max(abs(float(cn["value"])),
                                abs(float(qn["value"])), 1e-12)
                    rel = abs(float(cn["value"]) - float(qn["value"])) / denom
                except (TypeError, ValueError):
                    continue
                if rel <= tol:
                    url = pkg.get("url", "")
                    return (
                        f"primary {url} quotes a figure within "
                        f"{tol:.0%} of the claim"
                    )
    return None


def code_refutation_reason(packages: list[dict] | None, text: str,
                           plan: dict | None) -> str | None:
    """Reason to close rounds via a deterministic WB refutation, or None.

    F2-7 fast-path refutation (extends F1): when the grounded ``code``
    primary package quotes a WB row whose figure differs from the claim
    beyond the (untouched, read-only) verifier tolerance, the kept
    package already sustains ``contradicted`` — no model rounds needed.
    The verifier still decides from the kept package; this only skips
    ROUNDS (web pool / model loop / redteam).

    Guards (all general, same F1 pact plus verifier mirrors): curated
    total indicator only, exact claim year (plan year must equal the
    claim year), non-arithmetic claims only, deterministic iso3 match,
    units compatible (direct or via the curated source_meta unit),
    no scale-slip backstop trigger, claim + quote carry non-year
    figures. Returns None (safe degrade) when the verifier helpers are
    not importable. Never raises.
    """
    try:
        if not wb_fallback_eligible(text or "", plan):
            return None
        code_pkgs = [
            p for p in packages or []
            if isinstance(p, dict)
            and p.get("extractor") == "code"
            and p.get("source_type") == "primary"
            and isinstance(p.get("quote"), str)
            and p["quote"].strip()
            and isinstance(p.get("source_meta"), dict)
        ]
        if not code_pkgs:
            return None
        parse_q = (_try_import("src.atlas.core.units", "parse_quantities")
                   or _try_import("atlas.core.units", "parse_quantities"))
        is_year_q = (_try_import("src.atlas.core.compare", "is_year_quantity")
                     or _try_import("atlas.core.compare", "is_year_quantity"))
        units_compat = (_try_import("src.atlas.core.units", "units_compatible")
                        or _try_import("atlas.core.units", "units_compatible"))
        is_expl = (_try_import("src.atlas.core.units", "is_explicit_scale")
                   or _try_import("atlas.core.units", "is_explicit_scale"))
        scale_pow = (_try_import("src.atlas.core.units", "scale_error_power")
                     or _try_import("atlas.core.units", "scale_error_power"))
        if not all(callable(f) for f in
                   (parse_q, is_year_q, units_compat, is_expl, scale_pow)):
            return None
        try:
            claim_cands = [q for q in parse_q(text or "", label="claim")
                           if not is_year_q(q)]
        except Exception:
            return None
        if not claim_cands:
            return None
        claim = claim_cands[0]
        try:
            claim_value = float(claim.value)
            claim_unit = claim.normalized_unit
            claim_raw = claim.raw_text or ""
        except Exception:
            return None
        try:
            claim_years = [int(m.group(0))
                           for m in _YEAR_RE.finditer(text or "")]
            claim_year = next(
                (y for y in claim_years if 1960 <= y <= 2100), None)
        except Exception:
            claim_year = None
        plan_year = plan.get("year") if isinstance(plan, dict) else None
        if claim_year is None or plan_year != claim_year:
            return None
        tol = _default_rel_tolerance()
        for pkg in code_pkgs:
            quote = pkg["quote"]
            meta = pkg["source_meta"]
            meta_unit = meta.get("unit")
            try:
                meta_scale = float(meta.get("scale", 1.0) or 1.0)
            except (TypeError, ValueError):
                continue
            if not meta_scale > 0:
                continue
            try:
                quote_cands = [q for q in parse_q(quote, label="quote")
                               if not is_year_q(q)]
            except Exception:
                continue
            if not quote_cands:
                continue
            try:
                if not is_expl(claim_raw or ""):
                    if any(scale_pow(claim_value, float(q.value) * meta_scale)
                           is not None for q in quote_cands):
                        continue
            except Exception:
                continue
            try:
                compat = [q for q in quote_cands
                          if units_compat(claim_unit, q.normalized_unit)]
                scale = 1.0
                if not compat and units_compat(claim_unit, meta_unit):
                    compat = list(quote_cands)
                    scale = meta_scale
                if not compat:
                    continue
            except Exception:
                continue
            try:
                def _rel(q: Any) -> float:
                    ev = float(q.value) * scale
                    denom = abs(ev) if ev != 0 else 1.0
                    return abs(claim_value - ev) / denom

                best = min(compat, key=_rel)
                rel = _rel(best)
                best_val = float(best.value) * scale
            except Exception:
                continue
            if rel > tol:
                url = pkg.get("url", "")
                return (
                    f"code {url} refutes: claim {claim_value:g} vs row "
                    f"{best_val:g} (rel {rel:.1%} > {tol:.0%})"
                )
        return None
    except Exception:
        return None


def no_exit_reason(packages: list[dict] | None, text: str,
                   plan: dict | None) -> str:
    """Why no grounding fast-path fired (F2-7 taxonomy, pure).

    Returns ``exit_support_fast`` / ``exit_refute_fast`` when a
    fast-path WOULD fire, else ``no_exit:<cause>``. Causes are general
    (never claim-, figure- or URL-specific):

    - ``ineligible_indicator/geo/year/arithmetic/country``: the F1 pact
      blocks a deterministic code package (wrong indicator family, no
      usable geo/year, computed figure, resolver disagreement).
    - ``no_code_package``: eligible but the WB row never materialized
      (fetch failed / year row missing or null).
    - ``year_mismatch``: eligible code row for a different year than the
      claim (refutation would be unsound).
    - ``numeric_undecided``: eligible code row present but neither the
      support match nor the refutation mismatch applies (units/scale
      edge or missing figures — the verifier would not decide from it
      alone, so model rounds are needed).
    - ``no_packages`` / ``no_primary``: fallback when no plan is
      available (should not happen in the pipeline; plan always set).

    Never raises.
    """
    try:
        if early_exit_reason(packages, text) is not None:
            return "exit_support_fast"
        if code_refutation_reason(packages, text, plan) is not None:
            return "exit_refute_fast"
        plan_d = plan if isinstance(plan, dict) else {}
        has_plan = isinstance(plan, dict) and bool(plan)
        if has_plan:
            ind = str(plan_d.get("indicator") or "").strip().upper()
            iso3 = str(plan_d.get("iso3") or "").strip().upper()
            year = plan_d.get("year")
            if ind not in _curated_total_indicators():
                return "no_exit_ineligible_indicator"
            if not _ISO3_RE.match(iso3):
                return "no_exit_ineligible_geo"
            if not (isinstance(year, int) and 1960 <= year <= 2100):
                return "no_exit_ineligible_year"
            if _is_arithmetic_claim(text or ""):
                return "no_exit_ineligible_arithmetic"
            try:
                code_iso3 = _code_plan(text or "").get("iso3")
            except Exception:
                code_iso3 = None
            if code_iso3 != iso3:
                return "no_exit_ineligible_country"
            code_primaries = [
                p for p in packages or []
                if isinstance(p, dict)
                and p.get("extractor") == "code"
                and p.get("source_type") == "primary"
                and isinstance(p.get("quote"), str)
                and p["quote"].strip()
            ]
            if not code_primaries:
                return "no_exit_no_code_package"
            try:
                claim_years = [int(m.group(0))
                               for m in _YEAR_RE.finditer(text or "")]
                claim_year = next(
                    (y for y in claim_years if 1960 <= y <= 2100), None)
            except Exception:
                claim_year = None
            if claim_year is None or plan_d.get("year") != claim_year:
                return "no_exit_year_mismatch"
            return "no_exit_numeric_undecided"
        pkgs = [p for p in packages or [] if isinstance(p, dict)]
        if not pkgs:
            return "no_exit_no_packages"
        if not any(p.get("source_type") == "primary" for p in pkgs):
            return "no_exit_no_primary"
        return "no_exit_numeric_undecided"
    except Exception:
        return "no_exit_unknown"


def early_abort_reason(n_packages: int, docs_added: int) -> str | None:
    """Reason to abort the remaining rounds, or None (F2-9, pure).

    Fires when completed round(s) produced nothing new: 0 packages in
    hand AND 0 documents added since grounding ended (sterile
    re-search: same queries, already-known or unfetchable hits). The
    counts are general (never claim-, figure- or URL-specific). A round
    that returns final, adds a package, or fetches any new document
    never aborts, so deciders keep their full loop. Never raises.
    """
    try:
        if int(n_packages) == 0 and int(docs_added) == 0:
            return "no packages and no new documents since grounding"
    except (TypeError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------
# per-claim pipeline
# ---------------------------------------------------------------------------

class _ClaimCtx:
    def __init__(self, claim_id: str, text: str, config: SwarmConfig) -> None:
        self.claim_id = claim_id
        self.text = text
        self.config = config
        self.docs = _DocStore()
        self.tools = _Tools(config, self.docs)
        # V5-EN: claim-aware excerpt ranking for generic fetches (the
        # WB/extra tools keep their own needle excerpts; None-safe).
        try:
            self.tools.excerpt_needles = excerpt_needles_for_claim(text)
        except Exception:
            self.tools.excerpt_needles = None
        self.usage = _Usage()
        self.packages: list[dict] = []
        self._seen_quotes: set[tuple[str, str]] = set()
        # F2-7: grounding exit taxonomy (exit_*/no_exit_*; "" when no plan).
        self.exit_taxonomy: str = ""
        # F2-9: set when a sterile round aborts the loop (None otherwise).
        self.early_abort: str | None = None
        # F2-3: monotonic per-claim budget; rounds abort gracefully when
        # it lapses (wait_for in run_claim stays as the hard backstop).
        self._deadline_mono = (
            time.monotonic() + float(config.claim_deadline_s or 0.0))

    def expired(self) -> bool:
        """True when the per-claim time budget has lapsed."""
        return time.monotonic() >= self._deadline_mono

    async def complete(self, system: str, user: str,
                       schema: dict | None) -> dict:
        if self.usage.total >= self.config.token_budget_per_claim:
            raise _BudgetOut("token budget exhausted")
        llm = self.config.llm

        def _call(sys: str) -> dict:
            return llm.complete_json(
                sys, user, max_tokens=self.config.model_max_tokens,
                reasoning_effort=self.config.reasoning_effort,
                json_schema=schema,
            )

        try:
            out = await asyncio.to_thread(_call, system)
        except Exception as exc:
            # One retry for malformed replies (observed live: the model
            # sometimes prepends chat text before the JSON object, which
            # surfaces as a JSON decode error). Transport/schema errors
            # are NOT retried here (the transport has its own retries;
            # a 400 would just fail twice). M2d: before spending the retry,
            # try a deterministic rescue of the raw reply text (exc.doc);
            # the retry itself gets the same rescue (live: retries repeat
            # the preamble, so the retry alone did not fix those claims).
            self.usage.record(llm)
            if not _is_malformed_reply(exc):
                raise
            rescued = _rescue_reply(exc)
            if rescued is not None:
                log.warning("rescued preamble-wrapped JSON reply "
                            "(no retry spent)")
                out = rescued
            else:
                log.warning("malformed model reply, retrying once: %r", exc)
                try:
                    out = await asyncio.to_thread(
                        _call, system + "\n\nSTRICT: your reply was not pure JSON. "
                        "Reply again with ONLY the JSON object.")
                except Exception as retry_exc:
                    self.usage.record(llm)
                    if not _is_malformed_reply(retry_exc):
                        raise
                    rescued = _rescue_reply(retry_exc)
                    if rescued is None:
                        raise
                    log.warning("rescued retry reply with preamble")
                    out = rescued
                else:
                    self.usage.record(llm)
        else:
            self.usage.record(llm)
        if self.usage.total >= self.config.token_budget_per_claim:
            raise _BudgetOut("token budget exhausted")
        if not isinstance(out, dict):
            raise RuntimeError("model returned non-dict JSON")
        return out

    def add_findings(self, findings: Any, source_year: int | None) -> int:
        added = 0
        if not isinstance(findings, list):
            return 0
        for item in findings[:5]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            quote = item.get("quote")
            if not isinstance(url, str) or not isinstance(quote, str):
                continue
            doc = self.docs.find(url)
            if doc is None:
                continue  # uncited-URL guard: only fetched docs count
            key = (doc.fetch.sha256, quote)
            if key in self._seen_quotes:
                continue
            pkg = build_package(self.claim_id, url, quote, doc,
                                source_year, len(self.packages))
            if pkg is None:
                continue  # invented/non-verbatim quote: dropped
            self._seen_quotes.add(key)
            self.packages.append(pkg)
            added += 1
        return added


def _dispatch_tool(ctx: _ClaimCtx, name: str, args: dict) -> dict:
    try:
        if name == "web_search":
            return ctx.tools.web_search(
                str(args.get("query", "")),
                int(args.get("top_k", SEARCH_TOP_K) or SEARCH_TOP_K))
        if name == "fetch_url":
            return ctx.tools.fetch_url(str(args.get("url", "")))
        if name == "wb_lookup":
            return ctx.tools.wb_lookup(
                str(args.get("indicator", "")),
                str(args.get("iso3", "")),
                args.get("year"))
        if name == "extra_lookup":
            return ctx.tools.extra_lookup(
                str(args.get("source", "")),
                str(args.get("dataset", "")),
                str(args.get("iso3", "") or ""),
                args.get("year"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}"}
    return {"ok": False, "error": f"unknown tool {name!r}"}


async def _recuperador_loop(ctx: _ClaimCtx, seed_user: str,
                            max_rounds: int,
                            source_year: int | None) -> None:
    """RECUPERADOR tool loop: model calls tools until final/rounds out."""
    transcript = seed_user
    # F2-9: doc baseline at loop entry (== end of grounding); a round that
    # adds no package and no document is sterile (see early_abort_reason).
    base_docs = len(ctx.docs.all())
    for _ in range(max(1, max_rounds)):
        if ctx.expired():  # F2-3: graceful round abort (packages kept)
            return
        step = await ctx.complete(
            RECUPERADOR_SYSTEM, transcript, RECUPERADOR_SCHEMA)
        calls, final = parse_tool_step(step)
        if final is not None:
            ctx.add_findings(final.get("findings"), source_year)
            return
        if not calls:
            return  # model stopped without tools or final: done
        tool_results = await asyncio.to_thread(
            lambda: [
                {"name": c["name"], "result": _dispatch_tool(ctx, c["name"], c["arguments"])}
                for c in calls[:3]
            ]
        )
        # Auto-fetch top web_search hits so the model reads snapshots,
        # not just result lists (bounded: first 2 unseen hits, F2-2
        # primary-first so official statistics win the fetch slots).
        for entry in tool_results:
            if entry["name"] != "web_search":
                continue
            result = entry["result"]
            if not isinstance(result, dict):
                continue
            ranked = rank_hits_primary_first(result.get("results") or [])
            for hit in ranked[:FETCH_TOP_K]:
                if not isinstance(hit, dict):
                    continue
                url = str(hit.get("url", ""))
                if url and ctx.docs.find(url) is None:
                    await asyncio.to_thread(ctx.tools.fetch_url, url)
        transcript = (
            f"{transcript}\n\ntool_results:\n"
            f"{json.dumps(tool_results, ensure_ascii=False)[:6000]}\n\n"
            f"fetched documents so far:\n{ctx.docs.render()[:9000]}\n\n"
            "Continue: more tool_calls, or final with findings."
        )
        if ctx.config.early_abort_enabled:  # F2-9: sterile round -> stop
            reason = early_abort_reason(
                len(ctx.packages), len(ctx.docs.all()) - base_docs)
            if reason is not None:
                ctx.early_abort = reason
                return
    # Rounds exhausted without final: one last forced-final call.
    if ctx.expired():  # F2-3: no forced call on a lapsed budget
        return
    try:
        step = await ctx.complete(
            RECUPERADOR_SYSTEM,
            transcript + "\n\nNo more tool rounds. Return final now.",
            RECUPERADOR_SCHEMA,
        )
        _calls, final = parse_tool_step(step)
        if final is not None:
            ctx.add_findings(final.get("findings"), source_year)
    except (_BudgetOut, RuntimeError):
        pass


async def _ground_claim(ctx: _ClaimCtx, plan: dict) -> None:
    """Deterministic grounding before the model loop: WB + multi-query web.

    F2-2: a deterministic WB row package first (FALSE-claim recall), then
    >=3 diverse queries whose pooled hits are fetched primary-first.
    F2-7: the web pool is skipped on a support match OR a code
    refutation; otherwise the >=3 queries run concurrently (cap 4,
    order-preserving) and fetches stay serial primary-first.
    """

    def _one_query(query: str) -> list[dict]:
        try:
            res = ctx.tools.web_search(query, SEARCH_TOP_K)
        except Exception:
            return []
        results = res.get("results", []) if isinstance(res, dict) else []
        return [h for h in results or [] if isinstance(h, dict)]

    def _ground_sync() -> None:
        if plan.get("indicator") and plan.get("iso3"):
            ctx.tools.wb_lookup(plan["indicator"], plan["iso3"],
                                plan.get("year"))
            _maybe_add_wb_fallback(ctx, plan)
        if (ctx.config.early_exit_enabled
                and early_exit_reason(ctx.packages, ctx.text) is not None):
            return  # F2-3: primary match in hand; skip the web pool
        if (ctx.config.code_refutation_enabled
                and code_refutation_reason(
                    ctx.packages, ctx.text, plan) is not None):
            return  # F2-7: code refutation in hand; skip the web pool
        queries = build_search_queries(plan, ctx.text)
        per_query: list[list[dict]] = []
        if ctx.config.ground_parallel and len(queries) > 1:
            try:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(
                        max_workers=GROUND_QUERY_WORKERS) as pool:
                    # map preserves query order: the pooled ranking stays
                    # deterministic regardless of completion order.
                    per_query = list(pool.map(_one_query, queries))
            except Exception:
                per_query = [_one_query(q) for q in queries]
        else:
            per_query = [_one_query(q) for q in queries]
        pooled: list[dict] = []
        seen: set[str] = set()
        for results in per_query:
            for hit in results:
                url = str(hit.get("url", ""))
                if not url or url in seen:
                    continue
                seen.add(url)
                pooled.append(hit)
        for hit in rank_hits_primary_first(pooled)[:GROUND_FETCH_K]:
            url = str(hit.get("url", ""))
            if url and ctx.docs.find(url) is None:
                ctx.tools.fetch_url(url)
        if not ctx.packages:  # V5-CATB: one bounded altroute attempt
            _maybe_add_altroutes(ctx)

    await asyncio.to_thread(_ground_sync)


def _expired_note(ctx: _ClaimCtx, stage: str) -> str:
    """Honest degraded note for a lapsed per-claim budget (F2-3)."""
    try:
        budget = float(ctx.config.claim_deadline_s)
    except (TypeError, ValueError):
        budget = CLAIM_DEADLINE_S
    kept = len(ctx.packages)
    return (f"claim deadline {budget:g}s exceeded at {stage} "
            f"({kept} packages kept)")


async def _run_claim_inner(ctx: _ClaimCtx) -> str:
    """Full per-claim pipeline. Returns the terminal note."""
    # 1. ESTRUCTURADOR (single structured call).
    try:
        raw_plan = await ctx.complete(
            ESTRUCTURADOR_SYSTEM,
            f"claim_id: {ctx.claim_id}\nclaim: {ctx.text}",
            ESTRUCTURADOR_SCHEMA,
        )
    except _BudgetOut:
        return "budget exhausted at estructurador"
    except Exception as exc:
        return f"estructurador failed: {type(exc).__name__}"
    plan = _merge_plan(raw_plan, ctx.text)

    # 2. Deterministic grounding (WB API + web search + fetch).
    try:
        await _ground_claim(ctx, plan)
    except Exception as exc:
        log.warning("grounding failed for %s: %r", ctx.claim_id, exc)

    # F2-3: a primary verbatim figure already matching the claim closes
    # the model rounds (the verifier still decides from kept packages).
    if ctx.config.early_exit_enabled:
        reason = early_exit_reason(ctx.packages, ctx.text)
        if reason is not None:
            ctx.exit_taxonomy = "exit_support_fast"
            return f"early-exit(grounding): {reason}"
    # F2-7: a code WB row refuting the claim also closes the rounds.
    if ctx.config.code_refutation_enabled:
        refute = code_refutation_reason(ctx.packages, ctx.text, plan)
        if refute is not None:
            ctx.exit_taxonomy = "exit_refute_fast"
            return f"early-exit(grounding): {refute}"
    # F2-7: grounding did not fast-path; record the general cause for
    # the claim.no_exit telemetry (emitted in run_claim). When a
    # fast-path WOULD fire but its kill-switch is off, name the switch
    # (revert/debug telemetry, never a verdict).
    try:
        tax = no_exit_reason(ctx.packages, ctx.text, plan)
    except Exception:
        tax = "no_exit_unknown"
    if tax == "exit_support_fast" and not ctx.config.early_exit_enabled:
        tax = "no_exit_support_disabled"
    if tax == "exit_refute_fast" and not ctx.config.code_refutation_enabled:
        tax = "no_exit_refutation_disabled"
    ctx.exit_taxonomy = tax

    # 3. RECUPERADOR tool loop over grounded docs.
    seed = (
        f"claim_id: {ctx.claim_id}\nclaim: {ctx.text}\n"
        f"plan: {json.dumps(plan, ensure_ascii=False)}\n\n"
        f"{_PREFER_FETCHED_NUDGE}\n\n"
        f"fetched documents:\n{ctx.docs.render()[:9000] or '(none yet — use your tools)'}"
    )
    try:
        await _recuperador_loop(ctx, seed, ctx.config.max_tool_rounds,
                                plan.get("year"))
    except _BudgetOut:
        return "budget exhausted at recuperador"
    except Exception as exc:
        log.warning("recuperador failed for %s: %r", ctx.claim_id, exc)
    if ctx.early_abort is not None:  # F2-9: sterile round, rest skipped
        return f"early-abort(recuperador): {ctx.early_abort}"
    if ctx.expired():  # F2-3: graceful abort (packages kept, honest note)
        return _expired_note(ctx, "recuperador")

    # F2-3: same match after the model loop skips the redteam round.
    if ctx.config.early_exit_enabled:
        reason = early_exit_reason(ctx.packages, ctx.text)
        if reason is not None:
            return f"early-exit(recuperador): {reason}"

    # 4. REDTEAM counter-query -> second retrieval round.
    # F2-7: deferred by default (advisory; the verdict never depends on
    # it). True restores the blocking round for revert/debug.
    if not ctx.config.redteam_enabled:
        return "done (redteam deferred)"
    if ctx.expired():  # F2-3: no new round on a lapsed budget
        return _expired_note(ctx, "redteam")
    try:
        red = await ctx.complete(
            REDTEAM_SYSTEM,
            f"claim: {ctx.text}\nevidence so far: "
            f"{json.dumps([p['quote'] for p in ctx.packages], ensure_ascii=False)[:2000]}",
            REDTEAM_SCHEMA,
        )
    except (_BudgetOut, RuntimeError, Exception):
        return "redteam skipped"
    counter = red.get("counter_query") if isinstance(red, dict) else None
    skip = bool(isinstance(red, dict) and red.get("skip"))
    if skip or not (isinstance(counter, str) and counter.strip()):
        return "redteam skipped"
    def _redteam_ground() -> None:
        res = ctx.tools.web_search(counter.strip()[:300], 2)
        hits = res.get("results", []) if isinstance(res, dict) else []
        for hit in (hits or [])[:1]:
            if not isinstance(hit, dict) or not hit.get("url"):
                continue
            url = str(hit["url"])
            if ctx.docs.find(url) is None:
                ctx.tools.fetch_url(url)

    try:
        await asyncio.to_thread(_redteam_ground)
        seed2 = (
            f"claim_id: {ctx.claim_id}\nclaim: {ctx.text}\n"
            f"REDTEAM counter-query was: {counter.strip()[:300]}\n"
            f"{_PREFER_FETCHED_NUDGE}\n\n"
            f"fetched documents:\n{ctx.docs.render()[:9000]}"
        )
        await _recuperador_loop(ctx, seed2, 2, plan.get("year"))
    except (_BudgetOut, RuntimeError, Exception) as exc:
        log.warning("redteam round failed for %s: %r", ctx.claim_id, exc)
    return "done"


async def run_claim(bus: Any, tracker: Any, claim_id: str, text: str,
                    config: SwarmConfig | None = None) -> ClaimResult:
    """Investigate one claim; publish claim.state + claim.evidence.

    Always publishes exactly one ``claim.evidence`` event (possibly
    with zero packages). Never raises on model/network failures:
    degradation is recorded in the result + event.
    """
    config = config or SwarmConfig()
    ctx = _ClaimCtx(claim_id, text, config)

    # investigando (bootstrap escuchada when the detector never saw it).
    try:
        state = tracker.state(claim_id) if tracker is not None else None
        if tracker is not None:
            if state is None:
                await tracker.mark_heard(
                    claim_id, text=text, actor="m2b-swarm")
            if tracker.state(claim_id) == "escuchada":
                await tracker.set_state(
                    claim_id, "investigando", actor="m2b-swarm")
    except Exception as exc:
        log.warning("tracker transition failed for %s: %r", claim_id, exc)

    llm = config.llm
    if llm is None or not bool(getattr(llm, "available", True)):
        return await _finish(
            bus, ctx, degraded=True, note="no model available")

    deadline = config.claim_deadline_s
    hard_timeout = float(deadline or 0.0) + CLAIM_DEADLINE_SLACK_S
    try:
        note = await asyncio.wait_for(_run_claim_inner(ctx),
                                      timeout=hard_timeout)
        degraded = note.startswith(
            ("budget exhausted", "estructurador failed",
             "claim deadline", "early-abort"))
        if note.startswith("early-exit"):  # F2-3: latency telemetry event
            await _publish_early_exit(bus, ctx, note)
        if note.startswith("early-abort"):  # F2-9: sterile-loop telemetry
            await _publish_early_abort(bus, ctx, note)
        if ctx.exit_taxonomy.startswith("no_exit"):  # F2-7: why not fast
            await _publish_no_exit(bus, ctx, ctx.exit_taxonomy)
        return await _finish(bus, ctx, degraded=degraded, note=note)
    except (asyncio.TimeoutError, TimeoutError):
        return await _finish(
            bus, ctx, degraded=True,
            note=f"deadline {hard_timeout:g}s exceeded (hard backstop)")
    except Exception as exc:  # never break the swarm on one claim
        log.warning("claim %s failed: %r", claim_id, exc)
        return await _finish(
            bus, ctx, degraded=True, note=f"error: {type(exc).__name__}")


async def _publish_early_exit(bus: Any, ctx: _ClaimCtx,
                              note: str) -> None:
    """Emit ``claim.early_exit`` telemetry (F2-3; never breaks the run)."""
    if bus is None:
        return
    try:
        await bus.publish({
            "type": "claim.early_exit",
            "claim_id": ctx.claim_id,
            "reason": note,
            "n_packages": len(ctx.packages),
        })
    except Exception as exc:
        log.warning("claim.early_exit publish failed: %r", exc)


async def _publish_early_abort(bus: Any, ctx: _ClaimCtx,
                             note: str) -> None:
    """Emit ``claim.early_abort`` telemetry (F2-9; never breaks the run)."""
    if bus is None:
        return
    try:
        await bus.publish({
            "type": "claim.early_abort",
            "claim_id": ctx.claim_id,
            "reason": note,
            "n_packages": len(ctx.packages),
        })
    except Exception as exc:
        log.warning("claim.early_abort publish failed: %r", exc)


async def _publish_no_exit(bus: Any, ctx: _ClaimCtx, reason: str) -> None:
    """Emit ``claim.no_exit`` telemetry (F2-7; never breaks the run)."""
    if bus is None:
        return
    try:
        await bus.publish({
            "type": "claim.no_exit",
            "claim_id": ctx.claim_id,
            "reason": reason,
            "n_packages": len(ctx.packages),
        })
    except Exception as exc:
        log.warning("claim.no_exit publish failed: %r", exc)


async def _finish(bus: Any, ctx: _ClaimCtx, degraded: bool,
                  note: str) -> ClaimResult:
    result = ClaimResult(
        claim_id=ctx.claim_id,
        packages=list(ctx.packages),
        degraded=degraded,
        note=note,
        cost_usd=ctx.usage.cost_usd(),
        usage=ctx.usage.as_dict(),
    )
    if bus is not None:
        try:
            await bus.publish({
                "type": "claim.evidence",
                "claim_id": ctx.claim_id,
                "packages": result.packages,
                "degraded": degraded,
                "note": note,
                "cost_usd": result.cost_usd,
                "usage": result.usage,
            })
        except Exception as exc:
            log.warning("claim.evidence publish failed: %r", exc)
    return result


async def run_swarm(bus: Any, tracker: Any,
                    claims: list[tuple[str, str] | dict],
                    config: SwarmConfig | None = None) -> SwarmReport:
    """Investigate N claims in parallel (semaphore ``max_concurrency``)."""
    config = config or SwarmConfig()
    started = _utcnow_iso()
    width = max(1, config.max_concurrency)
    sem = asyncio.Semaphore(width)
    # F2-3 contention telemetry: peak simultaneous bodies + worst wait.
    telemetry = {"inflight": 0, "peak": 0, "wait_max": 0.0}

    async def _one(claim_id: str, text: str) -> ClaimResult:
        queued_at = time.monotonic()
        async with sem:
            telemetry["wait_max"] = max(
                telemetry["wait_max"], time.monotonic() - queued_at)
            telemetry["inflight"] += 1
            telemetry["peak"] = max(telemetry["peak"],
                                    telemetry["inflight"])
            try:
                return await run_claim(bus, tracker, claim_id, text, config)
            finally:
                telemetry["inflight"] -= 1

    async def _runner() -> list[ClaimResult]:
        tasks = []
        for item in claims:
            if isinstance(item, dict):
                claim_id = str(item.get("claim_id") or item.get("id"))
                text = str(item.get("text", ""))
            else:
                claim_id, text = str(item[0]), str(item[1])
            tasks.append(asyncio.create_task(_one(claim_id, text)))
        return [await t for t in tasks]

    results = await _runner()
    total_cost = sum(r.cost_usd for r in results)
    total_usage = {
        "prompt_tokens": sum(r.usage.get("prompt_tokens", 0) for r in results),
        "completion_tokens": sum(
            r.usage.get("completion_tokens", 0) for r in results),
        "model_calls": sum(r.usage.get("model_calls", 0) for r in results),
    }
    return SwarmReport(
        results=results,
        total_cost_usd=total_cost,
        total_usage=total_usage,
        started_at=started,
        finished_at=_utcnow_iso(),
        max_concurrency=width,
        peak_inflight=telemetry["peak"],
        sem_wait_s_max=round(telemetry["wait_max"], 3),
    )


__all__ = [
    "CLAIM_DEADLINE_S",
    "GROUND_FETCH_K",
    "GROUND_QUERY_WORKERS",
    "MAX_CONCURRENCY",
    "MAX_TOOL_ROUNDS",
    "TOKEN_BUDGET_PER_CLAIM",
    "SwarmConfig",
    "ClaimResult",
    "SwarmReport",
    "build_package",
    "build_search_queries",
    "code_refutation_reason",
    "doc_excerpts",
    "early_abort_reason",
    "early_exit_reason",
    "excerpt_needles_for_claim",
    "no_exit_reason",
    "numbers_from_quote",
    "rank_hits_primary_first",
    "row_year_for_quote",
    "run_claim",
    "run_swarm",
    "source_type_for_url",
    "tables_from_html",
    "validate_package_dict",
    "wb_fallback_eligible",
    "wb_row_quote_for",
    "web_search",
    "windows_around",
]
