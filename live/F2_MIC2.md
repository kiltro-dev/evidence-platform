# F2-8 mic E2E + clip2 PD-puro (reporte f2-mic2, 2026-11-02)

## Mic: degradación verificada (NO PASS)

Criterio: solo PASS con bytes capturados → transcritos reales. Este host
no puede capturar: sin micrófono HW, sin loopback virtual posible y sin
binario ffmpeg con demuxer `pulse`. No se ejecutó `transcribe_stream`
(no había bytes reales que transcribir).

Evidencia (comandos corridos en este host, salidas recortadas):

- `ls /dev/snd/` → solo `timer` (sin nodos `pcmC*D*c`).
- `cat /proc/asound/cards` → `No such file or directory`.
- `command -v arecord aplay speaker-test pactl parec modprobe ffmpeg`
  → vacío (sin userland ALSA/Pulse ni ffmpeg en PATH).
- `/sbin/modprobe snd-aloop` →
  `modprobe: FATAL: Module snd-aloop not found in directory
  /lib/modules/6.6.114.1-microsoft-standard-WSL2` (rc=1).
- `zgrep SND_ALOOP /proc/config.gz` → `# CONFIG_SND_ALOOP is not set`:
  el kernel WSL2 no trae el módulo; ni con root cargaría.
- `sudo -n true` → `sudo: a password is required` (sin root anyway).
- `ffmpeg-estático -f alsa -i hw:Loopback` →
  `cannot open audio device hw:Loopback (No such device)`.
- `ffmpeg-estático -f alsa -i default` → `Unknown PCM default`.
- `ffmpeg-estático -f pulse -i default` →
  `Unknown input format: 'pulse'` (el build estático v7.0.2 no trae
  ese demuxer; no hay ffmpeg del sistema que lo aporte).
- `detect_microphone()` → `NoMicrophoneError` honesto:
  `no pulse demuxer in this build` + `alsa demuxer ok, no capture nodes`.

Fix incluido (`live/mic/capture.py`): `_has_demuxer` usaba el exit code
de `-h demuxer=<name>`, que es 0 incluso para formatos inexistentes
(siempre-True; reportaba `pulse` como presente). Ahora parsea
`ffmpeg -devices` exigiendo flag `D`. El error de autodetección nombra
la causa real.

## Clip2: sin derivado (declaración documentada)

Veredicto: **ningún segmento limpio ≥ 20 s existe** en
`data/clips/VOA_20170203_SuperBowl_AdFirms.mp4`. No se produjo
`data/clips/VOA_narration_only.mp4`; se conserva el mixto documentado,
bit-idéntico (`4d571372…84c8f`, verificado).

Método: rejilla de 27 fotogramas cada 4 s (t=0–104) + bordes a 1 s +
cola t=107, extraídos con ffmpeg y clasificados por contenido
(criterio: narración = presentadora en estudio; inserto = footage de
anuncios de terceros). Un tramo de presentadora ≥ 8 s tocaría ≥ 2
fotogramas de la rejilla; se vieron **cero**: no hay presentadora en
estudio en todo el clip.

Línea de tiempo (video):

| t (s) | contenido |
|---|---|
| 0–~37 | insertos: Pistachios, barco/ballena, Kia, Avocados from Mexico |
| ~37–43 | entrevista James Cooper, Adweek (~6 s) |
| ~43–51,4 | insertos: cocina, body-spray |
| 51,4–74,3 | entrevista Tony Case, Adweek (~22,9 s, cortes de escena en 51,36/74,32) |
| 74,3–107 | insertos: vestidor, living, Anheuser-Busch largo |
| 107–108,7 | cortinilla VOA Learning English (~1,7 s) |

Por qué no hay derivado válido:

1. El t≈24–38 s propuesto es exactamente el inserto (video: secta de
   los Avocados con bug `AVOCADOS FROM MEXICO`; audio: diálogo del
   anuncio — "Can we lose the masks? … secret society" en la
   transcripción de referencia). La propuesta confundía el inserto con
   el tramo limpio.
2. El único tramo no-anuncio ≥ 20 s (Case, 51,4–74,3) es una
   **entrevista, no narración**, sin cifras verificables, y su pureza
   PD no es certificable desde el fichero (locación de terceros,
   monitores de fondo con contenido no identificado).
3. Todo tramo con narración VOA en audio lleva video de anuncios
   encima; un corte `-ss/-t` no puede separarlos.

ASR-recall del derivado: N/A (sin derivado, sin llamada). Detalle y
método en el addendum de `live/material/PROVENANCE_clip2.md`.

## USD

**$0,00** live (0 llamadas: mic sin bytes reales; clip2 sin derivado
que transcribir). Techo $2.

## Tests

`live/tests/test_f2_mic2.py` (9 tests, todos offline): demuxers reales
del binario estático (pulse ausente, alsa presente, nombres
inexistentes rechazados), `describe_host` consistente, error honesto
con la causa real, autodetección jamás devuelve doubles, addendum F2-8
presente con marcadores, hash del mixto inalterado, este reporte
completo. `live/tests/test_f2_mic.py` sin cambios (sigue verde).

## Unresolved

1. Mic E2E real (captura → `transcribe_stream` WS) pendiente de un host
   con micrófono: este kernel/host lo hace imposible, no solo ausente.
2. Clip2 sigue mixto: un PD-puro exigiría otra pieza (estudio VOA/NASA
   sin insertos) o licenciar los extractos; fuera del time-box.
3. Contenido de audio en t≈24–38 s inferido de transcripción +
   subtítulos (sin escucha humana); suficiente para el veredicto de
   video, no una prueba de audio forense.
