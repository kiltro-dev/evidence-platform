# PROVENANCE_ES — corpus sintético ES Atlas Live

6 piezas ES originales (guion propio, no traducción del EN): 2 keynotes,
1 panel a dos voces, 2 clases, 1 earnings de empresa ficticia. 240 claims
(40/pieza), categorías a–f de `live/eval2/dev.json`. Construido 2026-11-02
con `live/material/build_corpus_es.py` (única fuente de verdad: tablas
`PIECES`).

## Voces (TTS piper, motor MIT)

- **Voz A (todas las piezas): `es_ES-carlfm-x_low`** — dataset de voz en
  **dominio público**, entrenada **desde cero** (sin modelo base).
  Verificado en `live/material/LICENSES_VOZ.md` (2026-11-01). Hashes
  re-verificados al descargar el 2026-11-02 (coinciden):
  `d6967732…c00d125` (.onnx), `d9bdfa9f…99966` (.onnx.json).
- **Voz B (solo panel P1, rol INVITADO): VARIANTE ffmpeg de carlfm** —
  `asetrate=16000*0.85,aresample=16000` (pitch ×0,85, duración ×1,18).
  Documentada como lo que es: **variante sintética de la misma voz, NO una
  segunda persona**. Rótulos del panel: `voz A` / `voz B (variante)`.

### Segunda voz: investigación (2026-11-02, alternativas limpias)

Se buscaron voces `es_*` con dataset + cadena de entrenamiento limpios
(uso comercial permitido). Resultado: **no existe alternativa limpia**;
todo el catálogo `es_*` restante arrastra taint, confirmando
`LICENSES_VOZ.md`:

| Voz | Cadena | Motivo del descarte |
|---|---|---|
| `es_ES-mls_10246-low` | CC BY 4.0 pero afinada desde `ryan` (CC BY-NC-SA 4.0) | no-comercial en la cadena (MODEL_CARD re-leído hoy: "Finetuned from U.S. English Ryan voice") |
| `es_ES-mls_9972-low` | igual que la anterior | no-comercial en la cadena |
| `es_ES-davefx/sharvard-medium` | afinadas desde `lessac` (Blizzard 2013, solo investigación) | research-only en la cadena |
| `es_MX-claude-high` | `apache-2.0` declarado en un Space de terceros, entrenamiento opaco ("Training: See URL above", MODEL_CARD re-leído hoy) | licencia del audio origen no verificable |
| `es_AR-daniela`, `es_MX-ald-*` | cadena → `lessac` / opaca | research-only u opaca |

Decisión: panel con carlfm + variante ffmpeg documentada (arriba).

## Datos (trues verificadas 2026-11-02, $0)

- **Banco Mundial API** (`api.worldbank.org`, `lastupdated 2026-10-08`):
  6 snapshots en `data/synth/es/snap/` + `SHA256SUMS.txt` (población/GDP
  2024 de 20 países, series 2023:2024, esperanza de vida, mortalidad <5).
  Cada truth cita la URL exacta del snapshot.
- **Categoría b (reciente 2026)**: ground truth reutilizado de
  `live/eval2/dev.json` (FMI WEO oct-2026, BLS empsit sep-2026),
  verificado contra primario por el autor del bench el 2026-11-01
  (ayer); sin re-fetch (bot-wall documentado en dev.json). Redacción ES
  propia por pieza. Cuentas como lecturas-modelo: 0 (fuente primaria
  citada, verificación delegada y declarada).
- **Lecturas-modelo (trues textbook sin fuente primaria, ≤5/pieza)**:
  K2 tiene 5 (c, sonido, ebullición, Everest, ecuador); K1/P1/C1/C2
  tienen 0; E1 es interno-ficticio (0).
- **E1 (earnings)**: empresa **ficticia** "Luminara del Norte S.A."
  (nombre inventado + disclaimer "ficticia" en guion y slides);
  `expected=coherent-internal` en los 40: verificación = aritmética
  interna (16 comprobaciones en `test_synth_es.py`), no WB.

## Visual y audio (todo generado por código, $0)

- Slides matplotlib 1920×1080: rótulos, badge **"Synthetic demo" quemado**,
  presentador **abstracto dibujado por código** (círculo + ondas + rol;
  sin caras, sin personas reales, sin nombres reales).
- Charts: honestos + props engañosos deliberados y documentados
  (≥2/keynote: ejes truncados, escala rota India/EE. UU.).
- Ruido SNR (20/10/5 dB): blanco gaussiano numpy, semilla fija, CC0 por
  construcción (sin assets). Sin música, sin efectos externos, sin
  YouTube. Sin GPUs (CPU local).

## Prohibiciones verificadas

Sin ops git en este rol, sin secretos (MODEL_API_KEY solo vía env, nunca
impreso), sin YouTube, sin música, sin personas reales (roles genéricos:
presentadora, moderadora, invitado, profesora, directora financiera),
sin GPUs.
