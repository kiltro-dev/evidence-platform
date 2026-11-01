"""OpenAlex scholarly works connector (keyless by default, key supported).

Source: https://api.openalex.org — secondary index of scholarly metadata;
contextual evidence (titles/years/venues), direct only when abstracts or
full statements are retrieved. Docs: https://docs.openalex.org/

Quota note (observed 2026-11-01): keyless requests share a $0.10/day IP
budget and `mailto` no longer raises limits; a free key
(OPENALEX_API_KEY) gives the project its own $1/day budget. Set the env
var before any demo on shared/hackathon IPs.
"""

from __future__ import annotations

import os
import threading

import httpx

from ..core.types import Evidence, StructuredClaim
from . import as_provider_error, register

BASE_URL = "https://api.openalex.org"
TIMEOUT_S = 20.0
MAILTO = "evidence-atlas@example.org"


def search_works(query: str, per_page: int = 5) -> list[dict]:
    params = {
        "search": query,
        "per-page": per_page,
        "select": (
            "id,title,publication_year,doi,cited_by_count,"
            "authorships,primary_location,open_access,type,"
            "abstract_inverted_index"
        ),
        "mailto": MAILTO,
    }
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(f"{BASE_URL}/works", params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise as_provider_error(exc, f"openalex {query!r}") from exc
    if not isinstance(data, dict):
        raise as_provider_error(
            ValueError(f"unexpected payload type {type(data).__name__}"),
            f"openalex {query!r}",
        )
    return data.get("results", [])


def author_names(work: dict, limit: int = 3) -> list[str]:
    names = []
    for a in work.get("authorships", [])[:limit]:
        author = (a.get("author") or {}).get("display_name")
        if author:
            names.append(author)
    return names


def venue(work: dict) -> str:
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    return source.get("display_name", "") or ""


# Cap for reconstructed abstracts: keeps payloads and downstream prompts slim.
ABSTRACT_MAX_CHARS = 2000


def reconstruct_abstract(work: dict) -> str | None:
    """Rebuild plain-text abstract from OpenAlex `abstract_inverted_index`.

    The index maps each word to its token positions; inverting the map
    restores reading order. Returns None when the work carries no usable
    index (absent, wrong type, or empty after sanitizing). Output is
    capped at ABSTRACT_MAX_CHARS. Never raises on malformed input.
    """
    inv = work.get("abstract_inverted_index")
    if not isinstance(inv, dict) or not inv:
        return None
    try:
        positions: dict[int, str] = {}
        for word, idxs in inv.items():
            if not isinstance(word, str) or not word:
                continue
            if not isinstance(idxs, list):
                continue
            for i in idxs:
                # bool is an int subclass; exclude explicitly.
                if isinstance(i, bool) or not isinstance(i, int) or i < 0:
                    continue
                positions.setdefault(i, word)
        if not positions:
            return None
        # Sorted emission (not range allocation): robust to absurd indices.
        text = " ".join(w for _, w in sorted(positions.items())).strip()
        if not text:
            return None
        return text[:ABSTRACT_MAX_CHARS]
    except Exception:
        return None


class OpenAlexConnector:
    name = "openalex"
    domains = ("science", "general")

    def __init__(self) -> None:
        # Shared registry instance: thread-local query echo, same
        # rationale as WorldBankConnector (parallel batch + server).
        self._query_local = threading.local()

    @property
    def last_query(self) -> str | None:
        return getattr(self._query_local, "value", None)

    @last_query.setter
    def last_query(self, value: str | None) -> None:
        self._query_local.value = value

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        query = claim.subject or claim.raw_text
        if len(query) > 300:
            query = query[:300]
        self.last_query = query
        try:
            works = search_works(query, per_page=limit)
        except (httpx.HTTPError, ValueError) as exc:
            raise as_provider_error(exc, f"openalex {query!r}") from exc
        evidences: list[Evidence] = []
        for work in works:
            doi = work.get("doi", "")
            year = work.get("publication_year")
            evidences.append(
                Evidence(
                    claim_id=claim.claim_id,
                    connector=self.name,
                    source_name="OpenAlex",
                    source_url=doi or work.get("id"),
                    source_kind="secondary",
                    evidence_kind="contextual",
                    locator=f"{work.get('id', '')} ({year})",
                    excerpt=(
                        f"\"{work.get('title', '')}\" ({year}) — "
                        f"{', '.join(author_names(work)) or 'unknown authors'}"
                        f"{(' — ' + venue(work)) if venue(work) else ''} "
                        f"[cited by {work.get('cited_by_count', 0)}]"
                    ),
                    payload={
                        "openalex_id": work.get("id"),
                        "title": work.get("title"),
                        "year": year,
                        "doi": doi,
                        "cited_by_count": work.get("cited_by_count"),
                        "authors": author_names(work),
                        "venue": venue(work),
                        "type": work.get("type"),
                        "abstract": reconstruct_abstract(work),
                        "query": query,
                    },
                )
            )
        return evidences


register(OpenAlexConnector())
