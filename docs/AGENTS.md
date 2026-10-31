# Agent design: a staged pipeline, not a multi-agent system

## Why no agent loop

The mission explicitly allows the simplest architecture that wins on
traceability. Evidence Atlas analyzes one claim through six fixed stages
with no tool-calling loop, no inter-agent messages, and no iteration
budgets to tune — because each stage has exactly one job and its output
schema is fixed. An agentic loop would add failure modes (runaway turns,
skipped checks, prompt-injected tool choice) without improving the
product's core promise: deterministic, auditable verification.

The "agents" below are therefore **roles**, each mapped to one module,
one input/output contract, and one actor badge in the audit trail.

## Roles

### 1. Claim structurer — `pipeline/extract.py`

- Input: raw claim text. Output: `StructuredClaim`.
- Primary worker: Muse Spark (`STRUCTURE_SYSTEM` JSON contract, T=0).
- Fallback: `models/heuristic.py` (regex quantities, years, countries,
  keyword domain/type classification).
- Constraint: LLM gaps in times/geos/quantities are backfilled from the
  heuristic pass so explicit text is never silently dropped.
- Failure: any LLM error → full heuristic result; audit shows `heuristic`.

### 2. Source router — `pipeline/run.py` (`for_domain`)

- Input: claim domain. Output: connector list.
- Pure code. Domain-tagged connectors only; `general`-tagged connectors
  run solely for unclassified claims (prevents cross-domain noise —
  found and fixed during live probing).

### 3. Evidence retriever — `connectors/`

- Input: structured claim. Output: `list[Evidence]`.
- World Bank: keyword→indicator match, country→ISO3 resolution, year span
  from claim times (default 2020–2023), live `fetch_observations`.
- OpenAlex: subject-truncated search, top-N works with metadata payload.
- Constraint: connectors return only what the HTTP API returned; empty on
  any transport error. Source/evidence kinds are set by construction
  (both MVP sources are `secondary`; WB values are `direct`, OpenAlex
  metadata is `contextual`).
- Failure: exception → `connector_error` uncertainty, pipeline continues.

### 4. Verifier — `pipeline/verify.py` (code-only, the core guarantee)

- Input: claim + evidences. Output: comparisons + uncertainties.
- `verify_quantitative`: same-unit matching → same-period gating →
  `compare_numbers` with explicit tolerance rule. Bare numbers match
  counts only, flagged. Year-like numbers excluded.
- `assess_science`: year-match checks + mandatory `metadata_only`
  uncertainty. Titles never verify a claim.
- Constraint: **this module cannot call a model** — it has no model
  dependency at all. Verification is therefore unskippable by
  construction, not by prompt instruction.
- No iteration, no limits needed: bounded loops over quantities × evidences.

### 5. Synthesizer — `pipeline/synthesize.py`

- Input: claim + comparisons + uncertainties. Output: `Conclusion`.
- Stance: deterministic `decide_stance` rules (contradiction wins;
  non-verifying checks cap at `partially_supported`).
- Summary: LLM rewrite attempted (`SUMMARY_SYSTEM`: cite comparison ids,
  no new facts); any failure keeps the deterministic template.
- Constraint: the model may rephrase but never changes stance, verdicts,
  or cited ids — those are computed before and independently.

## Model/code boundary (auditable)

| Decision | Made by | Enforced by |
|---|---|---|
| Claim interpretation | model → heuristic fallback | `extract.py`, audit badge |
| Which sources to query | code (domain routing) | `connectors/__init__.py` |
| What sources say | code (HTTP payloads) | connector modules |
| Whether values agree | code (compare.py) | no model import in verify |
| Final stance | code (stance rules) | `synthesize.decide_stance` |
| Summary wording | model → template fallback | `synthesize.synthesize` |

## Limits

- `limit` clamped to 1–10 evidences per connector per claim.
- Claim text capped at 4000 chars; OpenAlex query truncated to 300.
- HTTP timeouts 20s (sources), 120s (model, 3 SDK retries).
- No loops over model calls: at most 2 model calls per analysis
  (structure + summary), verified by `tests/e2e/test_pipeline_offline.py`.
