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

## Grounding policy (read before touching retrieval or verify)

**Snippets are never Evidence.** A text excerpt — a search snippet, a
model-recalled passage, a summary sentence — is at most a *pointer* that
tells the system where to look. `Evidence` objects are constructed only
from structured source payloads (indicator observations with unit/year/
value, works metadata with id/year/doi) returned by a registered
connector over HTTP. Consequences, enforced in code:

- Connectors return only what the HTTP API returned (`worldbank.py`,
  `openalex.py`); transport errors become uncertainties, never guesses.
- `verify.py` compares payload fields, never excerpt prose. There is no
  semantic-similarity check, no keyword-overlap verdict, and no code path
  that treats retrieved text as a fact.
- The model never retrieves: `src/` contains no web-search path
  (verified: no `web_search` references anywhere under `src/`). The model
  structures claims and rephrases summaries; every number in a verdict
  comes from a connector payload via `compare.py`.
- Eval pins this: `prose_traceability` requires every `[cmp_*]` token in
  a summary to resolve to a real comparison, and citation validation
  strips ids that match nothing.

## Roles

### 1. Claim structurer — `pipeline/extract.py`

- Input: raw claim text. Output: `StructuredClaim`.
- Primary worker: Muse Spark (`STRUCTURE_SYSTEM` JSON contract, T=0,
  `max_tokens=4000`).
- Fallback: `models/heuristic.py` (regex quantities, years, countries,
  keyword domain/type classification, EN + ES).
- Constraint: model output is sanitized (unit allowlist, finite floats,
  year-like quantities dropped, 20-item list caps); LLM gaps in
  times/geos/quantities are backfilled from the heuristic pass so
  explicit text is never silently dropped.
- Failure: any LLM error → full heuristic result; audit shows `heuristic`.

### 2. Source router — `pipeline/run.py` (`for_domain`)

- Input: claim domain (+ optional API `domain` override). Output:
  connector list.
- Pure code. An explicit override replaces the extracted domain and is
  recorded in the audit (`domain_override=A->B`). Domain-tagged
  connectors only; `general`-tagged connectors run solely for
  unclassified claims (prevents cross-domain noise — found and fixed
  during live probing).

### 3. Evidence retriever — `connectors/`

- Input: structured claim. Output: `list[Evidence]`.
- World Bank: keyword→indicator match (EN + ES aliases), country→ISO3
  resolution with pre-request allowlist, year span from claim times
  (default 2020–2023), live `fetch_observations`.
- OpenAlex: subject-truncated search (≤300 chars), top-N works with
  metadata payload.
- Constraint: connectors return only what the HTTP API returned; empty on
  "no match". Source/evidence kinds are set by construction (both MVP
  sources are `secondary`; WB values are `direct`, OpenAlex metadata is
  `contextual`). Each retrieve echoes its query (`last_query`) into the
  audit trail.
- Failure: `InvalidGeoError` → `invalid_geo` (no request sent);
  `ProviderError(transient)` → `connector_error`;
  `ProviderError(permanent)` → `source_unavailable`; anything else →
  `connector_error` (≤200 chars). Pipeline continues in all cases.

### 4. Verifier — `pipeline/verify.py` (code-only, the core guarantee)

- Input: claim + evidences. Output: comparisons + uncertainties.
- `verify_quantitative`: same-unit matching → same-period gating →
  `compare_numbers` with explicit tolerance rule. Bare numbers match
  counts only, flagged. Year-like numbers excluded.
- `assess_science`: year-match checks + mandatory `metadata_only`
  uncertainty. Titles never verify a claim. General claims with no
  numeric comparisons get this assessment too.
- `detect_claim_shape`: flags opinions/non-claims (`opinion_or_nonclaim`,
  steering stance to `not_assessable`) and compound claims
  (`compound_claim`, detect+disclose only — analysis proceeds as one unit).
- Constraint: **this module cannot call a model** — it has no model
  dependency at all. Verification is therefore unskippable by
  construction, not by prompt instruction.
- No iteration, no limits needed: bounded loops over quantities × evidences.

### 5. Synthesizer — `pipeline/synthesize.py`

- Input: claim + comparisons + uncertainties. Output: `Conclusion`.
- Stance: deterministic `decide_stance` rules — `not_assessable` checked
  first (opinion flag or claim empty of assessable content), then
  contradiction wins; non-verifying checks cap at `partially_supported`.
- Summary: LLM rewrite attempted (`SUMMARY_SYSTEM`: cite comparison ids,
  no new facts, `max_tokens=4000`); any failure keeps the deterministic
  template.
- Constraint: the model may rephrase but never changes stance, verdicts,
  or cited ids — those are computed before and independently.
  Model-cited `[cmp_*]` ids are validated against real comparisons
  (unknown ids stripped + logged), and blocking/major template caveats
  are always retained (caveat union: caveats are code ∪ model).

## Model/code boundary (auditable)

| Decision | Made by | Enforced by |
|---|---|---|
| Claim interpretation | model → heuristic fallback | `extract.py`, audit badge |
| Which sources to query | code (domain routing) | `connectors/__init__.py` |
| What sources say | code (HTTP payloads) | connector modules |
| Whether values agree | code (compare.py) | no model import in verify |
| Whether the input is assessable | code (claim-shape flags + stance rule 1) | `verify.detect_claim_shape`, `synthesize.decide_stance` |
| Final stance (5 values) | code (stance rules) | `synthesize.decide_stance` |
| Cited comparison ids | code (validation strips invented ids) | `synthesize.validate_citations` |
| Required caveats | code (caveat union retains blocking/major) | `synthesize.required_caveats` |
| Summary wording | model → template fallback | `synthesize.synthesize` |

## Limits

- `limit` clamped to 1–10 evidences per connector per claim.
- Claim text capped at 4000 chars; OpenAlex query truncated to 300.
- HTTP timeouts 20s (sources), 120s (model, 3 SDK retries).
- No loops over model calls: at most 4 model calls per analysis
  (structure + abstract-stance + falsify + summary; abstracts only for
  science claims), verified by `tests/e2e/test_pipeline_offline.py`.
- Structure/falsify/summary capped at `max_tokens=4000`, abstracts at
  1500; all `temperature=0` (hidden reasoning consumes the budget; see
  `docs/LIVE_MODEL.md`). Falsify/abstracts pass `reasoning_effort="low"`
  (default depth truncated them live). Per-stage usage persists in
  `meta.usage_*`.
- Model list fields truncated past 20 items; persisted exception text
  truncated to 200 chars.
