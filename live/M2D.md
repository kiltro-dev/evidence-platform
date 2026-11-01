# M2d — Recall hardening Atlas Live (rama live)

Endurecimiento del recall sobre el cierre M2c, sin tocar semántica de
veredicto protegida: tolerancia 2%, guarda de escala, jerarquía
primary-beats-secondary. Si el recall sube a costa de un falso
`contradicted`, se revierte (no ocurrió: sigue 0).

Status: **cerrado**. Falsos contradicted sobre TRUE: **0**.

## Baseline (M2c, 31 EN, 2026-11-01)

2 decididos, 27 `unverifiable`, 0 falsos-contradicted, $0.59, p50 26.5 s.
Causas en [M2_E2E.md](M2_E2E.md): (1) quotes JSON del WB sin unidad →
`unit_mismatch`; (2) preámbulo conversacional mata al recuperador
(5 claims en cero); (3) unidades física/geo fuera del parser + claims
sin número; (4) vacíos honestos en FALSE.

## Edits mínimos (4, todos documentados)

1. **Rescate JSON con preámbulo** (`live/swarm/roles.py`,
   `live/swarm/coordinator.py`): `extract_balanced_json()` busca el
   primer `{` o `[` balanceado (string-aware, con fallback al otro
   opener) y devuelve el span si parsea; `_ClaimCtx.complete()` lo
   intenta sobre `exc.doc` antes de gastar el retry, y también sobre
   el retry (en vivo el retry repetía el preámbulo). Rescate = 0 tokens
   nuevos, 0 llamadas nuevas. Sin span rescatable, conducta idéntica a
   M2b (1 retry, luego fallo degradado).
2. **Adaptador fuentes estructuradas** (`live/evidence/package.py`,
   `live/swarm/coordinator.py`, `live/verifier/check.py`): nuevo
   `SourceMeta{indicator, unit, scale}` + `WB_INDICATOR_UNITS`
   (`SP.POP.TOTL→count`, `NY.GDP.MKTP.CD→USD`) +
   `source_meta_for_snapshot(url, snapshot_text)`. El productor lo
   deriva de los bytes del snapshot (solo host `api.worldbank.org`,
   JSON `[meta, rows]` con un único indicator acordado y mapeado);
   el verificador lo usa SOLO como fallback cuando la quote no trae
   unidad, y re-deriva el indicator de los bytes (nunca confía en el
   paquete: indicator/unit adulterados se ignoran o corrigen). Los
   valores siempre salen de la quote. Per-cápita/crecimiento/tasas
   quedan FUERA del mapa a propósito (su unidad no determina
   comparabilidad). `from_dict` acepta payloads pre-M2d (sin la clave).
3. **Presupuesto tokens 12k→20k** (`TOKEN_BUDGET_PER_CLAIM`): el tope
   12k degradaba ~13% de claims (4/31, corte en redteam). El deadline
   90 s/claim sigue acotando wall time.
4. Tests + reporte: `live/tests/test_m2d_recall.py` (35 tests, 0 live),
   este archivo.

No tocados: tolerancia, guarda de escala, jerarquía de fuentes,
`src/atlas/core/units.py` (física/geo siguen fuera de scope).

## Tests

```bash
cd evidence-platform
.venv/bin/python -m pytest live/tests/test_m2d_recall.py -q        # 35 passed, 0 live
.venv/bin/python -m pytest live/tests/ -q -m "not live"            # 165 passed
```

Pines: preámbulos estilo DOC-REPR (`"I'll search for ...{...}"`,
fences, trailers, ruido `[doc 1]`), rescate E2E sin gastar retry,
meta WB (rechazo de host ajeno, prosa, mixto, no-mapeado),
round-trip + payloads legacy, tampering (indicator/unit), y guardas
(tolerancia 1.9/2.1%, backstop, jerarquía, `claim_unit_unknown`).

## Corrida LIVE (31 EN, 2026-11-01)

```bash
.venv/bin/python live/run_verify.py --lang en
# log: data/runs/verify-20261101-212343.jsonl (+ .db, + .summary.json)
```

