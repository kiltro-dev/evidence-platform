# S5 Ruido — grid ASR completo 12×4 + propagación E2E (Atlas Live)

Rama main, checkout compartido. Módulo: `live/synthqa/noise.py` (grid +
E2E) + sección ruido en `live/synthqa/report.py`. Tests offline:
`live/tests/test_s5_noise.py` (0 llamadas live). Artefactos (todo bajo
`data/`, ignorado): `data/synth/qa/noise_grid.json` (48 celdas + filas
E2E), `data/synth/qa/noise_transcripts/` (48 transcripts),
`data/synth/manifest.json` (regenerado vía report.py, sección `noise`),
`data/synth/bench/report.json` (sección `noise`).

## 0. Método (paridad con builders)

Niveles exactos de los builders (sin re-generar audio): EN
clean+{20,10,0} dB (`data/synth/en/wavs/`), ES clean+{20,10,5} dB
(`data/synth/es/`). Cada celda se transcribe vía POST asr con el MISMO
bias de idioma + keywords que el pase FINAL del builder de esa pieza
(EN: attempt de su `wer.json` — 0 = todas, 1 = solo-números; ES:
siempre todas), timeout 300 s, reintentos ×3 con backoff ante 408
transitorios. Scoring con los helpers propios de cada builder
(`build_corpus_en.wer_details`+`claim_recall`,
`measure_wer.wer_details`+`asr_recall`, ref = texto plano del SRT).

Consecuencia: las 48 celdas S5 SÍ son comparables con los clean de
builders (misma config ASR + mismo scorer; resta solo varianza entre
corridas del path file-fallback). El spot S4 (k1_econ@10dB, SIN
keywords) queda marcado NO comparable — ver §4.

## 1. Grid WER/recall por nivel e idioma (4×2)

WER micro-promediado por slice (Σ errores / Σ palabras ref; `wer_mean`
en el JSON como referencia). Recall exacto hits/claims con Wilson 95 %
sobre claims (n = 240 por celda lang×nivel: 6 piezas × 40 claims).

| Nivel | EN WER | EN recall | EN Wilson 95 % | ES WER | ES recall | ES Wilson 95 % |
|---|---|---|---|---|---|---|
| clean | 0,0532 | 0,929 (223/240) | [0,890, 0,955] | 0,0389 | 0,983 (236/240) | [0,958, 0,994] |
| SNR20 | 0,0552 | 0,954 (229/240) | [0,920, 0,974] | 0,0392 | 0,958 (230/240) | [0,925, 0,977] |
| SNR10 | 0,0549 | 0,933 (224/240) | [0,895, 0,959] | 0,0415 | 0,967 (232/240) | [0,936, 0,983] |
| SNR0¹ | 0,0532 | 0,917 (220/240) | [0,875, 0,945] | — | — | — |
| SNR5¹ | — | — | — | 0,0461 | 0,946 (227/240) | [0,910, 0,968] |

¹ EN no tiene SNR5; ES no tiene SNR0 (layouts de builders).

Lectura: la curva es PLANA — el ruido apenas degrada (EN 0,053→0,053
a 0 dB; ES 0,039→0,046 a 5 dB; recalls dentro de bandas Wilson
solapadas). O el ASR con keywords es genuinamente robusto a este
ruido, o la escalera de ruido del corpus es demasiado fácil; en ambos
casos el corpus NO estresa el ASR por debajo de 0/5 dB. La
re-transcripción clean S5 replica los clean de builders dentro de
varianza entre corridas (misma config, ±0,03 WER por pieza — e1/s1 se
mueven en direcciones opuestas, firma del file-fallback, no sesgo).

## 2. Propagación E2E: detección-vs-SNR (1 EN + 1 ES × 4 niveles)

Sobre CADA transcript ruidoso guardado corre el Detector heurístico
(`live/detector/claims.py`, `llm=None`, offline, $0, instancia fresca
por celda para no arrastrar la ventana dedup): nº claims detectadas vs
40 truth. Es conteo-vs-conteo, NO recall de matching (la heurística es
un piso grueso: conserva segmentos con dígitos/palabras indicadoras;
sirve como señal de degradación, no como métrica de calidad).

| Pieza | clean | SNR20 | SNR10 | SNR duro (0 EN / 5 ES) |
|---|---|---|---|---|
| k1_econ (EN) | 16/40 (seg 18) | 6/40 (seg 8) | 7/40 (seg 8) | 4/40 (seg 5) |
| keynote_economia (ES) | 25/40 (seg 36) | 20/40 (seg 23) | 30/40 (seg 40) | 34/40 (seg 41) |

Hallazgo honesto: el conteo heurístico lo domina la SEGMENTACIÓN
(puntuación del ASR), no el contenido. En k1 la puntuación colapsa
con ruido (18→5 segmentos: frases fusionadas) y la detección cae
16→4 aunque el WER apenas se mueve (0,039→0,047). En keynote la
segmentación se mantiene/crece (incluso con duplicaciones del
file-fallback a SNR20: 3698 chars) y el conteo sube 25→34. La
heurística sin modelo es frágil como proxy E2E: mide si el ASR
puntúa, no si los claims sobreviven.

## 3. Coste (live, medido)

Grid **$0,5246** (techo $2): EN $0,2869 + ES $0,2377; 48 transcripciones
a $0,18/h sobre audio real (audioDurationMs). E2E $0 (heurística
offline). 1× 408 transitorio en la primera celda, recuperado con
reintento (backoff 20 s). Total acumulado del informe S4+S5: $4,86.

## 4. Spot S4: veredicto de comparabilidad

`report.noise_table` → `spot_comparison`, mecánico: k1_econ@10dB
**NO comparable** — mismo wav y mismo scorer, pero distinta config
ASR (spot sin keywords: WER 0,0408/recall 0,95; grid con 92 keywords:
WER 0,0533/recall 0,90). La diferencia cae dentro de varianza entre
corridas + efecto keywords; no se fusionan.

## 5. Unresolved

- Curva plana (§1): sin celdas bajo 0/5 dB no se sabe dónde rompe el
  ASR; re-generar audio quedaba prohibido en S5 (los wavs existen).
- E2E heurístico (§2): proxy dominado por puntuación; un E2E con
  modelo queda fuera de alcance S5 (coste + 1 llamada/pieza ya
  consumida en S4).
- s1_sci clean S5 (WER 0,064/recall 0,85) peor que su builder (0,033/
  0,90): varianza file-fallback, misma config; no investigado más.

## 6. Repro

```sh
.venv/bin/python -m pytest live/tests/test_s5_noise.py -q  # offline, $0
.venv/bin/python live/synthqa/noise.py --live              # grid 48, resume
.venv/bin/python live/synthqa/noise.py --e2e               # E2E offline, $0
.venv/bin/python live/synthqa/noise.py                     # resumen offline
.venv/bin/python live/synthqa/report.py                    # manifest + informe
```
