# F2_LATENCY — F2-3 latencia Atlas Live (DEV solo; retention sellada)

Tarea F2-3, rama main, checkout compartido. Objetivo: bajar la latencia
Atlas sobre `live/eval2/dev.json` (36 claims) SIN tocar tolerancia,
escala, jerarquía ni veredictos (solo velocidad + estados UI), con
recall ≥ 0.55 (aceptado por varianza vs 0.611 F2-2) y 0
falsos-contradicted; si recall < 0.55 o 1 falso-contradicted, se
REVIERTE la palanca culpable.

Sellos y límites:

- `dev.json` sha256 `db4ebe71…` (coincide con prereg; verificado antes
  de correr).
- `retention.json` SELLADA: ni abierta ni listada en ningún paso.
- Sin git ops. Sin secretos en el reporte (solo presencia booleana).
- Intocados: tolerancia 2%, backstop `possible_scale_error`, jerarquía
  primary > secondary, lógica de veredictos (cada invariante trae
  test-pin que sigue vigente; §5).
- Live total ≤ $6 (este reporte: solo la corrida dev atlas ×1 + smokes).

Implementación: `live/tests/test_f2_latency.py` (35 tests). Edits
mínimos en `live/swarm/coordinator.py`, `live/swarm/fetch.py`,
`live/run_live.py`, `live/server/app.py`, `live/ui/index.html`
(detalle §2).

## 0. Baseline (antes)

- Enunciado F2-3: p50 ~75s, p95 ~90s por claim atlas.
- Medido F2-2 (`live/F2_RECALL.md` §5, post-fix, deadline 90s):
  p50 ~84–85s, p95 ~90s; recall 0.611 (22/36), exactitud 22/22,
  $/claim ~0.021.
- Referencia para §6: ambas cifras (enunciado y medida F2-2).

## 1. Taxonomía de la latencia (de dónde salen los ~75–90s)

Del código (sin abrir evals): por claim, 1 llamada ESTRUCTURADOR +
grounding secuencial (1 `wb_lookup` + ≥3 consultas × (Wikipedia + DDG)
+ hasta 4 fetches, todo serie en `_ground_sync`) + loop RECUPERADOR
(hasta 4 rondas + forced-final, cada llamada de modelo ~5–30s) +
REDTEAM (1 llamada + fetch + hasta 2 rondas más). Ancho 4 ⇒ 36 claims
en ~9 oleadas. Las categorías que queman presupuesto (b: primarios
403; TIME: re-búsquedas) marcan el p95 contra el deadline.

## 2. Palancas (todas generales + test general)

F-L1. Paralelismo 4 → 8 + telemetría de contención.
 `coordinator.MAX_CONCURRENCY` 4 → 8 (el default de `SwarmConfig` lo
 sigue); `run_swarm` mide `peak_inflight` y `sem_wait_s_max`
 (espera máxima de semáforo) en `SwarmReport` (campos aditivos).
 Velocidad pura: el mismo pipeline por claim, más claims a la vez.
 Tests: `test_max_concurrency_is_8`, `test_run_swarm_*`
 (ancho 2 ⇒ peak 2 + espera > 0; default ⇒ ancho 8).

F-L2. Salida temprana por primario + match numérico (default-on, con
 kill-switch `SwarmConfig.early_exit_enabled`).
 `early_exit_reason(packages, text)` (pura): dispara cuando un paquete
 PRIMARY con quote verbatim (todos lo son por construcción) trae una
 cifra dentro de la tolerancia del verificador (LEÍDA del mismo
 `DEFAULT_REL_TOLERANCE`, jamás cambiada) respecto a una cifra del
 claim. Los años (1960–2100 sin escala) nunca emparejan.
 Dos niveles: (i) tras el rescate WB en grounding — si dispara, se
 SALTA el pool web (`_ground_sync` retorna antes de las ≥3 consultas +
 4 fetches) y el loop de modelo; (ii) tras RECUPERADOR — si dispara,
 se salta la ronda REDTEAM. Ambos emiten `claim.early_exit`
 `{claim_id, reason, n_packages}` y el `claim.evidence` normal sigue
 su curso: el verificador decide con los paquetes guardados (la
 lógica de veredicto no se toca; el exit solo cierra RONDAS).
 Nota: el exit a nivel grounding solo puede producir `supported` o
 `unverifiable` desde el paquete code (el match numérico excluye
 `contradicted` por construcción) ⇒ no puede crear falsos-C nuevos.
 Tests: `test_early_exit_*` (dispara / no-dispara ×4 / kill-switch /
 post-recuperador / pool-web-saltado).

