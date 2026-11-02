# S3 — directo-simulado HLS + UI Atlas Live

HLS loop infinito desde 1 pieza sintética (`data/synth/en/k1_econ.mp4`,
ver `live/SYNTH_EN.md`) + servicio HTTP + badges UI. Offline, $0 (no
usa ASR en vivo; la ingesta HLS→ASR no se probó contra live API).

## Piezas

| Archivo | Qué hace |
|---|---|
| `live/simulive/__init__.py` | Re-exports del paquete |
| `live/simulive/hls.py` | `build_loop_cmd` / `start_loop` (ffmpeg `-re -stream_loop -1` → `data/synth/hls/`), `ensure_metadata`, `parse_playlist`, `generate_short_hls`, `write_live_window` |
| `live/simulive/serve.py` | `mount_simulive(app)`: `GET /hls/*` + `GET /hls-status` |
| `live/tests/test_s3_simulive.py` | 13 tests offline |
| `live/server/app.py` | EDITS MÍNIMOS: import + `mount_simulive(app)` + 2 líneas docstring (rutas aditivas; contrato M1b/M4 intacto) |
| `live/ui/index.html` | EDITS MÍNIMOS: badge `#simuliveBadge`, badge esquina `#corpusBadge`, selector `#srcSel`, flags `?source=` / `?corpus=` |

## Loop HLS

```sh
.venv/bin/python -c "
from live.simulive.hls import start_loop
import time
p = start_loop()   # k1_econ.mp4 -> data/synth/hls/ (hls_time 4, list_size 10)
time.sleep(3600)"
```

Comando generado (`build_loop_cmd`): `ffmpeg -re -stream_loop -1 -i
…/k1_econ.mp4 -c:v libx264 -preset veryfast -c:a aac
-force_key_frames expr:gte(t,n_forced*4) -f hls -hls_time 4
-hls_list_size 10 -hls_flags delete_segments+append_list …/stream.m3u8`.
El `-force_key_frames` es necesario: HLS solo corta en keyframes y el
GOP por defecto del encoder (250) supera `hls_time`.

Manifiesto: `X-ATLAS-SIMULIVE:1` + comentario `# ATLAS simulive loop`,
inyectados por `ensure_metadata` **al servir** (`serve.py`), así la
ventana móvil que ffmpeg reescribe siempre los lleva sin tocar el loop.

Estado: `GET /hls-status` → `{live, seq, segments, uptime_s, playlist}`.

## UI

- Selector de fuente (`#srcSel`): Archivo / Directo simulado
  (ES) · File / Simulated live (EN). Con `?source=hls` el `<video>`
  apunta a `/hls/stream.m3u8` (HLS nativo Safari; otros navegadores
  necesitan hls.js, no vendorizado — ver unresolved).
- Badge `● DIRECTO SIMULADO` / `● SIMULATED LIVE` (`#simuliveBadge`)
  visible solo cuando `source=hls`.
- Badge permanente en esquina `Synthetic demo · Simulación sintética`
  (`#corpusBadge`) cuando el contenido es corpus: flag por run
  `?corpus=1`, o implícito con `source=hls` (el loop es siempre corpus).

## run_live WIRING (sin editar run_live.py)

`run_live.py` no se tocó. Alimentarlo desde HLS es idéntico al path
archivo: el demux PCM (`FifoStream.demux_argv_for`) acepta cualquier
input ffmpeg — un playlist HLS local o la URL servida — en lugar del
fifo:

```python
from live.run_live import FifoStream  # NO editar este archivo

# Opción A: playlist local del loop (el loop S3 ya corre en data/synth/hls/).
argv = FifoStream.demux_argv_for("data/synth/hls/stream.m3u8")

# Opción B: URL servida por la app (server corriendo + loop corriendo).
argv = FifoStream.demux_argv_for("http://127.0.0.1:8000/hls/stream.m3u8")

# argv = [ffmpeg, -hide_banner, -nostats, -i <hls>, -map 0:a?,
#        -ac 1, -ar 24000, -f s16le -]  -> PCM mono 24k por stdout,
# idéntico al path archivo; el resto del pipeline (chunks/ASR) no cambia.
```

Nota: HLS mete latencia de ~1-2 segmentos (4-8 s con `hls_time 4`);
para demos de latencia fina seguir usando el path archivo/fifo.

## Tests

```sh
.venv/bin/python -m pytest live/tests/test_s3_simulive.py -q   # 13 passed, $0
.venv/bin/python -m pytest live/tests/test_server.py live/tests/test_m4_ui.py -q  # 28 passed (sin regresión M1b/M4)
```

Cobertura: manifiesto (metadato CUSTOM), `EXT-X-MEDIA-SEQUENCE` móvil,
`EXT-X-DISCONTINUITY` si recorte (simulado vía `write_live_window`),
mid-join (cliente tardío resuelve ventana válida), pausa (freeze =
mismo seq), rutas `/hls/*` + `/hls-status` (incl. rechazo de traversal
y 404s), badges/selector en HTML. Verificado además contra loop ffmpeg
real (k1_econ, 2 s/seg): seq 0→2, ventana deslizante ≤3, segmentos en
disco.

## Costo

**$0.** Todo offline (ffmpeg estático imageio). No se probó ingesta
HLS→ASR en vivo (habría sido ≤$1 en 1 ventana corta); el wiring queda
documentado arriba.

## Unresolved

- `EXT-X-DISCONTINUITY` del loop real (wrap en `-stream_loop`) no
  observado en vivo — el wrap de k1_econ tarda 267 s; cubierto por test
  simulado. Si el re-encode continuo no emite el tag al wrapear, el
  player lo tolera (mismo codec/parámetros).
- Reproducción HLS en navegadores sin soporte nativo (Chrome/Firefox)
  requiere hls.js vendorizado; ahora solo funciona out-of-the-box en
  Safari. La ingesta HLS→ASR (ffmpeg) no se ve afectada.
- Latencia HLS (~1-2 segmentos) documentada arriba; sin medición live.
