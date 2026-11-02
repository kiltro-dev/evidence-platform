# F2_VIDEO — F2-6 rebuild del video Atlas Live

Tarea F2-6, checkout compartido. Rebuild completo del demo sobre el
keynote ES nativo (F2-4) con las métricas F2 (recall/latencia/baseline/
voz) y arco baseline-honesto. Sin git ops. `retention.json` jamás
abierta: retención SOLO vía addendum de `live/F2_RECALL.md`.

## Entregable

- `data/demo/atlas-live-demo.mp4` (sobrescrito): **2:29.72 (149.7 s),
  1920×1080, H.264 + AAC, 6 786 979 bytes**. Cap ≤2:50: margen 20.3 s.
- `live/demo/narration_es.txt` (11 bloques, voz carlfm),
  `live/demo/narration_en.srt` (22 cues, generado por el build).
- `live/demo/build_video.py` (extendido), `live/demo/qa_video.py`
  (nuevo, 34 aserciones), `live/demo/script.md` + `live/demo/SUBMISSION.md`
  (números nuevos + arco baseline-honesto).

## Pipeline ejecutado

1. **Log live ES**: `live/run_live.py --video
   data/keynote_es/keynote_es_native.mp4 --detector heuristic
   --language-bias "Spanish" --keywords <71 ES> --log
   data/runs/run-es-native.jsonl --port 8871` → 48 eventos, 14 turnos,
   17 claims, wall 194.1 s, **$0.0095**. Keywords: las 71 de
   `measure_wer.extract_keywords` sobre `truth.json` ES.
2. **Grabación UI 1080p** (playwright, server replay `:8897` del log ES
   + estático Range `:8899` para el mp4 nativo, sin tocar `app.py`):
   arco falsa-atrapada (slide 5 + tarjeta F3 turno 6) → paneles →
   `verify_bundle` real → dicho-vs-mostrado → cierre F2.
3. **Narración** piper `es_ES-carlfm-x_low` (16 kHz → 22050 Hz,
   149.7 s total) + subs EN incrustados.
4. **ffmpeg**: MP4 1080p30, 2:29.72 ≤ 2:50 ✓.

## Números nuevos (todos ejecutados)

| cifra en video | fuente ejecutada |
|---|---|
| recall DEV 22/36 = 0.611, 22/22 [0.851, 1.000], 0 falsos, cats a5/b0/c6/d0/e5/f6, r1-r2 36/36 | `data/runs/f2recall-fix-report.json` (r1+r2) |
| retención 12/18 = 0.667, 12/12 [0.757, 1.000], 0 falsos, $0.0189/claim, p50/p95 82.8/90.1 s | addendum `live/F2_RECALL.md` (única fuente) |
| baseline DEV 70/72 = 0.972 [0.904, 0.992] (35+35), consistencia 36/36, falsos-C D3×2 | `data/runs/eval2-20261101-225539-report.json` + runs |
| baseline ret 18/18 [0.824, 1.000] | addendum `live/F2_RECALL.md` |
| fidelidad pooled 9/63 = 0.143 [0.077, 0.250] (5/31 + 4/32); motivos 34/13/7; 72 stated 0 browsed | fidelity JSONs r1/r2 |
| wall 372.4 s vs 779.5 s = −52%; p50 80.3, p95 115.7 (sube, honesto); $/claim 0.0227 | `data/runs/f2lat-atlas-report.json` + `f2recall-fix-report.json` (wall/2) |
| early-exit 9×med 4.0 s (2.7–5.7); full 20×82.2 s; budget 7×114.1 s (nearest-rank `percentile`) | `data/runs/f2lat-atlas-atlas-run1.json` (notas por claim) |
| WER 0.0296→0.03 (2/14/1/574), ASR-recall 41/41, 189.6 s = 3:10 | `data/keynote_es/wer_report.json` |
| bundle 31/31 ALL PASS (salida real verbatim) + tamper 1 byte → FAIL | `live/bundle/verify_bundle.py` + `data/runs/f2-tamper-demo/demo.json` |
| gap ~59 s: turnos ASR 0 (0.0–18.5) y 6 (63.5–81.1) + SRT nativo (slide2 12.696 s, F3 ≈71.6 s proporcional); rel_diff 0.9 exacto | `data/runs/run-es-native.jsonl` + `keynote_es_native.srt` + aritmética |
| M3: said 40.0 / shown 0.97, d = 0.976; F5 27.8→29.3 eje 27–30; F11 India>EE.UU. | `live/M3.md` (frames ES, mismos datos) |

