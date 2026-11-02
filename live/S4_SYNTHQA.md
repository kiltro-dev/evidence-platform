# S4 SynthQA — QA + benchmark del corpus Atlas Live (12 piezas)

Rama main, checkout compartido. Módulo: `live/synthqa/` (`review.py`,
`bench.py`, `report.py`). Tests offline:
`live/tests/test_s4_synthqa.py` (31 tests, 0 llamadas live).
Artefactos (todo bajo `data/`, ignorado): `data/synth/qa/review.json`,
`data/synth/qa/adjudication.json`, `data/synth/qa_frames/`,
`data/synth/bench/results.json`, `data/synth/bench/report.json`,
`data/synth/bench/spot_wer.json`, `data/synth/manifest.json`.

## 1. QA de las 12 piezas

Por pieza: 2 frames (t=25 %/75 %), gates de código (duración 150–300 s,
1920×1080, fps 25–30 ±0,05 por redondeo de timebase, audio sin clipping
en 2 etapas, SRT parseable con cues dentro de la duración, badge
"Synthetic demo" como flag de builder + muestreo de 1 frame con visión),
1 llamada de visión Muse sobre ambos frames (claridad, ritmo,
coherencia dicho-vs-mostrado contra muestra truth centrada en los
tiempos de frame) y scan de rutas/correos/secretos en guion+SRT+notas.

| Pieza | Dur | FPS | MaxVol | Cues | Badge¹ | Secrets | Claridad | Ritmo | Dicho-vs-mostrado | PASS |
|---|---|---|---|---|---|---|---|---|---|---|
| k1_econ | 267,1 s | 30,0 | −0,0 dB² | 10 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| k2_clim | 243,0 s | 30,0 | −0,0 dB² | 10 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| p1_panel | 254,3 s | 30,0 | −0,0 dB² | 10 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| s1_sci | 252,4 s | 30,0 | −0,0 dB² | 10 | flag+visión | 0 | ok | ok | unclear | PASS |
| s2_sci | 246,8 s | 30,0 | −0,0 dB² | 10 | flag+visión | 0 | ok | ok | coherent | PASS |
| e1_earn | 219,0 s | 30,0 | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| keynote_economia | 196,2 s | 25,0⁴ | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | coherent | PASS |
| keynote_ciencia | 185,0 s | 25,0⁴ | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | unclear | PASS |
| panel_datos | 258,9 s | 25,0 | 0,0 dB² | 40 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| clase_poblacion | 182,9 s | 25,0⁴ | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | coherent | PASS |
| clase_economia | 189,5 s | 25,0⁴ | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | mismatch→trampa³ | PASS* |
| earnings_luminara | 179,7 s | 25,0 | 0,0 dB² | 10 | flag+visión | 0 | ok | ok | coherent | PASS |

¹ Badge: flag de builder verificado (`BADGE_ENABLED`/`fig.text`) + badge
visible en frame en 12/12 piezas por visión. ² Max 0 dB = audio
normalizado a pico; el análisis flat-top (PCM 16 kHz) no encontró
clipping en ninguna pieza (rachas máx 1–10 muestras; umbral 64, ver §5).
³ Los 6 "mismatch" de visión son trampas plantadas expected=false que
disparan (adjudicación offline contra labels truth, §2), no defectos.
⁴ Medido 24,99 por redondeo de contenedor; nominal 25.

**Producción: 12/12.** Claridad ok 12/12, ritmo ok 12/12 (frames de
slides distintos), badge 12/12, secretos 0/12. Sin re-render en ningún
caso (píxeles solo por muestreo).

## 2. Adjudicación de los 6 mismatch (§1, ³)

La llamada de visión corrió SIN labels (1 llamada/pieza respetada, sin
re-corridas); la muestra incluía trampas expected=false sin marcar y el
modelo las señaló como contradicciones. Adjudicación offline
(`review.adjudicate_mismatch` + verificación de frames/refs,
`data/synth/qa/adjudication.json`):

- k1_econ: CONFIRMADO — 40 % dicho (K1-30 [false]) vs 0,96 % mostrado
  ([true]; el truth trae hasta la corrección explícita).
- p1_panel: CONFIRMADO — "shrank 2.5 %" (P1-12 [false]) vs 7,4 % y
  9,2 % rural (P1-32 [false]) vs 92,2 %.
- e1_earn: CONFIRMADO — par 38,5/385 (E1-12 [false]) + feb-30 vs 28-oct
  (E1-32 [false]); slides coherentes con claims coherent-internal.
- panel_datos: CONFIRMADO — FMI +3,1 % mostrado (P1B1 [true]) vs +3,4 %
  dicho (P1B6 [false], valor de 2027).
- clase_economia: CONFIRMADO, frame verificado por revisor — slide con
  el valor verdadero −0,2 % (WB −0,242 %) vs +5,2 % dicho (C2D6
  [false]).
