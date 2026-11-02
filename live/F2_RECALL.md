# F2_RECALL — F2-2 recall Atlas (DEV solo; retention sellada)

Tarea F2-2, rama main, checkout compartido. Objetivo: recall Atlas ≥ 0.60
sobre `live/eval2/dev.json` (36 claims) con 0 falsos-contradicted, con
fixes GENERALES (ninguno nombra claims, cifras o URLs concretas) + tests
offline generales.

Sellos y límites:

- `dev.json` sha256 `db4ebe71…f78a` (coincide con prereg; verificado
  antes de correr).
- `retention.json` SELLADA: ni abierta ni listada su contenido en ningún
  paso (solo `dev.json` alimentó ajustes y corridas).
- Sin git ops. Sin secretos en el reporte (solo longitudes/prefijos).
- Intocados: tolerancia 2%, guarda `possible_scale_error`, jerarquía
  primary > secondary (cada fix trae test-pin de que siguen vigentes).

Implementación: `live/tests/test_f2_recall.py` (43 tests). Edits en
`live/swarm/coordinator.py`, `live/swarm/roles.py`, `live/swarm/fetch.py`,
`live/evidence/package.py`, `live/verifier/check.py` (detalle abajo).

## 1. Taxonomía de abstenciones (baseline pre-fix)

Fuente primaria: corrida fresca pre-fix
`data/runs/f2recall-base-atlas-run1.json` (código sin edits; lanzada
2026-11-01 23:29 UTC, antes de cualquier edit — los módulos ya estaban
cargados en ese proceso). Corroborada contra F2-1
(`eval2-20261101-225539-atlas-run{1,2}.json`).

Categorías: búsqueda (recuperador vacío / sin citas), fuente (primario
inaccesible: 403/robots), parseo (citas no-verbatim, tablas/PDF
ilegibles), regla (verificador abstiene con paquetes: unit/scale/
temporal/aritmética), tiempo (deadline 90s / presupuesto 20k tokens).

Resultado fresco: 8/36 decididos (recall 0.222), 8/8 exactitud, 0 falsos,
20 degradados, $1.1157. Detalle `data/runs/f2recall-base-atlas-run1.json`:

- Decididos (8, todos TRUE WB de una cifra): A1, A2, E1, E2, E3, F1, F2,
  F3. CERO claims FALSE decididos.
- BÚSQUEDA (6, note=done/redteam-skipped, pkgs=0): A5, D1, D4, D6, E4,
  E5. El recuperador devuelve vacío aunque el doc WB grounded contiene
  la refutación (el prompt pre-fix ordenaba `[]` ante todo lo que no
  "apoya" el claim).
- TIEMPO (20, budget-exhausted o deadline, pkgs=0): A3, A4, A6, B1–B6,
  C1–C5, D2, D5, E6, F4, F5, F6. El loop quema 20k tokens / 90s
  re-buscando en web en vez de citar el grounded.
- REGLA (2, pkgs>0 + abstención): C6 (2 pkgs wiki con la refutación
  correcta 58.9M pero citas sin palabra de unidad ⇒ `unit_mismatch`),
  D3 (2 pkgs componentes USD vs ratio percent ⇒ `unit_mismatch`,
  abstención correcta).
- FUENTE (transversal b): IMF WEO y BLS empsit devuelven 403 al UA
  `atlas-live-research` (probe curl 2026-11-01: IMF 403, BLS-pdf 403,
  WB API 200). Primario directo inalcanzable; solo secundarios.
- D5 esta corrida abstuvo (budget, pkgs=0): el falso-contradicted F2-1
  (D5×2, componentes ESP+ITA) es flaky — aparece cuando el modelo sí
  trae ambas filas. La guarda F6 lo elimina por construcción.

Conclusión: 26/28 abstenciones son recuperación (búsqueda+tiempo), 2 son
regla. El fix de mayor palanca es el rescate determinista F1 + la
instrucción refutatoria F7; F6 cierra el P0 D5.

## 2. Fixes (todos generales + test general)

F1. Rescate determinista de fila WB (rescate de unidades estructuradas).
 grounding cita el span `"date":"AÑO","value":N` exacto del doc WB API
 ya fetched (verbatim por construcción regex-sobre-bytes) como paquete
 `extractor="code"` con `source_meta` (unidad curada). Sobrevive a
 loop vacío / budget / deadline (el grounding persiste).
 Guardas: indicador en el mapa curado de totales (per-cápita/crecimiento
 quedan fuera), año exacto del plan, claim NO aritmético (§F5), iso3 del
 plan == resolver determinista (anti wrong-country).
 `coordinator._maybe_add_wb_fallback/wb_row_quote_for/wb_fallback_eligible`,
 `build_package(..., extractor="code")`.
 Tests: `test_wb_row_quote_*`, `test_wb_fallback_*`,
 `test_build_package_code_extractor_attaches_meta`.

