# M2b — Enjambre de investigación Atlas Live

Swarm de investigación por claim: 3 subagentes Muse Spark (chat
completions) + grounding determinista, con snapshots inmutables y
validación literal de quotes por código. Nunca inventa quotes ni
snapshots: sin red/modelo el claim sale con cero paquetes (M2a lo
marcará `unverifiable`).

## Archivos (6, todos nuevos)

| Archivo | Rol |
|---|---|
| `live/swarm/__init__.py` | Re-exports del paquete |
| `live/swarm/fetch.py` | Fetch responsable + snapshots `data/snap/{sha}.html` |
| `live/swarm/roles.py` | Prompts ESTRUCTURADOR / RECUPERADOR / REDTEAM + specs de tools |
| `live/swarm/coordinator.py` | Orquestación asyncio, tools, validación, eventos bus |
| `live/tests/test_m2b_swarm.py` | 21 tests offline (doubles) + 1 test LIVE (gated) |
| `live/M2B.md` | Este reporte |

## Pipeline por claim

1. **ESTRUCTURADOR** (1 llamada estructurada): claim → entidades,
   `indicator_candidate` (código WB), geo ISO3, año, `web_query`.
   Código rellena huecos: `match_indicator` + `resolve_country` +
   regex de año (cues world/global/planet → `WLD`).
2. **Grounding determinista**: `wb_lookup` (URL API World Bank →
   snapshot JSON) + `web_search` (Wikipedia API + DuckDuckGo HTML,
   keyless) + fetch de los top 2.
3. **RECUPERADOR** (tool loop, máx 4 rondas): el modelo puede llamar
   `web_search` / `fetch_url` / `wb_lookup` / `extra_lookup`
   (Fred/OWID/Wikidata vía `src/atlas/connectors/extra.py`, sin
   modificarlo); los top hits de `web_search` se fetchean
   automáticamente para que cite snapshots, no listados. Termina con
   `final.findings: [{url, quote}]`.
4. **Validación por código** (no por confianza): cada finding requiere
   URL de un doc realmente fetcheado + quote **substring exacto** del
   snapshot (`text.find`). Offsets = código. Lo que no grepea se
   descarta. Dicts compatibles con `EvidencePackage` de M2a
   (`live/evidence/package.py`), verificados con `from_dict` cuando
   M2a es importable: `source_type` primary (hosts estadísticos
   oficiales) / secondary, `numbers[]` como `{value, unit,
   scale_raw}` con escalas million/billion/trillion aplicadas,
   `extractor="model"`, `offsets=[start,end]`, `snapshot_path`
   relativo `data/snap/{sha}.html`.
5. **REDTEAM** (1 llamada): propone `counter_query` o `skip` →
   segunda ronda de retrieval + recuperador (máx 2 rondas).
6. **Eventos bus**: `claim.state=investigando` al inicio (con
   bootstrap `escuchada` si el detector no vio el claim) +
   exactamente un `claim.evidence` por claim (`packages`, `degraded`,
   `note`, `cost_usd`, `usage`).

## Límites

- Paralelismo: semáforo 4. Deadline 90 s/claim (entrega parcial:
  paquetes ya validados). Presupuesto 12000 tokens/claim.
- `fetch.py`: solo http(s), robots.txt (`urllib.robotparser`,
  UA `atlas-live-research`), timeout 15 s, tope 2 MB, rechaza
  401/402/403/429 y marcadores fuertes de paywall/login; snapshot
  atómico content-addressed + `captured_at` UTC.
- Robustez live observada: el modelo a veces antepone texto conversacional al
  JSON pese a `json_schema` estricto → cada llamada tiene **1 retry** ante
  reply malformada (solo `ValueError`/`empty content`, nunca transporte/400).
  Además los schemas estrictos exigen `required` = todas las keys y
  `additionalProperties: false` en cada nodo (el API lo impone con 400).

## Tests

- `pytest live/tests/test_m2b_swarm.py -m "not live"`: **21 passed**,
  0 llamadas live (FakeLLM/FakeSearch/FakeFetch): contrato fetch
  (scheme/robots/tamaño/paywall/snapshot), specs tools, números,
  `source_type`, happy path con validación M2a real, quote inventada →
  0 paquetes, URL no fetcheada descartada, sin modelo → degraded,
  tool loop, redteam segunda ronda, deadline, presupuesto, retry,
  `run_swarm` paralelo.
- LIVE gated (`ATLAS_LIVE=1`, `@pytest.mark.live`):
  `test_live_three_true_claims` — coordinator real sobre 3 claims
  TRUE EN con verdad WB conocida (T1-en mundo, T2-en China, T9-en
  Brasil); exige ≥2 con quote literal que grepea en su snapshot y
  costo ≤ $6.

## Resultado LIVE (medido, no estimado)

| Claim | Paquetes | Quote (extracto) | Snapshot greps | Costo |
|---|---|---|---|---|
| T1-en (mundo 8.14B) | 1–2 | `"date":"2024","value":8140897523` (WB API) | sí | — |
| T2-en (China 1.41B) | 1–2 | `"date":"2024","value":1408975000` (WB API) | sí | — |
| T9-en (Brasil 212M) | 0–1 | `"date":"2024","value":211998573` (WB API) | sí* | — |

- Corrida 1 (tras fix de schemas): **2/3**, $0.0732 — T9 falló por
  replies JSON malformadas (preámbulo conversacional), causa del retry.
- Corrida 2 (con retry): **3/3**, $0.0732. Corrida 3 (corroboración +
  `EvidencePackage.from_dict` por paquete): **3/3**, $0.0640, todos
  M2a-OK con offsets exactos y valores idénticos a truth.json WB.
- Gasto live total de la tarea (incl. probes de depuración): **≈ $0.25**,
  muy por debajo del tope $6.

\* T9 en corrida de corroboración agotó el presupuesto de 12k tokens en
la ronda redteam (un retry + prompts largos): el tope funcionó como
diseñado — se saltó solo la segunda ronda y el claim igual salió con
su paquete primario, `degraded=false`.

## Unresolved / supuestos de contrato

1. `numbers[].unit`: solo `USD`/`percent`/null (marcadores obvios);
   M2a no define vocabulario de unidades — si espera otro, ajustar
   `numbers_from_quote`.
2. `sha256` hashea **bytes crudos** del snapshot; M2a verifica
   `sha256_of_text` (utf-8 del texto). Coinciden cuando la fuente es
   UTF-8 (todas las live: WB API, Wikipedia); divergen en charsets
   legacy — pendiente acordar bytes-vs-texto con M2a.
3. `source_type` binario M2a (primary/secondary): primario = hosts
   `api/data.worldbank.org`, `fred`, `owid`, `wikidata/query`;
   resto secundario (incl. Wikipedia). Criterio documentado, no validado
   con M2a.
4. `source_year` = año del plan (claim), no vintage de la fuente; el
   `lastupdated` WB queda dentro del snapshot JSON por si M2a lo usa.
5. DuckDuckGo HTML aportó 0 hits en vivo (probable anti-bot, HTTP 202);
   el grounding web recayó en Wikipedia API. Sin impacto en claims WB
   (ruta primaria API), pero claims no estadísticos dependen hoy de
   Wikipedia + fetch directo.
6. `claim.evidence` es evento nuevo (M2b): M2a/verifier debe suscribirse;
   no tocado porque está fuera de la lista de archivos.