Wilson recomputados con `live/auditor/wilson.py`: 70/72→[0.904,0.992],
9/63→[0.077,0.250], 22/22→[0.851,1.000], 12/12→[0.757,1.000],
18/18→[0.824,1.000]. Medianas de grupo con `live/server/app.percentile`
(nearest-rank; la mediana clásica del path completo daría 82.0, no 82.2).

## Baseline tal cual (comparación honesta)

- **Donde acierta igual**: veredictos DEV 70/72 = 0.972 y retención
  18/18; recall 1.000 vs 0.611/0.667; velocidad p50 ~5–10 s vs ~80 s.
- **Donde falla**: citas 14% fieles (9/63, motivos 34/13/7), 72/72 URLs
  stated 0 browsed, 2 falsos-contradicted (D3×2), **0 prueba offline**.
- **Qué agrega Atlas**: bundle re-verificable (31/31 + tamper FAIL),
  0 falsos-C en F2-2/F2-3, dicho-vs-mostrado (F6/F5/F11).

## QA (4 puntos, iterado hasta pasar)

- **(a) Transcripción mp4** (`/tmp/qa_transcribe_mp4.py`, file ASR bias
  ES, $0.0075): final mp4 → **WER 0.1086** (sub 17/ins 10/del 17,
  n_ref 405) < 0.25 y **26/26 frases-clave** (11 bloques) tras
  tolerancia documentada a artefactos del path file (duplicadas
  «solo solo», homófono «bite»→«byte», fusión «contramostrado»;
  audio verificado correcto). Duración 149.7 s ≤ 170 s ✓. PASS.
- **(b) Visión juez**: 16 frames inspeccionados (todos los segmentos):
  UI replay ES + video pineado, seek a slide 5 + tarjeta F3 con flash,
  pipeline, contradicción, terminal ALL PASS + tamper, F6/F5/F11 ES,
  recall, latencia, baseline, cierre; subs EN legibles, sin overflow.
  Dos iteraciones: frames EN rancios → sentinel `.frames.json`;
  seek mudo (http.server sin Range) → estático Range propio +
  seek tras metadata. PASS.
- **(c) Secretos**: `qa_video.N3` (8 patrones sobre
  narración/SRT/paneles/cues: sk-, Bearer, MODEL_API_KEY=, AIza, xox,
  PRIVATE KEY, tokens en URLs) + inspección visual de 16 frames (solo
  datos públicos de evidencia: URLs WB, prefijos sha, claim ids).
  PASS, 0 hallazgos.
- **(d) 34/34 aserciones** (`live/demo/qa_video.py`, $0, offline):
  R1–R8 recall/retención, B1–B5 baseline/fidelidad, L1–L5 latencia,
  V1–V3 voz, T1–T2 bundle/tamper, C1–C5 contradicción ES + run log,
  N1–N6 narración/SRT/mp4/secretos/M3/wedge/config. PASS.

## Costo live

`run-es-native` $0.0095 + transcripciones QA ×2 ≈ $0.0149 → **total
≈ $0.02** (tope $3). Builds/TTS/frames/QA(d): $0.

## Unresolved

- `truth.json` ES `t_aprox` (18.01/86.96) son del ES anterior, no del
  nativo: el gap ~59 s se deriva del log + SRT (ver tabla), no de
  `t_aprox`. Sin word-timings, F3 ≈71.6 s es proporcional a caracteres
  (±2 s); los turnos ASR (63.5–81.1 s) lo acotan.
- WB quote del panel F3 dice 2025/1.406B (paquete real M2d verbatim)
  mientras la línea Ref cita 2024/1,408,975,000 (truth): ambas
  individuales verdaderas, heredado de M9b.
- M3 vista leyó frames EN; frames ES muestran mismos datos (citado).
- Mediana `percentile` nearest-rank (82.2) vs clásica (82.0):
  documentado arriba; panel usa la del repo.
- Puertos de build 8897/8899 y 8871 (run ES): libres al correr;
  coordinar en checkout compartido.