F2. Reformulación multi-consulta (≥3). `build_search_queries(plan, text)`:
 web_query del plan + entidad+año+authority + entidad+cifra + código
 indicador/iso3/año + fallbacks; pool dedup de hits, fetch de hasta 4.
 `coordinator._ground_claim` reescrito sobre el pool.
 Tests: `test_build_search_queries_*` (≥3, únicas, facets, acotadas).

F3. Preferencia de fuentes primarias. `rank_hits_primary_first` (criterio
 canónico `source_type_for_url`, estable) ordena el pool de grounding y
 el auto-fetch del loop recuperador: APIs oficiales antes que prosa.
 Tests: `test_rank_hits_*`.

F4. Lectura de tablas HTML (stdlib). `tables_from_html` (html.parser, sin
 pandas) + líneas `TABLE i row j` en `doc_excerpts` (ayuda de lectura;
 NO verbatim — documentado en docstring). Las celdas con cifras siguen
 ocurriendo raw para cita.
 Tests: `test_tables_from_html_*`, `test_doc_excerpts_*`.

F5. Lectura PDF mínima (stdlib, sin deps nuevas). `fetch.is_pdf_body`
 (content-type o magia `%PDF-`) + `extract_pdf_text` (zlib FlateDecode +
 strings literales `Tj/TJ`); `fetch.text` pasa a ser el texto extraído.
 Snapshot sigue siendo bytes raw (proceso): solo PDFs sin comprimir /
 metadata verifican verbatim — limitación documentada. `pdftotext`
 ausente, PyPDF2/pdfminer/pandas ausentes (verificado): no se añadió
 ninguna dependencia.
 Tests: `test_is_pdf_body_*`, `test_extract_pdf_text_*`.
 Impacto live esperado ≈ 0 en DEV-b (los PDFs IMF/BLS devuelven 403
 antes de parsear); el fix es capacidad general + tests.

F6. Guarda aritmética multi-fuente (cero falsos-contradicted).
 `package.is_multi_source_arithmetic(text)`: average/mean/median,
 cambio porcentual, share-of ("N percent of"), span from-AÑO-to-AÑO;
 excepción de nivel ("doubled/grew/fell TO x" ⇒ comparable directo).
 Verificador: claim aritmético + `numeric_mismatch` contra cita SIN
 lenguaje computado ⇒ `unverifiable/arithmetic_needs_computation` en vez
 de `contradicted`. Una cita EN prosa que sí enuncia lo computado
 ("grew 7.4 percent") sigue pudiendo refutar; un match sigue apoyando.
 Tolerancia/escala/jerarquía intactas (la guarda solo estrecha
 contradict → unverifiable).
 Tests: `test_is_multi_source_arithmetic_*`,
 `test_arithmetic_*`, `test_level_assertion_*`, `test_non_arithmetic_*`.

F7. Instrucción de citas refutatorias (prompt general, roles.py).
 RECUPERADOR: ante figura DISTINTA para misma entidad/indicador/año,
 devolver la cita refutatoria (evidencia correcta — el verificador
 decide); `[]` SOLO si ningún doc menciona la entidad/indicador.
 Justificación: con `[]`-ante-todo-lo-no-apoyado, ningún FALSE se puede
 decidir. + nudge en el seed a citar docs grounded antes de re-buscar.
 Test-pin: `test_recuperador_system_allows_refuting_quotes`.

## 3. Edits a tests existentes (justificados, invariantes preservadas)

El rescate determinista añade un paquete `code` donde antes solo había
(0–1) paquetes `model`; 3 tests pineaban inventarios exactos. Ninguno
cambia de veredicto ni pierde su invariante original:

- `test_m2b_swarm.py::test_happy_path_emits_m2a_valid_package`: el modelo
  cita un span verbatim MÁS LARGO que el rescate (spans idénticos
  dedupan por diseño); los pines M2a corren sobre el paquete model + se
  pinean 1 code con `source_meta`.
- `test_m2c_e2e.py::test_e2e_supported_offline`: 2 paquetes (wiki model +
  WB code, ambos support); veredicto `supported` y pines de store sin
  cambios salvo conteo 1→2.
- `test_m2c_e2e.py::test_e2e_invented_quote_is_unverifiable`: fixture sin
  cuerpo WB (el rescate verificaría el claim desde el primario);
  CERO cambios en asserts — la cita inventada sigue dando `[]` +
  `unverifiable/no_usable_package`.

Suite offline final: `live/tests/` 351 passed, 4 skipped (live-gated) —
incluye los 43 nuevos `test_f2_recall.py` + los 3 ajustes §3.

## 4. Resultados DEV antes/después

Corridas post-fix: `data/runs/f2recall-fix-atlas-run{1,2}.json` +
`f2recall-fix-report.json` (2026-11-01/02, wall 1559s, sin guardas).

| brazo | n | decididos (recall) | exactitud | Wilson 95% | falsos-C | falsos-S |
|---|---|---|---|---|---|---|
| atlas pre-fix base r1 | 36 | 8 (0.222) | 8/8 = 1.000 | [0.676, 1.000] | 0 | 0 |
| atlas post-fix run1 | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas post-fix run2 | 36 | 22 (0.611) | 22/22 = 1.000 | [0.851, 1.000] | 0 | 0 |
| atlas post-fix pooled | 72 | 44 (0.611) | 44/44 = 1.000 | [0.920, 1.000] | 0 | 0 |

