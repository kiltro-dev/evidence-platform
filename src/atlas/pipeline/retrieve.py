"""Parallel connector fan-out for the retrieve stage.

retrieve_all() runs every connector's retrieve() on its own thread and
returns (evidence, errors) with deterministic ordering. Semantics mirror
run.py's sequential loop exactly:

- per-connector retry: one retry after RETRY_DELAY_S on transient
  ProviderError only (same rule as run._retrieve_with_retry);
- per-connector timeouts: each connector's own httpx timeout governs its
  call; the executor adds NO global timeout, exactly as sequential;
- isolation: an exception from one connector is captured into `errors`
  and never kills the others (run.py catches Exception per connector);
- ordering: evidence is concatenated in connector input order and each
  connector's own list order is kept; errors follow connector order.

`errors` is a list of (connector_name, exception) in connector order,
containing one entry per failed connector. The exception objects keep
their types (InvalidGeoError / ProviderError / other), so the caller can
apply run.py's existing per-type handlers unchanged.

Note: RETRY_DELAY_S duplicates run.RETRY_DELAY_S deliberately — importing
run here would be a cycle (run imports this module). Keep the two in sync,
or consolidate behind a shared constant later.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from ..connectors import ProviderError, SourceConnector
from ..core.types import Evidence, StructuredClaim

# Mirrors run.RETRY_DELAY_S (monkeypatched to 0 in tests).
RETRY_DELAY_S = 2.0


def _retrieve_with_retry(conn, claim, limit: int, retry_delay_s: float):
    """Call conn.retrieve, retrying once on transient ProviderError.

    Same rule as run._retrieve_with_retry: permanent ProviderErrors,
    InvalidGeoError and anything else propagate immediately.
    """
    try:
        return conn.retrieve(claim, limit=limit)
    except ProviderError as exc:
        if not exc.transient:
            raise
        time.sleep(retry_delay_s)
        return conn.retrieve(claim, limit=limit)


def _retrieve_one(
    conn: SourceConnector,
    claim: StructuredClaim,
    limit: int,
    retry_delay_s: float,
) -> tuple[bool, object]:
    """Run one connector, capturing its exception instead of raising."""
    try:
        return (True, _retrieve_with_retry(conn, claim, limit, retry_delay_s))
    except Exception as exc:  # same net as run.py's per-connector handler
        return (False, exc)


def retrieve_all(
    connectors: Sequence[SourceConnector],
    claim: StructuredClaim,
    limit: int = 5,
    *,
    retry_delay_s: float | None = None,
) -> tuple[list[Evidence], list[tuple[str, BaseException]]]:
    """Retrieve from all connectors in parallel.

    Returns (evidence, errors): evidence is every connector's list
    concatenated in connector input order; errors holds
    (connector_name, exception) per failed connector, in connector order.
    `retry_delay_s` defaults to this module's RETRY_DELAY_S (None reads
    the current module value so tests can monkeypatch it, as with run's).
    """
    if retry_delay_s is None:
        retry_delay_s = RETRY_DELAY_S
    ordered = list(connectors)
    if not ordered:
        return ([], [])
    with ThreadPoolExecutor(
        max_workers=len(ordered), thread_name_prefix="atlas-retrieve"
    ) as pool:
        # executor.map yields results in input order however threads finish.
        outcomes = list(
            pool.map(
                lambda c: _retrieve_one(c, claim, limit, retry_delay_s),
                ordered,
            )
        )
    evidences: list[Evidence] = []
    errors: list[tuple[str, BaseException]] = []
    for conn, (ok, payload) in zip(ordered, outcomes):
        if ok:
            evidences.extend(payload)  # type: ignore[arg-type]
        else:
            errors.append((conn.name, payload))  # type: ignore[arg-type]
    return (evidences, errors)
