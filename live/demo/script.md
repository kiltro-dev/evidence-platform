# Atlas Live — guion de video demo (2:33 + colchón 17 s, cap 2:50)

ES neutro (voz nativa carlfm) + marcas EN. Todas las cifras salen de
reportes ejecutados (`data/runs/*.json`, `data/keynote_es/wer_report.json`)
o del addendum de `live/F2_RECALL.md` (retención, única fuente permitida).

Formato por bloque: TIEMPO — SE VE / SE DICE. Tiempos medidos del build
F2-6b (re-render parcial F2-7, TTS carlfm): total 2:32.7 (F2-6: 2:29.7).

---

## 0:00–0:12 — Hook: el orador se contradice a sí mismo

SE VE: keynote ES nativo corriendo en la sala de redacción
(`live/ui/index.html`, replay `run-es-native` 4×); tarjeta de claim en
estado `escuchada`.

SE DICE: «Un orador dice dos cosas que no pueden ser verdad a la vez,
con cincuenta y nueve segundos de distancia. Atlas Live lo atrapa en
vivo — *caught live* — y te muestra la prueba, no solo el veredicto.»

Fuente: turnos ASR 0 (0.0–18.5 s) y 6 (63.5–81.1 s) de
`run-es-native.jsonl` + SRT nativo (gap ~59 s); método M7 (rel_diff 0.9).

## 0:12–0:29 — Qué es Atlas Live + oídos ES nativos

SE VE: diagrama simple en pantalla: video → ASR → claims → agentes →
código verificador → tarjeta ES/EN. Panel de voz nativa ES.

SE DICE: «Atlas Live escucha y mira un video en directo, extrae cada
afirmación chequeable, la investiga con agentes Muse en la web abierta,
y el veredicto lo decide código determinista — *the model proposes,
code disposes*. Y entiende español nativo, con error de palabra de
cero coma cero tres.»

Fuente: `live/README.md`, M1_E2E (bus + `/ws`); `live/F2_VOICE.md` +
`wer_report.json` (WER 0.0296, recall 41/41).

## 0:29–0:41 — La falsa, parte 1: F3 en vivo

SE VE: minuto 1:10 del keynote ES (slide 5 pineado). Turno 6 con
«…cayó a solo ciento cuarenta y uno millones de personas». Tarjeta F3
resaltada con *flash*.

SE DICE: «Minuto uno diez. El orador dice que China cayó a ciento
cuarenta y un millones de personas. Tres agentes investigadores salen
a la web — *three researchers hit the open web* — mientras el video
sigue corriendo.»

Fuente: `truth.json` F3-es + turno ASR 6 (63.5–81.1 s, slide 5).

## 0:41–0:57 — La falsa, parte 2: contradicción verificada

SE VE: tarjeta F3 → `contradicted`. Zoom al paquete de evidencia: quote
del Banco Mundial, URL api.worldbank.org, sha256 del snapshot. Luego el
par `speaker.contradiction`: 1.41e9 vs 1.41e8, `rel_diff=0.9`.

SE DICE: «Contradecido. El Banco Mundial dice mil cuatrocientos nueve
millones — diez veces más. Al minuto cero el orador había dicho mil
cuatrocientos diez millones: contradicción propia verificada por
código, *rel diff* de cero coma nueve.»

Fuente: M2d summary (F3-en, misma falsedad plantada: contradicted,
3 paquetes) + turnos ASR 0/6 + método M7.

## 0:57–1:12 — Prueba re-verificable + tamper

SE VE: terminal. Comando `verify_bundle.py` sobre el ZIP exportado.
Salida: `ALL PASS (31/31)`. Tarjeta tamper: 1 byte → `FAIL
(hash mismatch)`.

SE DICE: «Y no tienes que creernos. Cada corrida exporta un paquete de
pruebas que cualquiera re-ejecuta sin red ni modelo: *all pass*.
Treinta y una de treinta y una verificadas, y si cambias un solo byte,
el verificador falla: *tamper detected*.»

Fuente: `atlas-live-bundle.zip` (salida real verbatim) +
`data/runs/f2-tamper-demo/demo.json` (pristine ok → 1 byte FAIL).

