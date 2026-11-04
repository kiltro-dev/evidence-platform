> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# Judge mode (Evidence Atlas demo)

One-click examples replay **real cached runs** instantly at $0 — no fetch,
no model cost, works even with the service down. Direct Run buttons always
analyze live.

## How it works

- The input card has a **Demo mode** toggle: `Judge (cached, $0)` (default)
  or `Live (real analysis)`. The choice persists in `localStorage`.
- In Judge mode, each one-click example replays an embedded
  `<script type="application/json">` payload (`#judge-s0..3` single reports,
  `#judge-b0..1` batch bodies) through the normal `render`/`renderBatch`
  paths, with a green "Cached demo run" banner on top.
- Cached reports were generated with `persist: false`, so replay sets **no**
  deep-link and touches no history (the ids are not re-fetchable).
- Any cache miss or shape mismatch falls back to the live submit path —
  never a dead click.

## Cache provenance (all real `muse-spark-1.3` runs, 2026-11-01)

| File | Source | Result |
|---|---|---|
| `eval/results/judge_cache/single_us_gdp.json` | `/api/analyze`, US GDP 2023 | `partially_supported` (precision cap) |
| `eval/results/judge_cache/single_japan_false.json` | `/api/analyze`, Japan 500M | `contradicted` |
| `eval/results/judge_cache/single_green_tea.json` | `/api/analyze`, green tea | `partially_supported` |
| `eval/results/judge_cache/single_german_jobs.json` | `/api/analyze`, German jobs | `supported` |
| `eval/results/judge_cache/batch_two_economy.json` | `/api/check`, 2 claims | 1 supported + 1 partial, 0 errors |
| `eval/results/judge_cache/batch_mixed.json` | `/api/check`, 2 claims | 1 supported + 1 partial, 0 errors |

Request shape for all: `{limit: 5, domain: "auto", persist: false}`.
Generation cost: 439,905 in / 53,986 out tokens ≈ **$0.78** at Standard
Spark pricing ($1.25/in + $4.25/out per 1M). `eval/results/` is gitignored:
**force-add** the six files above so judges can audit them:

```bash
git add -f eval/results/judge_cache/*.json
```

The payloads embedded in `web/index.html` are byte-identical to those files
(verified by extraction + diff after injection).

## SSE batch (live path)

Live batch checks POST `/api/check/stream` first (`fetch` + `ReadableStream`,
`data:` lines): `started` → per-claim chips (`Claim N: <stance>`) and a
`Checked i of N…` line → final `done` renders the batch. Fallback to one
plain POST `/api/check` happens on: transport failure, HTTP 5xx, a non-SSE
content type, or a stream cut without `done` (the progress line says so).
Deterministic 4xx (validation/auth/quota) renders its card directly — a POST
retry would answer the same. Cancel, 5-minute timeout, and run supersession
behave exactly as before (same typed cards). Stream mocks in tests must use
`contentType: 'text/event-stream'`.

## Disclosure

The UI header and the README demo section both carry:

> Demo notice: pasted text is sent to Meta's Model API (Standard no-training tier); don't paste sensitive data.

Covered by `tests/e2e-browser/specs/judge-mode.spec.js` (page + README).
