# Licencia de la voz nativa ES (F2-4 voz nativa ES Atlas Live)

Voz elegida: **`es_ES-carlfm-x_low`** (piper, español de España, nativa).

## Voz elegida

- Modelo: `es_ES-carlfm-x_low.onnx` (+ `.onnx.json`), calidad `x_low`, 16 kHz, 1 hablante.
- URL modelo: <https://huggingface.co/rhasspy/piper-voices> (`es/es_ES/carlfm/x_low/`)
- MODEL_CARD: <https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/carlfm/x_low/MODEL_CARD>
- Dataset: <https://github.com/carlfm01/my-speech-datasets>
- Licencia del dataset (la VOZ, no solo el código): **dominio público**
  ("Public domain", declarado en el `MODEL_CARD` y en el `README.md` del
  dataset, sección `## License`). Verificado 2026-11-01.
- Entrenamiento: **desde cero** (`Trained from scratch`, sin modelo base que
  arrastre otra licencia).
- Uso: generar el audio sintético de `data/keynote_es/keynote_es_native.mp4`
  (fixture original del ejercicio Atlas Live). Dominio público: apto para este
  uso y para redistribuir el audio generado, sin atribución exigida.
- Motor: **piper** (licencia MIT), igual que `LICENSES.md`. Fonemizador
  espeak-ng (GPLv3) como herramienta local, no distribuido.

## Hashes (sha256, archivos descargados 2026-11-01)

- `es_ES-carlfm-x_low.onnx`: `d69677323a907cd4963f42b29c20a98b5d6bfa7f3e64df339915e4650c00d125`
- `es_ES-carlfm-x_low.onnx.json`: `d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966`

Ruta local (caché, no versionada): `/tmp/piper_voices_es/`.

## Candidatas descartadas

| Voz | Licencia del dataset | Motivo del descarte |
|---|---|---|
| `es_ES-davefx-medium` | CC0, pero afinada desde `lessac` (Blizzard 2013 Research Licence: solo investigación, excluye uso comercial) | base con restricción research-only |
| `es_ES-sharvard-medium` | CC-BY 3.0, pero afinada desde `lessac` (Blizzard 2013, research-only) | base con restricción research-only |
| `es_ES-mls_10246-low` | CC BY 4.0, pero afinada desde `ryan` (RyanSpeech, **CC BY-NC-SA 4.0**) | **no-comercial** en la cadena |
| `es_ES-mls_9972-low` | CC BY 4.0, pero afinada desde `ryan` (RyanSpeech, **CC BY-NC-SA 4.0**) | **no-comercial** en la cadena |
| `es_AR-daniela-high` | CC BY-SA 4.0, afinada desde `lessac` (research-only) | base research-only + no es es_ES |
| `es_MX-ald-medium` | Unlicense, pero afinada desde `davefx` (cadena → `lessac`) | base research-only en la cadena |
| `es_MX-ald-x_low` | Sintética desde `ald-medium` + frases Tatoeba (CC-BY) | cadena opaca + no es es_ES |
| `es_MX-claude-high` | `apache-2.0` declarado en un Space, entrenamiento opaco | licencia no verificable |

Fuentes consultadas (todas 2026-11-01): `MODEL_CARD` de cada voz en
`huggingface.co/rhasspy/piper-voices`, `README.md` de
`github.com/OHF-Voice/voice-datasets` (CC0) y de
`github.com/carlfm01/my-speech-datasets` (dominio público), licencia Blizzard
2013 Lessac (<https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html>,
cláusula "Research Purposes" excluye uso comercial), `MODEL_CARD` de
`en_US-ryan-*` (CC BY-NC-SA 4.0).

## Notas

- El repo HF `rhasspy/piper-voices` declara `license: mit` a nivel de repo,
  pero esa etiqueta cubre el empaquetado/código, no cada voz: por eso se
  verificó la licencia voz por voz (dataset + modelo base).
- Caveat menor documentado: el autor de `carlfm` validó alineamientos con un
  modelo DeepSpeech entrenado sobre datos voxforge (GPL); ese modelo solo se
  usó como herramienta de validación, no forma parte del dataset publicado
  como dominio público.
