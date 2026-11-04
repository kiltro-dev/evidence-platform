# V7 — Conector oficial FRED (Federal Reserve Bank of St. Louis)

Fecha: 2026-11-04. Clave oficial `FRED_API_KEY` (solo lectura, en `.env`,
nunca en repo/logs). Sin scraping: solo `api.stlouisfed.org/fred`
(documentado, con key, robots `Allow: /`).

## 1. Qué se construyó

- **`live/swarm/fred.py`** (nuevo): tabla curada de 6 series
  (FEDFUNDS, DGS10, CPIAUCSL, UNRATE, GDPC1, PAYEMS) con matchers EN+ES
  por concepto explícito — "inflation" solo NO casa (podría ser PCE),
  "GDP" solo NO casa (podría ser nominal) —, constructor de URL
  acotada por año, parse/pick de observaciones, `source_meta` y
  `vintage`, lectura de clave y redacción de URLs.
- **`live/swarm/altroutes.py`**: pata FRED en `attempt_altroutes`
  (parámetro `get`, presupuesto compartido con BLS, max 2 llamadas).
  Sin seam GET, sin match o sin clave: skip registrado con 0 paquetes.
  Clave: bypass de caché (`use_cache=False`) + redacción de
  `url`/`final_url` antes de empaquetar.
- **`live/swarm/coordinator.py`**: `alt_lookup` pasa el seam GET
  (con fallback para doubles pre-V7).
- **`live/evidence/package.py`**: `source_meta_for_snapshot` acepta
  FRED (host + `series_id` curada en la URL code-built + bytes con
  forma observations). Sin esto la pata decidía 0 (cierra el "gap
  documentado" de V5-CATB para FRED; BLS sigue inerte).

## 2. Decisión de diseño: cita solo-valor

La cita FRED es `"value":"4.2"` (span verbatim), NO el par
fecha+valor. Motivo medido: `"date":"2026-09-01"` inyecta candidatos
numéricos vivos (9, 1) en el closest-match del verificador y una
afirmación de "1 percent" casaría falsamente con el DÍA. La vigencia
viaja en `source_year`/`vintage` + snapshot, nunca en la cita.
Pin: `test_quote_has_single_numeric_candidate`.

## 3. Medición (1 corrida dev + corpus-b + FOMC, FRED_API_KEY real)

Corridas: `data/runs/v7fred-dev-atlas-run1.*` (dev 36),
`v7fred-corpusb-{en,es}.*` (22+20 = mismos 42 b de V5-CATB, §§
reconstruidos por filtro categoría+checkable−E1; corrida en 2 slices
por idioma con idéntico semáforo 8 — método declarado),
`data/fomc/v7fred-verify-fomc.*` (5).

| conjunto | antes | después | exactitud (Wilson95) | falsos |
|---|---|---|---|---|
| dev 36 | 26/36 r 0.722 | **27/36 r 0.750** [0.589,0.862] | 27/27 1.000 [0.875,1.000] | 0 C + 0 S |
| dev-b 6 | 0/6 | **3/6** | 3/3 | 0 |
| corpus-b 42 | 1/42 (0/1 correctos, 1 falso-C P1-17) | **2/42 r 0.048** [0.013,0.158] | 2/2 1.000 [0.342,1.000] | 0 C + 0 S |
| FOMC 5 | 5xUnverifiable | **5xUnverifiable** (estable) | n/a | 0 |

Por afirmación (decididas):

- Dev-b: **B3 TRUE→supported (FRED UNRATE code-path ✓)**,
  **B4 FALSE→contradicted (FRED code-path ✓)**,
  B2 FALSE→contradicted **vía model-path Wikipedia** ("Economic impact
  of the 2026 Iran war", quote "growth rate to 3%") — correcta por
  score pero evidencia débil/fuera de tema (mismo patrón P1-17-en de
  V5-CATB); NO es victoria FRED. B1/B5/B6 abstienen (IMF sin ruta;
  niveles BLS sin pata FRED equivalente).
- Corpus-b: K2-38-en TRUE→supported + K1-23-en FALSE→contradicted,
  ambas code-path FRED UNRATE 4.2 Sep-2026 ✓. P1-17-en (el falso-C
  previo) hoy abstiene (varianza model-path). ES 0/20 (IMF/WMO/UN,
  fuera de tabla FRED).
- Dev-d 4/6→2/6: D3/D6 pasan a abstención (varianza model-path;
  probado: FRED no dispara en sus textos). Neto dev +1.
- FOMC: FRED dispara 0/5 por diseño (target range ≠ effective rate;
  IORB/RRP/discount fuera de tabla) — chequeo de estabilidad puro.

Coste/latencia V7 (1 corrida, varianza alta, NO sustituye pins):
dev $0.4423 ($0.0123/claim) p50 24.6s/p95 66.7s;
corpus-b EN $0.2402 + ES $0.2348 = $0.4751; FOMC $0.0364.
Total medición V7 ≈ **$0.99** (sondas FRED $0).

## 4. Registro y pins

- `live/eval2/scores/dev-atlas.json` y los claims `dev_recall`,
  `decided_accuracy`, `latency_cost` **NO se tocan**: la corrida V7
  cae fuera de la banda publicada (varianza de rondas-modelo, no
  FRED) y la banda existe para eso. Los pins V5-1 siguen vigentes;
  V7 se reporta como experimento separado (este doc).
- `tests_live`: 1073+9 → **1085+10** (1095 colectados; +13 tests V7,
  1 live-skip). `tests_v1` intacto 755.
- `scripts/render_metrics.py` + `scripts/check_claims.py` re-verdes.

## 5. Higiene verificada

- Clave ausente en db/jsonl/summary/snapshots de las 4 corridas
  (grep del valor, todo limpio); URLs persistidas con
  `api_key=key-redacted`.
- Presupuesto por afirmación intacto (FRED comparte el cap 2 con
  BLS; pin `test_budget_shared_with_bls`).
- Suites: live 1085+10skip, v1 755, `check_claims` OK.

## 6. Límites declarados

- FRED cubre 6 series EEUU; IMF, niveles BLS-miles, WMO/NOAA/UN y
  todo lo no-estadounidense siguen sin ruta (b ES 0/20).
- B2 muestra que el model-path puede decidir con evidencia débil:
  el score la cuenta correcta; este doc no la reclama.
- 1 corrida por conjunto (retención sellada intacta, no tocada).
