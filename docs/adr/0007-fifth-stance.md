# ADR-0007: Fifth stance `not_assessable`

- Status: accepted. Date: 2026-11-01.

## Context

Opinions, recommendations, questions, and empty inputs used to land in
`unverifiable` — the same stance as "I searched but couldn't conclude".
That conflates "nothing to assess" with "assessment attempted".

## Decision

Add `not_assessable`, decided first in `decide_stance`: it fires when an
`opinion_or_nonclaim` uncertainty is present or when the claim is empty
of assessable content (no quantities, times, or geos + type `other`).
`unverifiable` now strictly means retrieval/verification was attempted.

## Consequences

- UIs and evals must handle five stances; eval pins both the opinion
  path and the empty-input path.
- Amends ADR-0005's four-stance list; the no-scores rationale is unchanged.
