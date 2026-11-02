# V5-EN — Cobertura EN Atlas Live (swarm-side, P0 cerrado)

Rama main, checkout compartido. Alcance: solo `live/swarm/*` (+ tests y
este informe); prohibido: git ops, secretos, abrir `retention.json`,
reglas por caso, tocar `live/verifier/check.py` y
`live/verifier/arithmetic.py`. `live/library/*`: inspeccionada, sin
cambios (justificación §7). `live/swarm/roles.py`: cambio propuesto en
iteración 1 y REVERTIDO por daño medido (vuelve a su estado previo;
§5).

## 1. Punto de partida

S4 (pre-V5-P0): EN n=181, 25 decididos, abstención 0,862, exactitud
1,000 [0,867, 1,000], recall 0,138. ES n=200, 92 decididos, abstención
0,540, recall 0,445. Replay $0 de los 181 claims EN con el verificador
actual (V5-P0 incluido, misma evidencia): 28 decididos (+K1-16, +K1-36,
+P1-15, los 3 correctos), abstención 0,845. Ese replay —no S4— es la
línea base honesta de este trabajo.

## 2. Taxonomía de las 156 abstenciones EN (medida, una por una)

Fuente: `data/synth/bench/results.json` + 5 run-logs `verify-*.jsonl`
(veredictos, reasons, telemetría `no_exit`, paquetes con URLs y quotes)
+ replays offline con snapshots persistentes (124/124 shas resuelven).

| # | Clase | n | Mecanismo (medido) | Swarm-side? |
|---|---|---|---|---|
| A | WB no-curado, fila correcta citada, quote sin unidades → `unit_mismatch` | 36 | Retrieval OK (el modelo trae FP.CPI/SL.UEM/AG.LND.FRST/SP.URB/SP.DYN/LE00/EN.GHG/EG… con valores correctos); el verificador rechaza el JSON sin unidades y `source_meta` es None fuera de los 2 curados. El nombre del indicador con `(%)`/`(years)` va ANTES del valor y `parse_quantities` solo mira DESPUÉS. Suma reason-exacta: 35 `unit_mismatch` + P1-36 (triple) | No (verifier/package) |
| B | Unidad del claim desconocida → `claim_unit_unknown` | 13 | El parser no conoce kilowatt/megawatt hours, meters, square kilometers, ni cifras sin palabra de unidad ("peak near 10.3 billion", "triple", "zero percent") | No (units.py + run_verify) |
| C | `pct_change` computable pero `source_year` colisionado | 5 | K1-08/K1-11/P1-06/P1-11/P1-12 traen los 2 extremos correctos pero AMBOS paquetes llevan `source_year` = año del plan (2024/2024); V5-P0 exige 2 años distintos ⊆ años del claim → compute None → `unit_mismatch` per-package | **SÍ (fix 1)** |
| D | Física/prosa con vocabulario figura+unidad, 0 paquetes | 10 | S1-02/03/04/05/06/07/09/33 + S2-30/32. El material existe en wiki-EN (medido: fetch + excerpt contienen figura+unidad) pero el modelo no cita (loops estériles, frase más allá del presupuesto). 8 prosa-decibles (m/s, celsius); S1-04 bloqueado por parser ("kilometers per second"), S2-32 por derivación física (fuera de V5-P0) | **Parcial (fix 2)** |
| E | Sin contexto extraíble (code ind+iso+año = None) | 22 | Anáfora/sin entidad ("Its population…", "that same year", "South Korea led our table at 97.9 percent"); cada claim entra solo, sin contexto discursivo → `ineligible_geo/year` → 0 paquetes | No (diseño) |
| F | Vintage 2026 (IMF/BLS, cat. b), 0 paquetes | 20 | DDG muerto (medido: 0 hits) + wiki sin vintage 2026 + PDFs comprimidos no citables verbatim. Falla igual en ES (cat. b 42/42 ambas lenguas, S4). (La cat. b tiene 22 claims; S2-03/04 sí citaron wiki y viven en B/A) | No |
| G | Clima/energía con indicador nicho en el ref, 0 paquetes | 18 | EN.GHG.*/EG.*/AG.*/SP.URB/IT.NET fuera del catálogo `match_indicator`; el modelo no llama `wb_lookup` útil y la web no da prosa con unidades ("megatonnes" además sin parser) | No (retrieval inútil sin verifier) |
| H | Resto 0-paquetes + c/capitales/entidades + d de 2 pasos | 29 | 28 sin grounding textual citado (Sydney/Canberra, Everest-frontera, rankings, años sueltos…) + K2-37 (2 paquetes sin número citable); incluye d de 2 pasos sum-then-share K2-07/P1-23/K2-29 (fuera de V5-P0 por diseño) | No |
| I | `d` gratis vía re-correr con V5-P0 | 3 | K1-36/P1-15/K1-16: la evidencia S4 ya decide con el código actual (replay §1) | (re-run) |

