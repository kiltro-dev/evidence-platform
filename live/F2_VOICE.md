# F2-4 — Voz nativa ES Atlas Live

Keynote ES re-generado con voz nativa española + medida WER/ASR-recall.

## Resultado

- Voz: **`es_ES-carlfm-x_low`** (piper, español de España, nativa, 16 kHz).
- Licencia de la voz: **dominio público**, entrenada desde cero (sin base).
  Detalle + descartes: `live/material/LICENSES_VOZ.md`.
- Hashes sha256:
  - `es_ES-carlfm-x_low.onnx`: `d69677323a907cd4963f42b29c20a98b5d6bfa7f3e64df339915e4650c00d125`
  - `es_ES-carlfm-x_low.onnx.json`: `d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966`
- MP4: `data/keynote_es/keynote_es_native.mp4` — **189.6 s (3.16 min)**,
  1280×720 H.264 + AAC, 4.2 MB. SRT: `data/keynote_es/keynote_es_native.srt`.
- **WER: 0.0296** (sub=2, ins=14, del=1, n_ref=574).
- **ASR-recall: 1.0000 (41/41)** — meta ≥0.9 superada.
- Coste live: **$0.0190** (2 llamadas transcribe × $0.0095; TTS local $0).

## Archivos

| Archivo | Qué es |
|---|---|
| `live/material/build_keynote_es.py` | Importa `SLIDES`/`narration` de `build_keynote.py` (sin duplicar guion), regenera charts, TTS nativo, ffmpeg → `data/keynote_es/` |
| `live/material/measure_wer.py` | Transcribe (POST `/v1/asr/transcribe`, bias ES + 71 keywords de `truth.json`) → WER + recall + `wer_report.json` |
| `live/material/LICENSES_VOZ.md` | Voz elegida: URL + licencia + hashes + candidatas descartadas |
| `live/tests/test_f2_voice.py` | 22 tests offline (WER exacto, fuzzy, keywords, números ES, SRT, recall) |
| `live/F2_VOICE.md` | Este informe |

## Notas de medida

- El ASR-ES deletrea los números ("veintinueve coma tres"), según el
  contrato de `live/oido`. El scoring expande dígitos de referencia a
  palabras ES (`int_to_es`, verificado contra la transcripción real) antes
  de WER/recall; sin esto el recall era 0.39 (todos los fallos, spans con
  dígitos).
- Las 14 inserciones del WER son palabras duplicadas del descodificador
  file ("planeta planeta"), artefacto conocido del path file
  (`live/oido/CONTRACT.md`); el path realtime es el primario.
- Keywords: 71 (todos los números + frases de cantidad + entidades ES de
  `truth.json`: países, Banco Mundial, Naciones Unidas, Everest, Sáhara,
  Nepal, Sídney, Australia, Celsius, PIB, noviembre...).
- Reproducir: `.venv/bin/python live/material/build_keynote_es.py` y
  `.venv/bin/python live/material/measure_wer.py` (con `MODEL_API_KEY` en
  env; `--transcript data/keynote_es/work/transcript_es.txt` re-evalúa a $0).

## Unresolved

- `live/tests/test_f2_mic.py::test_provenance_file_complete` falla en este
  checkout (formato SHA256 del provenance de F2-mic): fuera de mi alcance,
  tarea hermana en curso. Resto de la suite: 277 passed + 4 skipped.
- La voz vive en caché efímera `/tmp/piper_voices_es/`; re-descargable desde
  las URLs + hashes de `LICENSES_VOZ.md` (override con `VOICE_ES`).
