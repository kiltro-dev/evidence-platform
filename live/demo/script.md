# Atlas Live — guion de video demo (≤2:55)

ES neutro + marcas EN. Todas las cifras salen de `live/M*.md` o de
`data/runs/*.summary.json`. Lo que no existe aún se marca TODO-M8.

Formato por bloque: TIEMPO — SE VE / SE DICE. Total: 2:45 + colchón 10 s.

---

## 0:00–0:15 — Hook: el orador se contradice a sí mismo

SE VE: keynote sintético corriendo en la sala de redacción
(`live/ui/index.html`); subtítulo EN: *"China is home to 1.41 billion
people…"*. Tarjeta de claim en estado `escuchada`.

SE DICE: «Un orador dice dos cosas que no pueden ser verdad a la vez,
con 57 segundos de distancia. Atlas Live lo atrapa en vivo — *caught
live* — y te muestra la prueba, no solo el veredicto.»

Fuente: M7, par T2 vs F3, gap 56.9 s.

## 0:15–0:30 — Qué es Atlas Live (una frase + pipeline)

SE VE: diagrama simple en pantalla: video → ASR → claims → agentes →
código verificador → tarjeta ES/EN. Banner EN VIVO del bus.

SE DICE: «Atlas Live escucha y mira un video en directo, extrae cada
afirmación chequeable, la investiga con agentes Muse en la web abierta,
y el veredicto lo decide código determinista — *the model proposes,
code disposes*.»

Fuente: `live/README.md`, M1_E2E (bus + `/ws`), M7 (contrato).

## 0:30–0:45 — La falsa, parte 1: F3 en vivo

SE VE: minuto 1:10 del keynote. Subtítulo: *"China's population has
fallen to just 141 million people."* Nueva tarjeta F3:
`escuchada → investigando`. Teatro de agentes activo.

SE DICE: «Minuto uno diez. El orador dice que China cayó a 141 millones
de personas. Tres agentes investigadores salen a la web — *three
researchers hit the open web* — mientras el video sigue corriendo.»

Fuente: `truth.json` F3-en (t_aprox 70.23), M2_E2E (swarm × claim).

## 0:45–1:00 — La falsa, parte 2: contradicción verificada

SE VE: tarjeta F3 → `contradicted`. Zoom al paquete de evidencia: quote
del Banco Mundial, URL api.worldbank.org, sha256 del snapshot. Luego el
evento `speaker.contradiction`: T2 1.41e9 vs F3 1.41e8, `rel_diff=0.9`.

SE DICE: «Contradecido. El Banco Mundial dice 1.409 millones — diez
veces más. Y el orador ya había dicho 1.41 billones al minuto cero:
contradicción propia verificada por código, *rel diff* de 0.9.»

Fuente: M2D (F3 contradicted, 3 paquetes), M7 (t=13.3 vs 70.2).

## 1:00–1:15 — Prueba re-verificable en pantalla

SE VE: terminal. Comando `verify_bundle.py` sobre el ZIP exportado.
Salida: `ALL PASS`. Se abre el ZIP: `run.jsonl`, `snapshots/`,
`packages.json`, `manifest.json` con sha256.

SE DICE: «Y no tienes que creernos. Cada corrida exporta un paquete de
pruebas que cualquiera re-ejecuta sin red ni modelo: *all pass*.
Cita literal, hash del snapshot, veredicto reproducido.»

Fuente: M5 (bundle + `verify_bundle.py`, `ALL PASS (3/3)` en tests).

## 1:15–1:30 — Giro: el gráfico contradice al orador

SE VE: slide 7 "Percent Games". Gráfico de barras honesto: 0.97%.
Subtítulo del habla: *"grew by nearly 40 percent"*. Alerta
`visual.mismatch` en pantalla: said 40.0 / shown 0.97.

SE DICE: «Pero hay algo mejor. El orador dice cuarenta por ciento… y su
propio gráfico muestra 0.97. *The chart contradicts the speaker.* Lo
detecta visión sobre el frame, y lo decide código: diferencia de 0.976.»

Fuente: M3 (F6, said 40.0 / shown 0.97, d=0.976).

## 1:30–1:45 — Ejes tramposos: F5 y F11

