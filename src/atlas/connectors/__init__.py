"""Connector protocol and registry. New domains plug in here."""

from __future__ import annotations

from typing import Protocol

from ..core.types import Evidence, StructuredClaim


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