F-L3. Presupuesto tiempo/claim ~120s, blando + duro.
 `CLAIM_DEADLINE_S` 90 → 120 (blando, por rondas) +
 `CLAIM_DEADLINE_SLACK_S` = 30 (duro, `wait_for` como backstop).
 Los rounds chequean `_ClaimCtx.expired()` (tope de loop, forced-final
 y antes de REDTEAM): al vencer, abortan con nota honesta
 `claim deadline 120s exceeded at <etapa> (N packages kept)` +
 `degraded:true`, conservando el grounding (el rescate WB sobrevive).
 El backstop solo alcanza a una llamada colgada individual.
 Tests: `test_deadline_aborts_rounds_gracefully`,
 `test_deadline_backstop_still_fires`.

F-L4. Caché fetch memoria + disco.
 `fetch.cache_store/cache_lookup` (nunca lanzan): clave sha256(URL),
 disco `data/cache/{sha}.json` (meta + cuerpo base64, TTL 24h, stale
 se trata como miss y se deslinkea), memoria FIFO 64 entradas ≤256KiB
 (TTL 24h). El lookup va DESPUÉS de robots (la política siempre gana)
 y respeta `Cache-Control: no-store` (no se almacena; contador
 `no_store`). Un hit devuelve sha/captured_at ORIGINALES y
 re-materializa el snapshot si falta. Contadores `get_cache_stats()`
 para atribución. Velocidad pura: mismos bytes, cero re-descargas.
 Tests: `test_cache_*`, `test_fetch_url_*` (hit/miss/TTL/no-store/
 robots-gana).

F-L5. Prefetch desde transcripción PROVISIONAL (solo warmup).
 `run_live.PrefetchManager`: `transcript.partial` (≥40 chars, throttle
 2s/turno) lanza search+fetch top-2 en worker thread; `transcript.final`
 confirma su turno y cancela warmups pendientes de turnos viejos.
 Un partial NUNCA decide nada (sin detector, sin eventos de bus, sin
 veredictos). Stats en el summary (`prefetch:{partials, launched,
 urls_warmed, confirmed, cancelled}`). Nota de medición: eval2 no usa
 ASR (sin partials) ⇒ esta palanca NO contribuye a los números §6;
 validada offline + lista para el live con stream.
 Tests: `test_prefetch_*` (lanza/calibra, throttle, confirma/cancela,
 fallos-tragados).

F-L6. Prompt-caching: accounting sí, keys por rol no.
 Medido en el SDK: `MetaClient` y `ResponsesClient` YA envían
 `prompt_cache_key="shipcheck-v0"` (F2-B) y exponen `cached_tokens`
 en `last_usage`. Lo que SÍ se hizo (edits permitidos): `collect_usage`
 pliega `cached_tokens` y el summary de `run_live` lo reporta
 (`model_cached_tokens`); pin de que los 3 system prompts son
 constantes estables a nivel módulo (prefijos cacheables idénticos).
 Lo que NO se hizo (documentado, fuera de alcance): keys distintas
 por rol (estructurador/recuperador/redteam) exigirían cambiar la
 firma de `complete_json` en `src/atlas/models/meta.py` (fuera de la
 lista de edits) y el comportamiento del proveedor ante keys
 distintas no está probado ⇒ no se tocó.
 Tests: `test_collect_usage_folds_cached_tokens`,
 `test_role_system_prompts_are_stable_constants`.

F-L7. UI: progreso real + veredicto PROVISIONAL rotulado.
 `live/ui/index.html`: (i) `renderProgress(n_sources, n_quotes)`
 DERIVADO del evento existente `claim.evidence` (urls únicas =
 fuentes, paquetes = citas; cero tráfico nuevo de bus; se tolera un
 frame explícito `claim.progress` futuro con el mismo renderer);
 (ii) `claim.verdict` con `provisional:true` se rotula PROVISIONAL
 (estilo dashed ámbar, sin promover a `veredicto`, sin cerrar
 timeline/teatro) y el `claim.verdict` final posterior lo corrige
 (línea de historial "corrección: provisional → final"); (iii)
 `claim.early_exit` pinta línea de historial (telemetría honesta).
 Contrato del productor provisional (stream con partials): futuro —
 más allá de los edits de este hito; el consumidor queda listo y
 pineado. `live/server/app.py`: `/metrics` suma `events_by_type`
 (aditivo; el broadcast ya re-emitía todo por WS).
 Tests: `test_ui_*` (pines estáticos), `test_metrics_*`.

