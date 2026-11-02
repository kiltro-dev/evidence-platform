# PROVENANCE — FOMC 28-oct-2026 real (Atlas Live V5-G2)

Decisión: **caso real, fuente federal primaria, dominio público EE. UU.**

## Documentos fuente

### 1. Comunicado FOMC (statement)

- **Título:** Federal Reserve issues FOMC statement (October 28, 2026,
  for release at 2:00 p.m. EDT)
- **Fuente:** Board of Governors of the Federal Reserve System
  (gobierno federal EE. UU.)
- **URL exacta:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm
- **HTTP:** 200, `last-modified: Wed, 28 Oct 2026 18:00:18 GMT`
- **Licencia:** obra del gobierno federal de EE. UU., **dominio público**
  (17 U.S.C. § 105). Sin material de terceros: texto institucional
  propio del Board.
- **Fecha acceso:** 2026-11-02
- **Archivo local:** `data/fomc/statement_20261028.htm`
- **Cabeceras guardadas:** `data/fomc/statement_headers.txt`
- **Tamaño:** 83.055 bytes
- **SHA256:** `e88e59613b54da64e82fdc05a8193cc1a17ce17c9dd319593002d87c5241439c`

### 2. Nota de implementación (Implementation Note)

- **Título:** Implementation Note issued October 28, 2026 —
  Decisions Regarding Monetary Policy Implementation
- **Fuente:** Board of Governors of the Federal Reserve System
- **URL exacta:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a1.htm
  (enlazada desde el comunicado: `<a href="/newsevents/pressreleases/monetary20261028a1.htm">`)
- **HTTP:** 200, `last-modified: Wed, 28 Oct 2026 18:00:18 GMT`
- **Licencia:** igual que (1), dominio público federal.
- **Fecha acceso:** 2026-11-02
- **Archivo local:** `data/fomc/implnote_20261028.htm`
- **Cabeceras guardadas:** `data/fomc/implnote_headers.txt`
- **Tamaño:** 79.046 bytes
- **SHA256:** `cdb328488e81c0ac680a10430ae82ec7b6113520833ac79048a3d749a39b8354`

### 3. SEP / proyecciones: NO disponibles para esta reunión

- `https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20261028.htm`
  → **HTTP 404** (probeta 2026-11-02). El SEP es trimestral
  (mar/jun/sep/dic); octubre no lo trae.
- Control de patrón: `.../fomcprojtabl20260916.htm` → **HTTP 200**
  (241.407 bytes), luego el patrón URL es válido y la ausencia de
  octubre es real, no un error de construcción.
- Conforme al brief, al traer el comunicado cifras verificables
  externamente, no se usan proyecciones SEP.

## Cortesía de acceso (robots / rate)

- `https://www.federalreserve.gov/robots.txt` → devuelve la página
  HTML genérica "Page not Found" (soft-404): **no hay reglas robots
  publicadas**; se procedió con cortesía de todos modos.
- Total: **6 peticiones** con UA `AtlasLive-research/1.0
  (fact-check research)` y pausas de 2–3 s entre ellas: robots,
  2 sondas HEAD (statement 200 / SEP-oct 404), 2 descargas GET,
  1 sonda control SEP-sep (200). Cero YouTube en todo el proceso.

## Afirmaciones numéricas REALES extraídas (verbatim + claim)

| # | Texto Fed (verbatim) | Claim run_verify | Figura líder |
|---|---|---|---|
| 1 | "maintain the target range for the federal funds rate at 3-3/4 to 4 percent" | FOMC-RANGE: top of 4 percent, Oct 2026 | 4 percent |
| 2 | "maintain the interest rate paid on reserve balances at 3.90 percent, effective October 29, 2026" | FOMC-IORB: 3.90 percent | 3.90 percent |
| 3 | "offering rate of 3.75 percent and with a per-counterparty limit of $160 billion per day" | FOMC-RRP: 160 billion dollars/day | $160 billion |
| 4 | "approve the establishment of the primary credit rate at the existing level of 4.0 percent" | FOMC-DISCOUNT: 4.0 percent | 4.0 percent |
| 5 | "a timely return of inflation to the Committee's 2 percent goal" | FOMC-GOAL: 2 percent goal | 2 percent |

