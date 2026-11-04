# Atlas Live — guion de video demo (2:37 + colchón 13 s, cap 2:50)

ES neutro (voz nativa carlfm) + marcas EN. Todas las cifras salen de
reportes ejecutados (`data/runs/*.json`, `data/keynote_es/wer_report.json`)
o del addendum de `live/F2_RECALL.md` (retención, única fuente permitida).

Formato por bloque: TIEMPO — SE VE / SE DICE. Tiempos medidos del build
F2-6c (re-render parcial F2-9, TTS carlfm): total 2:37.0
(F2-6b: 2:32.7; F2-6: 2:29.7).

---

## 0:00–0:12 — Hook: el orador se contradice a sí mismo [replay rotulado]

SE VE: keynote ES nativo corriendo en la sala de redacción
(`live/ui/index.html`, replay `run-es-native` 4×); tarjeta de claim en
estado `escuchada`.

SE DICE: «Un orador dice dos cosas que no pueden ser verdad a la vez,
con cincuenta y nueve segundos de distancia. Atlas Live lo atrapa
mientras se emite — *caught as it airs* — y te muestra la prueba, no
solo el veredicto.» [Modo en pantalla: replay rotulado de corrida real.]

Fuente: turnos ASR 0 (0.0–18.5 s) y 6 (63.5–81.1 s) de
`run-es-native.jsonl` + SRT nativo (gap ~59 s); método M7 (rel_diff 0.9).

## 0:12–0:29 — Qué es Atlas Live + oídos ES nativos [diagrama + replay rotulado]

SE VE: diagrama simple en pantalla: video → ASR → claims → agentes →
código verificador → tarjeta ES/EN. Panel de voz nativa ES.

SE DICE: «Atlas Live escucha y mira un video mientras se emite,
extrae cada afirmación chequeable, la investiga con agentes Muse en
la web abierta, y el veredicto lo decide código determinista — *the
model proposes, code disposes*. Y entiende español nativo, con error
de palabra de cero coma cero tres.» [Modo en pantalla: diagrama +
replay rotulado.]

Fuente: `live/README.md`, M1_E2E (bus + `/ws`); `live/F2_VOICE.md` +
`wer_report.json` (WER 0.0296, recall 41/41).

## 0:29–0:41 — La falsa, parte 1: F3 al emitirse [replay rotulado]

SE VE: minuto 1:10 del keynote ES (slide 5 pineado). Turno 6 con
«…cayó a solo ciento cuarenta y uno millones de personas». Tarjeta F3
resaltada con *flash*.

SE DICE: «Minuto uno diez. El orador dice que China cayó a ciento
cuarenta y un millones de personas. Tres agentes investigadores salen
a la web — *three researchers hit the open web* — mientras el video
sigue corriendo.»

Fuente: `truth.json` F3-es + turno ASR 6 (63.5–81.1 s, slide 5).

## 0:41–0:57 — La falsa, parte 2: contradicción verificada [replay rotulado + informe]

SE VE: tarjeta F3 → `contradicted`. Zoom al paquete de evidencia: quote
del Banco Mundial, URL api.worldbank.org, sha256 del snapshot. Luego el
par `speaker.contradiction`: 1.41e9 vs 1.41e8, `rel_diff=0.9`.

SE DICE: «Contradecido. El Banco Mundial dice mil cuatrocientos nueve
millones — diez veces más. Al minuto cero el orador había dicho mil
cuatrocientos diez millones: contradicción propia verificada por
código, *rel diff* de cero coma nueve.»

Fuente: M2d summary (F3-en, misma falsedad plantada: contradicted,
3 paquetes) + turnos ASR 0/6 + método M7.

## 0:57–1:12 — Prueba re-verificable + tamper [terminal, bundle publicado]

SE VE: terminal. Comando `verify_bundle.py` sobre el ZIP exportado.
Salida: `ALL PASS (31/31)`. Tarjeta tamper: 1 byte → `FAIL
(hash mismatch)`.

SE DICE: «Y no tienes que creernos. Cada corrida exporta un paquete de
pruebas que cualquiera re-ejecuta sin red ni modelo: *all pass*.
Treinta y una de treinta y una verificadas, y si cambias un solo byte,
el verificador falla: *tamper detected*.»

Fuente: `atlas-live-bundle.zip` (salida real verbatim) +
`data/runs/f2-tamper-demo/demo.json` (pristine ok → 1 byte FAIL).

## 1:12–1:26 — Giro: el gráfico contradice al orador [replay rotulado (visión: chequeo offline M3)]

SE VE: slide 7 ES "Juegos de porcentajes" (frame real t=102.0 s).
Gráfico de barras honesto: 0.97%. Alerta `visual.mismatch`: said 40.0
/ shown 0.97.

SE DICE: «Pero hay algo mejor. El orador dice cuarenta por ciento… y su
propio gráfico muestra cero coma noventa y siete. *The chart
contradicts the speaker.* Lo detecta visión sobre el frame, y lo decide
código.»

Fuente: M3 (F6, said 40.0 / shown 0.97, d=0.976; frames ES, mismos datos).

## 1:26–1:37 — Ejes tramposos: F5 y F11 [replay rotulado (visión: chequeo offline M3)]

SE VE: slide 6 ES, eje Y 27–30, barras 27.8→29.3 que parecen duplicarse.
Flag `visual.misleading_axis`, cue "doubled". Corte al slide 10 ES:
India dibujada más alta que EE. UU., segundo flag, cue "larger than".

SE DICE: «Mismo truco en dos slides: un eje truncado que convierte un
cinco por ciento en una duplicación, y una escala rota que pone a India
por encima de Estados Unidos. Dos *flags* automáticos.»

