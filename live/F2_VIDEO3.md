# F2_VIDEO3 — F2-6c delta del video Atlas Live (p95 F2-9 CUMPLE)

Tarea F2-6c, rama main, checkout compartido. Re-render QUIRÚRGICO del
`atlas-live-demo.mp4` de F2-6b (`live/F2_VIDEO2.md`, 2:32.7): solo paneles
de métricas + narración del bloque 8 + aserciones QA. Sin git ops.
`retention.json` jamás abierta: retención F2-9 SOLO vía cifras
ejecutadas del brief F2-6c (este doc es su fuente citable).

Corrección central: el video F2-6b declaraba p95 MISS vs meta 60s.
Con F2-9 el p95 CUMPLE (40.7/38.9s, 0 claims >60s): paneles, narración
ES/EN y cierre corregidos a CUMPLE (el QA L2 falla si aparece MISS).

## Entregable

- `data/demo/atlas-live-demo.mp4` (sobrescrito): **2:37.04 (157.0 s),
  1920×1080, H.264 + AAC, 7 154 508 bytes**. Cap ≤2:50: margen 13.0 s.
- `live/demo/narration_es.txt` (bloque 8 re-escrito, voz carlfm),
  `live/demo/narration_en.srt` (22 cues, regenerado por el build).
- `live/demo/build_video.py` (paneles recall/latencia/baseline/cierre +
  `EN_CUES[8]` + dicts F2R/F2L + consts F2P95_*),
  `live/demo/qa_video.py` (R8 + L1–L5 + N6 re-escritos),
  `live/demo/script.md` + `live/demo/SUBMISSION.md`.

## Números finales (ejecutados, verbatim del brief F2-6c)

- Atlas DEV 22/36 recall 0.611, 22/22 [0.851,1.000] (pooled 44/44
  [0.920,1.000]); retención 12/18 0.667, 12/12 [0.757,1.000];
  0 falsos-C/S siempre.
- DEV p50 4.9/5.0 s, **p95 40.7/38.9 s CUMPLE ≤60** (0 claims >60s,
  antes 7); retención p50 4.8 s / p95 40.8 s.
- $0.0073/0.0059/claim DEV (−68%/−74% vs 0.0227); retención $0.0067;
  wall ~78 s/run (−79% vs 372.4 s).
- Palanca: aborto no-progress — cola A3+B1–B6 de 530 s a 274 s (−48%).

Fuentes DEV: `data/runs/f2p95-final-report.json` (wall 155.3/2) + runs
r1/r2 (grupos: apoyo 9 med 3.6/4.2 rango 2.9–5.6; refuta 11 med 3.8/3.8
rango 2.7–11.0; lento 8/9 med 28.0/28.4 rango 23.3–33.1; abort 7 med
38.2/38.1 rango 35.8–41.3; abortados exacto A3+B1–B6 ambas corridas;
D5-run1 budget 30.1 s mismo veredicto; lentos que deciden C1/C4/C6) +
`live/F2_P95.md` §4. Veredictos idénticos a F2-7 claim por claim
(ambas corridas).

## Pipeline parcial ejecutado (`/tmp/rebuild_partial_v3.py`)

1. Re-TTS piper carlfm SOLO bloque 8 (19.4 s; resto `narr_*.wav`
   reutilizados, `narr_00.wav` intacto desde F2-6).
2. `step_bundle` (re-export + ALL PASS 31/31) + `step_panels` nuevo.
3. Re-record playwright SOLO segs 7–10 (paneles nuevos); clips 0–6
   reutilizados. Puertos 8897/8899 propios, libres al correr.
4. `step_audio_srt` + `step_compose` → mp4 sobreescrito.
5. Sin fixups: primer compose pasó QA(d) 34/34 directo.

## QA (4 puntos)

- **(a)** Duración 157.0 s ≤ 170 s ✓ + 1 transcripción spot
  (`/tmp/qa_transcribe_mp4_v3.py`, 8 claves nuevas bloque 8):
  **WER 0.1226** (19/12/21, n_ref 424) < 0.25 y **30/31 frases-clave**.
  Único miss: «atrapa en vivo» (bloque 0, audio reutilizado byte-idéntico
  que pasó 28/28 en F2-6b) — varianza del path file ASR («Atlas
  Lighthouse en vivo»), misma clase de artefacto tolerada en F2-6
  (homófonos, duplicadas). Las 8/8 claves nuevas del bloque 8
  transcriben verbatim. PASS con tolerancia documentada.
- **(b)** Visión juez: 4 frames nuevos (`/tmp/qab_v3_seg{7,8,9,10}.png`):
  recall (retención F2-9 p50 4.8/p95 40.8/$0.0067), latencia F2-9
  (tablas + CUMPLE verde + cola 530→274s), baseline (Atlas F2-9 ~5s),
  cierre (4.9s/~78s + p95 39–41s CUMPLE); subs EN legibles, sin
  overflow, 0 residuos MISS. PASS.
- **(c)** Secretos: `qa_video.N3` + grep (8 patrones) sobre
  build/qa/narración/SRT/panels/ass + inspección visual 4 frames. PASS,
  0 hallazgos (único match `MODEL_API_KEY`: el patrón detector en
  `qa_video.py:502`, no un valor).
- **(d)** **34/34 aserciones** `live/demo/qa_video.py` ($0, offline).
  PASS. R8/L1–L5 verifican F2-9 vs JSONs DEV + brief (retención);
  L2 pinea CUMPLE (p95 ≤60, 0 >60s) y prohibe MISS;
  el QA jamás abre `retention.json` ni runs `f2ret*`.

## Costo live

1 transcripción spot ≈ **$0.008** (157.1 s audio; tope $1). Resto $0.

## Unresolved

- Retención F2-9 (p50 4.8 s / p95 40.8 s / $0.0067) viene del brief
  ejecutado, no de un addendum en repo: si el integrador publica el
  reporte, re-citar.
- Retención conserva recall 12/18 + addendum F2-2 como fuente de
  recall; pooled DEV 44/44 [0.920,1.000] no se muestra en video
  (panel DEV sin cambios estructurales, quirúrgico).
- Heredados de F2_VIDEO.md §Unresolved (t_aprox, WB quote 2025 vs
  Ref 2024, frames M3 EN→ES, nearest-rank): sin cambios.
