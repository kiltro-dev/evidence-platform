"""End-to-end demo: exercise all 3 MCP tools against a local server.

Spawns ``mcp_server/server.py`` over stdio with a real MCP client, then::

    1. verify_claim    — one economic claim  -> stance/summary/receipt_id
    2. verify_document — two-sentence text   -> rollup + per-claim reports
    3. get_receipt     — re-fetch (1)'s full report by receipt_id

Runs the real in-process pipeline with ``llm=None`` (heuristic path, no
model spend; public connectors may be contacted). Reports persist to the
local SQLite store (``data/atlas.db`` or ``$ATLAS_DB``).

Run from the repo root::

    python mcp_server/example.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server" / "server.py"

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def _show(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])


def _result_json(result) -> object:
    """Unwrap a CallToolResult's first text block as JSON (or raw text)."""
    blocks = getattr(result, "content", []) or []
    text = "".join(getattr(b, "text", "") for b in blocks)
    try:
        return json.loads(text)
    except ValueError:
        return {"_raw": text, "_structured": getattr(result, "structuredContent", None)}


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            _show("tools/list", [t.name for t in tools.tools])

            claim = "The US GDP reached $27.36 trillion in 2023."
            res = await session.call_tool("verify_claim", {"text": claim})
            verdict = _result_json(res)
            _show("verify_claim", verdict)

            doc = (
                "The US GDP reached $27.36 trillion in 2023. "
                "France's GDP was about $3 trillion in 2023."
            )
            res = await session.call_tool("verify_document", {"text": doc})
            batch = _result_json(res)
            if isinstance(batch, dict):
                slim = {
                    "rollup": batch.get("rollup"),
                    "claim_count": batch.get("claim_count"),
                    "errors": batch.get("errors"),
                    "stances": [
                        (r.get("conclusion") or {}).get("stance")
                        if isinstance(r, dict) else None
                        for r in (batch.get("reports") or [])
                    ],
                }
                _show("verify_document (slimmed)", slim)
            else:
                _show("verify_document", batch)

            receipt_id = verdict.get("receipt_id") if isinstance(verdict, dict) else None
            if receipt_id:
                res = await session.call_tool(
                    "get_receipt", {"receipt_id": receipt_id}
                )
                report = _result_json(res)
                if isinstance(report, dict) and "conclusion" in report:
                    _show("get_receipt (slimmed)", {
                        "report_id": report.get("report_id"),
                        "stance": (report.get("conclusion") or {}).get("stance"),
                        "summary": (report.get("conclusion") or {}).get("summary"),
                        "n_evidences": len(report.get("evidences") or []),
                        "n_comparisons": len(report.get("comparisons") or []),
                    })
                else:
                    _show("get_receipt", report)
            else:
                print("\n!! no receipt_id from verify_claim; skipping get_receipt")
                return 1
    print("\nOK: all 3 tools exercised against the local MCP server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
