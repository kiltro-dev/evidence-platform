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

## 2026-10-31 — Docs + delivery

- Committed full docs: README, ARCHITECTURE, AGENTS, 5 ADRs, EVAL,
  FRAMEWORK (Evidence–Validity Dual Assessment), REFERENCE_ANALYSIS,
  HACKATHON (rules verified), STUDY_GUIDE, DEMO_SCRIPT.
- Secrets audit: no secret values in tracked files, no `.env`, sensitive
  paths gitignored.
- Created public repo `kiltro-dev/evidence-platform` (main, 4 commits
  pushed, visibility PUBLIC, verified via `gh repo view`).
- Clean-env check from public clone (/tmp/cleanenv): fresh venv install,
  28/28 offline tests, server GET / 200, live GDP analysis → supported.
- Dev server left running on 127.0.0.1:8765 for immediate demo.

## 2026-10-31 — Live model integration

- User provisioned `.env` (gitignored, mode 600). App did not auto-load
  it → added `python-dotenv` loading in `MetaClient` + pinned dep.
- Verified real `muse-spark-1.3`: minimal JSON call OK (~3s); full
  `analyze()` model/model with stance `supported`; HTTP full mode OK.
- Found + fixed: reasoning-token truncation (`finish=length`, empty
  content) → explicit error, logged fallbacks, slim summary payload,
  `max_tokens=4000` on both calls. Offline suite still 28/28.
- Server restarted on :8765 in full mode. Details: docs/LIVE_MODEL.md.

## Open risks / not done

- Submit-portal fields and video specs unverifiable without login; demo
  video recording itself is a human task (script ready in docs/).
- No per-report token accounting yet; truncation fallback possible
  under load (handled by degradation).
