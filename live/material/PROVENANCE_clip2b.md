# PROVENANCE — clip 2b sintético, dominio ciencia (Atlas Live F2-10)

Decisión: **obra 100 % propia, sin material de terceros**.

## Ganador (generado, no descargado)

- **Título:** Un paseo por el sistema solar (clip sintético 2b)
- **Fuente:** generado en este repo por `live/material/build_clip2b.py`
  (guion propio + slides matplotlib propios + TTS local + ffmpeg).
- **Guion:** 5 slides, 8 afirmaciones numéricas literales sobre el
  sistema solar (3 distancias al Sol, 3 diámetros, 2 periodos orbitales),
  todas con `esperado: true`. Relleno sin cifras nuevas.
- **Cifras y refs:** cada claim cita su ficha NASA en `ref_url`
  (`https://nssdc.gsfc.nasa.gov/planetary/factsheet/<planeta>fact.html`),
  verificadas contra la tabla métrica maestra
  (`.../factsheet/index.html`, consultada 2026-11-02):
  Mercurio 57,9 · Tierra 149,6 · Júpiter 778,5 (10^6 km);
  Venus 12104 · Marte 6792 · Saturno 120536 (km);
  Marte 687 días · Júpiter 4331 días.
- **Licencia de la obra:** los autores del repo la publican bajo la
  **licencia MIT del repo** (ver `LICENSE` en la raíz): guion, slides,
  charts y ensamblado son originales de esta tarea.
- **Licencia de la voz:** `es_ES-carlfm-x_low` (piper), dataset en
  **dominio público**, entrenada desde cero. Detalle + hashes + descartes:
  `live/material/LICENSES_VOZ.md`. Sin atribución exigida; se cita igual.
- **Fecha generación:** 2026-11-02
- **Archivo local:** `data/clips/clip2b.mp4`
- **Tamaño:** 1.657.973 bytes
- **SHA256 (mp4):** `74e8f9efcd799873e8f2ad4a2f06919fa88f7b4ed6818ae4d70a5d5c1a92352f`
- **Duración medida (ffmpeg `Duration`):** 76,25 s (1:16) — dentro de 60–90 s.
- **Streams:** vídeo H.264 1280x720 + audio AAC mono.
- **SRT:** `data/clips/clip2b.srt` (5 entradas, 1/slide),
  SHA256: `8ef95628597d09bd3e80d703970bbdb42e5f596f704120d26683ce8cd5d1c993`
- **Truth:** `data/clips/truth2b.json` (8 filas
  `{id,texto,t,esperado,ref_url}`, ids `2b-1`–`2b-8`),
  SHA256: `1ad449ce628b18849cc470dc2466f3e26ecfe45e02e3342892e1e1c637e8821b`

## Por qué cumple

1. **60–90 s:** 76,25 s verificados en el archivo (assert en el builder).
2. **Cifras verificables habladas + ASR-recall medido:** 1 llamada al
   endpoint file (DIARIZATION, bias ES + 16 keywords de los claims, 76,4 s):
   **recall 0,8750 (7/8)** con el scorer de `measure_wer.py` (método F2-4).
   El único miss (2b-3) conserva íntegra la carga numérica
   ("setecientos setenta y ocho coma cinco" literal); falla el span por
   la partícula "a" elidida + duplicación "kilómetros del kilómetros",
   artefacto conocido del path file (`live/oido/CONTRACT.md`). Las 8
   cargas numéricas están presentes (7 literales + "ochentaenta" en 2b-7,
   glitch menor que igual pasó el fuzzy).
3. **Dominio neutral y distinto del clip 2:** ciencia (sistema solar),
   frente a economía/negocios del clip 2 (VOA Super Bowl).
4. **Sin políticos vivos:** solo planetas y el Sol; cero personas.
5. **Licencia limpia desde el origen:** sin descargas, sin YouTube, sin
   extractos de terceros; todo se regenera con
   `.venv/bin/python live/material/build_clip2b.py` ($0, offline).

## Transcripción de referencia (endpoint file, 2026-11-02)

> Bienvenidos a este paseo por el sistema solar. Todas las cifras que
> escucharán provienen de las fact sheets planetarias de la NASA. […]
> Mercurio orbita cincuenta y siete coma nueve millones de kilómetros
> del Sol. La Tierra está a ciento cuarenta y nueve coma seis millones
> de kilómetros del Sol. Júpiter orbita setecientos setenta y ocho coma
> cinco millones de kilómetros del kilómetros del Sol. […] Venus mide
> doce mil ciento cuatro kilómetros de diámetro. Marte mide seis mil
> setecientos noventa y dos kilómetros de diámetro. Saturno mide ciento
> veinte mil quinientos treinta y seis kilómetros de diámetro. […]
> Marte tarda seiscientos ochentaenta y siete días […] Júpiter tarda
> cuatro mil trescientos treinta y uno días […] Gracias por escuchar
> este Atlas en miniatura.

Texto completo: `data/clips/work_2b/transcript_2b.txt` (ignorado).
Reporte: `data/clips/clip2b_recall.json` (ignorado). Re-scoring a $0:
`.venv/bin/python live/material/build_clip2b.py --score-transcript
data/clips/work_2b/transcript_2b.txt`.

## Notas operativas

- **AVISO gitignore (sin editar, por instrucción):**
  `evidence-platform/.gitignore` ignora `data/` completo: el .mp4, .srt,
  `truth2b.json` y `work_2b/` quedan ignorados; ESTA provenance, el builder
  y los tests sí se versionan. Regenerable bit-no-idéntico (timestamps
  ffmpeg) pero contenido-idéntico.
- Coste: 1 transcribe file de 76,4 s → 76/3600×$0,18 = **$0,0038**.

## Unresolved

- El .mp4 no es bit-reproducible (metadatos/timestamps de ffmpeg); el
  contenido (guion, timings, claims) sí es determinista.
- La voz vive en caché efímera `/tmp/piper_voices_es/`; re-descargable
  desde las URLs + hashes de `LICENSES_VOZ.md` (override con `VOICE_ES`).
- Recall file-path 7/8; el path WS (primario) no se midió para este clip
  (1 sola llamada file por presupuesto de la tarea).