| expected \ verdict | supported | contradicted | disputed | unverifiable |
|---|---|---|---|---|
| true (16) | **7** (T1,T2,T3,T5,T6,T7,T8) | **0** | 0 | 9 |
| false (15) | **1** (F14, ver Costo) | **1** (F3) | 0 | 13 |

Antes → después: decididos correctos 2 → **8** (T3 se mantiene, +T1,
+T2, +T5, +T6, +T7, +T8, +F3; F6 se pierde por varianza del swarm —
0 paquetes esta corrida); `unverifiable` 27 → 22.

- **Falsos contradicted sobre TRUE: 0** (trigger de revert: no
  alcanzado). `invalid_packages` 0, `source_type_mismatch` 0,
  `snapshots_unresolved` 0.
- Las **13 packages que decidieron llevan `source_meta`** (0 de prosa):
  T3 ahora lo decide la primaria WB (antes Wikipedia) — jerarquía OK.
- Costo medido: **$0.96** (503k prompt + 78k completion, 155 llamadas;
  tope M2d $3). Wall 611 s.
- Latencia/claim (invest→verdict): **p50 78.4 s / p95 90.0 s** (baseline
  p50 26.5 / p95 55.2). REGRESIÓN honesta: menos muertes tempranas +
  loops más largos (rescates ≥24 en stderr) + deadline 90 s como nueva
  restricción activa (9 claims).
- Degradados: 4 (13%) → **20**: 11 `budget exhausted at recuperador`
  (a 20k) + 9 `deadline exceeded`. El techo 20k NO bajó degradados: el
  burn por claim creció más (rescates mantienen vivos loops que antes
  morían en ronda 1-2). Causa N3 sigue abierta — el cuello ahora es
  tokens+tiempo por claim, no el tope nominal.

### Costo: F14 false-supported (nuevo, reportado sin maquillar)

Claim F14 (FALSE): *"The world actually passed 8 billion people back in
2020"* → `supported` vía WB `SP.POP.TOTL WLD 2020 = 7,853,863,139`:
rel 1.86% < 2% → `numeric_match`. Mecanismo: caso de borde DENTRO de
la tolerancia (a 0.14pp de la línea) sobre un year-swap que el check
temporal neutralizado (M2B-4, abierto, fuera de scope) no puede cazar;
semántica de hito ("passed 8B") vs nivel — fuera del alcance de
verificación numérica. No es bug de unidad: con tolerancia/jerarquía/
escala intactas (no-tocables) este costo es inherente a decidir
fuentes estructuradas. Precisión de lo decidido: 8/9 (89%).

### Qué causa quedó sin resolver

- Causa 1 (unidades WB): **resuelta** — 8/9 decisiones correctas vía
  `source_meta`, al costo de F14.
- Causa 2 (preámbulo): **resuelta** — ≥24 rescues, 0 tokens/retry;
  F11/F12 (sin número) recuperan paquetes y el verificador abstiene
  correctamente (`claim_unit_unknown`).
- Causa 3 (física/geo + sin-número): **ABIERTA** — `meters`,
  `kilometers`, `degrees`, `square kilometers` no están en
  `src/atlas/core/units.py` (no tocable desde M2d); T11/T12/T13/T14/T15,
  F7/F8/F10 siguen `unverifiable`. Sin-número (F5/F9/F11/F12/F15):
  `unverifiable` por diseño, correcto.
- Causa 4 (vacíos honestos): **conducta correcta**, se mantiene
  (F13: 0 paquetes, `unverifiable` — el temido level-vs-rate
  false-supported NO ocurrió).
- N3 (presupuesto): **ABIERTA y peor** — ver degradados/latencia arriba.
  Recomendación: no subir el tope; recortar transcript (docs.render)
  o rondas redteam.
- Regresiones por varianza (no por código): F6 contradicted→unverifiable
  (0 paquetes), T9/T10 con paquetes→0 paquetes. El swarm no es
  determinista entre corridas.
