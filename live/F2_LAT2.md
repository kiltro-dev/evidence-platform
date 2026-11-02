# F2_LAT2 — F2-7 sprint latencia Atlas Live (DEV solo; retention sellada)

Tarea F2-7, rama main, checkout compartido. Objetivo: p50 ≤ 25s y
p95 ≤ 60s por claim atlas sobre `live/eval2/dev.json` (36 claims) SIN
tocar tolerancia, escala, jerarquía ni veredictos, con recall ≥ 0.55
y 0 falsos-contradicted; si un cambio rompe los gates, se REVIERTE.

Sellos y límites:

- `dev.json` sha256 `db4ebe71…f78a` (coincide con prereg; verificado
  antes de correr).
- `retention.json` SELLADA: ni abierta ni listada en ningún paso.
- Sin git ops. Sin secretos en el reporte (solo costes y presencia).
- Intocados: tolerancia 2%, backstop `possible_scale_error`, jerarquía
  primary > secondary, guarda aritmética, lógica de veredictos (cada
  invariante trae test-pin que sigue vigente).
- Live total ≤ $8 (este reporte: §5).

Implementación: `live/tests/test_f2_latency2.py` (34 tests). Edits
mínimos en `live/swarm/coordinator.py`, `live/swarm/roles.py`
(prompt restaurado tras la reversión; detalle §2 y §6). Sin edits en
`live/verifier/*` ni `live/evidence/*` (la vía rápida reutiliza la
lógica intacta en modo lectura).

## 0. Baseline (antes) y meta

- F2-3 medido (`live/F2_LATENCY.md` §4, deadline 120s): p50 80.3s,
  p95 115.7s por claim; recall 0.611 (22/36), exactitud 22/22,
  0 falsos-C/S; $/claim 0.0227; wall 372.4s/run.
- META F2-7: p50 ≤ 25s y p95 ≤ 60s, CON recall ≥ 0.55 y 0 falsos-C.
- RESULTADO: p50 4.3–4.5s (CUMPLE, 18×), p95 73–79s (MISS honesto
  por 13–19s; causa §4–§5), recall 0.611 idéntico, 0 falsos,
  consistencia r1-vs-r2 36/36. Ver §4 (tabla) y §5 (atribución).

## 1. Taxonomía de la latencia (de dónde salían los ~80–116s)

Del código y de F2_LATENCY §1 (sin abrir evals): por claim,
1 ESTRUCTURADOR + grounding serie (1 `wb_lookup` + 3 consultas ×
(Wikipedia + DDG) + hasta 4 fetches, todo serie) + loop RECUPERADOR
(hasta 4 rondas + forced-final, cada llamada ~5–30s) + REDTEAM
(1 llamada + fetch + hasta 2 rondas más). Ancho 8 ⇒ 36 claims en
~5 oleadas. F2-3 dejó 9 claims en vía rápida (TRUE con fila WB,
~4s) y 27 en path lento: 20 `done` (~82s med, incluyen redteam) y
7 budget-exhausted (~114s med, re-búsquedas estériles en b/A3).
La mediana (puestos 18–19) era path completo; la cola moría por
presupuesto de tokens a ~110–117s.

## 2. Palancas (todas generales + test general)

F2-L1. FAST-PATH REFUTACIÓN POR CÓDIGO (extiende F1).
`code_refutation_reason(packages, text, plan)` (pura salvo imports
perezosos de lectura): dispara cuando un paquete `code` PRIMARY con
`source_meta` cita una fila WB cuya cifra difiere del claim MÁS ALLÁ
de la tolerancia del verificador (LEÍDA del mismo
`DEFAULT_REL_TOLERANCE`, jamás cambiada). Guardas: las mismas F1
(indicador total curado, año exacto, no-aritmético, iso3
determinista) MÁS espejos del verificador (unidades compatibles
directa o vía `source_meta`, sin backstop de escala, año del plan
== año del claim, cifras no-año en ambos lados; años 1960–2100
nunca emparejan). Si dispara, se SALTA el pool web y el loop de
modelo (igual que F-L2), emite `claim.early_exit` con `refutes`, y
el verificador decide `contradicted` desde el paquete guardado (la
lógica de veredicto no se toca; el exit solo cierra RONDAS).
Kill-switch `SwarmConfig.code_refutation_enabled` (default-on).
Tests: `test_refutation_*` (dispara / silencio ×7 guardas /
kill-switch / pool-saltado / paquete-sostiene-contradicted /
TRUE-nunca-refuta) + pines de tolerancia/escala/jerarquía §5.
Efecto medido §5: 11 claims FALSE de ~80s a ~4s (~20×).

