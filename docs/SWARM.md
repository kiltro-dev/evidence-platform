# Swarm (Evidence Atlas V3, Pilar C)

Four advisory model roles around the deterministic pipeline, plus a
long-document runner with per-document time and cost reporting.

## The one rule

**The pipeline verdict is the verdict.** Roles propose and criticize;
a deterministic arbiter in code only *adds* uncertainties. Roles can
never flip a stance — a conflicted claim keeps its pipeline stance
with a `major` uncertainty attached, never `contradicted` by vote.

## Roles (`src/atlas/swarm/roles.py`)

| Role | Output (strict schema) | Use |
|---|---|---|
| estructurador | StructuredClaim JSON (`STRUCTURE_SCHEMA`, imported from `pipeline/extract`, not copied) | Compared against the pipeline extraction; quantity/domain mismatch → `major` `swarm_struct_mismatch` |
| recuperador | `{"candidates": [{source, indicator, rationale}]}` | Each candidate is checked in code against retrieved connectors; uncovered → `minor` `swarm_uncovered_candidate` |
| red_team | `{"attacks": [{claim_span, weakness, severity}]}` | Non-verbatim spans dropped in code; each attack → `minor` note; any `high` → `major` `swarm_redteam_challenge` |
| explicador | `{"summary", "caveats"}` | Stored on the arbiter record as `plain_summary`; never merged into the conclusion |

All role calls use `reasoning_effort="low"`, `max_tokens=1500`, strict
`json_schema` decoding. Model failure → role degrades (`ok: false`
with a note), never raises, never blocks the claim.

## Coordinator (`src/atlas/swarm/coordinator.py`)

- `analyze_with_swarm(claim_text, llm, ...)`: pipeline `analyze()` +
  4 roles on a 5-worker ThreadPool (one forked client per worker, so
  token usage attributes exactly). Then `arbitrate()` applies the
  deterministic rules above. `persist` defaults to `False` (eval
  harness; must not spam the report store on long runs).
- `run_roles_parallel(...)`: the four roles concurrently, keyed dict.
- `chunk_document(text)`: greedy paragraph-aware chunking under the
  splitter limit; every input char lands in exactly one chunk.
- `check_document_swarm(text, llm, ...)`: chunk → `split_claims` per
  chunk → dedupe → `analyze_with_swarm` per claim (sequential across
  claims; each already fans out to 5 workers). A failing claim becomes
  an error entry, never kills the document.

## Cost model

Token counts come from each forked client's `last_usage` plus the
pipeline report's per-stage usage — exact. USD is an **estimate** at
assumed rates (`DEFAULT_PRICING`: $0.0003/1k prompt, $0.0012/1k
completion), labeled estimate everywhere. Spike reference: a 219k-token
document costs roughly $0.10–0.20 per 100 pages at these rates.

## Demo (`eval/swarm_demo.py`)

One live run over `eval/fixtures/long_report_synth.txt` (synthetic,
22 pages, 32 invented figures — no real data):

```sh
.venv/bin/python eval/swarm_demo.py [--max-claims N]
```

Prints claims mapped, verdict roll-up, figures mapped, wall-time,
tokens, USD estimate; saves JSON to `eval/results/`. Refuses to run
past a $4 rough estimate or without `MODEL_API_KEY`. Prints
usage/tokens only — never keys.

## Tests (`tests/unit/test_swarm.py`)

23 tests, doubles only (`ScriptedClient`/`FaultClient` keyed on the
`[ROLE:...]` prompt marker; pipeline monkeypatched with stub Reports).
0 live calls. Covers: arbiter verdict-source rule, never-contradicted-
by-vote, struct/candidate/red-team rules, role validation (verbatim,
dedupe, strength flags), parallel fork-per-role, usage/cost math,
chunk coverage, offline end-to-end, error isolation, max-claims cap.