## 3. Edits a tests existentes (justificados, invariantes preservadas)

Precedente: mismo criterio que F2_RECALL §3 (ningún cambio de
veredicto, ninguna invariante perdida; cada ajuste documenta por qué).

- `test_m2b_swarm.py` (4 tests): `early_exit_enabled=False` explícito
  porque sus fixtures WB SÍ matchean (el exit default-on es correcto
  en producción, pero estos tests pinean el path completo):
  `test_happy_path_emits_m2a_valid_package` (paquete model + usage 3),
  `test_redteam_counter_query_triggers_second_round` (2ª ronda),
  `test_malformed_reply_retried_once` (3 llamadas),
  `test_run_swarm_entrypoint` (usage 3). Cero cambios en asserts.
- `test_m2c_e2e.py::test_e2e_supported_offline`: mismo flag (pinea la
  cadena model+code de 2 paquetes). Cero cambios en asserts.
- `test_m2b_swarm.py::test_deadline_degrades`: stub de `search_fn`
  (el split blando/duro deja correr el grounding pasado el budget
  mínimo; sin stub tocaría red). Asserts intactos.
- `test_m6_auditor.py::test_runner_reuses_run_verify_pipeline`:
  pin 4 → 8 (el cambio EXPLÍCITO pedido en (a); el pin de reuso sigue).
- `test_e2e_live.py::test_offline_pipeline_model_double`: pin de usage
  suma `"cached_tokens": 0` (fold aditivo F-L6). Resto intacto.

Suite offline final: `live/tests/` 386 passed, 4 skipped (live-gated)
— incluye los 35 nuevos `test_f2_latency.py` + los 8 ajustes §3;
`tests/` raíz 758 passed. Cero regresiones.

## 4. Resultados DEV antes/después

Corrida: `data/runs/f2lat-atlas-atlas-run1.json` (+reporte
`f2lat-atlas-report.json`, log `f2lat-atlas-atlas-run1.jsonl`),
2026-11-02, `--arms atlas --runs 1 --max-concurrency 8
--atlas-deadline 120`, wall 372.4s. Smokes previos:
`f2lat-smoke*` (3 claims $0.0534; 2 claims $0.0086).

| brazo | n | decididos (recall) | exactitud | Wilson 95% | falsos-C | falsos-S |
|---|---|---|---|---|---|---|
| atlas F2-2 post-fix (ref) | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas F2-3 run1 | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |

META CUMPLIDA SIN REVERSIÓN: recall 0.611 ≥ 0.55 con 0
falsos-contradicted (gatillos de reversión no tocados). Más fuerte:
las 6 categorías repiten EXACTO el panel F2-2 (a 5/6, b 0/6, c 6/6,
d 0/6, e 5/6, f 6/6) y el set unresolved es idéntico
(A3, B1–B6, D1–D6, E6): NINGUNA palanca cambió ningún veredicto.

Latencia por claim (veredicto − investigando, mismo reloj que F2-2):

| métrica | enunciado F2-3 (antes) | F2-2 medido (antes) | F2-3 run1 (después) |
|---|---|---|---|
| p50 | ~75s | ~84–85s | 80.3s |
| p95 | ~90s | ~90s | 115.7s |
| wall 36 claims | — | ~780s/run (1559s/2runs) | 372.4s (−52%) |
| $/claim | — | 0.0212–0.0216 | 0.0227 (+6%) |

Por grupo de ejecución (run1, medido):

| grupo | n | decididos | lat med | lat min–max | $/claim |
|---|---|---|---|---|---|
| early-exit(grounding) | 9 | 8 | 4.0s | 2.7–5.7s | 0.0035 |
| full path (done) | 20 | 14 | 82.2s | 76.3–104.4s | 0.0257 |
| budget exhausted | 7 | 0 | 114.1s | 93.8–116.8s | 0.0387 |

