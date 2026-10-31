# ADR-0005: No confidence scores or probabilities

- Status: accepted. Date: 2026-10-31.

## Context

Evidence tools are tempted to emit "87% true" style scores. The mission
forbids arbitrary scores without a defined, validated methodology.

## Options

1. Four-stance categorical conclusion + explicit uncertainties.
2. Numeric confidence per comparison/conclusion.
3. Verbal probabilities ("likely", "very likely").

## Decision

Option 1. Stances: `supported`, `partially_supported`, `contradicted`,
`unverifiable` — computed by published deterministic rules from
comparison verdicts. Uncertainty is a list of coded, severitized items,
not a number.

## Rationale

- Any number would imply calibration the project has not measured.
- Stance rules are inspectable (`synthesize.decide_stance`) and covered
  by unit tests; a score would hide the same information.
- Reviewers can challenge a rule; they cannot challenge "87%".

## Consequences

- UI copy must never show percentages except measured relative
  differences (Δ%), which are labeled as such.
- If future work adds calibrated scoring, it needs its own ADR with the
  calibration procedure and measured results.
