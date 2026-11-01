# F2_BENCH — benchmark justo v2 (F2-1: DEV; F2-2: retention)

Rama `live`. Atlas (pipeline completo con verificador determinista) vs
baseline model-only (1 llamada Responses + `web_search` por claim).
Preregistro: [prereg.md](live/eval2/prereg.md) (sellado ANTES de correr).

## Diseño

- DEV: 36 claims (6 por categoría a–f), 15 TRUE / 21 FALSE.
  [dev.json](live/eval2/dev.json)
- RETENTION: 18 claims (3 por categoría), 9 TRUE / 9 FALSE, SELLADO
  hasta F2-2. [retention.json](live/eval2/retention.json) (no abrir).
- Categorías: a) hechos conocidos; b) recientes 2026 en PDFs primarios
  (IMF WEO, BLS empsit — ground truth verificado por el autor);
  c) inventadas plausibles sobre entidades reales (todas FALSE, con
  fuente que las refuta); d) aritmética multi-fuente (variación/razón/
  promedio, cálculo en el JSON); e) trampas unidad/escala;
  f) claims normales para fidelidad de citas del baseline.
- Brazos × 2 corridas: `baseline` (reutiliza
  `live/ablation/baseline.py`, contexto standalone) vs `atlas`
  (reutiliza `live/run_verify.py` completo). Métricas + Wilson 95%:
  exactitud, recall, falsos, consistencia r1-r2, $/claim, p50/p95.
- Fidelidad: `live/eval2/fidelity.py` (quote literal + cifra respaldada).
- Tamper: `live/eval2/tamper.py` (1 byte ⇒ FAIL).

## Sello prereg (2026-11-01T22:50:30Z)

```
dev.json       sha256 db4ebe719721263957f40d4d08a2fdae8e32f83d883fc0084c2fef3e05bbf78a
retention.json sha256 5278ba4c493f25ab51eeb9b51c121d009cb86748b9b978d81efd78f8b1ecb42c
```

## Resultados DEV (F2-1)

Corridas: baseline 2× + atlas 2× sobre DEV (36 claims), 2026-11-01.
Reporte: `data/runs/eval2-20261101-225539-report.json`. Guarda: ninguna
(`guard_note: null`). Retention sigue SELLADO (F2-2).

### Matriz por brazo (pooled r1+r2 + Wilson 95%)

| brazo | n | decididos (recall) | exactitud | Wilson 95% | falsos-C | falsos-S |
|---|---|---|---|---|---|---|
| baseline-pooled | 72 | 72 (1.000) | 70/72 = 0.972 | [0.904, 0.992] | 2 (D3×2) | 0 |
| atlas-pooled | 72 | 19 (0.264) | 17/19 = 0.895 | [0.686, 0.971] | 2 (D5×2) | 0 |

Por corrida: baseline-run1 35/36 = 0.972 [0.858, 0.995];
baseline-run2 35/36 = 0.972 [0.858, 0.995]; atlas-run1 8/9 = 0.889
[0.565, 0.980]; atlas-run2 9/10 = 0.900 [0.596, 0.982].
Consistencia r1-vs-r2: baseline 36/36 = 1.000; atlas 33/36 = 0.917
(difieren A5, C5, F1).

Regla de decisión (prereg §6): los intervalos Wilson se solapan ⇒
**empate estadístico en exactitud**. Desempate: (a) falsos-contradicted
1 por corrida en ambos brazos ⇒ empate; (b) recall 1.000 vs 0.264 ⇒
**baseline gana el desempate por recall**. Leaders computados: exactitud,
hallucinations (21 vs 1), abstención, coste y latencia → baseline;
falsos-total → empate a 4.

### Por categoría (decididos/correctos por corrida)

