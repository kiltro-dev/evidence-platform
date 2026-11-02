# F2-10 mic E2E "driver-stubbed" + clip2b Atlas Live (reporte f2-mic3, 2026-11-02)

## Mic: veredicto sobre F2_MIC2 (kernel) + E2E PASS menos el driver

Sobre `live/F2_MIC2.md`: **asumo el hecho, refuto la implicación**.
Asumo —sin repetir probes ciegos (nada de modprobe/arecord/pactl en esta
tarea)— que este host/kernel no puede capturar: sin nodos ALSA, sin
demuxer pulse en el ffmpeg estático y sin `snd-aloop` posible en WSL2.
Refuto que eso deje el mic E2E sin probar: el driver kernel es solo el
primer eslabón, y **todo lo demás sí es testeable en vivo**.

Prueba: corrida live `driver-stubbed-file-fed` — `MicCapture` con fuente
ffmpeg arbitraria `("-re", "-i", feed.wav)` (pacing realtime) →
`iter_audio`/`chunks` → `transcribe_stream` por WebSocket REAL:

- Feed: `data/clips/mic3_e2e_feed.wav`, 25,0 s mono 24 kHz (voz carlfm
  14,3 s + 0,5 s silencio inicial + cola, `mic3_e2e_feed.txt` el guion).
- Resultado: **PASS** — 42 eventos (40 partial, 2 final), cierre 1000
  limpio, `audio_sent_s=25.00` (íntegro, sin silence inyectado),
  `first_partial_s=2.83`.
- Transcript muestra (turno 0, t=[0,38, 11,10], speaker A):
  > Bienvenidos a esta prueba de micrófono en vivo. La Tierra orbita
  > ciento cuarenta y nueve coma seis millones de kilómetros del Sol.
  > Marte mide seis mil setecientos noventa y dos kilómetros de diámetro.
- Turno 1, t=[11,10, 14,86]: "Si escucha este mensaje, el camino de
  audio funciona de principio a fin." Tiempos dentro de [0, 25];
  el fin de voz real (14,84 s) coincide con el cierre del turno 1.
- Soporte nuevo (`live/mic/*`, único código editado):
  `custom_ffmpeg_input` (fuente ffmpeg arbitraria, siempre `real=False`),
  `driver_stubbed_file_input` (`-re` + label honesto),
  `MicCapture.for_custom_input` (rechaza inputs reales).
- Eventos íntegros: `data/clips/mic3_e2e_transcript.json` (ignorado).
- Coste: 25 s → floor(25)/3600×$0,18 = **$0,0013**.

Reproducir (1 corrida, ~40 s): `set -a; . ./.env; set +a` y
alimentar `MicCapture.for_custom_input(driver_stubbed_file_input(
"data/clips/mic3_e2e_feed.wav")).chunks()` a `transcribe_stream` con
`ASRConfig(api_key, language_bias=("Spanish",), keywords=(...))`.

## Clip2b: sintético 100 % propio, ciencia

Builder `live/material/build_clip2b.py` (patrón TTS+ffmpeg de
`build_keynote_es.py`): 5 slides matplotlib (2 charts honestos) + TTS
carlfm + ffmpeg + SRT + `truth2b.json`. Cero YouTube, cero descargas.

- Path: `data/clips/clip2b.mp4` (+ `.srt`, `truth2b.json`).
- Duración: **76,25 s** (banda 60–90, assert en el builder).
- Claims: 8/8 `esperado=true` (distancias, diámetros, periodos),
  cada cifra verificada contra la tabla métrica NASA
  (`nssdc.gsfc.nasa.gov/planetary/factsheet/index.html`, 2026-11-02)
  y con `ref_url` a su ficha por planeta en el código y en truth2b.
- Licencia: obra propia bajo **MIT del repo** + voz **dominio público**;
  detalle en `live/material/PROVENANCE_clip2b.md`.
- ASR-recall (1 transcribe file, bias ES + 16 keywords, 76,4 s):
  **0,8750 (7/8)** con el scorer F2-4. Miss 2b-3 conserva literal la
  carga numérica; falla el span por "a" elidida + duplicación
  "kilómetros del kilómetros" (artefacto conocido del path file).
  Re-scoring $0: `build_clip2b.py --score-transcript
  data/clips/work_2b/transcript_2b.txt`.
- Coste: 76 s → **$0,0038**.

## USD

**$0,0051** live total (2 llamadas: $0,0013 WS + $0,0038 file). Techo $2.

## Tests

`live/tests/test_f2_mic3.py` (14 tests, todos offline): args arbitrarios
verbatim + orden `-re < -i < PCM`, label/pacing del feed stubbed,
rechazo de inputs reales, contrato t_media file-fed con doubles,
`chunks()`→`PCMFeed`→EOF, autodetección jamás devuelve customs,
transcript live guardado (skip si ausente), tabla CLAIMS (8, NASA, true),
8 cifras presentes, slides citan literal, banda 60–90, truth2b bien
formado (skip si ausente), provenance completa, artefacto en banda
(skip si ausente), este reporte completo.

## Unresolved

1. El driver kernel real (pulse/ALSA → bytes) sigue sin probarse aquí:
   imposible en este host por diseño (ver F2_MIC2). En un host con mic,
   el mismo `transcribe_stream` ya verificado cierra el E2E completo.
2. Recall clip2b 7/8 en path file (artefacto del decoder, no del clip);
   el path WS primario no se midió para clip2b (1 llamada por tarea).
3. El .mp4 no es bit-reproducible (timestamps ffmpeg); guion, timings
   y claims sí son deterministas. Voz en caché efímera `/tmp/`
   (re-descargable, ver `LICENSES_VOZ.md`).