## 1:12–1:26 — Giro: el gráfico contradice al orador

SE VE: slide 7 ES "Juegos de porcentajes" (frame real t=102.0 s).
Gráfico de barras honesto: 0.97%. Alerta `visual.mismatch`: said 40.0
/ shown 0.97.

SE DICE: «Pero hay algo mejor. El orador dice cuarenta por ciento… y su
propio gráfico muestra cero coma noventa y siete. *The chart
contradicts the speaker.* Lo detecta visión sobre el frame, y lo decide
código.»

Fuente: M3 (F6, said 40.0 / shown 0.97, d=0.976; frames ES, mismos datos).

## 1:26–1:37 — Ejes tramposos: F5 y F11

SE VE: slide 6 ES, eje Y 27–30, barras 27.8→29.3 que parecen duplicarse.
Flag `visual.misleading_axis`, cue "doubled". Corte al slide 10 ES:
India dibujada más alta que EE. UU., segundo flag, cue "larger than".

SE DICE: «Mismo truco en dos slides: un eje truncado que convierte un
cinco por ciento en una duplicación, y una escala rota que pone a India
por encima de Estados Unidos. Dos *flags* automáticos.»

Fuente: M3 (F5 + F11 `visual.misleading_axis`; frames ES, mismos datos).

## 1:37–1:51 — Recall F2 que generaliza

SE VE: panel DEV 22/36 = 0.611 (Wilson [0.851, 1.000], 0 falsos) +
retención 12/18 = 0.667 (Wilson [0.757, 1.000], 0 falsos, p50 3.4 s
con código F2-7). Categorías a–f y consistencia r1–r2 36/36.

SE DICE: «El benchmark justo, versión dos: treinta y seis claims de
desarrollo y dieciocho de retención sellada. Atlas decide el sesenta y
uno por ciento en desarrollo y el sesenta y siete en retención, con
cero falsos contradicted en ambas.»

Fuente: `f2recall-fix-report.json` (DEV ambas 22/22) + addendum
F2_RECALL (retención; `retention.json` jamás abierta).

## 1:51–2:06 — Latencia honesta (F2-7)

SE VE: tabla antes→después: p50 80.3 s → 4.3/4.5 s, p95 115.7 s →
79.1/73.3 s (MISS vs meta 60, en ámbar), wall 372.4 s → ~108 s (−71%),
$/claim 0.0227 → 0.0080/0.0086. Grupos r1/r2: early-exit apoyo 9×,
refuta 11× (~4 s), lento ok 9/8× (~25 s), deadline-70 7× (~76 s).

SE DICE: «Y ahora es mucho más rápido: la mediana cayó de ochenta
segundos a cuatro, a ocho décimas de centavo por claim. Lo honesto: la
cola queda en setenta y tres a setenta y nueve segundos y no cumple la
meta de sesenta: la fija el deadline de setenta segundos.»

Fuente: `f2lat2-final-report.json` (p50 4.3/4.5, p95 79.1/73.3,
wall 216.1/2, $0.0080/0.0086) + runs r1/r2 (grupos, nearest-rank) +
`live/F2_LAT2.md` §4.

## 2:07–2:21 — El baseline, tal cual

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

## 2:22–2:33 — Cierre: números F2 + wedge

SE VE: tabla Atlas F2 vs baseline (recall, exactitud, citas, falsos,
prueba offline, dicho-vs-mostrado, latencia F2-7: p50 4.3 s / ~108 s
con p95 73–79 s MISS vs 60, ASR ES). Banner «el
baseline gana en recall» + banner wedge.

SE DICE: «Modelo propone, código dispone. Nuestro wedge: prueba
re-verificable, cero falsos contradicted, y dicho contra mostrado. Todo
medido, todo reproducible: pruébalo.»

Fuente: reportes F2 (recall/latencia/retención/fidelity/WER citados
arriba).

---

## Notas de producción (no narrar)

- Duración objetivo: ≤2:50; build F2-6b: 2:32.7 (margen 17 s).
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
  0.611/0.667; Atlas conserva prueba + 0 falsos-C + dicho-vs-mostrado).