Lectura honesta: el p50 apenas se mueve porque la mediana (puestos
18–19 de 36) sigue siendo un claim de path completo (~80s); solo 9/36
salen por la vía rápida (los TRUE con fila WB; los FALSE
correctamente NO matchean y recorren el path completo). El p95 SUBE
por el deadline 120 mandado: la cola antes moría a 90s por wall y
ahora agota presupuesto a ~110–117s (mismos veredictos
`unverifiable`, 0 pkgs). El wall se parte a la mitad (ancho 8 +
menos trabajo). Ninguna nota `claim deadline`/`hard backstop`
disparó en dev: el presupuesto de tokens (20k) muerde antes que los
120s de wall en todos los claims lentos.

## 5. Coste, atribución por palanca, unresolved

Live F2-3 total ≈ **$0.88** (tope $6): smokes $0.0534 + $0.0086 +
final $0.8188 (run $0.8159 + probe $0.0029). `invalid_packages=0`,
`snapshots_unresolved=0`.

Atribución MEDIDA (no supuesta):

- F-L2 exit: 9 claims a med 4.0s vs 82.2s del path completo (~20× en
  esa clase; smoke pre-split: 51–57s ⇒ post-split: 2.6–3.8s, el skip
  del pool web aporta ~48s de los ~76s ahorrados). Ahorra
  ~684 claim-segundos (~27% del trabajo total). E6 prueba que el
  exit preserva veredictos: sale temprano y abstiene igual que F2-2.
  Cero falsos-C (por construcción §2).
- F-L1 ancho 8: 2526 claim-segundos / 8 ≈ 316s + overhead = 372s
  wall medido; al ancho 4 anterior habrían sido ~630s+overhead ⇒
  ~300s wall ahorrados sobre la MISMA carga. (Nota: eval2 paraleliza
  vía el semáforo propio de `run_verify`, no `run_swarm`: el ancho 8
  sí se ejercitó en dev; la telemetría peak/wait queda verificada
  offline.)
- F-L4 caché: ~73 stores en la ventana de la corrida (frío: casi
  todo miss); hits intra-run NO instrumentados en el path de eval
  (gap declarado; hit/miss/TTL/no-store verificados offline + el
  probe calienta 1 URL: A1 2.7s warm vs A2 3.8s frío en smoke2).
- F-L3 budget 120: convierte muertes-a-90s-por-wall en
  agotamiento-de-presupuesto a ~114s (7 claims, veredictos iguales);
  degradados 7 vs 9–10 en F2-2. Coste visible: p95 90 → 115.7s.
- F-L5/F-L6/F-L7: eval2 no ejercita ASR partials, UI ni caché de
  prompt ⇒ 0 contribución a estos números (verificadas offline);
  el fold de `cached_tokens` queda listo para el live con stream.

Unresolved (14, honestos, idénticos a F2-2): A3 (física, budget);
B1–B6 (primarios 403, budget 0 pkgs); D1–D6 (reglas aritmética/unit,
`done` 2 pkgs — abstención correcta); E6 (euros, regla — vía exit,
mismo veredicto que F2-2). Limitaciones heredadas intactas (PDFs
comprimidos, claims sin año, metros/km/coronas).

## 6. Archivos tocados (diff mínimo)

- `live/swarm/coordinator.py`: `MAX_CONCURRENCY` 8,
  `CLAIM_DEADLINE_S` 120 + `CLAIM_DEADLINE_SLACK_S` 30,
  `SwarmConfig.early_exit_enabled`, `SwarmReport`+telemetría,
  `+early_exit_reason`, `_ClaimCtx.expired`, cheques por ronda,
  skip del pool web en grounding, evento `claim.early_exit`,
  `__all__`.
- `live/swarm/fetch.py`: `+default_cache_dir`, capa caché
  (`cache_key_for_url`, `cache_store/lookup`, `cache_entry_fresh`,
  stats), wiring en `fetch_url` (+`use_cache`/`cache_dir`,
  no-store, robots-primero) y `fetch_url_async`,
  `+_materialize_snapshot`.
- `live/run_live.py`: `+PrefetchManager` + wiring partial/final +
  `prefetch` en summary; `collect_usage` pliega `cached_tokens`.
- `live/server/app.py`: `events_by_type` en `/metrics`.
- `live/ui/index.html`: progreso derivado, provisional + corrección,
  línea early-exit, ramas `claim.progress`/`claim.early_exit`.
- `live/tests/test_f2_latency.py`: NUEVO (35 tests).
- Tests existentes: ajustes §3.
