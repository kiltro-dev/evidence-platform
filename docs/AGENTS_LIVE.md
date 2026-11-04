# Atlas Live agent design: who decides what

Atlas Live IS a multi-agent system (unlike the v1 staged pipeline in
`AGENTS.md`, kept as [Histórico](AGENTS.md)). Roles below are real
modules with bus contracts. The invariant holds everywhere: **the
model proposes, deterministic code disposes** — no verdict, quote, or
number survives without a code check.

## Roles (model proposes)

| Role | Module | Model job | Output contract |
|---|---|---|---|
| Detector | `live/detector/claims.py` | claim spans, entities, coreference | `claim.detected` (strict schema, verbatim) |
| Structurer | `live/swarm/roles.py` | normalize claim → query plan | structured task for retriever |
| Retriever | `live/swarm/roles.py` | propose sources/indicators, pick quotes | evidence packages (URL + offsets) |
| Red-team | `live/swarm/roles.py`, `live/skeptic/attack.py` | counter-evidence, attacks on `supported` | advisory only, deferred by default, never blocking |
| Vision reader | `live/vista/read.py` | read slide text/tables/chart values | strict schema read per frame |
| Card editor | `live/editor/cards.py` | draft ES/EN verdict cards | card text (facts pinned to packages) |
| Long-context | `live/longctx/check.py` | propose speaker-contradiction pairs | candidate pairs only |

## Code decides (model never votes)

| Gate | Module | Rule |
|---|---|---|
| Verdicts | `live/verifier/check.py` | quote-must-exist, number re-extraction, 2% tolerance, scale guard, temporal alignment, primary-beats-secondary; no valid package → `unverifiable` |
| Said-vs-shown | `live/vista/xcheck.py` | speech-vs-chart compare ±15 s, axis/scale flags |
| Contradictions | `live/longctx/check.py` | pair proposed by model, confirmed by code (`rel_diff`) |
| Orchestration | `live/orchestrator/` | bus + claim state machine, budgets, deadlines, early aborts |
| Reliability | `live/auditor/` | 12 canaries, accuracy + Wilson panel (no invented confidence) |
| Proof | `live/bundle/` | hashed snapshots, offline re-verify |

Grounding policy (same as v1, enforced in `live/`): snippets are
pointers, never evidence. A quote counts only as a verbatim substring
of a fetched, hashed snapshot. Fetch is responsible (robots.txt,
user-agent, rate limits, 2 MB cap, paywall refusals).

Scope: this doc covers `live/` at HEAD. The v1 pipeline doc is
`AGENTS.md` (Histórico v0.4.0, `src/atlas/` only).
