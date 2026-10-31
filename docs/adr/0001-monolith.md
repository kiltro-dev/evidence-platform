# ADR-0001: Python monolith, one process

- Status: accepted. Date: 2026-10-31.

## Context

Four days to the deadline, single developer (autonomous agent), demo over
local UI + video. The product needs HTTP serving, JSON API, static UI,
persistence, and scheduled-less request handling.

## Options

1. Python monolith (FastAPI + static files + SQLite, one process).
2. Node/TypeScript service + separate frontend build.
3. stdlib-only `http.server` console (reference-repo pattern).
4. Microservices / serverless split.

## Decision

Option 1. FastAPI serves API + static single-page UI; SQLite persists
reports; connectors are sync httpx calls. Six dependencies total.

## Rationale

- Mature, documented, actively maintained; matches the reference repo's
  Python/OpenAI-SDK direction without copying its legal-domain code.
- One process = trivial local run, trivial demo, no CORS/build/deploy matrix.
- stdlib-only (3) would cost more hand-rolled code than FastAPI saves, for a
  worse demo UI. Microservices (4) add nothing at this scale.
- Sync code throughout keeps the pipeline testable without async fixtures.

## Consequences

- No horizontal scaling story (fine for MVP; SQLite + single process).
- Long analyses block a worker — acceptable for ≤10 evidences/claim;
  documented limit, not a hidden bottleneck.
