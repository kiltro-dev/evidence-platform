"""M2b swarm coordinator: parallel asyncio claim investigation.

Per claim: ESTRUCTURADOR (plan) -> deterministic grounding (World Bank
API URL + web_search) -> responsible fetch + snapshots -> RECUPERADOR
(model tool loop) -> code-validated quotes -> REDTEAM counter-query ->
second retrieval round -> ``claim.evidence`` with EvidencePackage-compat
dicts (M2a ``live/evidence/package.py`` validates the final schema).

Resilience: semaphore-limited parallelism (4), 90 s deadline per claim,
per-claim token budget, graceful degradation (no network/model ->
zero packages, never invented quotes). All blocking I/O (model, fetch,
search) runs in worker threads; the event loop only orchestrates.
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
from pathlib import Path
from typing import Any, Callable

from .fetch import FetchResult, fetch_url
from .roles import (
    ESTRUCTURADOR_SCHEMA,
    ESTRUCTURADOR_SYSTEM,
    RECUPERADOR_SCHEMA,
    RECUPERADOR_SYSTEM,
    REDTEAM_SCHEMA,
    REDTEAM_SYSTEM,
    parse_tool_step,
)

log = logging.getLogger(__name__)

CLAIM_DEADLINE_S = 90.0
MAX_CONCURRENCY = 4
MAX_TOOL_ROUNDS = 4
MODEL_MAX_TOKENS = 1500
MODEL_REASONING_EFFORT = "low"
TOKEN_BUDGET_PER_CLAIM = 12000

# Caps for model-read excerpts (quotes stay verbatim: see _segments()).
DOC_EXCERPT_CHARS = 6000
SEARCH_TOP_K = 3
FETCH_TOP_K = 2

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

# Hosts whose snapshots count as M2a "primary" sources.
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


# ---------------------------------------------------------------------------
# model-read excerpts that keep quotes verbatim
# ---------------------------------------------------------------------------

def doc_excerpts(text: str, limit: int = DOC_EXCERPT_CHARS) -> str:
    """Readable excerpt where every line is a verbatim snapshot substring.

    HTML is split on tags (never reflowed): each surviving segment is a
    contiguous slice of the snapshot, so a quote copied from ONE segment
    greps exactly. Document order is kept (article leads come first);
    markup/data junk (chart JSON, template blobs, CSS) is dropped.
    """
    clean = _SCRIPT_STYLE_RE.sub(" ", text or "")
    out: list[str] = []
    budget = limit
    for raw in _TAG_SPLIT_RE.split(clean):
        if budget <= 0:
            break
        seg = _WS_RE.sub(" ", raw).strip()
        # Sentences carry quotable claims; tiny fragments are nav/TOC.
        # (Figure-dense API bodies are one big segment: unaffected.)
        if len(seg) < 60 and not (
                len(seg) >= 25 and " " in seg
                and any(ch.isdigit() for ch in seg)):
            continue
        if not seg or _is_junk_segment(seg):
            continue
        out.append(seg[:budget])
        budget -= len(out[-1]) + 1
    return "\n".join(out)


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
        out.append({"value": value, "unit": unit, "scale_raw": scale_raw})
    # de-dup, keep order, cap at a sane bound
    seen: set[str] = set()
    uniq = [n for n in out if not (repr(n) in seen or seen.add(repr(n)))]
    return uniq[:10]


def source_type_for_url(url: str) -> str:
    """M2a source_type: official statistics hosts are primary."""
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
    snapshot_dir: str | Path | None = None
    max_concurrency: int = MAX_CONCURRENCY
    claim_deadline_s: float = CLAIM_DEADLINE_S
    token_budget_per_claim: int = TOKEN_BUDGET_PER_CLAIM
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    model_max_tokens: int = MODEL_MAX_TOKENS
    reasoning_effort: str | None = MODEL_REASONING_EFFORT


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
        doc = self._docs.add(fetched)
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
# package builder (strict verbatim validation)
# ---------------------------------------------------------------------------

def build_package(claim_id: str, url: str, quote: str, doc: Doc,
                  source_year: int | None, index: int) -> dict | None:
    """Validate a finding and build an M2a-compatible package dict.

    Returns None (never raises on model-shaped input) when the quote
    is not a verbatim substring of the snapshot text, the URL does not
    match the fetched doc, or M2a validation rejects the dict.
    """
    if not isinstance(quote, str) or not quote.strip():
        return None
    text = doc.fetch.text or ""
    start = text.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    sha = doc.fetch.sha256
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
        "extractor": "model",
        "source_year": source_year,
    }
    try:
        json.dumps(pkg)  # bus contract: JSON-only events
    except (TypeError, ValueError):
        return None
    if not validate_package_dict(pkg):
        return None
    return pkg


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
        self.usage = _Usage()
        self.packages: list[dict] = []
        self._seen_quotes: set[tuple[str, str]] = set()

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
            # a 400 would just fail twice).
            self.usage.record(llm)
            if not _is_malformed_reply(exc):
                raise
            log.warning("malformed model reply, retrying once: %r", exc)
            out = await asyncio.to_thread(
                _call, system + "\n\nSTRICT: your reply was not pure JSON. "
                "Reply again with ONLY the JSON object.")
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
    for _ in range(max(1, max_rounds)):
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
        # not just result lists (bounded: first 2 unseen hits).
        for entry in tool_results:
            if entry["name"] != "web_search":
                continue
            result = entry["result"]
            if not isinstance(result, dict):
                continue
            for hit in (result.get("results") or [])[:FETCH_TOP_K]:
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
    # Rounds exhausted without final: one last forced-final call.
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
    """Deterministic grounding before the model loop: WB + web."""

    def _ground_sync() -> None:
        if plan.get("indicator") and plan.get("iso3"):
            ctx.tools.wb_lookup(plan["indicator"], plan["iso3"],
                                plan.get("year"))
        hits = ctx.tools.web_search(
            str(plan.get("web_query", "")), SEARCH_TOP_K)
        results = hits.get("results", []) if isinstance(hits, dict) else []
        for hit in (results or [])[:FETCH_TOP_K]:
            if not isinstance(hit, dict):
                continue
            url = str(hit.get("url", ""))
            if url and ctx.docs.find(url) is None:
                ctx.tools.fetch_url(url)

    await asyncio.to_thread(_ground_sync)


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

    # 3. RECUPERADOR tool loop over grounded docs.
    seed = (
        f"claim_id: {ctx.claim_id}\nclaim: {ctx.text}\n"
        f"plan: {json.dumps(plan, ensure_ascii=False)}\n\n"
        f"fetched documents:\n{ctx.docs.render()[:9000] or '(none yet — use your tools)'}"
    )
    try:
        await _recuperador_loop(ctx, seed, ctx.config.max_tool_rounds,
                                plan.get("year"))
    except _BudgetOut:
        return "budget exhausted at recuperador"
    except Exception as exc:
        log.warning("recuperador failed for %s: %r", ctx.claim_id, exc)

    # 4. REDTEAM counter-query -> second retrieval round.
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
    try:
        note = await asyncio.wait_for(_run_claim_inner(ctx), timeout=deadline)
        degraded = note.startswith(
            ("budget exhausted", "estructurador failed"))
        return await _finish(bus, ctx, degraded=degraded, note=note)
    except (asyncio.TimeoutError, TimeoutError):
        return await _finish(
            bus, ctx, degraded=True,
            note=f"deadline {deadline}s exceeded")
    except Exception as exc:  # never break the swarm on one claim
        log.warning("claim %s failed: %r", claim_id, exc)
        return await _finish(
            bus, ctx, degraded=True, note=f"error: {type(exc).__name__}")


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
    """Investigate N claims in parallel (semaphore 4)."""
    config = config or SwarmConfig()
    started = _utcnow_iso()
    sem = asyncio.Semaphore(max(1, config.max_concurrency))

    async def _one(claim_id: str, text: str) -> ClaimResult:
        async with sem:
            return await run_claim(bus, tracker, claim_id, text, config)

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
    )


__all__ = [
    "CLAIM_DEADLINE_S",
    "MAX_CONCURRENCY",
    "TOKEN_BUDGET_PER_CLAIM",
    "SwarmConfig",
    "ClaimResult",
    "SwarmReport",
    "build_package",
    "doc_excerpts",
    "numbers_from_quote",
    "run_claim",
    "run_swarm",
    "source_type_for_url",
    "validate_package_dict",
    "web_search",
    "windows_around",
]
