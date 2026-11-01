# F2-1 preregistro — benchmark justo v2 (Atlas Live vs baseline model-only)

Rama `live`, checkout compartido. Este documento se escribió ANTES de la
primera corrida F2 (ver sello de hashes abajo). Nada de lo observado en
las corridas puede cambiar el diseño, los claims o la regla de decisión.

## 1. Pregunta

¿Dónde gana cada brazo? El bench NO busca un único ganador global por
accuracy ingenua: busca el mapa honesto por categoría (exactitud,
recall, falsos, fidelidad de citas, coste, latencia).

## 2. Brazos (código reutilizado, no reimplementado)

- `baseline`: `live.ablation.baseline.judge_claim` (1 llamada Responses +
  `web_search` por claim). Contexto standalone neutro (los claims son
  independientes, sin keynote): la plantilla de instrucciones es la del
  módulo; el transcript se sustituye por una nota standalone declarada
  en `run.py` (`EVAL2_CONTEXT`). Veredicto del modelo, verbatim.
- `atlas`: pipeline completo `live.run_verify` (`run_claim` del swarm +
  `make_evidence_handler` + store + `verify_and_emit`). Importado, no
  copiado.

2 corridas independientes por brazo sobre DEV (`dev.json`).

## 3. Categorías y Ns (DEV: 36 = 6×6; RETENTION: 18 = 3×6)

| cat | nombre | dev | ret | diseño |
|---|---|---|---|---|
| a | hechos conocidos | 6 (3T/3F) | 3 (2T/1F) | WB 2024 + 1 constante física; T y F directos |
| b | recientes/poco conocidos | 6 (3T/3F) | 3 (2T/1F) | datos 2026 en PDFs primarios (IMF WEO, BLS empsit); ground truth verificado por el autor contra la fuente primaria, URL+fecha en el JSON |
| c | inventadas plausibles | 6 (0T/6F) | 3 (0T/3F) | cifras inventadas sobre entidades reales; cada una cita la fuente primaria que la refuta |
| d | aritmética multi-fuente | 6 (3T/3F) | 3 (2T/1F) | variación/razón/promedio sobre WB; ground truth + cálculo exacto en el JSON |
| e | trampas unidad/escala | 6 (3T/3F) | 3 (1T/2F) | billion/trillion, ×10, euros-vs-dólares (E6: Atlas abstiene por `unit_mismatch` por construcción — declarado) |
| f | fidelidad de citas | 6 (3T/3F) | 3 (2T/1F) | claims normales; la métrica distintiva es la fidelidad de las citas del baseline (`fidelity.py`) |

DEV total: 15 TRUE / 21 FALSE (el sesgo viene de c, declarado).
RETENTION total: 9 TRUE / 9 FALSE. RETENTION SELLADO hasta F2-2: nadie
lo abre para ajustar; solo `run.py` lo lee al medir en F2-2.

Equidad de unidades: los 36 claims de DEV (y los 18 de retention)
tienen primera cantidad no-año en {USD, EUR, count, percent, m/s},
todas parseables por `src/atlas/core/units.py` (verificado por script
antes del sellado). Ningún claim condena a Atlas a `claim_unit_unknown`
por diseño. Limitación declarada: no hay claims en metros/km/coronas —
la cobertura de unidades del parser NO se evalúa aquí.

## 4. Métricas (por brazo×corrida + Wilson 95%)

Reutilizan `live.ablation.report.arm_metrics` + `live.auditor.wilson`:

- exactitud sobre decididos + intervalo Wilson 95%.
- recall = decididos/n (abstención = 1 − recall, nunca dentro de accuracy).
- falsos-contradicted (TRUE juzgado contradicted — el P0) y
  falsos-supported (FALSE juzgado supported), con IDs.
- consistencia r1-vs-r2 por brazo (`report.consistency`).
- $/claim (usage real × tarifas `estimate_cost`) y latencia p50/p95.
- fidelidad de citas del baseline (`fidelity.py`): fracción de citas
  literales fieles (quote existe en la fuente + respalda la cifra) +
  Wilson; fetched responsable con snapshot declarado si la URL muere.
- tamper (`tamper.py`): 1 bundle real + 1 byte alterado ⇒
  `verify_bundle.py` → FAIL (test automatizado + 1 demo real).

## 5. Hipótesis direccionales (declaradas antes de correr)

- H1: baseline gana recall global (juzga todo; Atlas abstiene en d
  multi-fuente y E6).
- H2: Atlas gana en e (trampas de escala deterministas) y empata o gana
  en c (refutación vía primario).
- H3: baseline gana en d (aritmética que ningún snapshot contiene
  literal) pero con fidelidad de citas < 100% en f.
- H4: Atlas tiene cero falsos-contradicted (P0) en DEV.

## 6. Regla de decisión (fija antes de correr)

1. Primario: exactitud sobre decididos con Wilson 95% por brazo
   (promedio de las 2 corridas; r1+r2 agregados para el intervalo).
2. Si los intervalos Wilson se solapan ⇒ "empate estadístico en
   exactitud" y deciden los secundarios en orden: (a) falsos-contradicted
   (menos gana), (b) recall, (c) fidelidad de citas, (d) $/claim.
3. Si no se solapan ⇒ gana el de mayor exactitud; los secundarios se
   reportan igual (incluido cada métrico donde el perdedor gana —
   tabla `leaders` computada, nunca narrada).
4. H1–H4 se confirman/refutan una por una en `F2_BENCH.md`, pase lo que
   pase. Ningún re-análisis post-hoc cambia esta regla.

## 7. Presupuesto live

Tope $12. Proyección: 36 claims × 2 corridas × 2 brazos × ~$0.033 ≈
$4.8 + fidelity (~$0, fetch directo) + guardas de coste en `run.py`
(probe serial; si la proyección excede el tope, se corre 1 corrida por
brazo y se declara).

## 8. Sello de preregistro (hashes ANTES de la primera corrida F2)

```
dev.json       sha256 db4ebe719721263957f40d4d08a2fdae8e32f83d883fc0084c2fef3e05bbf78a
retention.json sha256 5278ba4c493f25ab51eeb9b51c121d009cb86748b9b978d81efd78f8b1ecb42c
sellado (UTC): 2026-11-01T22:50:30Z
```

Declaración de ediciones post-sello: corrección transcripcional del
hash de retention.json en este documento (2026-11-01 ~23:00 UTC): la
línea publicada primero omitía 20 caracteres centrales; el archivo
`retention.json` NO cambió (su sha256sum del sello coincide con el de
aquí). Ningún claim editado; hashes de archivos intactos desde el
sello. Si un claim se edita después del sello, el hash cambia y la
edición se declara aquí con fecha, motivo y hashes nuevo/viejo.