F2-L2. Grounding en paralelo (queries concurrentes, cap 4).
`_ground_claim` ejecuta las 3 queries multi-consulta en un
`ThreadPoolExecutor(max_workers=4)` (`GROUND_QUERY_WORKERS`, pineado);
`map` preserva el orden de queries ⇒ el ranking pooled + fetch
primary-first serial es DETERMINISTA (mismo orden que en serie).
Rate limits: cap 4 concurrentes sobre búsquedas keyless (Wiki + DDG),
timeouts y reintentos intactos; un search que lanza degrada a pool
vacío (nunca rompe el claim). Los fetches siguen seriales
primary-first (determinismo del store). Kill-switch
`SwarmConfig.ground_parallel` (default-on).
Tests: `test_ground_*` (workers==4 / pico 3 en paralelo / serie pico
1 mismos paquetes / dedup determinista / excepción-degrada).
Efecto medido §5: microbench offline 0.24s→0.08s por pool; en live
el efecto queda confundido con varianza de modelo (§5 honesto).

F2-L3. Rondas + REDTEAM: 4→2 REVERTIDO; redteam diferido SÍ.
(a) El corte 4→2 se IMPLEMENTÓ, se midió en 2 corridas dev… y se
REVIRTIÓ por romper el gate 0 falsos-C: run2 juzgó B1 (TRUE 3.1%)
`contradicted` desde un secundario redondeado (3% wiki vs 3.1%,
rel 3.3% > 2%). Con 2 rondas el prompt apremia al modelo a citar
pronto (secundario débil); con 4 rondas el modelo re-busca y
abstiene en b (0 pkgs, 4/4 corridas previas + aislamiento).
`MAX_TOOL_ROUNDS` vuelve a 4 y el prompt a “at most 4 tool rounds”
(restauración exacta; §3 justifica los pines). El forced-final tras
4 rondas se mantiene (pineado).
(b) REDTEAM fuera del path crítico SÍ se embarca (default
`SwarmConfig.redteam_enabled=False`; True restaura el round para
revert/debug). Tras recuperador, `done (redteam deferred)` sin
llamada redteam ni 2ª ronda: el veredicto nunca dependió del
redteam (advisory: propone una contra-query, no decide), y las 2
corridas finales repiten EXACTO los 22 veredictos F2-3 (§4).
Ahorra 1 + hasta 3 llamadas por claim lento (~60s med §5).
Tests: `test_max_tool_rounds_stays_4_*`, `test_forced_final_after_*`,
`test_redteam_deferred/opt_in_*`.

F2-L4. Presupuesto 120→70s (blando; duro +30 intacto).
`CLAIM_DEADLINE_S` 120 → 70. Los rounds abortan con nota honesta
`claim deadline 70s exceeded at <etapa> (N packages kept)` +
`degraded:true`, conservando el grounding. Mide §4: 7 claims
degradados/corrida (A3+B1–B6, 0 pkgs, `unverifiable` correcto).
El p95 ≤ 60 exige cola < 60 (§4: MISS honesto a 73–79s por
overhead post-aborto; con deadline 70 la cola no puede bajar de
~70s; se documenta, no se fuerza a 60 contra el enunciado).
Tests: `test_claim_deadline_is_70`,
`test_deadline_aborts_with_new_defaults`.