- k2_clim: TRAMPA-PLAUSIBLE — muestra 3/8 false (10x K2-12, 53,4 %
  urbano, 35,4B absurdo); ningún claim true contradicho en acta.

En ningún caso la visión contradijo un claim expected=true o
coherent-internal. Mejora aplicada al prompt para futuras rondas (sin
coste, sin re-correr): la muestra truth ahora lleva labels `[expected]`
y el sistema distingue trampas [false] de defectos genuinos.

## 3. Benchmark mundo (run_verify, 1 corrida/pieza, semáforo 8)

Regla de scoring (declarada): al mundo solo entran claims
expected=true/false CON ref verificable-mundo. Excluidos del scoring y
contados aparte: `coherent-internal`, `unverifiable` y los
expected=false DENTRO de piezas earnings (trampas contra el informe
INVENTADO, no contra el mundo; van al scoring E1 separado, §4).
Consecuencia: 10 piezas con corrida run_verify; las 2 earnings solo con
scoring interno por código. Total mundo: 381 claims
(EN 181: 35+36+38+36+36; ES 200: 5×40). Excluidos: 99
(69 coherent-internal + 23 unverifiable + 7 earnings-false).

## 3.1 Global (n=381)

Decididos 117/381 (abstención 0,693), correctos 114/117:
**exactitud 0,974, Wilson 95 % [0,927, 0,991]**; recall global 0,299
(true→supported 0,321, false→contradicted 0,268); 0 disputed.
Falsos: 3 contradicted (C1D3, C1D5, C1D7 — §3.4), 0 supported.
Lectura: el pipeline casi no se equivoca cuando decide, pero decide
poco (31 %): precisión alta, cobertura baja.

## 3.2 Por categoría (a–f)

| Cat | n | Decididos | Correctos | Exactitud | Wilson 95 % | Abstención | Recall |
|---|---|---|---|---|---|---|---|
| a | 100 | 47 | 47 | 1,000 | [0,924, 1,000] | 0,530 | 0,470 |
| b | 42 | 0 | 0 | — | [0,000, 1,000] | 1,000 | 0,000 |
| c | 48 | 13 | 13 | 1,000 | [0,772, 1,000] | 0,729 | 0,271 |
| d | 60 | 4 | 1 | 0,250 | [0,046, 0,699] | 0,933 | 0,017 |
| e | 62 | 28 | 28 | 1,000 | [0,879, 1,000] | 0,548 | 0,452 |
| f | 69 | 25 | 25 | 1,000 | [0,867, 1,000] | 0,638 | 0,362 |

b (claims con fecha 2026: IMF oct-2026, BLS sep-2026) abstiene 42/42:
sin grounding para vintage reciente. d (derivados) solo decide 4/60 y
concentra los 3 errores (§3.4).

## 3.3 Por idioma, nº oradores y pieza

Idioma — EN: n=181, dec=25, exactitud 1,000 [0,867, 1,000],
abstención 0,862, recall 0,138. ES: n=200, dec=92, exactitud 0,967
[0,909, 0,989], abstención 0,540, recall 0,445. (EN carga con toda la
física y clima: s1+k2 = 0/72 decididos.)

Oradores — 1 voz: n=303, dec=88, exactitud 0,966 [0,905, 0,988],
abstención 0,710. 2 voces (paneles): n=78, dec=29, exactitud 1,000
[0,883, 1,000], abstención 0,628. Sin efecto orador apreciable.

Pieza — k1 12/35, k2 0/36, p1 10/38, s1 0/36, s2 3/36, keynote_economia
19/40, keynote_ciencia 9/40, panel_datos 19/40, clase_poblacion 25+3err/40,
clase_economia 17/40 (detalle con Wilson por pieza en `report.json`).

## 3.4 Los 3 errores (todos C1, categoría d, `numeric_mismatch`)

C1D3 (China −1,7 M), C1D5 (India−China 42,0 M), C1D7 (México−Colombia
78,0 M): claims de DIFERENCIA A−B con expected=true y aritmética del
truth correcta (41,96 M vs 42,0; 77,97 M vs 78,0). El swarm trae los
extremos en paquetes y el verificador emite `contradicted` por
`numeric_mismatch` — consistente con comparar la diferencia dicha
contra valores extremo en vez de derivarla (mecanismo inferido del
acta, no trazado al código; hallazgo pipeline, fuera del alcance S4).

## 4. Earnings E1: coherencia interna (código vs truth, no vs mundo)

Aritmética re-computada por código (`bench.score_earnings`):
re-evaluación AST restringida de cada cálculo del truth (decimales ES
con coma, miles EN, cadenas multi-paso con `;`, anclas de unidad
`0.72B = 720M`, magnitudes con verbo de caída), comparación con lo DICHO
en el claim (resultado, operandos, o magnitud+verbo) y verificación de
trampas (negación explícita "not X", span aritmético, trampa de
unidades, fecha imposible, entidad ficticia). Declarativos sin
aritmética: consistencia de label (el truth debe declarar la
construcción ficticia).

