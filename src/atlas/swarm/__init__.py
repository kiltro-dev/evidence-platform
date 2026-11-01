"""Role swarm: four advisory model roles around the deterministic pipeline.

Each role proposes (candidates, critiques, summaries) under a strict
decoding schema. Roles NEVER decide: the verdict always comes from
``atlas.pipeline.run.analyze`` and the coordinator's arbiter only adds
uncertainties in code. See docs/SWARM.md and coordinator.py.
"""

from __future__ import annotations

from .roles import (
    ESTRUCTURADOR_SCHEMA,
    ESTRUCTURADOR_SYSTEM,
    EXPLICADOR_SCHEMA,
    EXPLICADOR_SYSTEM,
    RECUPERADOR_SCHEMA,
    RECUPERADOR_SYSTEM,
    REDTEAM_SCHEMA,
    REDTEAM_SYSTEM,
    ROLE_NAMES,
)

__all__ = [
    "ROLE_NAMES",
    "ESTRUCTURADOR_SYSTEM",
    "ESTRUCTURADOR_SCHEMA",
    "RECUPERADOR_SYSTEM",
    "RECUPERADOR_SCHEMA",
    "REDTEAM_SYSTEM",
    "REDTEAM_SCHEMA",
    "EXPLICADOR_SYSTEM",
    "EXPLICADOR_SCHEMA",
]