F2-L5. Taxonomía exit/no-exit (telemetría honesta).
`no_exit_reason(packages, text, plan)` (pura): `exit_support_fast` /
`exit_refute_fast` cuando un fast-path dispararía; si no,
`no_exit:<causa>` general (nunca claim/cifra/URL):
`ineligible_indicator/geo/year/arithmetic/country`,
`no_code_package` (elegible pero sin fila), `year_mismatch`,
`numeric_undecided` (borde unidades/escala — el verificador no
decidiría solo), `support/refutation_disabled` (kill-switch off,
solo pipeline), fallbacks sin plan. Cada claim lento emite
`claim.no_exit` `{claim_id, reason, n_packages}` (el rápido emite
`claim.early_exit`; ambos conviven si el post-loop exitea).
Distribución medida §5 (idéntica en ambas corridas).
Tests: `test_no_exit_*` (cada causa / evento emitido/ausente /
seguridad).

## 3. Edits a tests existentes (justificados, invariantes preservadas)

Precedente: mismo criterio que F2_LATENCY §3 (ningún cambio de
veredicto, ninguna invariante perdida; cada ajuste documenta por qué).
La vía rápida de refutación y el redteam diferido cambian el PATH
por defecto (menos llamadas); los tests que pinean el path completo
optan explícitamente por el modo previo. Cero cambios en asserts de
veredictos/unidades/escala/jerarquía.

- `test_m2b_swarm.py` (4 tests): `redteam_enabled=True` explícito
  porque el default diferido recorta la 3ª llamada (estos tests
  pinean el path con redteam): `test_happy_path_emits_m2a_valid_package`
  (usage 3), `test_redteam_counter_query_triggers_second_round`
  (2ª ronda), `test_malformed_reply_retried_once` (3 llamadas),
  `test_run_swarm_entrypoint` (usage 3). Cero cambios en asserts.
- `test_f2_latency.py`: `test_max_concurrency_is_8` pin 120→70 (el
  cambio EXPLÍCITO de (d)); `test_early_exit_kill_switch_*` +
  `test_early_exit_silent_on_mismatch_*` añaden `redteam_enabled=True`
  (pinean 3 llamadas) y el segundo además
  `code_refutation_enabled=False` (su fixture FALSE ahora
  refute-exitea por defecto; para pinar el path completo se apaga
  la vía rápida). Cero cambios en asserts.
- `test_m2d_recall.py::test_preamble_rescued_end_to_end`:
  `redteam_enabled=True` (pinea recup+redteam = 2 llamadas).
  Cero cambios en asserts.
- `test_f2_latency2.py` (nuevo, 34 tests): pinea los defaults F2-7
  (deadline 70, rounds 4 tras reversión, redteam diferido,
  refutación on, paralelo on) + cada guarda intocada.

Suite offline final: `live/tests/` 429 passed, 4 skipped (live-gated)
— incluye los 34 nuevos + los 7 ajustes §3; `tests/` raíz 758 passed.
Cero regresiones.

## 4. Resultados DEV antes/después

Corridas finales (código embarcado, rounds 4 + redteam diferido):
`data/runs/f2lat2-final-atlas-run{1,2}.json` (+reporte
`f2lat2-final-report.json`, logs `.jsonl`), 2026-11-02,
`--arms atlas --runs 2 --max-concurrency 8 --atlas-deadline 70`,
wall 216.1s ambas. Smokes/control: `f2lat2-smoke*` (6 claims A1–A6:
5/6, p50 3.6s/p95 15.3s, $0.0457), A/B paralelo C1 (§5),
aislamiento B1 2-vs-4 rounds (§2 F2-L3, ambos abstienen sueltos).

| brazo | n | decididos (recall) | exactitud | Wilson 95% | falsos-C | falsos-S |
|---|---|---|---|---|---|---|
| atlas F2-3 run1 (ref) | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas F2-7 final run1 | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas F2-7 final run2 | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas F2-7 pooled | 72 | 44 (0.611) | 44/44 = 1.000 | [0.920, 1.000] | 0 | 0 |

