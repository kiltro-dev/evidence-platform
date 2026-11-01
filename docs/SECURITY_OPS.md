# Security & operations

## Threat model

Evidence Atlas is a **single-user localhost demo**, not a multi-tenant
service. The model it defends against:

- **In scope**: malicious or malformed *claim text* (oversize input,
  prompt injection, invented geos/units from a model), hostile or broken
  *upstream sources* (bad payloads, outages, non-HTTPS links), accidental
  *secret leakage* (key in logs/repo/DB), and *resource abuse* of an
  exposed loopback port (rate limit, bounded caches).
- **Out of scope by design**: authentication, multi-user isolation,
  network attackers (there is no network exposure — localhost-only),
  malicious operators (the operator holds the key and the DB).

## Secrets

- `MODEL_API_KEY` lives in `.env` (gitignored, `chmod 600`) or process
  env. `.env.example` carries only empty keys and defaults.
- Key presence is checked by boolean only; values never appear in logs,
  responses, the database, or commits.
- `MetaClient` loads `.env` on construction; a key added later requires a
  **server restart** (documented in README).
- No other credentials exist: both sources are keyless public APIs.
  `OPENALEX_API_KEY` (optional, free) buys the project its own
  $1/day OpenAlex budget instead of the shared keyless $0.10/day IP
  budget; like the model key it lives in process env/`.env` only and
  never appears in logs, responses, the DB, or commits.

## Hardening dispositions (v2 security pass)

Fixed in code (grounded below; S-numbers cited only where the code names
them):

| Finding | Disposition | Evidence |
|---|---|---|
| Unvalidated geo codes reach HTTP URLs (S1) | **Fixed**: ISO3 allowlist — shape (`^[A-Z]{3}$`) + membership in the known-country set (+WLD/EUU) — checked before any request; failures raise `InvalidGeoError` → `invalid_geo` uncertainty, no request sent | `connectors/worldbank.py: is_allowed_iso3`, `pipeline/run.py` handler |
| Unbounded exception text persisted/logged (S5) | **Fixed**: `_truncate` caps persisted exception text at 200 chars (`EXC_TEXT_LIMIT`) | `pipeline/run.py` |
| Non-finite / garbage model quantities | **Fixed**: model quantities must parse to finite floats; non-dict, missing, unparseable, and non-finite entries are dropped with counts in `context_notes` | `pipeline/extract.py: _parse_llm_quantities` |
| Unbounded model list fields | **Fixed**: security cap — model lists truncated past 20 items, noted in `context_notes` (PRD §5) | `pipeline/extract.py: _MAX_LIST_ITEMS` |
| String `"null"` unit treated as a unit | **Fixed**: units validated against the `KNOWN_UNITS` allowlist; anything else (incl. `"null"`) becomes unit-less, which can only mismatch — never compare | `pipeline/extract.py: _valid_unit` |
| Year-as-quantity contradictions | **Fixed**: year-like model quantities dropped under the same `_looks_like_year` rule verify uses | `pipeline/extract.py`, `pipeline/verify.py` |
| `javascript:`/opaque links rendered as anchors | **Fixed**: UI renders only `http(s)` URLs as links; anything else is plain text (plus HTML-escaping throughout) | `web/index.html` link renderer |
| Reports undeletable | **Fixed**: `DELETE /api/reports/{id}` + per-row Delete in the UI history strip | `api/app.py`, `web/index.html` |
| Reads create files/dirs | **Fixed**: init-on-write — only `save_report` creates the DB; all reads check `_has_reports_table` first and return empty | `storage/store.py` |
| Error responses leak internals | **Fixed**: unified envelope; tracebacks/bodies never in responses; pipeline crashes map to `analysis_failed` | `api/app.py` handlers |
| Unbounded abuse of /api/analyze | **Fixed**: 60 quota units/min per IP (429 `rate_limited`; analyze = 1, batch = 8 fixed worst-case); tracker itself capped at 10k IPs | `api/app.py: _check_rate_limit` |
| Non-loopback clients reach an authless server | **Fixed**: loopback guard middleware — non-loopback gets 403 `non_loopback_blocked` unless `ATLAS_ALLOW_NON_LOOPBACK=1` | `api/app.py: _loopback_guard` |
| Deleted reports resurrect from cache | **Fixed**: DELETE evicts that report's cache entries; `fresh:true` re-runs past the cache | `api/app.py: _cache_evict_report`, `AnalyzeRequest.fresh` |
| Invented model citations | **Fixed**: `[cmp_*]` ids in model summaries validated; unknown ids stripped + logged | `pipeline/synthesize.py: validate_citations` |
| Model drops required caveats | **Fixed**: caveat union — blocking/major template caveats always retained | `pipeline/synthesize.py: required_caveats` |
| Prompt injection flips verdicts | **Fixed by construction**: stance/verdicts computed in code before the summary; pinned by eval case | `pipeline/synthesize.py: decide_stance`, `general_prompt_injection_cheese` |