Suma de control (partición disjunta sobre las 156 abstenciones S4):
36+13+5+10+22+20+18+29+3 = 156 ✓. Reglas de partición: reasons del
veredicto (A/B/C+I/arith) + set 0-paquetes (98 = D10+E22+F20+G18+H28,
reglas: regex figura+unidad / code-plan vacío / ref WB nicho / resto)
+ K2-37 (`no_number_in_quote` con paquetes) en H.

Por pieza (S4): k1 12/35, k2 0/36, p1 10/38, s1 0/36, s2 3/36. Por
categoría EN: a 16/58, b 0/22, c 4/18, d 0/20, e 4/30, f 1/33.

## 3. EN vs ES: qué hace distinto el pipeline (medido, no supuesto)

- **Año explícito en el texto**: code-plan `year` 0,44 EN vs 0,86 ES.
  Los guiones EN abusan de "that same year"/"that year"/sin año; los
  ES repiten "en 2024". Sin año no hay paquete-code ni fast-path.
- **Temas**: `indicator` 0,30 EN vs 0,41 ES. ES es PIB/población
  (curados); EN carga clima (k2), física (s1/s2) y energía cuyos
  indicadores no están en el catálogo `match_indicator` en NINGÚN
  idioma. El gap de keywords es temático, no lingüístico.
- **Geo**: `iso3` 0,71 EN vs 0,74 ES (sin diferencia). Los demónimos
  ("American", "estadounidense") faltan en ambos; el modelo los
  resuelve igual de bien/mal en ambos (trazas: FP.CPI/USA/2024
  perfectos en K1-18).
- **Grounding**: `_wiki_search` usa en.wikipedia.org para AMBOS
  idiomas (sin diferencia); `_ddg_search` devuelve [] medido (web
  genérica muerta para ambos). No hay "grounding wiki-ES" en el
  código.
- **Queries**: `build_search_queries` es agnóstico al idioma; los
  `web_query` del estructurador son buenos en EN (trazas S1-02/S1-05).
- **Damerau/fuzzy**: no existe en el pipeline (búsqueda exhaustiva en
  `live/` y `src/`: sin amarau/levenshtein/difflib/fuzzy). Mención
  especulativa del encargo; reportada como ausente.
- **Consecuencia observable**: `no_exit_ineligible_indicator`
  141/156 EN vs 59/171 ES; ES tiene 70 `no_exit_numeric_undecided`
  (paquete-code presente); paquetes 0: 98/181 EN (54 %) vs 39/200 ES
  (20 %); `extracción billion/trillion`: verificada correcta
  (§7, tests).

Conclusión: el gap EN es ~15 % anáfora/estilo de guion y ~85 % pared
del verificador (unidades + curación WB), NO un fallo del
entity-resolver ni de las queries en EN. El resolutor-modelo trae las
filas correctas; el verificador no las puede usar.

## 4. Fixes generales (2 enviados, 1 revertido)

### Fix 1 — `source_year` de la fila citada (determinista)

`live/swarm/coordinator.py`: nuevo `row_year_for_quote(quote, url)`
(puro, never-raises) + resolución en `build_package`. Si el quote
trae EXACTAMENTE UNA fecha de fila WB (`"date":"YYYY"`) y la URL es
`api.worldbank.org`, el paquete se fecha con la fila; si no, año del
plan (prosa/CSV/multi-fila intactos). General: sin ids, sin idiomas,
sin indicadores concretos.

Efecto medido offline (replay con años simulados): K1-08/K1-11/P1-06/
P1-11 supported `computed_match`, P1-12 contradicted
`computed_sign_mismatch` — 5/5 correctos, 0 falsos. Efecto live §6.

### Fix 2 — Excerpts con ranking por claim (determinista)

`live/swarm/coordinator.py`: `excerpt_needles_for_claim(text)` (dígitos
+ variantes sin comas + palabras 4+, dedup, cap 32) y
`doc_excerpts(..., needles=None)` (los segmentos que mencionan la
claim leen primero, orden estable; segmentos intactos → pacto
verbatim idéntico; `None` = orden histórico byte a byte). Cableado
solo en `_Tools.fetch_url` (WB/extra conservan sus excerpts propios);
`live/skeptic/attack.py` intacto (default).

