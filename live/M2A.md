# M2a — Verificador + Biblioteca Atlas Live

Deterministic claim verification against captured source snapshots.
**Live $0**: no network, no model calls — `sqlite3` + `src/atlas/core`
only. The model (researcher, M2b) proposes packages; this milestone
checks them with code.

## Files

| File | Role |
|---|---|
| `live/evidence/package.py` | `EvidencePackage` frozen dataclass + validation |
| `live/library/store.py` | `EvidenceStore`: SQLite FTS5 + BM25 snapshot library |
| `live/verifier/check.py` | Deterministic verifier, emits `claim.verdict` |
| `live/tests/test_m2a_verify.py` | 12 tests, local `tmp_path` fixtures only |

## EvidencePackage contract

`package_id, claim_id, url, captured_at, sha256, snapshot_path,
quote, offsets, source_type (primary/secondary),
numbers[] (value, unit, scale_raw), extractor (model/code)`,
plus optional `source_year` for temporal alignment.

Validation (`__post_init__`, raises `ValueError`): non-empty strings,
64-char hex sha256, `0 <= start < end` offsets, closed `source_type` /
`extractor` vocabularies, `NumberMention` tuple, sane `source_year`.

Snapshots live under `data/snap/` (git-ignored via `data/`); the
package carries path + sha256, never bytes.

## Verifier pipeline (`verify_claim`)

Per package, in order:

1. **Quote literal**: sha256 must match the snapshot bytes and the
   quote must appear verbatim (whitespace-normalized). Else
   `quote_not_found` / `snapshot_hash_mismatch`.
2. **Re-extraction**: numbers are re-parsed from the quote with
   `src/atlas/core/units.py` (suffixes T/B/M, ES thousands
   `1.400.000`); researcher `numbers[]` are never trusted blindly.
   Calendar years are skipped via `is_year_quantity`.
3. **Numeric compare** (rel. tolerance 2%, `DEFAULT_REL_TOLERANCE`):
   a 10^3k gap with **no explicit scale word** abstains with
   `possible_scale_error` (`scale_error_power`); an **explicit**
   scale (`1.25 billion`) that is 1000x off is a real
   `contradicted`.
4. **Temporal alignment**: `claim_year` vs `source_year` must agree
   when both are known, else `temporal_mismatch`.
5. **Hierarchy**: usable primaries outrank ALL secondaries.

Aggregation: all support → `supported`; all contradict →
`contradicted`; mixed → `disputed`; **no usable package →
`unverifiable` ALWAYS**. Reasons are machine-readable codes.

`verify_and_emit(claim, packages, loader, bus)` publishes:

```json
{"type": "claim.verdict", "claim_id": "c1", "verdict": "supported",
 "reasons": ["numeric_match"], "package_ids": ["pkg_1"],
 "actor": "m2a-verifier"}
```

## Library (`EvidenceStore`)

- `upsert_snapshot(url, text, source_type)` — dedup by sha256, writes
  `data/snap/<sha>.txt` + one FTS5 row.
- `search(query, limit)` — BM25-ranked (`bm25()`), tokens quoted
  against FTS5-syntax injection.
- `store_package / get_package / packages_for_claim /
  packages_for_snapshot` — package JSON + cross-claim reuse audit.

Reuse flow: new claim → `search()` → existing snapshot → new
`EvidencePackage` with the same sha256 (no re-fetch).

## Run

```bash
cd evidence-platform
.venv/bin/python -m pytest live/tests/test_m2a_verify.py -q
```

## Unresolved / out of scope

- Researcher (M2b) that *produces* packages from live sources.
- `source_year` relies on researcher metadata; no in-snapshot date
  extraction yet.
- Unit conversion beyond strict compatibility (e.g. FX) is out of
  scope — mismatched units are `unit_mismatch`.
