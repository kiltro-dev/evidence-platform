"""OpenAlex scholarly works connector (no API key required).

Source: https://api.openalex.org — secondary index of scholarly metadata;
contextual evidence (titles/years/venues), direct only when abstracts or
full statements are retrieved. Docs: https://docs.openalex.org/
"""

from __future__ import annotations

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
            "authorships,primary_location,open_access,type"
        ),
        "mailto": MAILTO,
    }
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


class OpenAlexConnector:
    name = "openalex"
    domains = ("science", "general")
    last_query: str | None = None

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
                        "query": query,
                    },
                )
            )
        return evidences


register(OpenAlexConnector())
