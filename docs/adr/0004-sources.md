# ADR-0004: Keyless public sources (World Bank + OpenAlex)

- Status: accepted. Date: 2026-10-31.

## Context

The MVP needs real, structured, checkable sources for economy and
science without depending on credentials that may never arrive.

## Options

1. World Bank indicators API + OpenAlex works API (both keyless).
2. Sources requiring keys (FRED, Semantic Scholar full, news APIs).
3. Web search scraping / general search API.
4. Bundled static fixtures only.

## Decision

Option 1. World Bank for macro-economic quantities (secondary source,
direct evidence); OpenAlex for scholarly metadata (secondary source,
contextual evidence). Both verified live 2026-10-31.

## Rationale

- Zero-credential operation: the demo works from a clean environment
  with only `pip install`.
- Structured responses (JSON with values/years/DOIs) instead of
  scraped prose — comparable deterministically.
- International, neutral, citable with stable URLs per indicator/work.

## Consequences

- Economy coverage limited to the coded indicator/country catalog
  (~16 indicators, ~50 countries); extension = data PR, no core change.
- Science evidence is metadata-only; full-text verification is out of
  MVP scope and disclosed in every science report (`metadata_only`).
- Live-source drift: sources revise data; eval cases use wide margins
  (see docs/EVAL.md).