- e1_earn (EN): 36/36 scoring (29 aritmética/declarativos + 7 trampas),
  coherencia 1,0. 4 unverifiable excluidos (saludos/meta).
- earnings_luminara (ES): 40/40 scoring (18 aritmética + 22
  declarativos), coherencia 1,0.

Toda la aritmética de ambos truths re-computa exacto (±0,1 pp en
porcentajes redondeados a 1 decimal, ±0,5 % relativo en sumas).

## 5. Audio: método anti-clipping en 2 etapas

volumedetect solo no distingue normalización-a-pico (máx 0 dB, las 12
piezas, histograma_0db 678–1814 muestras de ~11 M) de clipping real.
Etapa 2: decode PCM s16 mono 16 kHz + rachas a fondo de escala.
Clipping ⟺ racha ≥64 muestras (~4 ms) o ≥10 rachas ≥16. Medido:
rachas máx 1–10, 0 rachas ≥16 en 12/12 → sin clipping en el corpus.

## 6. WER por nivel de ruido (agregación, sin re-transcribir)

Clean = medición de builders con keywords; celdas SNR = builders nunca
las transcribieron ("no medido") salvo 1 spot-check propio (bias de
idioma, SIN keywords — no directamente comparable).

| Pieza | Clean WER | Recall | SNR20 | SNR10 | SNR5 | SNR0 |
|---|---|---|---|---|---|---|
| k1_econ | 0,060 | 0,925 | no medido | **0,041**¹ | — | no medido |
| k2_clim | 0,058 | 0,925 | no medido | no medido | — | no medido |
| p1_panel | 0,062 | 0,900 | no medido | no medido | — | no medido |
| s1_sci | 0,033 | 0,900 | no medido | no medido | — | no medido |
| s2_sci | 0,036 | 1,000 | no medido | no medido | — | no medido |
| e1_earn | 0,034 | 0,950 | no medido | no medido | — | no medido |
| keynote_economia | 0,043 | 0,950 | no medido | no medido | no medido | — |
| keynote_ciencia | 0,051 | 0,950 | no medido | no medido | no medido | — |
| panel_datos | 0,054 | 0,975 | no medido | no medido | no medido | — |
| clase_poblacion | 0,038 | 1,000 | no medido | no medido | no medido | — |
| clase_economia | 0,040 | 0,925 | no medido | no medido | no medido | — |
| earnings_luminara | 0,039 | 0,950 | no medido | no medido | no medido | — |

¹ Spot-check S4 (1 fichero, 39 s, $0,013): k1_econ@10dB → WER 0,041,
recall 0,95, sin keywords. Curiosamente mejor que el clean del builder
(0,060 con keywords): varianza entre corridas del path file-fallback
(duplicaciones), no comparables 1:1; la celda prueba que 10 dB es
transcribible. EN no tiene SNR5; ES no tiene SNR0.

## 7. Coste (live, medido)

Total **$4,34** (techo $10): verify mundo $4,209 (381 claims, ~$0,011
por claim — por encima de los $0,008 estimados por las abstenciones
caras con loops largos), visión QA $0,114 (12 llamadas, ~$0,0095 por
pieza), spot SNR $0,013. Smokes previos (4 claims) ≈ $0,025 no incluidos
en el informe (corridas `smoke*.jsonl` aparte).

## 8. Unresolved

- Visión ronda 1 corrió sin labels `[expected]` (6 mismatch
  trap-inducidos, adjudicados §2). Prompt ya corregido para rondas
  futuras; no se re-corrió por respetar 1 llamada/pieza.
- Hallazgos pipeline (medidos, no bloquean S4; fuera de alcance):
  física/clima sin indicador WB abstienen 72/72 (k2+s1) pese a que
  web_search responde; categoría b (vintage 2026) 42/42; categoría d
  56/60 con 3 errores sistemáticos en diferencias A−B (§3.4).
- k2_clim: adjudicación trap-plausible (hit dígito débil por
  numeración de slides); ningún claim true afectado.
- Celdas SNR 20/5/0 sin medir (1 solo spot-check a 10 dB, por diseño).

## 9. Repro

```sh
.venv/bin/python -m pytest live/tests/test_s4_synthqa.py -q  # offline, $0
.venv/bin/python live/synthqa/review.py                      # gates, $0
.venv/bin/python live/synthqa/review.py --live               # +visión 12×~$0,01
.venv/bin/python live/synthqa/bench.py --skip-world          # E1 código, $0
.venv/bin/python live/synthqa/bench.py                       # mundo 381 claims, sem 8
.venv/bin/python live/synthqa/report.py                      # manifest + informe
.venv/bin/python live/synthqa/report.py --spot k1_econ@10    # 1 celda SNR
```
