# F2-5 — micrófono real + 2º clip (reporte)

## Micrófono

- **Verificado en este host: NO** (degradación honesta, por diseño).
  - ffmpeg estático `imageio_ffmpeg` v7.0.2: demuxer `alsa` SÍ, demuxer
    `pulse` NO (`Unknown format 'pulse'`); sin ffmpeg del sistema.
  - `/dev/snd/` solo contiene `timer` (sin nodos `pcmC*D*c`); `arecord`,
    `parec`, `pactl` no instalados.
  - Existe socket PulseServer de WSLg (`/mnt/wslg/PulseServer`, vía
    `PULSE_SERVER`), pero ningún binario disponible puede hablarle sin
    nuevas dependencias (prohibidas) → no utilizable.
  - `detect_microphone()` lanza `NoMicrophoneError` con el log de
    intentos; el smoke test de hardware salta (`pytest.skip`).
- **Entregado:** `live/mic/` (`capture.py`, `__init__.py`,
  `README_MIC.md`): captura ffmpeg pulse/alsa autodetectada con prueba
  en vivo → PCM s16le mono 24 kHz; `iter_audio()` rinde `(t_media, bytes)`
  con `t_media = bytes/48000`; `chunks()` entra directo en
  `transcribe_stream`; etiqueta `"live-microphone"`; doubles lavfi/file
  explícitos y etiquetados (jamás por autodetección). Wiring `--mic`
  para `run_live.py` documentado con snippet (sin tocar el archivo).

## Clip 2 (otro dominio: economía/negocios)

- **Fuente:** VOA Learning English (Voice of America, gobierno federal
  EE. UU.) — "Super Bowl Is Big Business for Big Ad Firms" (2017-02-03).
  Descarga DIRECTA del CDN de VOA (HTTP 200, 6.752.975 bytes). Cero
  YouTube en todo el proceso.
- **Licencia:** narración/empaquetado VOA = dominio público (§1 términos
  VOA, texto guardado + hash). **Caveat:** inserta breves extractos de
  anuncios de terceros → fichero mixto, no PD puro (ver Unresolved).
- **Duración:** 108,71 s (1:48) ≤ 4 min; H.264 640x360 + AAC.
- **ASR-recall (1 transcribe file, EN):** "$5 million per 30 second
  spot", "60 second ad", "Adolphus Busch … Germany to the US in the
  1850s … Eberhard Anheuser" — todo íntegro; cifras verificadas contra
  fuentes independientes (Variety/NYT/AdAge para los $5M; Fox
  Sports/Wikipedia para el spot de 60 s). 5 etiquetas de diarización.
- **Archivos:** `data/clips/VOA_20170203_SuperBowl_AdFirms.mp4`
  (`4d571372…84c8f`), `data/clips/license_VOA_….txt` (`c21f9da1…edc89`),
  espejo de licencia + `live/material/PROVENANCE_clip2.md` (versionados).

## USD

- **$0,0054** total live (1 transcribe file 108,7 s; techo $2).

## Tests

- `live/tests/test_f2_mic.py`: **13 passed, 1 skipped en 0.29 s**
  (2026-11-01, `.venv/bin/python -m pytest live/tests/test_f2_mic.py -v`),
  todos offline: doubles lavfi + wav stdlib, contrato `t_media`, error
  honesto con monkeypatch, smoke de hardware con skip, provenance
  completa incl. hashes y duración.

## Unresolved

1. Mic no verificable en este host (headless/WSL sin captura ALSA ni
   cliente Pulse). Falta: corrida del smoke en un host con micrófono.
2. Clip2 PD mixto por insertos de anuncios de terceros (detalle en
   PROVENANCE_clip2.md). Falta (opcional): reemplazo 100 % PD o
   derivado solo-narración escindiendo t≈24–38 s.
3. Transcripción de referencia via endpoint file (duplicaciones
   conocidas); revalidar recall por WS en el pipeline vivo.
