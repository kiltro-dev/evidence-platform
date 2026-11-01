# Agent self-correction loop (Pilar A-backend)

`atlas.agent.loop.run_agent(topic)` drafts a short economics/science analysis,
verifies each numeric claim through the `verify_claim` tool (in-process
`atlas.pipeline.run.analyze`, heuristic extract, `persist=False`), then
rewrites the draft correcting `contradicted` / `unverifiable` figures.

## Flow

1. **Draft** — chat call, no tools. Must contain ≥4 concrete figures
   (amounts, percents, counts with units; bare years don't count). One retry.
2. **Verify** — chat call with `tools=[verify_claim]`, `tool_choice="auto"`,
   bounded multi-round drain. If the model makes zero tool calls, one forced
   round with `tool_choice="required"` runs, so `finish_reasons` always
   contains `"tool_calls"` (unless `force_verify=False`).
3. **Rewrite** — chat call with all receipts in context; keeps supported
   figures, corrects/drops contradicted ones, qualifies/drops unverifiable.

Model: `muse-spark-1.3` via OpenAI-style chat completions. `max_tokens`
floor is 1500; the live demo needs **4000** (the verify turn exhausts 2000
in reasoning, `finish_reason=length` with empty content).

## Tool spec (OpenAI `tools` shape)

```json
{
  "type": "function",
  "function": {
    "name": "verify_claim",
    "description": "Verify one factual claim sentence against retrieved evidence. Returns stance, summary, report_id.",
    "parameters": {
      "type": "object",
      "properties": {"text": {"type": "string"}},
      "required": ["text"],
      "additionalProperties": false
    }
  }
}
```

## JSON contract (`run_agent` return, UI-ready)

```json
{
  "draft": "string (pre-correction analysis)",
  "tool_calls": [{"id": "call_…", "name": "verify_claim", "arguments": "{\"text\": \"…\"}"}],
  "final": "string (corrected analysis)",
  "receipts": [
    {
      "claim_text": "…",
      "stance": "supported|partially_supported|contradicted|unverifiable|not_assessable",
      "summary": "…",
      "report_id": "rep_…",
      "n_comparisons": 0,
      "n_evidences": 0,
      "error": "optional, only on failure"
    }
  ],
  "finish_reasons": ["stop", "tool_calls", "stop", "stop"],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0},
  "cost_usd": 0.0,
  "figures": {"draft": 0, "final": 0},
  "model": "muse-spark-1.3"
}
```

Cost: `(prompt×$1.25 + completion×$4.25)/1M`. Tool runs cost $0 (no LLM
inside `analyze` by default). All values are JSON-serializable.

## Measured result (live, 2026-11-01, `eval/agent_demo.py`, 1 run)

Topic: 2023 snapshot (US GDP, Germany unemployment, Japan population, China GDP).

| metric | value |
|---|---|
| draft figures | 9 |
| tool calls (model-issued) | 4 |
| finish_reasons | stop, tool_calls, stop, stop |
| receipt stances | partially_supported, contradicted, contradicted, unverifiable |
| final figures | 5 |
| contradicted figures before → after | 4 → 0 |
| tokens prompt / completion / total | 5572 / 5369 / 10941 |
| cost | $0.0298 (budget $3) |

The rewrite dropped the contradicted Germany/Japan figures and qualified
the unverifiable China figures; the supported US figures were kept.

Run 2 (TechLead re-run, same day, live): **8 → 2** contradicted figures
(5572→5717 prompt / 6830 completion tokens, $0.0362). The draft varies run
to run, so the before/after counts vary too — the loop reduces but does not
promise zero; both runs are on record.

## Tests

`tests/unit/test_agent_loop.py` — 18 tests, scripted chat doubles + stub
analyzer, 0 live calls. Includes a recorded `tool_calls` transcript fixture
covering both SDK-object and plain-dict response parsing.