Fuente: M3 (F5 + F11 `visual.misleading_axis`; frames ES, mismos datos).

## 1:37–1:51 — Recall F2 que generaliza [panel de cifras bench v2]

SE VE: panel DEV 26/36 = 0.722 (recall Wilson [0.560, 0.842]) +
retención 14/18 = 0.778 (recall Wilson [0.548, 0.910]); exactitud entre
decididos 26/26 [0.871, 1.000] + 14/14 [0.785, 1.000], 0 falsos,
p50 ~5 s / p95 ~40 s con código F2-9. Categorías a–f; consistencia
baseline r1–r2 36/36 (no Atlas).

SE DICE: «El benchmark justo, versión dos: treinta y seis claims de
desarrollo y dieciocho de retención sellada. Atlas decide el setenta y
dos por ciento en desarrollo y el setenta y ocho en retención, con
cero falsos veredictos en ambas.»

Fuente: `v5p0-dev-atlas-run1.json` (DEV 26/36) + `v6ret-atlas-run1.json`
(retención 14/18; sello intacto, corrida 3× solo-lectura — ver
`live/claims_registry.json` ret_recall).

## 1:51–2:11 — Latencia honesta (F2-9) [panel de cifras F2-9]

SE VE: tabla antes→después: p50 80.3 s → 4.9/5.0 s, p95 115.7 s →
40.7/38.9 s (CUMPLE vs meta 60, en verde), wall 372.4 s → ~78 s (−79%),
$/claim 0.0227 → 0.0073/0.0059. Grupos r1/r2: early-exit apoyo 9×,
refuta 11× (~4 s), lento ok 8/9× (~28 s), early-abort 7× (~38 s).

SE DICE: «Y ahora es mucho más rápido: la mediana cayó de ochenta
segundos a cinco, a entre seis y siete décimas de centavo por claim.
Y la cola ahora cumple la meta de sesenta segundos, en treinta y nueve
a cuarenta y un segundos: un aborto sin progreso recortó la cola vieja
de quinientos treinta a doscientos setenta y cuatro segundos.»

Fuente: `f2p95-final-report.json` (p50 4.9/5.0, p95 40.7/38.9,
wall 155.3/2, $0.0073/0.0059) + runs r1/r2 (grupos, nearest-rank) +
`live/F2_P95.md` §4.

## 2:11–2:26 — El baseline, tal cual [panel de cifras F2-1]

SE VE: dos tarjetas: donde acierta igual (DEV 70/72 = 0.972, retención
18/18, «Nos gana en recall. Punto.») y donde falla (fidelidad pooled
9/63 = 0.143, motivos 34/13/7, 72/72 stated 0 browsed, prueba offline
0).

SE DICE: «El baseline, tal cual: setenta de setenta y dos en desarrollo
y dieciocho de dieciocho en retención; nos gana en recall. Pero solo el
catorce por ciento de sus citas son fieles a la fuente, y no exporta
ninguna prueba re-ejecutable.»

Fuente: `eval2-20261101-225539-report.json` (70/72) + fidelity JSONs
r1/r2 (5/31, 4/32) + addendum F2_RECALL (ret 18/18).

## 2:26–2:37 — Cierre: números F2 + wedge [panel + UI replay]

SE VE: tabla Atlas F2 vs baseline (recall, exactitud, citas, falsos,
prueba offline, dicho-vs-mostrado, latencia F2-9: p50 4.9 s / ~78 s
con p95 39–41 s CUMPLE vs 60, ASR ES). Banner «el
baseline gana en recall» + banner wedge.

SE DICE: «Modelo propone, código dispone. Nuestro wedge: prueba
re-verificable, cero falsos en el benchmark preregistrado, y dicho
contra mostrado. Todo medido, todo reproducible: pruébalo.»

Fuente: reportes F2 (recall/latencia/retención/fidelity/WER citados
arriba).

---

## Notas de producción (no narrar)

<!-- METRICS:START -->
- Figures (V6 frozen): DEV 26/36 = 0.722 (recall Wilson [0.560,0.842]), retention 14/18 = 0.778 (recall Wilson [0.548,0.910]), decided 26/26 [0.871,1.000] + 14/14 [0.785,1.000] (decided-accuracy Wilsons), 0 false verdicts, baseline 18/18 retention with fidelity 9/63 = 0.143 (fidelity Wilson [0.077,0.250]; NOT comparable to Atlas fidelity, different mechanisms).
<!-- METRICS:END -->

- Duración objetivo: ≤2:50; build F2-6c: 2:37.0 (margen 13 s).
  Re-render parcial: re-TTS bloque 8 + re-record segs 7–10 (paneles
  recall/latencia/baseline/cierre); segs 0–6 reutilizados.
- Capturas: UI en `?replay=run-es-native` (log live ES, 48 eventos),
  terminal con `verify_bundle.py` (salida real), slides ES 6/7/10
  (frames t=87.5/102.0/157.5 s del mp4 nativo).
- Voz: piper `es_ES-carlfm-x_low` (dominio público, F2-4); SRT EN de
  22 cues generado por el build (`narration_en.srt`).
- QA: `live/demo/qa_video.py` (34 aserciones, §d) + transcripción del
  mp4 (§a) + juez de visión (§b) + scan de secretos (§c). Detalle:
  `live/F2_VIDEO.md`.
- M8/M9b (EN, baseline 31/31) quedan como precedente histórico; el arco
  actual es F2 baseline-honesto (el baseline gana recall 1.000 vs
  0.722/0.778; Atlas conserva prueba + 0 falsos-C en benchmark v2 +
  dicho-vs-mostrado). Corpus: 3 falsos-C diferencia CORREGIDOS (V5-1),
  1 abierto retrieval (P1-17).
