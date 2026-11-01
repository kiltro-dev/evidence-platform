"""Evidence Atlas MCP server (Pilar F, Evidence Atlas V3).

Exposes the verification pipeline as MCP tools over stdio:

- ``verify_claim(text)`` -> ``{stance, summary, receipt_id}``
- ``verify_document(text)`` -> ``{rollup, reports, claim_count, errors}``
- ``get_receipt(receipt_id)`` -> full persisted report (or an error dict)

Transport decision: the tools call the pipeline **in-process**
(``PYTHONPATH=src``, ``atlas.pipeline.run``), with no LLM client
(``llm=None``: heuristic extraction + deterministic rules, degraded-safe
when sources are unreachable). Rationale: zero extra moving parts for an
agent host — no sidecar HTTP server to launch or supervise, no API keys,
no per-call model spend. See ``docs/MCP.md`` for the trade-off note and
for how to point the server at a live LLM instead.

Run from the repo root::

    PYTHONPATH=src python mcp_server/server.py        # stdio server
    python mcp_server/example.py                      # end-to-end demo
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from atlas.pipeline.run import analyze, check_document  # noqa: E402
from atlas.storage.store import get_report  # noqa: E402

mcp = FastMCP("evidence-atlas")

# Module-level indirection so unit tests can stub the pipeline without
# touching the network or the model (also see the ``*_fn`` kwargs below).
_analyze_fn: Callable[..., Any] = analyze
_check_fn: Callable[..., Any] = check_document
_get_fn: Callable[..., Any] = get_report


def _err(code: str, message: str) -> dict:
    return {"error": code, "message": message[:500]}


def do_verify_claim(
    text: str,
    *,
    analyze_fn: Callable[..., Any] | None = None,
    persist: bool = True,
) -> dict:
    """Verify one claim; returns ``{stance, summary, receipt_id}``.

    ``persist=True`` (default) stores the report so the returned
    ``receipt_id`` is retrievable later via :func:`do_get_receipt`.
    """
    if not text or not text.strip():
        return _err("empty_text", "text must be a non-empty claim.")
    run = analyze_fn or _analyze_fn
    try:
        report = run(text, llm=None, persist=persist)
    except Exception as exc:  # noqa: BLE001 — surfaced as a tool error
        return _err("analysis_failed", str(exc))
    conclusion = getattr(report, "conclusion", None)
    return {
        "stance": getattr(conclusion, "stance", "unverifiable"),
        "summary": getattr(conclusion, "summary", ""),
        "receipt_id": getattr(report, "report_id", ""),
    }


def do_verify_document(
    text: str,
    *,
    check_fn: Callable[..., Any] | None = None,
    persist: bool = True,
) -> dict:
    """Verify pasted text claim-by-claim; returns rollup + per-claim reports."""
    if not text or not text.strip():
        return _err("empty_text", "text must be non-empty.")
    run = check_fn or _check_fn
    try:
        batch = run(text, llm=None, persist=persist)
    except Exception as exc:  # noqa: BLE001 — surfaced as a tool error
        return _err("analysis_failed", str(exc))
    return {
        "rollup": batch.get("rollup", {}),
        "reports": batch.get("reports", []),
        "claim_count": batch.get("claim_count", 0),
        "errors": batch.get("errors", 0),
    }


def do_get_receipt(
    receipt_id: str,
    *,
    get_fn: Callable[..., Any] | None = None,
) -> dict:
    """Fetch a persisted report by id (``verify_claim``'s ``receipt_id``)."""
    if not receipt_id or not receipt_id.strip():
        return _err("empty_id", "receipt_id must be non-empty.")
    run = get_fn or _get_fn
    try:
        report = run(receipt_id)
    except Exception as exc:  # noqa: BLE001 — surfaced as a tool error
        return _err("store_failed", str(exc))
    if report is None:
        return _err("receipt_not_found", f"No report {receipt_id!r}.")
    dump = getattr(report, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(report, dict):
        return report
    return _err("bad_report", "Stored report has an unexpected shape.")


@mcp.tool()
def verify_claim(text: str) -> dict:
    """Verify a single factual claim against retrieved evidence.

    Returns {"stance", "summary", "receipt_id"}; the receipt_id re-fetches
    the full persisted report via get_receipt.
    """
    return do_verify_claim(text)


@mcp.tool()
def verify_document(text: str) -> dict:
    """Verify pasted text claim-by-claim.

    Returns {"rollup" (stance counts), "reports" (per-claim reports),
    "claim_count", "errors"}.
    """
    return do_verify_document(text)


@mcp.tool()
def get_receipt(receipt_id: str) -> dict:
    """Fetch a full persisted verification report by receipt_id."""
    return do_get_receipt(receipt_id)


if __name__ == "__main__":
    mcp.run()
