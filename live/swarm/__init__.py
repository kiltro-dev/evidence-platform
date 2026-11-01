"""Atlas Live M2b research swarm: parallel claim investigators.

Pipeline per claim (see ``coordinator.run_swarm``)::

    ESTRUCTURADOR (model) -> plan {entities, indicator, geo, year, query}
    deterministic grounding: World Bank API URL + web_search results
    fetch.py (responsible fetch + snapshot) -> snapshots in data/snap/
    RECUPERADOR (model tool loop) -> url + literal quote
    code validates quote verbatim in snapshot -> EvidencePackage dicts
    REDTEAM (model) -> counter-query -> second retrieval round

Degradation: without network/model a claim yields zero packages (M2a
marks it unverifiable). Quotes and snapshots are never invented: every
quote must be a verbatim substring of its snapshot, verified by code.
"""

from .coordinator import SwarmConfig, SwarmReport, run_claim, run_swarm
from .fetch import FetchBlocked, FetchError, FetchResult, fetch_url
from .roles import (
    ESTRUCTURADOR_SCHEMA,
    ESTRUCTURADOR_SYSTEM,
    RECUPERADOR_SCHEMA,
    RECUPERADOR_SYSTEM,
    REDTEAM_SCHEMA,
    REDTEAM_SYSTEM,
    TOOLS,
)

__all__ = [
    "SwarmConfig",
    "SwarmReport",
    "run_claim",
    "run_swarm",
    "FetchBlocked",
    "FetchError",
    "FetchResult",
    "fetch_url",
    "ESTRUCTURADOR_SYSTEM",
    "ESTRUCTURADOR_SCHEMA",
    "RECUPERADOR_SYSTEM",
    "RECUPERADOR_SCHEMA",
    "REDTEAM_SYSTEM",
    "REDTEAM_SCHEMA",
    "TOOLS",
]
