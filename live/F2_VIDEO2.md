# F2_VIDEO2 — F2-6b delta del video Atlas Live (latencia F2-7)

Tarea F2-6b, rama main, checkout compartido. Re-render QUIRÚRGICO del
`atlas-live-demo.mp4` de F2-6 (`live/F2_VIDEO.md`, 2:29.7): solo paneles
de métricas + narración del bloque 8 + aserciones QA. Sin git ops.
`retention.json` jamás abierta: retención F2-7 SOLO vía cifras
ejecutadas del brief F2-6b (este doc es su fuente citable).

## Entregable

- `data/demo/atlas-live-demo.mp4` (sobrescrito): **2:32.71 (152.7 s),
  1920×1080, H.264 + AAC, 6 894 110 bytes**. Cap ≤2:50: margen 17.3 s.
- `live/demo/narration_es.txt` (bloque 8 re-escrito, voz carlfm),
  `live/demo/narration_en.srt` (22 cues, regenerado por el build).
- `live/demo/build_video.py` (paneles recall/latencia/baseline/cierre +
  `EN_CUES[8]` + dicts F2R/F2L), `live/demo/qa_video.py` (R8 + L1–L5 +
  N6 re-escritos), `live/demo/script.md` + `live/demo/SUBMISSION.md`.

## Números finales (ejecutados, verbatim del brief F2-6b)

- Atlas DEV 22/36 recall 0.611, 22/22 [0.851,1.000]; retención 12/18
  0.667, 12/12 [0.757,1.000]; 0 falsos-C/S siempre.
- p50 4.3–4.5 s DEV (r1 4.28, r2 4.52) / 3.4 s retención; antes ~80 s.
- p95 73–79 s DEV (r1 79.1, r2 73.3): **MISS vs meta 60** (declarado en
  panel + narración ES/EN + cierre).
- $0.008/claim DEV (r1 0.0080, r2 0.0086); wall ~108 s/run (−71%).
- Baseline retención 18/18 + fidelidad de citas 0.143 (sin cambios).
- WER 0.03, ASR-recall 41/41 (sin cambios).

Fuentes DEV: `data/runs/f2lat2-final-report.json` + runs r1/r2 (grupos:
apoyo 9 med 4.1/3.0 rango 2.4–4.9; refuta 11 med 3.9/3.5 rango 2.6–5.3;
lento 9/8 med 25.3/26.1 rango 21.5–29.0; deadline-70 7 med 75.9/72.9
rango 70.1–81.1; D3-run2 budget 37.3 s mismo veredicto) + `F2_LAT2.md`
§4. Veredictos idénticos a F2-3 claim por claim (ambas corridas).

## Pipeline parcial ejecutado (`/tmp/rebuild_partial_v2.py`)

1. Re-TTS piper carlfm SOLO bloque 8 (15.0 s; resto `narr_*.wav`
   reutilizados, flags `--skip-tts` equivalentes).
2. `step_bundle` (re-export + ALL PASS 31/31) + `step_panels` nuevo.
3. Re-record playwright SOLO segs 7–10 (paneles nuevos); clips 0–6
   reutilizados (`--skip-record` parcial). Puertos 8897/8899 propios.
4. `step_audio_srt` + `step_compose` → mp4 sobreescrito.
5. Fixup: rango EE-apoyo 2.4–5.3→2.4–4.9 (medido; QA L3 lo cazó),
   re-record seg 8 + recompose (`/tmp/fixup_seg8.py`).

## QA (4 puntos)

- **(a)** Duración 152.7 s ≤ 170 s ✓ + 1 transcripción spot
  (`/tmp/qa_transcribe_mp4_v2.py`, claves bloque 8 actualizadas):
  **WER 0.1186** (21/9/19, n_ref 413) < 0.25 y **28/28 frases-clave**.
  PASS. (F2-6: WER 0.1086, 26/26.)
- **(b)** Visión juez: 4 frames nuevos (`/tmp/qab_v2_seg{7,8,9,10}.png`):
  recall (p50 3.4 s retención), latencia F2-7 (tablas + MISS),
  baseline (Atlas F2-7 ~4 s), cierre (4.3 s/~108 s + MISS); subs EN
  legibles, sin overflow. PASS.
- **(c)** Secretos: `qa_video.N3` + grep (8 patrones) sobre
  build/qa/narración/SRT/panels + inspección visual 4 frames. PASS,
  0 hallazgos.
- **(d)** **34/34 aserciones** `live/demo/qa_video.py` ($0, offline).
  PASS. R8/L1–L5 verifican F2-7 vs JSONs DEV + brief (retención);
  el QA jamás abre `retention.json` ni runs `f2ret*`.

## Costo live

1 transcripción spot ≈ **$0.008** (152.8 s audio; tope $1). Resto $0.

## Unresolved

- Retención F2-7 (p50 3.4 s) viene del brief ejecutado, no de un
  addendum en repo: si el integrador publica el reporte, re-citar.
- Retención conserva recall 12/18 + addendum F2-2 como fuente de
  recall; costo nuevo de retención no se muestra (no estaba en brief).
- Heredados de F2_VIDEO.md §Unresolved (t_aprox, WB quote 2025 vs
  Ref 2024, frames M3 EN→ES, nearest-rank): sin cambios.