Accepted / remaining (conscious, documented):

- **No authentication by design.** The server must stay on loopback;
  binding `0.0.0.0` or placing it behind a proxy without adding auth is
  unsupported and unsafe.
- **Localhost-only is now a code guarantee** (loopback guard, 403
  otherwise), but binding `0.0.0.0` or proxying without auth stays
  unsupported — the guard is a backstop, not a network design.
- **Malformed requests don't consume quota** (validation precedes
  the charge, so invalid floods are unthrottled). Conscious scope
  choice for a localhost demo; a multi-user server must throttle
  invalid traffic separately.
- **No TTL on stored reports.** Rows persist until explicitly deleted.
  Disk use grows ~KBs per report; prune via the UI or DELETE.
- **Per-report token accounting present.** Each report's `meta.usage_*`
  records per-stage prompt/completion/reasoning/total tokens (None when
  a stage made no call); cost control is the ≤4-calls/token-budget cap.
- **In-memory rate limit + cache.** Both reset on restart and do not span
  processes. Fine for one demo server; not a distributed control.
- **HTTP timeouts only (20s sources, 120s model).** No circuit breaker;
  a hung upstream degrades per-analysis, and the live tests fail loudly.

## Logging policy

- Log: fallback causes (`LLM structuring failed…`, `LLM summary failed…`),
  sanitization notes, stripped citations, server errors. No request bodies,
  no claim text beyond what the operator already typed, and **never** the key.
- `request_id` (32-hex, per error) correlates user-visible errors with
  server-side events.
- Eval harness prints PASS/FAIL plus failed-check names; full observed
  values go to the result JSON, not stdout.

## Ops runbook

Start (from the repo root, `.venv` installed):

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

Stop: `Ctrl-C` (foreground) or `kill <pid>`. The server holds no
irreplaceable state — cache and rate counters are purely in-memory.

Restart (required after `.env` key changes):

```bash
# stop, then start again; verify the mode flipped:
curl -s http://127.0.0.1:8765/api/status
```

Database: `data/atlas.db` (default) or `$ATLAS_DB`. Created on first
persisted analysis. Back up by copying the file while the server is
stopped; inspect with `sqlite3 data/atlas.db 'select report_id, stance,
created_at from reports order by created_at desc limit 5;'`.

Health checks:

```bash
curl -s http://127.0.0.1:8765/api/status   # 200 + mode
bash scripts/smoke.sh --port 8765          # 29 offline checks, throwaway DB
bash scripts/smoke.sh --port 8765 --live --eval  # + live API + full eval
```

Common failures:

| Symptom | Likely cause | Action |
|---|---|---|
| Status shows degraded, key set | Server started before the key | Restart |
| `address already in use` | Stale server on the port | `ss -ltnp \| grep 8765`, kill it, or use `--port 8770` |
| Live analyzes return `connector_error` | No internet / source down | Check `curl https://api.worldbank.org/v2/...`; degraded eval still works |
| Full-mode summaries fall back to template | Model truncation / key invalid | Check server log for `LLM summary failed`; verify key |
| Smoke FAILs on install phase | `.venv` missing or stale | Re-run the README install block; smoke never creates venvs |
| Disk growth in `data/` | Reports accumulate (no TTL) | DELETE unneeded reports; old DBs can be archived off |
