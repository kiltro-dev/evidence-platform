> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# Evidence Atlas MCP server (Pilar F)

Exposes verification as [Model Context Protocol](https://modelcontextprotocol.io/)
tools over **stdio**, so any MCP-capable agent (Claude Desktop, IDE agents,
custom harnesses) can fact-check claims without touching the codebase.

## Tools

| Tool | Input | Output |
|---|---|---|
| `verify_claim` | `text: string` — one factual claim | `{stance, summary, receipt_id}` |
| `verify_document` | `text: string` — pasted text | `{rollup, reports, claim_count, errors}` |
| `get_receipt` | `receipt_id: string` | full persisted report (JSON) |

`stance` is one of `supported`, `partially_supported`, `contradicted`,
`unverifiable`, `not_assessable`. `rollup` counts stances across the
document's sub-claims. Every error is a `{error, message}` dict
(`empty_text`, `receipt_not_found`, `analysis_failed`, ...), never an
exception over the wire.

## Architecture decision: in-process pipeline

The server calls `atlas.pipeline.run` **in-process** (`PYTHONPATH=src`)
with `llm=None` instead of proxying HTTP to a sidecar:

- **Why:** no second process to launch/supervise, no API keys, no per-call
  model spend ($0 per verification). The degraded path (heuristic
  extraction + deterministic rules) still retrieves public evidence and
  always returns a verdict with uncertainties.
- **Trade-off:** verdict quality is below the LLM-backed API, and public
  connectors (World Bank, OpenAlex) are contacted on each call. For the
  full-quality path, run the HTTP API (`docs/API.md`) with a model key.
- **Persistence:** reports save to the local SQLite store
  (`data/atlas.db`, overridable via `$ATLAS_DB`), so `receipt_id`s from
  `verify_claim` re-fetch via `get_receipt` even across restarts.

## Install & run

```bash
# Server-only dependencies (repo requirements.txt untouched):
python -m pip install -r mcp_server/requirements.txt   # mcp 1.x SDK, pydantic, httpx

# Start the stdio server (from repo root):
PYTHONPATH=src python mcp_server/server.py

# End-to-end demo of all 3 tools against a local server:
python mcp_server/example.py
```

`mcp_server/requirements.txt` pins `mcp>=1.12,<2`: mcp 2.x renamed
`FastMCP` to `MCPServer` and this server targets the stable 1.x API.

## Client configuration

Claude Desktop (`claude_desktop_config.json`), adjusting the path:

```json
{
  "mcpServers": {
    "evidence-atlas": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["/abs/path/to/evidence-platform/mcp_server/server.py"],
      "cwd": "/abs/path/to/evidence-platform"
    }
  }
}
```

`server.py` adds `src/` to `sys.path` itself, so no `PYTHONPATH` is needed
when launched this way. Set `"env": {"ATLAS_DB": "..."}` to redirect the
SQLite store.

## Files

- `mcp_server/server.py` — FastMCP stdio server + `do_*` tool functions
  (pipeline-injectable for tests).
- `mcp_server/example.py` — real stdio-client demo of all 3 tools.
- `mcp_server/requirements.txt` — minimal server dependencies.
- `tests/unit/test_mcp_tools.py` — stubbed-pipeline unit tests + tool
  registration/schema checks (offline; skips if `mcp` is not installed).