SE VE: slide 6, eje Y 27–30, barras 27.8→29.3 que parecen duplicarse.
Flag `visual.misleading_axis`, cue "doubled". Corte rápido al slide 10:
India dibujada más alta que EE. UU., segundo flag, cue "larger than".

SE DICE: «Mismo truco en dos slides: un eje truncado que convierte un
cinco por ciento en una duplicación, y una escala rota que pone a India
por encima de Estados Unidos. Dos *flags* automáticos.»

Fuente: M3 (F5 + F11 `visual.misleading_axis`; T10 match d=0.0).

## 1:45–2:00 — El escéptico: atacamos nuestros propios veredictos

SE VE: tarjeta T7 `supported` (Japón, PIB 4.19T). Panel del escéptico:
contra-búsqueda lanzada, 1 documento, extract abstiene → `skeptic.note`.
Tarjeta publicada ES/EN con cita confinada.

SE DICE: «Cada veredicto afirmativo es atacado por un escéptico que
busca contra-evidencia real. Aquí no encontró nada — *honest
abstention* — y la tarjeta se publica en español e inglés.»

Fuente: M5 (T7 live: ataque real $0.0041, tarjeta `fallback=False`).

## 2:00–2:15 — Fiabilidad medida, no prometida

SE VE: panel del auditor: 12 canarios, accuracy 1.0, Wilson 95%
[0.65, 1.0], abstención 0.42. Fila destacada: falsos contradicted
sobre TRUE: 0.

SE DICE: «Doce canarios internos, disjuntos del video, por el pipeline
real: precisión perfecta sobre lo decidido, con intervalo de Wilson
honesto. Y cero falsos contradicted — *zero false contradicts*.»

Fuente: M6 (`audit-20261101-213747.json`: accuracy 1.0, Wilson
[0.6457, 1.0], 0 falsos-contradicted).

## 2:15–2:30 — Límites honestos

SE VE: matriz M2d en pantalla: 8 correctos de 31, 22 abstenciones, 1
F14 marcado. Métricas: p50 78 s, costo $0.96.

SE DICE: «Los límites, sin maquillar: ocho de treinta y un claims
decididos correctamente, una falsa afirmación aceptada por 0.14 puntos
de tolerancia, y 78 segundos de mediana por claim. Abstenerse es una
virtud cuando la evidencia no alcanza.»

Fuente: M2D + `verify-20261101-212343.jsonl.summary.json`
(p50 78.39 s, F14 rel 1.86% < 2%).

## 2:30–2:45 — Cierre: ablación + invitación

SE VE: pantalla final: logo Atlas Live, tabla de ablación (Atlas 8/9
vs baseline run1 30/31 vs run2 31/31), banner "el baseline gana",
wedge, enlace al repo y al paquete de pruebas.

SE DICE: «Modelo propone, código dispone, escéptico ataca, auditor mide.
La ablación honesta: el baseline gana, treinta y uno de treinta y uno.
Nuestro wedge: prueba re-verificable, cero falsos contradicted, y dicho
contra mostrado.»

Fuente: M8 (`ablation-20261101-214721-report.json`: Atlas 8/9=0.889
[0.565,0.980] vs run1 30/31=0.968 [0.838,0.994] vs run2 31/31=1.000
[0.890,1.000]; Atlas no lidera ninguna métrica; baseline 15/15
alucinaciones vs Atlas 1; wedge = bundle re-verificable +
0 falsos-contradicted + dicho-vs-mostrado).

---

## Notas de producción (no narrar)

- Duración objetivo: 2:45; colchón de 10 s para pausas. No pasar de 2:55.
- Capturas: UI en `?replay=run-m1e-heuristic` (M4), terminal con
  `verify_bundle.py` (M5), slides 6/7/10 del keynote (M3).
- M8 CERRADO (2026-11-01, ya no TODO): ablación corrida, 31 EN × 2
  (`ablation-20261101-214721-report.json`). El baseline gana en todo lo
  medido (run2 31/31=1.000 vs Atlas 8/9=0.889); bloque 2:30–2:45 ya
  reemplazado con números reales. M8 compara brazos completos, no
  etapas: no prometer aporte por etapa.
- TODO-M8 (2): screenshots desktop/móvil reales pendientes desde M4.
- TODO-M8 (3): si se re-corre M2d, F6 puede volver a decidir (varianza
  del swarm, M2D); no prometer veredictos idénticos entre corridas.
