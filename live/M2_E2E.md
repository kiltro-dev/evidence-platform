# M2c — Cierre M2 E2E Atlas Live (rama live)

E2E real sobre los **31 verificables EN** (`true` 16 + `false` 15):
`coordinator → claim.evidence → EvidencePackage → store → verifier →
claim.verdict`, con matriz vs truth, costo medido y latencia por claim.

Status: **cerrado**. Falsos contradicted sobre TRUE: **0**. Live total
**$0.70** (tope $4; corrida principal $0.59).

## Pipeline

```
truth.json --lang en  →  ClaimInput (parse determinista, parse_quantities)
  → swarm run_claim × N (semáforo 4, deadline 90 s/claim, 1 fork/claim)
  → claim.evidence  →  [suscripción M2c] EvidencePackage.from_dict
  → EvidenceStore (snapshot bytes + package)  →  verify_and_emit
  → claim.verdict  →  tracker veredicto
```

Todo evento queda en `data/runs/verify-*.jsonl`; metadatos en el
`.summary.json` hermano. Latencia/claim = `claim.verdict.bus_ts −
claim.state(investigando).bus_ts`.

## Archivos

| Archivo | Rol |
|---|---|
| `live/run_verify.py` | Runner asyncio + CLI `--claims/--lang/--limit` + suscripción `claim.evidence` + reporte |
| `live/tests/test_m2c_e2e.py` | 12 tests offline con doubles (0 live) + pins del contrato alineado |
| `live/M2_E2E.md` | Este reporte |

## Edits mínimos de alineación (4 + 1 fix incidental)

1. **Vocabulario `numbers[].unit` compartido** (`live/evidence/package.py`,
   `live/swarm/coordinator.py`): `UNIT_VOCAB` canónico (targets
   normalizados de `src/atlas/core/units.py` + `None`); `NumberMention`
   rechaza unidades fuera del vocabulario; `numbers_from_quote` emite el
   subconjunto `USD/percent/None` con guarda (sin cambio de conducta;
   el verificador re-extrae y nunca confía en `numbers[]`).
2. **sha256 unificado sobre bytes crudos** (`package.py`, `library/store.py`,
   `verifier/check.py`): `sha256_of_bytes` canónico; `store` gana
   `upsert_snapshot_bytes`/`get_snapshot_bytes` (`upsert_snapshot` delega
   vía utf-8); el verificador acepta snapshots `bytes` (hash crudo +
   búsqueda del quote en decodificaciones utf-8 y latin-1). Lado
   ajustado: M2a (antes hasheaba texto re-codificado; divergía en
   charsets legacy). Comportamiento `str` intacto.
3. **Criterio primary/secondary documentado y validado en ambos lados**
   (`package.py` canónico + `coordinator.py` delega con fallback local):
   primario = host estadístico oficial (`PRIMARY_HOSTS`, exacto o
   subdominio); resto secundario. Productor asigna vía función
   compartida; la suscripción M2c audita cada paquete contra el mismo
   criterio (`source_type_mismatch` en el resumen; **0 en vivo**).
4. **Suscripción `claim.evidence → store → verifier → claim.verdict`**
   (`live/run_verify.py::make_evidence_handler`): valida cada dict a
   `EvidencePackage` (rechazos contados, nunca rompen la corrida),
   persiste snapshot bytes + paquete, `verify_and_emit`, `veredicto`.
   Exactamente un `claim.verdict` por `claim.evidence`, incluso con cero
   paquetes (→ `unverifiable/no_usable_package`). Resuelve M2B-6.
5. Fix incidental: `run_verify.amain` carga `.env` (como `MetaClient`)
   antes de exigir `MODEL_API_KEY`.

## Tests

```bash
cd evidence-platform
.venv/bin/python -m pytest live/tests/test_m2c_e2e.py -q        # 12 passed, 0 live
.venv/bin/python -m pytest live/tests/ -q -m "not live"        # 114 passed
```

Doubles `FakeLLM/FakeSearch/FakeFetch`; `test_zero_live_guard`
parchea `urlopen` para probar 0 red. Pins: vocab (incl. rechazo de
`lightyears`), bytes crudos con fixture latin-1 real (`café`: los hash
texto/bytes **divergen** y el verificador acepta el crudo), criterio
primario en ambos lados (incl. anti-spoof `worldbank.org.evil`), cadena
completa supported/contradicted/unverifiable, y el contrato real
“quote JSON WB sin unidades → `unit_mismatch`” (ver Diagnóstico).

## Corrida LIVE principal (31 EN, 2026-11-01)

```bash
.venv/bin/python live/run_verify.py --lang en
# log: data/runs/verify-20261101-210921.jsonl (+ .db, + .summary.json)
```

