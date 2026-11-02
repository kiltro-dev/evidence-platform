# V5-G2 — Informe exportable + Atlas Live sobre el FOMC real

Rama main, checkout compartido. Alcance: crear `live/report/`
(export HTML+JSON+ZIP), `live/tests/test_v5_report.py`, este doc y
`live/material/PROVENANCE_FOMC.md`. Respetado: cero git ops,
cero secretos, cero deps pip nuevas, cero YouTube, live ≤ $3
(gastado: **$0,0372**). No se editó ningún otro archivo.

## 1. Exportador (`live/report/export.py`, stdlib + reuso)

Entrada: un run `run_verify` — `run.jsonl` (eventos `claim.state` /
`claim.evidence` / `claim.verdict`) + hermano `.summary.json`
(coste, latencias) + fichero de claims opcional (textos y
`expected`; sin él, textos de las notas `escuchada` y
`expected=None`).

- `load_run(run_log, summary_path?, claims_path?, lang?)` → dict
  reporte: claims (texto, expected, veredicto, reasons, paquetes
  completos, latencia) + summary + Wilson
  (`live/auditor/wilson.summarize`, accuracy sobre decided +
  intervalo 95%).
- `export_json(...)` → JSON máquina (mismo contenido + provenance +
  totales). `export_html(...)` → **un solo archivo auto-contenido**
  (CSS inline, sin JS, sin refs externas): badge de fuente,
  tabla de veredictos con chips de color, panel Wilson +
  coste/latencia, detalle por claim (tabla de paquetes con
  url/sha12/año/cita + línea receipt `veredicto;reasons;packages`)
  y bloque provenance. Render con `string.Template` + `html.escape`
  (solo stdlib). Ambos aceptan `created_at` para salidas
  deterministas (goldens).
- `export_bundle_for_run(...)` → ZIP: **reutiliza**
  `live.bundle.export.export_bundle` (no reimplementa); las
  numéricas del claim (que no están en run.jsonl) vienen de
  `claim_rows` explícitas o del claims-file vía
  `run_verify.claim_input_for`. `export_all(...)` → los tres.
- `source`: `"real"` (badge verde REAL SOURCE) o `"synthetic"`
  (badge ámbar SYNTHETIC); otro valor → `ValueError`.

## 2. Decisión PDF: no se genera

`SUPPORTED_FORMATS = ("html", "json", "zip")`. Motivo
(`PDF_DECISION` en el módulo): reportlab explícitamente prohibido,
fpdf no instalado y prohibidas nuevas deps pip, y sin navegador
headless garantizado (chromium-headless-print no disponible) —
no hay escritor PDF sin-deps. En esta máquina existe un binario
weasyprint, pero no es dependencia declarada del proyecto y usarlo
haría el export no hermético. El HTML auto-contenido es imprimible
a PDF desde cualquier navegador; el ZIP es el formato verificable
offline (`verify_bundle.py --engine stdlib`).

## 3. Caso real: FOMC 28-oct-2026

Fuentes (detalle + hashes en `live/material/PROVENANCE_FOMC.md`):

- Comunicado:
  `https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm`
  (200, 83.055 B, sha256 `e88e5961…c1439c`)
- Nota de implementación:
  `https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a1.htm`
  (200, 79.046 B, sha256 `cdb32848…9b8354`)
- SEP: no existe para octubre (URL trimestral → 404; control
  septiembre → 200). Acceso cortés: 6 peticiones, UA de
  investigación, pausas; sin robots.txt publicado (soft-404).

Claims (`data/fomc/truth_fomc.json`, expected true, refs verbatim):

| id | claim | figura |
|---|---|---|
| FOMC-RANGE | techo del rango fed funds, oct 2026 | 4 % (rango 3-3/4–4 %) |
| FOMC-IORB | interés sobre reservas, efec. 29-oct | 3.90 % |
| FOMC-RRP | tope ON-RRP por contraparte/día | $160 B |
| FOMC-DISCOUNT | tasa de crédito primario | 4.0 % |
| FOMC-GOAL | objetivo de inflación | 2 % |

Descartadas con motivo medido: voto 10–2 (parser sin unidad
"votes" → `claim_unit_unknown` forzado) y disidencia +1/4 pp
(fracción `1/4` no parseable en ningún lado).

Corrida: `run_verify --claims data/fomc/truth_fomc.json --lang en
--log data/fomc/verify-fomc.jsonl --max-concurrency 8` (semáforo 8,
wall 27,5 s). **Veredictos: 5 × `unverifiable`
(`no_usable_package`), 0 paquetes.** Causa medida: descubrimiento
web degradado en el sandbox (DuckDuckGo inalcanzable, exit 000;
solo Wikipedia, sin cifras oct-2026). Las páginas del Fed sí son
fetchables pero el swarm no las descubre. Sin reintento (mismo
resultado esperado). Coste **$0,0372**.

Exportes (`data/fomc/`, ignorado; `source=real`): `fomc.html`
(badge REAL), `fomc.json`, `fomc_bundle.zip` →
`verify_bundle` **ALL PASS 5/5** en motores stdlib y live, offline.

## 4. Tests (`live/tests/test_v5_report.py`, offline, $0)

- Goldens byte-exactos (normalizando tmp-path) de HTML y JSON
  sobre fixture de 2 claims (supported + contradicted, Wilson
  [34.2%, 100.0%], receipts, provenance).
- HTML auto-contenido (sin script/link/import), badge real/synthetic
  + `ValueError`, escape de texto hostil, formatos + decisión PDF,
  fallback a notas sin claims-file, error sin numéricas.
- Roundtrip hermético de bundle en tmp (snapshot + 2 paquetes) →
  PASS en ambos motores, offline.
- Provenance FOMC completa (URLs, SHAs, fecha, SEP, robots, cifras;
  re-hash condicional de los .htm) + `verify_bundle` del ZIP real
  del caso (salta si `data/fomc/` ausente en checkout fresco).

## 5. Unresolved

- Cobertura retrieval: sin DDG, lo fresco/no-WB rinde 0 paquetes.
  El informe lo declara en vez de disimularlo (`retrieval_note`).
- Parser: sin unidad voto ni fracciones (`1/4`, `3-3/4`) — dos
  clases de cifras Fed inverificables por construcción.
- El ZIP del caso FOMC, al tener 0 paquetes, verifica la
  reproducción del `unverifiable`, no un apoyo positivo; el apoyo
  positivo del bundle queda cubierto por el roundtrip hermético.
- Artefactos `data/fomc/` ignorados por git: quien clone fresco
  debe re-descargar (la provenance trae URLs+SHAs) y re-correr.