GATES CUMPLIDOS SIN REVERSIÓN FINAL: recall 0.611 ≥ 0.55 con 0
falsos-contradicted en ambas corridas (la reversión de rondas fue
INTERMEDIA, antes de embarcar; §2). Más fuerte: las 6 categorías
repiten EXACTO el panel F2-3 (a 5/6, b 0/6, c 6/6, d 0/6, e 5/6,
f 6/6), el set unresolved es idéntico (A3, B1–B6, D1–D6, E6) y la
consistencia r1-vs-r2 es 36/36 = 1.000: NINGUNA palanca embarcada
cambió ningún veredicto.

Latencia por claim (veredicto − investigando, mismo reloj que F2-3):

| métrica | F2-3 run1 (antes) | F2-7 final run1 | F2-7 final run2 |
|---|---|---|---|
| p50 | 80.3s | 4.3s | 4.5s |
| p95 | 115.7s | 79.1s | 73.3s |
| wall/run 36 claims | 372.4s | ~108s (216.1s/2runs) | ~108s |
| $/claim | 0.0227 | 0.0080 (−65%) | 0.0086 (−62%) |
| trabajo total | 2520 claim-s | 832 claim-s (−67%) | 817 claim-s |

META: p50 ≤ 25s CUMPLE (4.3–4.5s, 18×); p95 ≤ 60s MISS honesto
(73–79s, +13–19s). Causa: 7 degradados por deadline-70 (A3+B1–B6,
0 pkgs, `unverifiable` correcto) miden 70–81s (aborto blando a 70s
+ cierre de evidencia/veredicto); con cola a ~70s el p95 (35º de
36) no puede bajar de ~70s. Bajar a deadline ~55 lo cumpliría,
pero el enunciado manda 70s: se declara el MISS, no se fuerza.

Por grupo de ejecución (final run1/run2, medido):

| grupo | n | decididos | lat med | lat min–max | $/claim |
|---|---|---|---|---|---|
| early-exit support (F-L2) | 9/9 | 8/8 | ~3.8s | 2.2–5.3s | ~0.003 |
| early-exit refute (F2-L1) | 11/11 | 11/11 | ~3.7s | 2.6–5.3s | ~0.003 |
| slow done (redteam diferido) | 9/8 | 3/3 (C1/C4/C6) | ~25s | 21–28s / 23–37s | ~0.015 |
| deadline 70 degradado | 7/7 | 0/0 | ~76s | 72–81s / 70–74s | ~0.018 |
| budget (run2 D3, flaky pkgs) | 0/1 | 0/0 | 37.3s | — | 0.047 |

Lectura honesta: 20/36 salen por vía rápida determinista (~4s, $0.003);
9–10 lentos deciden/abstienen correctamente en ~25s sin redteam (antes
~82–89s); 7 abstienen por deadline a ~76s (antes ~114s por budget).
24/36 ≤ 25s en ambas corridas. D3-run2 muestra varianza de pkgs (0 vs
2) con MISMO veredicto (`unverifiable`): la abstención aritmética es
robusta al inventario.

## 5. Coste, atribución por palanca, unresolved

Live F2-7 total ≈ **$1.28** (tope $8): smokes $0.0457 + reversión
2-rounds $0.5451 (r1 $0.2487 + r2 $0.2964, con 1 falso-C B1 — causa
de la reversión §2) + aislamiento B1 $0.0435 + finales $0.5991
(r1 $0.2888 + r2 $0.3103) + A/B paralelo C1 $0.0279 + probes ~$0.01.
`invalid_packages=0`, `snapshots_unresolved=0` en las finales.

Atribución MEDIDA por clase (run1 final vs F2-3 run1, claim-segundos):

- F2-L1 refutación (11 FALSE WB): 900.4s → 40.3s (−860s, −96%,
  med 79.7s → 3.9s, ~20×; $0.0257→$0.003/claim). Toda la ganancia
  del p50: la mediana pasa de path completo a vía rápida.
