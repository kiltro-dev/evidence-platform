# PROVENANCE — M1d clip real (Atlas Live)

Decisión: **clip real con derechos OK** (no sintético).

## Ganador

- **Título:** 2014 Warmest Year On Record
- **Fuente:** NASA Image and Video Library (Goddard Space Flight Center / GISS)
- **NASA ID:** `GSFC_20150116_GISS_m11727_Global_Temps`
- **URL activo:** https://images.nasa.gov/details/GSFC_20150116_GISS_m11727_Global_Temps
- **URL licencia:** https://www.nasa.gov/nasa-brand-center/images-and-media/
- **Licencia:** Obra del gobierno federal de EE. UU. — NASA indica que su contenido
  generalmente *no está sujeto a copyright en Estados Unidos*; el material de
  terceros con copyright se marca como tal. Este activo no tiene marcas ni
  créditos de terceros (ver "Créditos" abajo). Atribución requerida: NASA.
- **Fecha acceso:** 2026-11-01
- **Archivo local:** `data/clips/GSFC_20150116_GISS_m11727_Global_Temps_medium.mp4`
- **URL descarga directa (sitio fuente):**
  https://images-assets.nasa.gov/video/GSFC_20150116_GISS_m11727_Global_Temps/GSFC_20150116_GISS_m11727_Global_Temps~medium.mp4
- **Tamaño:** 27.678.183 bytes (coincide con Content-Length del servidor)
- **SHA256 (mp4):** `b1ad43f54015fd60d717d6ce6e2143a631887e4316141017bff3e7e1d70fa42d`
- **Duración medida (cabecera mvhd del mp4):** 95,25 s (1:35) — coincide con el
  metadato NASA (`QuickTime:Duration 0:01:35`). Cumple ≤ 4 min.
- **Texto licencia guardado:** `data/clips/license_GSFC_20150116_GISS_m11727_Global_Temps.txt`
- **SHA256 (licencia):** `0f56fefd66acc6e29f82b38c76b8903756334a5a812724b2f5b388b76363a7f4`

## Por qué cumple

1. **≤ 4 min:** 1:35 verificado en el archivo descargado y en el metadato NASA.
2. **Cifras verificables habladas:** narración en inglés con cifras contrastables
   contra el análisis GISTEMP de NASA GISS (subtítulos oficiales `*.vtt` del
   propio activo, sitio fuente, NO YouTube):
   - "2014 was the hottest year on record"
   - "Since temperature records began in 1880, the global average has increased
     by 1.4 degrees Fahrenheit"
   - mapa de anomalías vs. "30-year baseline"; descripción del activo añade
     "0.8 degrees Celsius", "Nine of the 10 warmest years … since 2000".
3. **Dominio neutral:** ciencia climática / observación de la Tierra (análisis
   global de temperaturas GISS, estaciones meteorológicas, mapa de anomalías).
4. **Sin políticos vivos:** vídeo narrado sobre datos; único científico nombrado
   en créditos: Gavin A. Schmidt (director GISS, científico, no político).
5. **Licencia verificada:** página del activo abierta (HTTP 200) + registro API
   del mismo sitio; política NASA (dominio público EE. UU. con salvedad de
   terceros) guardada con hash. NUNCA YouTube: sin yt-dlp, audio ni subtítulos
   de YouTube en ningún paso.

## Créditos (descarte de material de terceros)

Registro del activo (API + `metadata.json` del sitio fuente):

- Center: GSFC; Location: Goddard Space Flight Center; Photographer: (vacío).
- SecondaryCreator: Joy Ng, Lori Perkins, Gavin A. Schmidt, Patrick Lynch —
  personal de producción científica NASA GSFC/GISS, sin entidades externas.
- Descripción: análisis propio NASA GISS + lecturas de estaciones
  meteorológicas (datos, no metraje con copyright) + visualización de mapa de
  anomalías NASA. Sin marcas "copyright", "courtesy" ni titulares externos.
- Salvedad estándar: si algún plano contuviera material de terceros, la
  política NASA exige contactar al titular; no hay indicio de ello en el
  registro. No se visionó fotograma a fotograma (ver "Unresolved").

## Finalistas descartados (máx. 3 evaluados a fondo)

1. `GSFC_20160120_Schmidt_m12134_Temp2015a` — "Gavin Schmidt on 2015's Record
   Global Temperature" (NASA GSFC, 24 s, dominio público).
   Descartado: el audio verificado (subtítulos oficiales) NO contiene cifras
   habladas ("2015 was a record warm year", El Niño, tendencia); las cifras
   (0,23 °F / 0,13 °C, techo 2 °C) solo están en la descripción escrita, no
   verificadas como habladas o en pantalla.
2. `GSFC_20160719_SeaIce_m12306_Climate` — "Record-Breaking Climate Trends
   2016" (NASA GSFC, 47 s, dominio público).
   Descartado: pista de audio solo música (`[ music ]` en subtítulos
   oficiales); las cifras quedarían solo "en pantalla" sin verificación
   práctica en el time-box. El ganador tiene cifras habladas explícitas.
3. Ganador (arriba): único con cifras habladas verificables + narración.

Fuentes consultadas y no necesitadas: NOAA, otras agencias federales, Internet
Archive, Wikimedia Commons (el time-box y el criterio de cifras habladas se
resolvieron en NASA; no hizo falta escalar).

## Notas operativas

- **AVISO gitignore (sin editar, por instrucción):** `evidence-platform/.gitignore`
  línea 4 ignora `data/` completo (`git check-ignore -v` lo confirma). El .mp4
  queda correctamente ignorado, PERO también este PROVENANCE.md y el
  `license_*.txt`. Si se quiere versionar la procedencia, moverla fuera de
  `data/` o ajustar `.gitignore` (decisión del owner).
- Coste: $0 (descarga directa pública, sin APIs de pago).

## Unresolved

- No se visionó el mp4 fotograma a fotograma; la ausencia de terceros se basa
  en el registro oficial del activo (sin marcas de copyright/cortesía) y en
  los subtítulos oficiales. Riesgo residual bajo.
- La página HTML del activo es JS-renderizada (fetch devolvió HTTP 200 con
  cuerpo procesable vacío); los metadatos se tomaron del registro API oficial
  del mismo sitio (`images-api.nasa.gov`) + `metadata.json` del activo.
- La variante descargada es `~medium.mp4` (H.264, 27 MB); el `~orig.mp4`
  (ProRes, 160 MB) existe en la misma URL fuente si se necesita máxima calidad.
