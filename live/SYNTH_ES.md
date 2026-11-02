# SYNTH_ES — corpus sintético ES Atlas Live (6 piezas)

Construido 2026-11-02 con `live/material/build_corpus_es.py`. Guion ES
propio (no traducción), 240 claims (40/pieza), categorías a–f de
`live/eval2/dev.json`. Detalle de voces/datos en
`live/material/PROVENANCE_ES.md`.

## Piezas (todas dentro de gates: WER<0.15, recall≥0.9, 2.5–5 min)

| Pieza | Fichero | Duración | WER | Recall | USD |
|---|---|---|---|---|---|
| K1 keynote economía | `keynote_economia` | 196 s (3:16) | 0.043 | 0.950 (38/40) | 0.0098 |
| K2 keynote ciencia/salud | `keynote_ciencia` | 185 s (3:05) | 0.051 | 0.950 (38/40) | 0.0092 |
| P1 panel dos voces | `panel_datos` | 259 s (4:19) | 0.054 | 0.975 (39/40) | 0.0129 |
| C1 clase población | `clase_poblacion` | 183 s (3:03) | 0.038 | 1.000 (40/40) | 0.0091 |
| C2 clase economía | `clase_economia` | 189 s (3:09) | 0.040 | 0.925 (37/40) | 0.0094 |
| E1 earnings ficticia | `earnings_luminara` | 180 s (3:00) | 0.039 | 0.950 (38/40) | 0.0090 |

Reintentos usados: **0** (las 6 pasaron al primer intento; máx 2/pieza
sin usar). Transcribe: bias ES + keywords por pieza (82–97), path file.

## Voces y licencias

- Voz A (todas): `es_ES-carlfm-x_low`, dominio público, desde cero.
- Voz B (panel, INVITADO 20/40 turnos): **variante ffmpeg de carlfm**
  (pitch ×0,85), documentada como variante, no segunda persona.
- Segunda voz limpia: no existe (todas las `es_*` alternativas con
  taint NC/research-only u opaca; ver PROVENANCE_ES.md).

## Salidas (`data/synth/es/`, ignorado)

Por pieza: mp4 1080p 25 fps + srt (panel: 40 entradas por turno; resto:
10 por slide) + `_clean.wav` + `_snr{20,10,5}db.wav` (ruido blanco por
código, medido ±0.01 dB). Truths: `live/material/truth_es/*.json`
(formato dev.json + piece/slide/t_aprox/voice). Snapshots WB:
`snap/` + SHA256SUMS.

## Coste y cómputo

- Live total: **$0.059** (tope $6). Build local $0.
- Cómputo: ~12 min wall (TTS+ffmpeg CPU, 6 piezas), tope 90 min.

## Misses de recall (11/240, todos artefactos del path file)

Duplicaciones/inserciones del file fallback ("dos en dos mil
veinticuatro", "China China", "interiormente interiormente",
"España España"), caveat ya documentado en `live/oido/asr_file.py`.
Ningún miss por guion, TTS ni keywords. Recall mín 0.925 (C2).

## Unresolved

- Ninguno bloqueante. Cosmético: letras A/B del avatar del panel
  parcialmente tapadas por los arcos (no afecta audio/eval).
- Categoría b reutiliza ground truth de dev.json verificado 2026-11-01
  (sin re-fetch por bot-wall; declarado en PROVENANCE_ES.md).