- F2-L3 redteam diferido (9 slow-done C1/C4/C6+D1–D6): 807.5s →
  225.5s (−582s, −72%, med 89.4s → 25.3s, ~3.5×). C1/C4/C6 siguen
  contradichos vía modelo (recall preservado); D1–D6 siguen
  absteniendo correctamente (unit/aritmética). Rondas 4 intactas
  tras la reversión (0 aporte, 0 daño).
- F2-L4 deadline 70 (cola A3+B1–B6, 7 claims): 778.0s → 529.8s
  (−248s, med 114.1s → 75.9s). Antes morían por budget-20k a
  ~110–117s; ahora abortan por wall-70 a 70–81s (overhead de
  cierre). Los 7 degradados abstienen con 0 pkgs (`unverifiable`
  correcto, primarios 403/física). Esta cola FIJA el p95 (MISS §4).
- F2-L2 paralelo: microbench offline determinista 3×0.08s serie
  0.24s → paralelo 0.08s (pico 3, cap 4); en live el A/B C1
  (on 27.9s/2 pkgs vs off 55.9s/1 pkg, MISMO veredicto
  `contradicted`) sale 28s más rápido pero confundido con varianza
  de modelo/web (distinto inventario): NO se atribuyen los 28s al
  paralelismo; aporte honesto ≈ segundos (2/3 del tiempo de search)
  dentro de los ~25s del path lento. Veredictos preservados en A/B.
- F2-L5 taxonomía (telemetría, 0 coste de path): 20 exit (9 support
  + 11 refute) + 16 no-exit idénticos en ambas corridas:
  `ineligible_indicator` 9 (A3, B1–B6, D1–D2 por guess de
  crecimiento), `ineligible_country` 1 (C1), `ineligible_year` 2
  (C4/C6), `ineligible_arithmetic` 4 (D3–D6). Determinista.

Unresolved (14, honestos, idénticos a F2-3): A3 (física, deadline);
B1–B6 (primarios 403, deadline 0 pkgs — sin el falso-B1 de las
corridas 2-rounds revertidas); D1–D6 (D1–D2 unit, D3–D6 aritmética —
abstención correcta); E6 (euros, vía support-exit, mismo veredicto
que F2-3). Limitaciones heredadas intactas (PDFs comprimidos, claims
sin año salvo modelo, metros/km/coronas).

Corridas 2-rounds REVERTIDAS (documentación de la reversión, no
embarcadas): `f2lat2-atlas-run1` 23/36 (0.639) 0 falsos
p50/p95 4.0/13.1s; `run2` 24/36 (0.667) 1 falso-C (B1 TRUE→
contradicted desde secundario 3% vs 3.1%) p50/p95 4.3/55.8s.
El falso (P0) fulminó el corte 4→2 aunque su p95 (55.8s) habría
cumplido: gates primero, latencia después.

## 6. Archivos tocados (diff mínimo)

- `live/swarm/coordinator.py`: `CLAIM_DEADLINE_S` 70,
  `MAX_TOOL_ROUNDS` 4 (revertido, documentado),
  `+GROUND_QUERY_WORKERS` 4, `SwarmConfig` (+`code_refutation_enabled`,
  `+ground_parallel`, `+redteam_enabled=False`),
  `+code_refutation_reason`, `+no_exit_reason`, `_ClaimCtx.exit_taxonomy`,
  grounding paralelo + skip por refutación, redteam diferido,
  eventos `claim.no_exit` (`+_publish_no_exit`), `__all__`.
- `live/swarm/roles.py`: prompt RECUPERADOR restaurado a “at most 4
  tool rounds” (reversión exacta; el corte a 2 vivió solo en las
  corridas revertidas).
- `live/tests/test_f2_latency2.py`: NUEVO (34 tests).
- Tests existentes: ajustes §3 (7 tests, solo flags de path).
- Sin edits en `live/verifier/*` ni `live/evidence/*` (lectura).