META CUMPLIDA: recall 0.611 ≥ 0.60 con 0 falsos-contradicted en ambas
corridas (protocolo de reversión: no hizo falta — ningún fix introdujo
falsos-C ni falsos-S).

Por categoría (decididos/correctos, ambas corridas idénticas): a 5/6
(abstiene A3 física), b 0/6 (primarios 403), c 6/6, d 0/6 (aritmética:
abstención honesta), e 5/6 (abstiene E6 euros por construcción), f 6/6.
Consistencia r1-vs-r2: 36/36 = 1.000 (pre-fix F2-1: 0.917). Degradados:
10/9 por corrida (pre-fix: 20–22). `invalid_packages=0`,
`snapshots_unresolved=0` en ambas.

Atribución de los +14 decididos/corrida:

- F1 rescate code: 20 paquetes code (1 por claim) en ambas corridas,
  exactamente los elegibles (A1/A2/A4/A5/A6, C2/C3/C5, E1–E6, F1–F6).
  Determinista: mismo set r1 y r2.
- F7 prompt refutatorio: C1 (iso3 en desacuerdo ⇒ sin code), C4/C6 (sin
  año ⇒ sin code) contradichos vía citas model en AMBAS corridas
  (pre-fix: 1/12 c en 2 corridas).
- F6 guarda: D5-run1 y D6-ambas muestran `arithmetic_needs_computation`
  en vivo (el falso-C D5×2 de F2-1, eliminado por construcción);
  D1–D4 abstienen por `unit_mismatch` (ratio/percent vs filas USD).
- F2/F3/F4: multi-query + primary-first + tablas operaron en todas las
  corridas (0 excepciones de grounding); su efecto marginal en DEV es
  robustez (b sigue 0/6 por 403 + deadline: los secundarios no
  entraron a tiempo).
- F5 PDF: sin efecto medible en DEV (403 previo al parseo), capacidad
  general probada offline.

## 5. Coste, latencia, unresolved

- F2-2 live total ≈ **$2.79** (tope $8): base $1.1157 + probe ~$0.03 +
  smoke post-fix $0.1033 (5 claims) + finales $0.7637 + $0.7777.
- $/claim post-fix: 0.0212/0.0216 (pre-fix ~0.031: MENOS coste porque
  la mitad de degradados queman menos presupuesto).
- Latencia atlas post-fix p50/p95: ~84–85s/~90s (b+categoría-b marcan
  el p95 por deadline; sin cambios estructurales).
- Unresolved (14/corrida, honestos): A3 (física, deadline); B1–B6
  (primarios 403, secundarios fuera de deadline); D1–D6 (D5/D6 por
  guarda aritmética, D1–D4 por unit_mismatch — ningún cómputo
  multi-fuente implementado, por diseño); E6 (euros por construcción).
- Limitaciones declaradas: PDFs comprimidos se leen pero no verifican
  verbatim (snapshot raw); claims sin año solo vía modelo; sin claims
  metros/km/coronas (fuera de alcance desde prereg).

## 6. Archivos tocados (diff mínimo)

- `live/evidence/package.py`: +`is_multi_source_arithmetic` (+export).
- `live/verifier/check.py`: guarda `arithmetic_needs_computation`.
- `live/swarm/fetch.py`: +`is_pdf_body`, +`extract_pdf_text`, wiring
  `fetch.text` para PDFs.
- `live/swarm/coordinator.py`: +`build_search_queries`,
  +`rank_hits_primary_first`, +`tables_from_html` e integración en
  `doc_excerpts`, +`wb_row_quote_for`, +`wb_fallback_eligible`,
  +`_maybe_add_wb_fallback`, `build_package(extractor=)`, grounding
  multi-query + nudge de seed, auto-fetch primary-first, `__all__`.
- `live/swarm/roles.py`: instrucción refutatoria RECUPERADOR.
- `live/tests/test_f2_recall.py`: NUEVO (43 tests).
- `live/tests/test_m2b_swarm.py`, `live/tests/test_m2c_e2e.py`: ajustes
  §3.

## Retención (medida por integrador, 2026-11-01, 1× por brazo, sin abrir claims para ajustar)
Atlas 12/18 (0.667) 12/12 [0.757,1.000], 0 falsos-C/S, p50/p95 82.8/90.1s, $0.0189/claim.
Baseline 18/18 [0.824,1.000], p50/p95 9.7/33.8s, $0.031/claim.
Dev 0.611 → retención 0.667: generaliza. Reporte: `data/runs/f2retention-report.json`.

## Retención final con código F2-7 (integrador, 2026-11-01, atlas 1×)
12/18 (0.667) 12/12 [0.757,1.000], 0 falsos-C/S, p50/p95 3.4/82.0s, $0.0121/claim ($0.22 total).
Reporte: `data/runs/f2ret2-report.json`. Confirma generalización post-sprint.
