# ADR-0006: Snippets are never Evidence

- Status: accepted. Date: 2026-11-01.

## Context

Retrieved text (search snippets, excerpts, model-recalled passages) is
tempting to treat as findings. But prose is unverifiable at the point of
use: it carries no unit, no timestamp semantics, no source commitment.

## Decision

`Evidence` objects are built only from structured source payloads
(indicator observations with unit/year/value; works metadata with
id/year/doi) returned by a registered connector. Excerpts are pointers
for humans, never inputs to verdicts. `verify.py` reads payload fields
only, and there is no web-search path in `src/`.

## Consequences

- Every verdict traces to typed fields; `prose_traceability` + citation
  validation keep summaries honest about which comparisons exist.
- Full-text science verification stays out of scope until a connector
  can return structured statements — correctly, by this rule.