Motivación medida: S1-05 ("Water boils at 100 degrees…") — la frase
citable de la página Celsius caía fuera del presupuesto en orden
documento y el modelo abortaba estéril (traza instrumentada §5).

### Fix 3 (REVERTIDO) — Nudge de quotecraft en RECUPERADOR

Añadía 2 frases (citar figura CON su unidad; filas WB no-curadas →
ALSO citar prosa). Medición live: K1-18/K1-21 pasaron de fila citada
(control, prompt original) a 0 paquetes + budget-burn (3/3); 2
revisiones (quotecraft-solo, instead-of-JSON) 0/4 citas; 0/7
compañeros-prosa en total. El modelo divaga en vez de finalizar.
`roles.py` vuelve a su estado previo; tests pinchan la ausencia.

## 5. Iteraciones live (máx 3)

1. **Fixes 1+2+3 + sonda 14 claims** ($0,2496): 3/14 decididos
   (P1-12 contradicted, K1-36/S1-02 supported, 0 falsos). P1-12/K1-36
   confirman fix 1 + V5-P0; K1-08/18/21 queman presupuesto (pista del
   daño del fix 3). Traza S1-05: excerpt sin frase + rescate-preámbulo
   → abort estéril.
2. **Controles + variantes de prompt** (~$0,10): control sin nudge
   2/2 citas (K1-18/21); variantes 0/4. Decisión: revertir fix 3.
   Sonda post-revert 4 claims ($0,052): K1-08 supported (fix 1,
   2.ª confirmación live), K1-18/P1-33 vuelven a 1 paquete JSON,
   S1-05 sigue 0 (varianza modelo).
3. **Validación final**: bench EN 5 piezas + 1 dev atlas (§6).

## 6. Validación

### 6a. Bench EN (5 piezas, run_verify por truth, 1 corrida, sem 8)

Logs: `data/synth/bench/runs/v5en-{k1_econ,k2_clim,p1_panel,s1_sci,
s2_sci}-20261102-*.jsonl` (+ `.summary.json`). `results.json` de S4
intacto (checkout compartido; las métricas salen de los summaries).

| | S4 (pre-V5-P0) | Replay $0 (V5-P0, evid. S4) | V5-EN live |
|---|---|---|---|
| Decididos | 25/181 | 28/181 | **32/181** |
| Abstención | 0,862 | 0,845 | **0,823** |
| Recall | 0,138 | 0,155 | **0,177** |
| Exactitud [Wilson 95 %] | 1,000 [0,867, 1,000] | 1,000 [0,879, 1,000] | **1,000 [0,893, 1,000]** |
| Falsos-C / falsos-S | 0 / 0 | 0 / 0 | **0 / 0** |

Por pieza (decididos): k1 12→16 (+K1-08, +K1-11, +K1-16, +K1-36),
k2 0→0, p1 10→13 (+P1-06, +P1-11, +P1-12, +P1-15, −P1-14 por varianza
retrieval: 0 paquetes + early-abort esta corrida), s1 0→0, s2 3→3.
Por categoría (decididos/correctos): a 16/16, b 0/22, c 4/4, **d 7/7**
(S4: 0/20 — todo fix 1 + V5-P0), e 4/4, f 1/1.

Atribución: +3 del re-run con V5-P0 (K1-36/P1-15/K1-16, clase I),
+5 del fix 1 (K1-08/K1-11/P1-06/P1-11/P1-12, clase C — los 5 con
recibo `computed_*`), −1 varianza (P1-14), +0 neto del fix 2 en s1
(S1-02 decidió en sonda pero no en bench: varianza modelo; el fix es
harmless y queda por su valor expositivo medido offline).

**Meta abstención <0,70: NO alcanzada (0,823).** Causa medida: con
los fixes enviados, el resto del gap (clases A36/B13/F20/G18 ≈ 87 +
E22/H29 contexto y resto) es pared del verificador (unidades +
curación WB), vintage sin grounding y anáfora — fuera del alcance
swarm/library. 0 falsos nuevos ✓.

### 6b. Dev guard (1 corrida atlas)

`live/eval2/run.py --arms atlas --runs 1 --probe-claims 0
--out-prefix data/runs/v5en-dev` — wall 180 s, **$0,2899**.

Global: 25/36 decididos (recall **0,694** ≥ 0,55 ✓), 25/25 correctos,
exactitud 1,000 [0,867, 1,000]; falsos-C 0 ✓, falsos-S 0. Por
categoría (decididos/correctos): a 5/5, b 0/6, c 6/6, d 3/3, e 5/5,
f 6/6. (V5-P0: recall 0,722; el delta −1 decisión es varianza
retrieval, dentro del guard con margen.)

