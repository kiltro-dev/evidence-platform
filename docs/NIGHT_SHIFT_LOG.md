> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# Night-shift log (living, lead-owned)

Mission: make ShipCheck maximally competitive before Nov 4 9PM ET.
Baseline: v0.4.0 `4e13d7d` (416 tests, 28/28 x3, SIGN OFF).

## Cycle 1 (2026-11-01, in progress)
Research (5 agents):
- NS competition scan: DONE (findings internal, not published).
- NS frontier scan: DONE (8 SHIP, incl. WB vintage, raw web results, cache).
- NS EVS attack: DONE (multi-geo hole = real bug; slate #1-15; full-paraphrase gap).
- NS eng strategy: DONE (parallel #1, SSE #2, WB rows+FX #3, budgets #4, SDK #5).
- NS hostile demo: RUNNING.
Engineering (lead): parallel batch + thread-local query echo DONE
(419/419 suite; live 46.7s vs ~100s, same 1-1-1, usage correct).
Spend this cycle: ~$0.25 Meta API (1 batch verify).

## Cycle 2 — lead integration (2026-11-01 ~05:10Z)
- V1 multi-geo fix merged (verify.py + test_multi_geo.py): cross-geo
  single-unit skip; #15 tripwire added (Germany+France text, DEU-only
  evidence) — post-fix: contradicted WITH multi_geo_unchecked (major).
- #5 redesigned pre-freeze: Italy debt draft probed EMPTY on WB
  (would duplicate #2) -> USA 10% vs 114.74% contradicted (~91% margin).
- #14 -> pre-registered stance_set {partially_supported, contradicted}.
- Lead honesty fixes: F4 best-collocation, F6 claim-side period text,
  R5 per-item time_ref sanitation (6 unit tests, R5 pair fails pre-fix).
- Gates: pytest 479 passed; degraded 11/11 (32 skipped, +1 new live);
  paraphrase 36/36; unanswerable 17/20 (unchanged).
- Open: hostile-demo report (R4 label ungrounded until it lands).

## Cycle 3 — full gate + no-conversion rule (2026-11-01 ~06-07Z)
- Full run 5 (`--mode both`): degraded 11/11 (32 skipped), full
  42/43. All 15 new cases green first try (#14 heuristic->partial
  as probed, #15 tripwire fires live, #5 USA contradicted).
- Single FAIL: OLD bp case (model converted 310bp->3.1% at
  extraction, 1/3 runs; triage cat.5, not a tree regression).
  Fix: no-conversion rule in extract (span-literal reset, 5 unit
  tests, 2 fail pre-fix, pin byte-identical). Post-fix bp probe
  3/3 unverifiable (incl. newly observed honest 310pp variant).
- Gates: pytest 491; paraphrase 36/36; unanswerable 17/20;
  ablation flips=1 (documented); smoke offline 31 PASS.
- Full re-run (run 6) launched; EVAL_REPORT run-5 section written.
- Intel re-check: no strategy changes (details internal, not published).

## Cycle 4 — run6 triage + moon stance_set (2026-11-01 ~07-08Z)
- Run 6: 42/43 (bp fix holds live; #14 model-path partial).
  FAIL = moon-cheese (OpenAlex set shift + model abstract
  judgment -> ceiling lift). Proven via work-ID probe 3x.
- Disposition: variance-declared stance_set (both members
  pre-observed; load-bearing property kept). No code change.
- EVAL_REPORT run-6 section + H3 restatement written.
- Run 7 (gate) next.

## Cycle 5 — gate green + demo verify (2026-11-01 ~08-09Z)
- Run 7 (`--mode both`): degraded 11/11, full 43/43. GATE GREEN.
  Pinned `20261101T061735Z.json` (force-add with commit).
- Live demo batch: 38.7s (was 95.3s sequential), 1-1-1, 0 errors,
  9 calls, $0.204, 49.7% prompt cached. Considered-not-cited
  shows BEA/WB files the model saw but didn't cite; cited list
  still model-chosen (whatisgdp x3 on GDP) — ordering ≠ invention.
  Pinned `demo_batch_ns_20261101T062500Z.json`.
- Smoke live: 48 PASS (31 offline + 17 live-source), 0 FAIL.
- Docs: EVAL_REPORT run7, DEMO_SCRIPT (timing/counts/beat),
  PROGRESS night-shift entry + moon risk.
- Meta spend tonight ~$8 est (3 full runs + probes + batches;
  only batch $0.204 + F2 probe measured exactly).