| cat | baseline-r1 | baseline-r2 | atlas-r1 | atlas-r2 |
|---|---|---|---|---|
| a | 6/6 | 6/6 | 2/2 | 3/3 |
| b (2026) | 6/6 | 6/6 | 0/n-a | 0/n-a |
| c (inventadas) | 6/6 | 6/6 | 1/1 | 0/n-a |
| d (aritmética) | 5/6 (falla D3) | 5/6 (falla D3) | 0/1 (falla D5) | 0/1 (falla D5) |
| e (trampas) | 6/6 | 6/6 | 3/3 (solo anclas T) | 3/3 (solo anclas T) |
| f | 6/6 | 6/6 | 2/2 | 3/3 |

Atlas decide casi solo claims WB de una sola cifra; abstiene en los 12
b, 11/12 c, y las 6 trampas e (E4/E5/E6 sin decidir en ninguna corrida).

### Fidelidad de citas del baseline

- run1: 5/31 = 0.161 [0.071, 0.326]; run2: 4/32 = 0.125 [0.050, 0.281];
  pooled: 9/63 = 0.143 [0.077, 0.250].
- Razones unfaithful: quote_not_found 34, unit_mismatch 13,
  numeric_mismatch 7. URLs: 72/72 stated (0 browsed — forma observada
  del cliente en M8 y F2; declarado en `fidelity.py`).
- El baseline acierta veredictos (70/72) con citas mayormente infieles:
  la métrica mide exactamente esa brecha. Chequeo determinista
  (quote literal + cifra ±2% + fallback M2d `source_meta` para JSON WB).

### Tamper

Demo real PASS: `data/demo/atlas-live-bundle.zip` pristine ok=True
(31/31, motor live) → 1 byte alterado en
`snapshots/06c3ddaa…f59efce9.html` → ok=False
(`files.bad=[...: hash mismatch]`). Reporte:
`data/runs/f2-tamper-demo/demo.json`. Test automatizado:
`test_tamper_pristine_pass_flipped_byte_fail`.

### Coste y latencia

Total F2-1 live: **$4.41** en run files + ~$0.12 probes/smoke ≈ **$4.53**
(tope $12). $/claim: baseline 0.0297/0.0281; atlas 0.0327/0.0299.
Latencia p50/p95: baseline ~5s/16s; atlas ~80s/90s (deadline 90s).
Wall total: 1507s.

### Hipótesis H1–H4

- H1 (baseline gana recall): **CONFIRMADA** (1.000 vs 0.264).
- H2 (Atlas gana e, empata/gana c): **REFUTADA** — baseline 12/12 en e
  y 12/12 en c; Atlas 6/6 en anclas e pero 0 trampas cazadas, 1/12 en c.
- H3 (baseline gana d, fidelidad <100%): **CONFIRMADA** — d 10/12 vs
  0/2; fidelidad pooled 0.143 [0.077, 0.250].
- H4 (Atlas cero falsos-contradicted): **REFUTADA** — D5 (promedio
  2.05T verdadero) contradicted en ambas corridas (`numeric_mismatch`:
  el verificador de una sola cita compara contra componentes, no contra
  el promedio). Baseline también falla D3×2 (aritmética sobre cifras
  secundarias erróneas: 28.75T/3.91T vs 29.30T/3.76T).

### Unresolved / limitaciones

- Fidelidad unresolved 9/72: 429 (6), 403 (1), 404 (1), página >2MB (1).
  Snapshots usados: 0 (ninguna URL requirió fallback declarado).
- Atlas degradados ~21/36 por corrida (redondeo: deadlines 90s en b/c;
  `snapshots_unresolved: 0`, `invalid_packages: 0`).
- Cobertura de unidades por diseño: solo {USD, EUR, count, percent,
  m/s} (declarado en prereg §3); metros/km/coronas fuera de alcance.
- Nota de instrumentación (pre-resultados, desde M8+smoke): el cliente
  Responses devuelve `citations` vacías; fidelidad usa `judge_urls[0]`
  con flag `url_source: stated`.
