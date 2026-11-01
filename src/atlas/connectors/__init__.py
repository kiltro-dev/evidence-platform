"""Connector protocol and registry. New domains plug in here."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..core.types import Evidence, StructuredClaim


class ProviderError(Exception):
    """Transport or decoding failure from a source connector.

    Never used for "no match" (unknown indicator/geo yields ``[]``).
    ``transient`` is True for timeouts/connect failures (worth a retry)
    and False for HTTP status errors and undecodable payloads.
    """

    def __init__(self, message: str = "", transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class InvalidGeoError(Exception):
    """A geo reference failed the ISO3 allowlist; no request was sent."""

    def __init__(self, iso3: object) -> None:
        self.iso3 = iso3
        super().__init__(f"invalid iso3: {iso3!r}")


def as_provider_error(exc: Exception, context: str) -> ProviderError:
    """Classify a transport/decode failure as transient or permanent."""
    transient = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
    if isinstance(exc, httpx.HTTPStatusError):
        # Rate limits and upstream outages are worth a retry; other 4xx are not.
        transient = exc.response.status_code == 429 or exc.response.status_code >= 500
    return ProviderError(f"{context}: {exc}", transient=transient)


class SourceConnector(Protocol):
    name: str
    domains: tuple[str, ...]

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        """Fetch candidate evidence for a structured claim. Never invents."""
        ...


REGISTRY: dict[str, SourceConnector] = {}


def register(connector: SourceConnector) -> SourceConnector:
    REGISTRY[connector.name] = connector
    return connector


def for_domain(domain: str) -> list[SourceConnector]:
    # A connector tagged "general" is a fallback for unclassified claims only;
    # domain-specific claims must not collect noise from unrelated sources.
    return [
        c
        for c in REGISTRY.values()
        if domain in c.domains or (domain == "general" and "general" in c.domains)
    ]