| expected \ verdict | supported | contradicted | disputed | unverifiable |
|---|---|---|---|---|
| true (16) | 1 (T3) | **0** | 0 | 15 |
| false (15) | 0 | 1 (F6) | 0 | 14 |

- **Falsos contradicted sobre TRUE: 0** (T1,T2,T3,T4,T5,T6,T7,T8,T9,T10,
  T11,T12,T13,T14,T15,T16: ninguno contradicted). Falsos supported
  sobre FALSE: 0.
- Costo medido: **$0.5911** (220k prompt + 74k completion, 144 llamadas).
- Latencia/claim (invest→verdict): **p50 26.5 s / p95 55.2 s**; wall 275 s.
- Integridad de cadena: `invalid_packages` 0, `source_type_mismatch` 0,
  `snapshots_unresolved` 0, cero `quote_not_found`/`snapshot_hash_mismatch`.
- Degradados (4): T6, F4, F7, F10 (`budget exhausted at recuperador`).

Paquetes que decidieron (únicos veredictos no-`unverifiable`):

- **T3 supported**: secundaria Wikipedia
  `"...$29.298 trillion in 2024"` vs claim 29.3T → `numeric_match`.
- **F6 contradicted**: secundaria Wikipedia
  `"dropped to 0.9% in 2023..."` vs claim 40% → `numeric_mismatch`.

## Diagnóstico (recall, no maquillado)

27/31 `unverifiable`, por tres causas cuantificadas + una conducta
correcta:

1. **Gap de unidades en primarias estructuradas (causa principal).**
   Las quotes JSON del WB (`"date":"2024","value":8140897523`) no
   llevan marcador de unidad → el verificador (re-extracción estricta)
   las marca `unit_mismatch`. Los valores recuperados son **exactos**
   vs truth (T1 8140897523, T2 1408975000, T9 211998573…), pero
   indecidibles; T3/F6 solo se decidieron por secundarias en prosa con
   unidades. 8 claims con paquetes cayeron aquí. Fix propuesto (dueño
   M2a/M2b): mapa indicador→unidad para fuentes estructuradas
   (`SP.POP.TOTL→count`, `NY.GDP.MKTP.CD→USD`); **no** aplicado aquí
   por tocar semántica de veredicto.
2. **Preámbulo conversacional ante JSON válido (5 claims en cero:
   F9, F11, F12, T13, F15).** El modelo responde
   `"I'll search for ...{...json válido...}"` y `json.loads` falla;
   el retry M2b no basta (repite preámbulo) → `recuperador failed`.
   Evidencia: `DOC-REPR` capturado en probe directo. Fix propuesto
   (dueño M2b): rescate determinista del primer objeto `{...}`
   balanceado antes de parsear.
3. **Unidades de claim fuera del parser** (bloqueo independiente para
   física/geo: `meters`, `kilometers`, `degrees Celsius`,
   `square kilometers` no están en `units.py` → `claim_unit_unknown`;
   y sin-número F5/F9/F11/F12/F15 → `unverifiable` por diseño).
4. **Vacíos honestos en FALSE** (F1, F2, F13, F14, …): el modelo
   devolvió `[]` antes que inventar — conducta correcta; `unverifiable`
   (contradecir exige contra-evidencia positiva). Caso límite F3:
   quote truncada `"stood at 1.404"` (cifra cortada) → inusable.

Smoke previo (3 claims, $0.065) + probes de diagnóstico (~$0.045):
gasto live total de la tarea **≈ $0.70**.

## Unresolved

- M2B-1 (vocab `unit`): **resuelto** por edit 1.
- M2B-2 (sha bytes-vs-texto): **resuelto** por edit 2 (bytes crudos).
- M2B-3 (primary/secondary): **resuelto** por edit 3 (0 mismatch en vivo).
- M2B-4 (`source_year` = año del claim, no vintage): **abierto** — el
  check temporal del verificador queda neutralizado; heredado, sin
  impacto en esta corrida (ningún veredicto dependió de él).
- M2B-5 (DDG 0 hits, anti-bot): **abierto** — grounding web recayó en
  Wikipedia API de nuevo; sin impacto WB, riesgo en no-estadísticos.
- M2B-6 (suscripción `claim.evidence`): **resuelto** por edit 4.
- **Nuevo N1**: rescate de JSON con preámbulo (causa 2).
- **Nuevo N2**: unidades para fuentes estructuradas + física/geo
  (causas 1 y 3).
- **Nuevo N3**: presupuesto 12k tokens/claim se agota en ~13% de
  claims (4 degradados); el tope funciona, pero corta redteam.