Fichero de claims: `data/fomc/truth_fomc.json` (5 filas, lang en,
expected true, con campo `ref` al verbatim). Veredicto `expected`:
true en las 5 — son citas fieles del primario.

### Consideradas y descartadas (motivo medido, no gusto)

- **Voto 10–2** ("approved … by a 10 – 2 vote"): el parser de
  cantidades no tiene unidad para "votes" (`(10.0, None)`), luego
  `claim_unit_unknown` forzaría `unverifiable` por construcción.
- **Disidencia +1/4 punto** ("preferred to raise … by 1/4 percentage
  point"): la fracción `1/4` no la parsea ni el lado claim ni el lado
  evidencia (`(1.0, None)`, `(4.0, 'pp')` — el 4 es denominador, no
  figura); verificarla sería ruidoso.
- **Suelo 3.75 del rango:** plegado en FOMC-RANGE liderando con el
  techo (4 %): el "3-3/4" del Fed parsea a doses unitless
  (`(3.0, None)` × 2) y un claim liderado por 3.75 fallaría contra
  citas verbatim del propio Fed (solo candidata percent: 4.0).

## Corrida live (`run_verify`, semáforo 8)

- Comando: `.venv/bin/python live/run_verify.py --claims
  data/fomc/truth_fomc.json --lang en --log data/fomc/verify-fomc.jsonl
  --max-concurrency 8` (2026-11-02)
- Log: `data/fomc/verify-fomc.jsonl` (35 eventos) + `.summary.json` +
  `.db`. Wall 27,5 s, latencias p50/p95 26,6/27,5 s.
- **Veredictos (los que salieron): 5 × `unverifiable`
  (`no_usable_package`), 0 paquetes en los 5 claims.**
- Causa raíz (medida, $0): el descubrimiento web genérico está
  degradado en este sandbox — DuckDuckGo inalcanzable
  (`curl` sale 000, `_ddg_search` siempre `[]`) y la búsqueda
  Wikipedia-only no devuelve páginas con las cifras de oct-2026;
  las páginas del Fed SÍ son descargables (`_http_get_text` 83.049
  chars) pero el swarm no tiene camino de descubrimiento hasta
  ellas. Consistente con corridas previas: claims no-WB
  (p. ej. F2D-A3 física) también rinden 0 paquetes.
- No se reintentó: un segundo run gastaría USD por el mismo
  resultado; el brief pide reportar los veredictos sean cuales sean.
- Coste: **$0,0372** (13.342 prompt + 4.819 completion tokens,
  10 model calls). Presupuesto V5-G2: ≤ $3 → OK.

## Exportes del caso (`live/report`, source=real)

| Formato | Archivo | Notas |
|---|---|---|
| HTML | `data/fomc/fomc.html` | auto-contenido, badge REAL SOURCE |
| JSON | `data/fomc/fomc.json` | máquina, `source: real`, 5 claims |
| ZIP | `data/fomc/fomc_bundle.zip` | bundle M5, verifica offline |

- `verify_bundle.py fomc_bundle.zip --engine stdlib` → **ALL PASS 5/5**.
- `verify_bundle.py fomc_bundle.zip --engine live` → **ALL PASS 5/5**.
- PDF: no se genera (decisión documentada en `live/V5_REPORT.md`).

## Notas operativas

- **AVISO gitignore (sin editar, por instrucción):**
  `evidence-platform/.gitignore` ignora `data/` completo: los .htm,
  cabeceras, truth, log, db, summary y exportes de `data/fomc/`
  quedan ignorados; ESTA provenance (con URLs + SHAs) sí se versiona
  y basta para re-descargar y reproducir.
- Cero secretos tocados; cero git ops; cero deps pip nuevas;
  cero YouTube.

## Unresolved

- Recuperación genérica: sin DDG, Atlas Live solo verifica claims
  con API estructurada (World Bank) o suerte en Wikipedia; noticias
  frescas (FOMC) rinden 0 paquetes. Veredicto honesto, cobertura pobre.
- El parser no cubre votos ("10 votes" → unidad None) ni fracciones
  ("1/4", "3-3/4"): dos clases de cifras Fed quedan fuera por
  construcción. Propuesta (no hecha, fuera de alcance): alias
  vote→count y fracciones vulgares/mixtas en `units.py`.
- No se descargó el SEP de septiembre (fuera del caso octubre);
  solo se probó su URL como control de patrón (200).
