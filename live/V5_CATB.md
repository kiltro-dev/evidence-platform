# V5-CATB — Rutas alternativas oficiales para categoría b (Atlas Live)

Rama main, checkout compartido. Alcance: `live/swarm/altroutes.py` (nuevo),
`live/swarm/fetch.py` + `live/swarm/coordinator.py` (edits mínimos,
aditivos), `live/tests/test_v5_catb.py` + fixtures grabados, este informe.
Prohibido: git ops, secretos, abrir `retention.json`, saltar
paywalls/logins, ignorar robots.txt o términos.

## 1. Punto de partida

Categoría b = claims de época 2026 (IMF WEO oct-2026, BLS Employment
Situation sep-2026, más WMO/NOAA/UN en corpus): **0/42 corpus (S4)** y
**0/6 dev** (F2-1 atlas r1+r2; re-confirmado en main actual por
`data/runs/v52b-dev-atlas-run1.json`: b 0/6, global 26/36, recall 0,722).
Causa documentada: las primarias botan a los bots con 403 (Akamai) y no
hay grounding keyless para época reciente.

## 2. Verificación de rutas (sondas en vivo 2026-11-02, UA atlas-live-research)

Cada sonda = 1 request cortés por URL (crawl-delay respetado en
catalog.data.gov). Resultado: **ninguna ruta keyless + permitida por
robots/términos + verificable por el verificador actual existe para
IMF/BLS**. Tabla:

| # | Ruta | Keyless | Robots/términos | Veredicto | Evidencia |
|---|---|---|---|---|---|
| 1 | FRED JSON API | NO | n/a | NO-VIABLE | `400 Variable api_key is not set` (api.stlouisfed.org) |
| 2 | FRED CSV download | SÍ (sin key) | stall selectivo | NO-VIABLE aquí | homepage + CSV = 0 bytes/timeout (urllib y curl); TLS+DNS OK y api.stlouisfed.org responde → mitigación anti-bot, no red |
| 3 | BLS API v2 POST | **SÍ** | **Disallow: /** | NO-VIABLE (robots) | `REQUEST_SUCCEEDED` sin key + cifras exactas (LNS14000000 M09=4.2, LNS13000000 M09=7109); pero `api.bls.gov/robots.txt` = `User-agent: * / Disallow: /` (200) → el contrato del repo rehúsa |
| 4 | BLS www/download (páginas, empsit.pdf, flat files) | SÍ | 403 bot-wall | NO-VIABLE | empsit.pdf 403, flat-file range 403, robots.txt 403 (página BLS: "bot activity that doesn't conform to BLS usage policy is prohibited") |
| 5 | BEA API | NO | key-gated | NO-VIABLE | sin key → 200 vacío; key falsa → `Invalid API UserId` |
| 6 | Census API | **NO (ya no)** | Missing Key | NO-VIABLE | keyless → 302 → página `Missing Key` (y no trae series BLS/IMF) |
| 7 | IMF imf.org (WEO, DataMapper) | SÍ | 403 bot-wall | NO-VIABLE | WEO oct-2026 403 + DataMapper 403 (Akamai EdgeSuite) |
| 8 | IMF SDMX | ? | n/a | NO-VIABLE | host legacy NXDOMAIN; api.imf.org → 404/502 en rutas documentadas; sin ruta WEO keyless hallada |
| 9 | IMF eLibrary | parcial | 403 en contenido | NO-VIABLE | home 200 + robots permisivo, pero `/search` y `/display/book/<ISBN-WEO>` (ISBN vía Crossref 9798229055079) → 403 |
| 10 | data.gov | SÍ | catálogo migrado | NO-VIABLE | CKAN `/api/3/action` → `Not Found`; search HTML 404/301; sin URL de dataset WEO/BLS-sep-2026 descubierta |
| 11 | NBER | SÍ | abierto (200) | NO-VIABLE | alcance = papers/lecturas/microdata; sin espejo WEO/empsit-headline |
| 12 | WMO/NOAA/UN (solo corpus) | SÍ | abiertos | FUERA DE ALCANCE | 9/10 claims bloqueados por claim-side (`claim_unit_unknown`: "degrees" pelado, "parts per million", "billion" pelado) — lado verifier/units; S2-04 solo necesitaría URL-discovery (no intentado) |

Control: `api.worldbank.org/robots.txt` → 404 (sin política → permitido),
que confirma que el `Disallow: /` explícito de BLS es deliberado.

Nota BLS-API/robots (decisión documentada): el endpoint es POST+JSON con
tier keyless documentado; el `Disallow: /` es higiene anti-crawl. Aun
así, el contrato del repo es mecánico (`can_fetch` falso → `FetchBlocked`)
y la tarea prohíbe ignorar robots.txt, así que la ruta se implementa
COMPLETA pero la política la rehúsa en vivo (0 POST enviados tras calentar
caché; probado en vivo §5). Una futura excepción para APIs documentadas
es decisión de los dueños del repo, no de este cambio.

## 3. Qué se implementó (general, sin reglas por caso)

`live/swarm/altroutes.py` (nuevo, ~650 líneas, 0 coste modelo, offline):

- **Registro** `ROUTES` (viable/no-viable + motivo por ruta) y
  `route_status()`. Las rutas muertas por 403 se registran como
  intentadas-saltadas SIN enviar paquetes (cortesía).
- **Matchers puros** EN+ES (nunca ids): `match_bls` (BLS/unemploy-/
  desempel-/paro/parados), `match_imf` (IMF/FMI/WEO u outlook+tema),
  `is_blike_claim`, `match_bls_series` (rate→LNS14000000,
  level→LNS13000000; IMF nunca prueba BLS). Cobertura medida sobre
  516 claims (dev+corpus): disparan los 38 b IMF/BLS (incl. 6/6 dev-b);
  misses = 5 E1-earnings (excluidos de scoring) + 10 WMO/NOAA/UN
  (fuera de alcance, §2.12); 4 fuegos fuera de b (unemployment-2024,
  K1-21/22/33, K2-40: rehúsan con 0 paquetes; pineado en tests).
- **Ruta BLS API**: `bls_payload` exacto, `parse_bls_observations`,
  `pick_observation` (periodo pedido o `latest`), `bls_quote_for`
  (span JSON verbatim por construcción), `bls_source_meta`
  (`{indicator, unit, scale}` con scale ×1000 en nivel),
  `bls_vintage` (YYYY-MM → `source_year` + título; el esquema no tiene
  campo vintage). `attempt_altroutes` con presupuesto
  `MAX_ALT_CALLS_PER_CLAIM=2`, nunca lanza.
- **Pata PDF**: `ingest_open_pdf` (fetch con política → snapshot +
  sidecar `data/snap/{sha}.pdftxt`), `pdf_quote_exists` (check
  determinista quote⊆texto archivado, WS-normalizado), `reader=`
  como seam para futuro vision-PDF (ningún cliente in-repo acepta
  PDFs hoy: `MetaClient` solo `complete_json`; vision existente es de
  imágenes). Sin uso en vivo: no existe PDF primario abierto para b.

Edits mínimos (aditivos, 0 cambios de comportamiento por defecto):

- `live/swarm/fetch.py`: `fetch_post()` (~60 líneas) — misma política
  que GET (robots ANTES de enviar, 401/402/403/429→`FetchBlocked`,
  tope 2 MB, snapshot content-addressed; sin caché: vintage + budget).
- `live/swarm/coordinator.py`: import `fetch_post`; campo
  `SwarmConfig.post_fn`; `build_package(..., source_meta_override=None)`
  (None = derivación histórica byte-a-byte); `_Tools.alt_lookup`
  (determinista, llamado por grounding — NO es tool de RECUPERADOR:
  `roles.py` intacto y el pin de 4 tools sigue verde);
  `_maybe_add_altroutes` (si b-like y 0 paquetes tras grounding);
  hook de 2 líneas en `_ground_claim`. No-b-like = 0 llamadas,
  comportamiento idéntico.

Brecha documentada (lado verifier/package, fuera de alcance): el
verificador re-deriva `source_meta` SOLO para `api.worldbank.org`
(`check.py`), así que meta BLS manual → `unit_mismatch` (abstención
segura, jamás falso: probado que no mueve veredictos). Sin
`package.py`/`check.py` (propiedad de otro equipo / prohibido tocar),
ninguna cita estructurada sin unidades puede decidir. Idem PDF: el
verificador lee bytes del snapshot, nunca el sidecar.

## 4. Tests (offline, $0)

`live/tests/test_v5_catb.py`: **73 passed**. Fixtures grabados en
`live/fixtures/`: respuestas BLS reales (`v5_catb_bls_LNS14000000_2026.json`
M09=4.2, `v5_catb_bls_LNS13000000_2026.json` M09=7109; 2 POST corteses
bajo el límite 25/día) + PDF mínimo sin comprimir. Cobertura: matchers
sintéticos + pins de cobertura (516 claims), payload/parse/quote/meta/
vintage sobre fixtures, robotsificación (rehúso sin enviar nada, sin
snapshot, sin paquete), presupuesto ≤2, `fetch_post` (robots/403/tope/
snapshot), PDF (check exacto, sidecar, seam reader, walled→raise),
override de `build_package` (válido/inválido/defecto), plumb
(`alt_lookup`, `_maybe_add_altroutes` incl. never-raise).
Suite completa `live/tests/`: **964 passed + 4 skipped** (958+6; 0
regresiones vs main).

## 5. Medición (1 corrida dev + bench corpus-b)

- **Dev después** (`data/runs/v5catb-dev-*`, atlas 1 run, 36 claims):
  b **0/6** (antes 0/6, v52b) → global **26/36, recall 0,722**,
  exactitud 1,000 Wilson95 **[0,871, 1,000]**, **0 falsos-C + 0 falsos-S**,
  $0,2547 ($0,0071/claim), p50 4,9 s / p95 42,7 s. Por categoría:
  a 5/6, b 0/6, c 6/6, d 4/6, e 5/6, f 6/6.
- **Guard**: recall 0,722 ≥ 0,55 ✓ + 0 falsos-C ✓ → SE MANTIENE (no revierte).
- **Prueba de plumb en vivo**: `attempt_altroutes`+`fetch_post` reales →
  BLS `calls=1, refused FetchBlocked, 0 snapshots`; IMF `calls=0,
  skipped`. El plumb corre; la política rehúsa; nada se archiva.
- **Corpus-b** (`data/runs/v5catb-corpusb-run1.json`, atlas 1 run,
  42 claims): **1/42 decididos (recall 0,024)**, 0/1 correctos,
  exactitud 0,000 Wilson95 **[0,000, 0,794]**, 1 falso-C (P1-17-en:
  TRUE "IMF ... 3.1 percent" → contradicted por 2 paquetes
  `extractor=model` de Wikipedia "growth rate to 3% for 2026",
  rel-diff 3,3% > tolerancia 2% — vía model-path preexistente, NO
  altroutes: ese texto rutea `series=[]`, 0 llamadas, 0 docs, 0
  paquetes altroutes — probado), 0 falsos-S, $0,6143 ($0,0146/claim),
  p50 38,0 s / p95 91,7 s. Nota: b quema latencia (el recuperador gira
  sobre claims sin grounding); altroutes añade ~0 s (rehúso local /
  skip sin paquetes).

## 6. Límite declarado (iteración completa, b sigue 0)

b dev 0/6→0/6 y corpus-b 0/42 (S4) →1/42 (0 correctos; el 1 es un
falso-C del model-path preexistente, §5) tras 1 iteración completa.
Causas raíz (medidas, no supuestas):

1. **IMF**: todo `imf.org` + eLibrary-contenido botan bots (403); sin
   API keyless hallada (SDMX 404/502, legacy NXDOMAIN). Sin transporte
   permitido no hay bytes; sin bytes no hay paquetes.
2. **BLS**: la API keyless FUNCIONA y trae las cifras exactas, pero su
   `robots.txt` (`Disallow: /`) la veta bajo el contrato del repo; el
   resto de BLS (www/download/PDF/flat files) da 403.
3. **Estructural**: aun con bytes, las citas JSON/CSV sin unidades
   necesitan `source_meta` consumible por el verificador, que hoy solo
   re-deriva para World Bank (cambio `package.py`+`check.py`, fuera de
   alcance). Los claims WMO/NOAA/UN necesitan parser de unidades
   (`units.py`, fuera de alcance).

## 7. Unresolved / follow-ups (para otros equipos)

- (verifier) Consumir `source_meta` re-derivable para BLS (o un
  `BLS_SERIES_UNITS` curado espejo de `WB_INDICATOR_UNITS`) + candidato
  de texto extraído para PDFs comprimidos.
- (units) `claim_unit_unknown` en "degrees" pelado, "parts per million",
  "billion" pelado (10 corpus-b).
- (repo-owners) Política para APIs POST documentadas con tier keyless
  vs `robots.txt` anti-crawl (el seam `fetch_post` ya existe).
- (retrieval) data.gov si el catálogo expone búsqueda estable; NBER si
  publica espejos headline (hoy no).

Coste total live: **$0,2547 (dev) + $0,6143 (corpus-b) + $0 (sondas)** =
**$0,8690 ≤ $5** ✓.

Archivos: nuevo `live/swarm/altroutes.py`, `live/tests/test_v5_catb.py`,
`live/V5_CATB.md`, fixtures `live/fixtures/v5_catb_*` (2 JSON + 1 PDF);
edits `live/swarm/fetch.py` (+`fetch_post`), `live/swarm/coordinator.py`
(+post_fn/override/alt_lookup/fallback/hook). Sin cambios en
verifier/package/roles/library.
