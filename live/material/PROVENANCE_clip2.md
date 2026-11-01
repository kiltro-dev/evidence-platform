# PROVENANCE — clip 2 real, dominio economía/negocios (Atlas Live F2-5)

Decisión: **clip real con derechos DOCUMENTADOS (mixtos, ver "Caveat")**.

## Ganador

- **Título:** Super Bowl Is Big Business for Big Ad Firms (serie VOA60)
- **Fuente:** VOA Learning English (Voice of America, gobierno federal EE. UU.)
- **URL página:** https://learningenglish.voanews.com/a/big-game-is-big-business-for-ad-firms/3705209.html
- **URL licencia:** https://www.voanews.com/p/5338.html (Terms of Use & Privacy Notice)
- **Licencia:** la narración/empaquetado producido exclusivamente por VOA es
  **dominio público** según §1 de sus términos ("All text, audio and video
  material produced exclusively by the Voice of America is in the public
  domain. Credit … should be given to voanews.com, Voice of America, or
  VOA"). **CAVEAT (importante):** el vídeo incrusta breves extractos de
  anuncios comerciales de terceros (creatividad de anunciantes del Super
  Bowl) y ~10 s de audio de un anuncio; ese material de terceros NO es
  dominio público (§2 excluye material con licencia de terceros). El
  fichero COMO UN TODO es, por tanto, **mixto, no PD puro**. Uso previsto:
  demo interna de investigación (transcribir → verificar cifras); no
  redistribuir la pista de vídeo como PD. Ver "Unresolved".
- **Fecha acceso:** 2026-11-01
- **Archivo local:** `data/clips/VOA_20170203_SuperBowl_AdFirms.mp4`
- **URL descarga directa (CDN propio de VOA):**
  https://voa-video-ns.akamaized.net/pangeavideo/2017/02/b/b9/b980cb37-63ec-4790-a0d9-5f091e031213.mp4?cb=ed026c381
  (responde 301 → `https://voa-video.voanews.eu/pangeavideo/2017/02/b/b9/b980cb37-63ec-4790-a0d9-5f091e031213.mp4?cb=ed026c381&cb=ed026c381`;
  descarga con `curl -L`, HTTP 200 directo de la fuente, sin YouTube ni yt-dlp)
- **Tamaño:** 6.752.975 bytes (coincide con Content-Length del servidor)
- **SHA256 (mp4):** `4d571372329f8c2d450af8928509d7723e93dbcbcf699d128d8401ca57484c8f`
- **Duración medida (ffmpeg `Duration`):** 108,71 s (1:48) — coincide con el
  JSON-LD de la página (`"duration":"PT1M48S"`). Cumple ≤ 4 min.
- **Streams:** vídeo H.264 640x360 25 fps + audio AAC estéreo 44,1 kHz.
- **Texto licencia guardado:** `data/clips/license_VOA_20170203_SuperBowl_AdFirms.txt`
  (espejo versionado: `live/material/license_VOA_20170203_SuperBowl_AdFirms.txt`)
- **SHA256 (licencia):** `c21f9da137c36d81dcc6f3101968f9576f50e9fb0bc3e9d2ad085bbb3fcedc89`

## Por qué cumple

1. **≤ 4 min:** 1:48 verificado en el archivo y en el metadato de la página.
2. **Cifras verificables habladas + ASR-recall medido:** transcripción real
   (1 llamada al endpoint file, DIARIZATION, 108,7 s):
   - "$5 million … 5 million dollars per 30 second spot" → verificado:
     Fox cobró $5–5,5M por 30 s en la Super Bowl LI 2017 (Variety via
     Sports Illustrated, NYT via Bleacher Report, AdAge, PPAI).
   - "The 60 second ad" (Anheuser-Busch "Born the Hard Way") → verificado:
     spot de 60 s (Fox Sports, Wikipedia, Sports Illustrated).
   - "Adolphus Busch traveling by boat from Germany to the US in the
     1850s … meeting fellow immigrant Eberhard Anheuser" → verificado
     (Fox Sports/AP: viaje de 1857; la narración dice "1850s").
   Recall: las tres cifras/entidades se transcribieron íntegras y con
   formato numérico ("$5 million", "30 second", "60 second", "1850s").
   Diarización: 5 turnos/etiquetas (narradora B + entrevistados + audio
   del anuncio C). Artefactos menores de file-decode (duplicaciones "be
   be", "literally literally") según caveat conocido del endpoint file.
3. **Dominio neutral y distinto del clip 1:** economía/negocios de medios
   (publicidad de la Super Bowl), frente a ciencia climática del clip 1.
4. **Sin políticos vivos:** narración sobre publicidad; personas nombradas:
   Adolphus Busch y Eberhard Anheuser (fallecidos s. XIX/XX). Marcas
   comerciales mencionadas (Anheuser-Busch): neutras para fact-checking.
5. **Licencia verificada desde la fuente:** términos VOA descargados (HTTP
   200) + hash; cero YouTube en el proceso (ni URLs, ni yt-dlp, ni
   linaje YouTube en este activo: descarga directa del CDN de VOA
   enlazado desde la página oficial).

## Transcripción de referencia (endpoint file, 2026-11-01)

> Um, that one's broken. With a mix of computer animation, big name
> celebrities and a taste for the absurd, this year's ads that will air
> during the Super Bowl are pulling out all the stops to get consumers to
> spend the almighty dollar. Can we lose the masks? Just take them off.
> How can we be be as secret […] How can we be a secret society if we
> can't keep all of our secrets? And the cost? It's a little more than
> last year, it always tends to go up, up, but it's I will call it
> $5 million 5 million dollars per 30 second spot. But does all all that
> money spent score a touchdown with consumers? there's always been an
> assumption that there's going to be a high demand for Super Bowl ads
> because this is a huge audience one of the biggest live audiences of
> the year however more companies have started stepping back […] is this
> really worth the money advertisers know they literally literally have
> seconds to score points with viewers. One anticipated ad is from beer
> maker Anheuser-Busch, the company says it was conceived last year but
> it may be more timely now. The 60 second ad shows the beer company's
> company's co-founder Adolphus Busch traveling by boat from Germany to
> the US in the 1850s and after a series of hardships meeting fellow
> immigrant Eberhard Anheuser. The ad's tagline when nothing stops your
> dream this is the beer we drink.

## Créditos (descarte de material de agencias)

- Página oficial: autor "VOA Learning English", editor "VOA - Voice of
  America English News"; sin menciones a AP/AFP/Reuters ni "courtesy".
- 3 fotogramas revisados (t=20/55/95 s): solo bugs "VOA Learning English"
  y "VOA" + subtítulos quemados en inglés que coinciden con la narración;
  sin marcas de agencias. SÍ se ven extractos de anuncios comerciales
  (motivo del caveat mixto).

## Finalistas descartados (máx. 3 evaluados a fondo + barridos)

1. `File:Energy By the Numbers - 2015 at the Department of Energy.webm`
   (Wikimedia Commons, CC BY 3.0, DOE/energía, con cifras).
   Descartado: linaje YouTube (`{{YouTube CC-BY}}`, `{{From YouTube}}`);
   viola el espíritu "nunca YouTube" del proyecto aunque la licencia sea
   limpia.
2. `File:At Thanksgiving, the Makings of a National Feast.webm`
   (Commons, VOA, PD-USGov, 3:58, cifras del Census: "248 million
   birds"…). Descartado: mismo linaje YouTube (video2commons) + temática
   estacional + 40 MB. Respaldo si el ganador falla.
3. DOE "Energy 101" directo en energy.gov — `energy.gov/eere/…` devuelve
   404 y solo quedan enlaces a YouTube. Inviable sin YouTube.
4. Barridos sin candidato: BLS (`bls.gov/videos/` → 403 bot-wall),
   EIA Energy Explained (sin vídeos descargables), NWS Weather-Ready
   (sin mp4 directos), NASA "energy budget" (sin piezas narradas con
   cifras), NOAA Ocean Today (sin mp4 directos), Internet Archive
   (duraciones/licencias poco fiables en el time-box).

Time-box búsqueda: ~55 min (dentro del máx. 60 min).

## Notas operativas

- **AVISO gitignore (sin editar, por instrucción):**
  `evidence-platform/.gitignore` ignora `data/` completo: el .mp4 y el
  `license_*.txt` de `data/clips/` quedan ignorados; ESTA provenance y el
  espejo de la licencia en `live/material/` sí se versionan.
- Coste: 1 transcribe file de 108,7 s → floor(108)/3600×$0,18 = **$0,0054**.

## Unresolved

- Estatus PD mixto (ver Caveat): los extractos de anuncios de terceros
  impiden certificar PD puro. Para un clip 100 % PD haría falta una
  producción íntegramente gubernamental sin insertos (p. ej. pieza de
  estudio VOA/NASA sin imágenes de terceros) — no encontrada en el
  time-box. Opción quirúrgica (no aplicada, para no alterar el hash):
  escindir los ~10 s de audio del anuncio (t≈24–38 s) y documentar un
  derivado solo-narración.
- No se visionó el mp4 fotograma a fotograma (3 frames revisados).
- La transcripción de referencia es del endpoint file (artefactos de
  duplicación conocidos); el path WS es el primario en producción.
