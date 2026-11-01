# live/oido contract — muse-voice-transcribe realtime + file fallback

Source of truth: LIVE-S1 spike (`/tmp/spike-asr/REPORT.md`, `spike.py`,
`results/`). Model `muse-voice-transcribe-1.0`. Key: `MODEL_API_KEY`
from `evidence-platform/.env` — presence/length only, never printed.

## Realtime WebSocket

- URL `wss://api.meta.ai/v1/asr/realtime`. Audio: mono s16 PCM 24 kHz
  binary frames (80 ms), paced real-time.
- Handshake = first JSON text frame, fixed for the session:

  ```json
  {"mode": "DIARIZATION",
   "authorization": {"accessToken": "Bearer <key>"},
   "audioEncoding": "PCM_24KHZ", "model": "muse-voice-transcribe-1.0",
   "partialMode": "CUMULATIVE", "emitAudioProgress": false,
   "keywords": [...], "languageBias": ["Spanish"]}
  ```

  Ack: `{"sessionId": "voyager/duplex/..."}` (no `type`). Both `Bearer`
  and raw token accepted; we send `Bearer`. `languageBias` takes full
  language names, not codes. `keywords` measurably fix hard entities.

- ENDPOINTING/DIARIZATION turn sequence per `turnId` (from 0):

  | server event | fields | meaning |
  |---|---|---|
  | `speechStart` | `turnId`, `audioProcessedMs` | turn start ≈ `audioProcessedMs` |
  | `transcript` (`final:false`) | `transcript`, `audioProcessedMs` | cumulative partial, NO turnId, resets per turn |
  | `speaker` (DIARIZATION) | `label`, `audioProcessedMs` | exactly one per turn at turn end, NO turnId → attribute to open turn |
  | `speechEnd` | `turnId`, `audioProcessedMs` | turn end ≈ `audioProcessedMs` (boundary only) |
  | `speechComplete` | `turnId`, `transcript`, `audioProcessedMs` | post-processed final text (punctuation, caps, EN number normalization) |

  Then one session-final `transcript{transcript:"",final:true}` —
  EMPTY. Text lives only in `speechComplete`; never use the empty
  final as text. PUSH_TO_TALK differs (`final:true` WITH text, no
  speech events) and is handled separately in `TurnTracker.feed`.
- Diarization labels (`A`, `B`, …) are stable across turns AND languages
  within a session. One turn ≠ one claim: endpointing splits on pauses.
- Every event carries monotonic `audioProcessedMs` (incl. silence).
  Turn timing = speechStart/speechEnd values. No word timestamps, no
  confidence, no sound/emotion fields. ES keeps number-words literal
  ("tres coma uno") — the verifier must parse them; EN normalizes.
- Latency (spike, 8 sessions): end-of-speech → final 177–348 ms;
  partials ~150 ms behind real-time; first partial <1.6 s wall.
- Silence: keep sending PCM zeros when idle (25 s silence session OK);
  never stop without `{"type":"endStream"}`. After endStream + drain
  the server closes 1000 "No more transcript segments" (11/11).
- 60-min cap, no resume token (docs, untested live): reconnect
  proactively at ~55 min, rejoin turns by cumulative offset
  (`TurnTracker(offset_ms=...)`).
- Cost: $0.18/h of streamed audio, per-request floor, whole seconds
  rounded down; failures/429 not billed.

## Bus events (shared envelope `{"type","t_wall","t_media","payload"}`)

`t_wall` = wall seconds (`time.time()`); `t_media` = media seconds
(`audioProcessedMs/1000` + reconnect offset).

- `transcript.partial{turn,text}` — cumulative partial; `t_media` = its
  `audioProcessedMs`.
- `transcript.final{turn,text,speaker,t_start,t_end}` — one per
  `speechComplete` (WS) or per `turns[]` entry (file); `speaker` null
  unless DIARIZATION; `t_media` = `t_end`; text stripped.
- (ingest) `ingest.frame{t_media,path}`, `ingest.eof{}` — see
  `live/ingest/demux.py`.

## File fallback (`POST /v1/asr/transcribe?sessionId=<id>`)

- Multipart `request` (JSON: same as handshake minus `authorization`,
  `audioEncoding: "WAV"`) + `audio` (WAV mono s16 16/24 kHz).
- Caps: 32 MB body (413), 10 min audio (400), 429 with backoff.
- Response: `{sessionId, transcript, audioDurationMs,
  turns:[{turnId,startMs,endMs,transcript,speaker?}]}`. Caveat: file
  decode once differed from realtime (duplicated word); WS is primary.

## Fixture

`live/fixtures/asr_sample.jsonl`: one REAL recorded session
(DIARIZATION, 15.5 s EN+ES clip). Lines: `c2s` (redacted handshake,
bin/silence byte counts, endStream), `s2c` (raw server JSON), `net`
(lifecycle). Token never stored. Tests replay `s2c` lines offline.