### 6c. Suite offline ($0)

Nueva `live/tests/test_v5_en.py`: 39 tests (año de fila 14, excerpts
14, prompt-revert 4, billion/trillion 7). Existente intacta:
`live/tests/` completa **743 passed + 4 skipped** (704 pre-V5-EN,
incl. los 6 de `test_e2e_live.py` —offline pese al nombre— + 39
nuevos).

## 7. Notas de alcance

- **`live/library/*`: sin cambios.** `EvidenceStore.search` no tiene
  ningún llamador en el pipeline (verificado por búsqueda: solo se
  usan persistencia `upsert/store/get`); ningún cambio de librería
  puede mover recall. Inspeccionada, sin edits que justificar.
- **Pins**: ningún pin de versión tocado (esta sección existe solo
  porque el encargo la exige: nada que justificar).
- **`live/swarm/roles.py`: revertido a su estado previo** (fix 3,
  §4–§5). Edits finales netos: solo `live/swarm/coordinator.py`
  (fix 1 + fix 2, documentados inline con marca V5-EN).
- **Extracción números EN verificada**: billion/trillion escalan
  1000× exacto (tests §6c). `numbers_from_quote` da `unit: None`
  para "people" pero el verificador re-extrae y nunca lo usa: sin
  efecto en veredictos (medido, no tocado).
- **Estructurador**: medido EXCELENTE en EN (trazas: indicadores,
  geos y años correctos incl. FP.CPI/USA/2024). El "entity-resolver
  falla en EN" hipotético del encargo queda refutado: la resolución
  de entidades no es el gap.

## 8. Unresolved (con causa medida y follow-up preciso)

1. **Clase A (36)**: filas WB no-curadas citadas pero sin unidades.
   Follow-up V5-P1 (verifier-side): extender `WB_INDICATOR_UNITS`
   con indicadores de unidad inequívoca (FP.CPI/SL.UEM/AG.LND.FRST/
   SP.URB/EG.ELC.ACCS/NY.GDP.MKTP.KD.ZG/SP.POP.GROW → percent;
   SP.DYN.LE00.IN → years) + revisión de la policy per-cápita/tasas.
   Decidiría ~30 EN sin tocar recall ES (mismo código).
2. **Clase B (13)**: unidades EN ausentes en `units.py` (kilowatt/
   megawatt hours, meters, square kilometers, tonnes, "degrees"
   pelado, ppm, days…). Follow-up: extensión del parser + tests
   (afecta claim y quote a la vez; riesgo bajo, mecánico).
3. **Clase F (20 + S2-03/04, cat. b)**: vintage 2026 sin grounding
   (DDG muerto, PDFs no citables verbatim). Común EN+ES desde F2-1.
4. **Clase E (22)**: anáfora/sin contexto ("Its population…", "that
   same year"). Requeriría contexto discursivo entre claims (cambio
   de contrato `run_claim`, no un fix puntual).
5. **Clase G (18) + H (29)**: indicadores nicho sin catálogo,
   capitales/entidades sin prosa citada, d de 2 pasos. Parcialmente
   cubiertos por los follow-ups 1–2.
6. **Varianza retrieval ±1-2 claims/corrida** (P1-14, S1-02):
   estructural del loop modelo (loops estériles, rescates-preámbulo,
   backend fluctuante); solo promediable con n>1 (fuera de techo).

## 9. Coste live (medido)

Sondas: s1 instrumentada $0,015 + iter1/14 $0,2496 + espías $0,106 +
iter2/4 $0,052 ≈ **$0,42**. Bench EN 5 piezas **$1,7371**
($0,0096/claim). Dev atlas **$0,2899**. **Total ≈ $2,45** (techo $6).

## 10. Repro

```sh
.venv/bin/python -m pytest live/tests/test_v5_en.py -q   # 39 tests, $0
.venv/bin/python -m pytest live/tests/ -q                # 743 passed, $0
.venv/bin/python /tmp/v5en_replay.py                     # replay 28/181, $0
.venv/bin/python live/run_verify.py --claims data/synth/bench/filtered/k1_econ.json --lang en --log data/synth/bench/runs/v5en-k1_econ-manual.jsonl --max-concurrency 8 --deadline 90  # 1 pieza live
.venv/bin/python live/eval2/run.py --arms atlas --runs 1 --probe-claims 0 --out-prefix data/runs/v5en-dev-check  # dev guard
```
