# ShipCheck browser end-to-end tests

Reusable Playwright-based tests that operate ShipCheck as a real user:
real server, real Chromium, real DOM. Degraded mode by default, so no
model key, no credits, and no network beyond localhost + public sources
are needed.

## Setup (once)

```bash
cd tests/e2e-browser
npm ci                                   # installs playwright 1.64.1 (pinned)
npm run install-browser                  # downloads chromium (~170MB, cached)
```

Headless-shell-only alternative (smaller, sufficient for these specs):

```bash
npm run install-browser-shell
```

Requires the project Python venv (`../.venv`) — the runner boots
`uvicorn` from it on a throwaway port with a temp SQLite DB.

## Run

```bash
npm test                      # all specs, degraded mode
npm test -- journeys           # filter by filename substring
SHIPCHECK_E2E_LIVE=1 npm test # full-model mode (needs MODEL_API_KEY)
```

Specs fail fast with `FAIL | spec | check | detail` lines and the run
exits nonzero; `E2E-SUMMARY` reports passed/total. Screenshots and temp
DBs go to a fresh `os.tmpdir()` directory printed in the summary —
nothing is written to the repo.

## Specs

| Spec | Covers |
|---|---|
| `ui-units` | Pure UI helpers (`prettySummary`, `fmtMetaVal`, `META_KEYS`) evaluated from the real `web/index.html` source — no copies. No server/browser. |
| `a11y-contract` | Announcement contract: no `aria-live` on `#out`, `role=alert` errors wired via `aria-describedby`, focus-to-verdict, zero page errors. Plus: skip link, focus-following jumps, focusable tables, named deletes/pills/checks, `aria-expanded` excerpt toggles, live copy feedback, one assertive card per batch, 32px touch floors, reduced-motion guard, no favicon request. |
| `abort-paths` | Typed cancel/timeout cards (not generic service-issue), 150s skeleton copy, single `role=status`. Plus: superseded runs stay silent (MutationObserver proof) and cancel keeps working after a supersede. |
| `render-fidelity` | Displayed text humanized while stored summaries keep machine `[cmp_*]` tokens, meta allowlist aligned, server `request_id` shown on error cards. Plus: source vintage surfaced, zero horizontal overflow at 375px with all disclosures open. |
| `journeys` | EN + ES single claims, deep-link reload + re-run determinism proof (persist regression), 2-claim batch roll-up, history delete round-trip. |

## Conventions for new specs

- One file per user-visible behavior in `specs/`; export
  `async function(ctx)` with `ctx = {base, playwright, outDir}`.
- Use `helpers/checks.js` (`makeChecks`, `launchBrowser`); never swallow
  page errors — assert zero at the end.
- Prefer accessibility locators and semantic queries over CSS where the
  UI supports them; fall back to stable ids.
- Keep specs hermetic: route-interception for failure injection, temp
  files under `outDir`, kill everything you start (the runner stops the
  shared server; close your own browsers).
- Live-model specs must be opt-in via `SHIPCHECK_E2E_LIVE` and state
  their call budget in a header comment.
