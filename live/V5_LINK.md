# V5-G1 — Ingesta por enlace + modo Turbo

Entrada por enlace público (página o medio directo) con guardia SSRF
completa, y gobernador de ingesta Vivo/Turbo. YouTube siempre rechazado.

## Archivos

Nuevos:

- `live/ingest/resolve.py` — `resolve_link(url)` → `ResolvedMedia`.
  GET página (robots + throttle 1 s/host, 2 MB cap) → extrae medio
  (`og:video` > `<video>/<source src>` > enlaces `.m3u8/.mpd/.mp4`) →
  HEAD verifica content-type + tamaño (cap 500 MB). URLs directas
  (`.m3u8/.mpd/.mp4`) saltan el GET y van a HEAD. Errores: `LinkBlocked`
  (política, con `reason`: `scheme|youtube|ssrf|robots|paywall|too-large`)
  vs `LinkError` (red, sin-medio, HEAD inválido).
- `live/ingest/turbo.py` — gobernador: `pacing_args(mode)` (vivo=`-re`,
  turbo=vacío), `concurrency_for_mode` (1 vs cpu_count),
  `fast_paths_for_mode`, `mode_event` (`run.mode`), `restream_for_mode`
  (mismo contrato que `simstream.restream_to`).
- `live/tests/test_v5_link.py` — 51 tests, 100 % offline (doubles
  inyectables + `HTTPServer` loopback para el opener real).
- `live/V5_LINK.md` — este documento.

Edits mínimos (aditivos, sin cambiar conducta existente por defecto):

- `live/run_live.py` — flag `--mode vivo|turbo` (default `vivo`);
  `FifoStream(..., mode)` usa `turbo.restream_for_mode`; `amain` publica
  `run.mode`, acepta `--video http(s)` remoto (salta el check `isfile`)
  y suma `mode` al summary + log line.
- `live/server/app.py` — `POST /ingest {url, mode[, launch]}` →
  `{run_id, mode, media, status}` (400 validación, 403 `LinkBlocked`
  con `reason`, 400 `LinkError`); `GET /ingest/{run_id}` (started/done/
  error); `_launch_link_run`/`_run_link_pipeline` reutilizan
  `run_live.amain` en thread daemon (import lazy, sin servidor).
  `mp4/audio` se materializa con `download_media` (SSRF-guardado,
  cap 500 MB) a `data/cache/link-<id>.media` y el pipeline ingiere la
  ruta local; `hls/dash` se rechazan con error claro (ver hallazgo
  ffmpeg abajo).
- `live/ui/index.html` — tarjeta “Ingesta por enlace”: campo URL
  (`#ingestUrl`), toggle Vivo/Turbo (`#modeVivo/#modeTurbo`), estado
  (`#ingestStatus`) y errores visibles (`#ingestError`).

## SSRF-FULL

- Solo `http(s)`; resto → `LinkBlocked(scheme)`.
- `check_url_public` resuelve DNS (`socket.getaddrinfo`, o doble
  inyectable) y rechaza TODA IP privada/loopback/link-local/reservada/
  multicast/unspecified (`ipaddress`), en el primer hop y tras CADA
  redirect (máx 5, seguidos a mano con opener sin redirects).
- Timeout 15 s; throttle cortés 1 s/host; robots.txt (parser cacheado
  de `live.swarm.fetch`; Disallow explícito bloquea).
- YouTube (`youtube.*`, `youtu.be`, `youtube-nocookie`) → siempre
  `LinkBlocked(youtube)` con mensaje claro, antes de cualquier DNS/red.

## Modo Turbo

- `vivo` (default): `ffmpeg -re`, concurrencia 1 — idéntico a M1e.
- `turbo`: sin `-re` (ingesta a máxima velocidad de demux),
  concurrencia = cpu_count, fast-paths documentados en
  `fast_paths_for_mode`. Mismo demux/ASR/detector/bus.
- `run.mode {"type","mode","pacing","concurrency","source"}` se publica
  al inicio de cada run (bus + replay + summary).

## Contratos

- `POST /ingest`: `{url, mode="vivo"|"turbo", launch?=true}` →
  `{run_id, mode, media:{url,kind,content_type,size}, status}`.
  `kind ∈ {hls,dash,mp4,audio}`. `launch:false` = dry-run (resuelve sin
  pipeline; usado por tests).
- UI: `GET /` contiene `#ingestUrl #modeVivo #modeTurbo #ingestGo
  #ingestStatus #ingestError` y POSTea a `/ingest`.
- CLI: `run_live.py --video URL --mode turbo --duration 5 --no-server`.

## Hallazgo: ffmpeg estático no lee https

El binario imageio-ffmpeg 7.0.2-static hace SIGSEGV (rc=-11, stderr
vacío) con CUALQUIER entrada `https://` (reproducido hasta con
`https://example.com/`; ficheros locales OK). Por eso el pipeline de
enlace NUNCA alimenta URLs remotas a ffmpeg: `download_media`
(materialización SSRF-guardada, cap 500 MB) + ingesta local. Las
playlists `hls/dash` remotas quedan rechazadas con error claro hasta
que haya un ffmpeg con TLS funcional.

## Verificación y coste

- `pytest live/tests/test_v5_link.py`: 51 passed (offline).
- Regresión: `test_server + test_e2e_live + test_ingest + test_m4_ui`:
  42 passed.
- Turbo real: `FifoStream` sobre `keynote_en.mp4` — vivo spawnea
  `ffmpeg -re`, turbo lo spawnea SIN `-re` (cmdline de `/proc`).
- Ingesta real ($0, sin ASR): resolve + descarga de
  `https://download.samplelib.com/mp4/sample-5s.mp4` → 301 →
  `https://samplelib.com/mp4/sample-5s.mp4` (`video/mp4`, 2.8 MB) →
  `probe_streams` local `{audio: True, video: True}`.
- Pipeline ASR completo omitido: `MODEL_API_KEY` ausente en el entorno
  (el thread lo reporta como `error` visible en `GET /ingest/{id}`).
  Coste total: $0.
