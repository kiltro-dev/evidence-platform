# Progress log

Persistent record for the autonomous build. Latest entry last.

## 2026-10-31 — Phase 0: discovery (workflow, 5 agents)

- Inspected env: git 2.43, gh 2.98 (auth kiltro-dev, ssh), python 3.12.3,
  node 22, uv 0.13, docker 29.5. No `MODEL_API_KEY` anywhere; api.meta.ai
  reachable (401 without key, as expected).
- Inspected `legal-agent-workshop/` (read-only): reusable patterns =
  verification-inside-tools, atomic JSON writes, quote normalization,
  scripted-model offline tests, file-decoupled UI. Rejected: legal-domain
  code reuse, filesystem-only persistence, static ground truth, hardcoded
  retry budgets. Full analysis: docs/REFERENCE_ANALYSIS.md.
- Verified hackathon rules from official pages (dev.meta.ai + Cerebral
  Valley Terms 2026-10-25): deadline **Nov 4, 9:00 PM ET**; criteria
  Impact 30 / Meta AI Tooling 25 / Demo 25 / Tech Depth 20; submit via
  Cerebral Valley portal. Details: docs/HACKATHON.md.
- Naming research (5 rounds): ClaimAtlas, VERA, Provexa, ClaimScope,
  Tekmerion, Dokime, ProofPack, Constata, Evalens all collide with
  existing projects. Chose descriptive **Evidence Atlas** (product) and
  **Evidence–Validity Dual Assessment** (method proposal).

## 2026-10-31 — MVP core + pipeline (verified live)

- Scaffolded `evidence-platform/` (Python 3.12, FastAPI, httpx, pydantic,
  openai, pytest). SQLite store, static single-page UI, one process.
- Deterministic core: unit parsing/normalization, strict comparability,
  numeric + time comparison, 2% tolerance rule. 13/13 unit tests.
- Connectors live-verified: World Bank (~16 indicators) + OpenAlex.
- Fixed during probing: connector routing noise (domain-gated),
  year-match never yields "supported" (NON_VERIFYING_METRICS),
  bare-number→count matching with explicit flag, year-like numbers
  excluded from quantities.
- Pipeline E2E live: GDP supported (Δ1.62%), Japan-pop contradicted,
  Germany-unemployment supported (Δ0.94%), science partial context.
- API + UI verified on uvicorn :8765; fixed WEB_DIR level bug (GET / 404).
- Tests: 28 offline + 3 live green. Eval harness: 10/10 live cases.
- Git: 3 checkpoints on local main.

## Next

- Docs batch 2–4 (architecture, agents, ADRs, eval, framework, study guide,
  reference analysis, hackathon rules, demo script).
- Public repo `kiltro-dev/evidence-platform`, clean-env install check,
  secrets review, 3-minute demo rehearsal against live UI.
- Open risk: no MODEL_API_KEY provisioned in this environment; full
  model-assisted mode is implemented but exercised only via scripted
  doubles. Degraded deterministic mode is fully verified live.
