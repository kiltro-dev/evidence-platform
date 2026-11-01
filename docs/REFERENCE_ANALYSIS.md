# Reference analysis: legal-agent-workshop (read-only)

Inspected 2026-10-31 via parallel research agents (56 + 21 tool calls).
The repository was never modified. Structure: `agent/` (agent_kit,
legal_agent, verification, tests, scripts), `cases/northbridge-v-caldera/`
(21 docs), `world/` (follow-up releases), `ground_truth/`, `solutions/`,
`ui/` (stdlib console), `HANDBOOK.md` (31KB), 231 files.

## Reusable patterns (adopted as principles)

- **Verification inside mutating tools** — checks the model cannot skip.
  Adapted as: verification in code modules the model cannot import/call.
- **Quote normalization + minimum-length citation matching** — adapted as
  unit normalization + strict comparability in `core/`.
- **Atomic JSON writes + append audit** — adapted as SQLite + audit trail
  per report stage.
- **Scripted model doubles for 100% offline tests** — adopted directly
  (`models/scripted.py`, `tests/e2e`).
- **File-decoupled UI** — adapted as API/UI separation (UI only consumes
  HTTP, never imports the pipeline).
- **Bounded budgets** (max docs/call, max facts/item) — adapted as
  `limit` clamping and at-most-4 model calls per analysis.

## Adapted, not copied

- Static ground-truth checklist → dynamic per-claim verification.
- `VERIFICATION=off/on` global flag → always-on verification core.
- Filesystem runs/ as persistence → SQLite single file.
- 915-line stdlib console → FastAPI + static page.

## Deliberately not used

- Legal-domain code (memos, checklists, signature checks): wrong domain.
- `check_signatures` placeholder pattern: MVP has no equivalent stub
  presented as functional; degraded mode is labeled as such.
- Filesystem-as-database for concurrent use; env-pinned case dirs;
  hardcoded retry/escalation counts; `solutions/` rsync versioning.

## Model configuration observed (reference)

`MODEL_API_KEY`, `MODEL_API_BASE_URL=https://api.meta.ai/v1`,
`MODEL_API_MODEL=muse-spark-1.3`, `SAM_MODEL=sam-3.1`, OpenAI SDK
`openai>=2,<3`, Responses API, retries on 429/503. Our `.env.example`
mirrors the Meta-API subset. SAM 3.1 (image segmentation) has no role
in a text/numbers MVP and was not integrated.
