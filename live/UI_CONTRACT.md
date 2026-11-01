# Atlas Live — UI + event contract (M1b)

Wire contract between detector → orchestrator → server → UI. M1c (E2E
player) and M2 (researchers/verifier) build against this file.

## Event flow

```
transcript.final{turn,text,speaker,t_start,t_end}
  → claim.detected{claim_id,text,resolved,t_start,t_end}
    → claim.state{claim_id,state,prev,actor,note}
```

- `transcript.final`: one finalized ASR turn (M1a owns production; M1b
  `POST /run` replays prerecorded turns through the same bus).
- `claim.detected`: detector output. `text` is a verbatim span of the
  turn; `resolved` is the coreference-resolved form or `null` (never
  guessed — see `live/detector/claims.py`). `claim_id` is
  `clm_<sha1(normalized span)[:12]>`, stable across replays.
- `claim.state`: `state` ∈ `escuchada | investigando | veredicto`.
  M1 emits `escuchada` only; `investigando`/`veredicto` are set by M2
  through `ClaimTracker.set_state` (+ `register_hook` side effects).

All bus events carry the envelope `{seq, bus_ts}` (`bus_ts` = server
wall-clock seconds). Every event is JSON-serializable and mirrored to
all `/ws` clients verbatim.

## HTTP + WS surface (`live/server/app.py`)

| Endpoint      | Shape |
|---------------|-------|
| `GET /`       | newsroom page (`live/ui/index.html`) |
| `GET /health` | `{"status":"ok","milestone":"m1b"}` |
| `GET /metrics`| `{"events":N,"latency_ms":{count,p50_ms,p95_ms}}` |
| `WS /ws`      | first frame `{"type":"hello",...}`, then every bus event as JSON; client→server frames ignored |
| `POST /run`   | `{"transcript":[{turn,text,speaker,t_start,t_end}]}` or `{"transcript_path":"….jsonl"}`; ≤500 turns; returns `{"turns","claims","latency_ms"}` |

## UI behavior (`live/ui/index.html`)

- Left: `<video id="player">` (src wired by M1c) + live subtitle lines
  from `transcript.final` (speaker + `t_start–t_end` + text, last 6 kept).
- Right: one card per `claim.detected` (verbatim text, `resolved` when
  present, `claim_id`, timestamps); badge follows `claim.state`.
- ES/EN toggle switches all copy via `data-es`/`data-en`; single-column
  layout under 860px.
- Demo button `POST`s 4 recorded turns to `/run` and shows claim count
  + p50/p95 in the footer.

## Latency metric

- **Pipeline latency** (measured by the server): `transcript.final`
  publish → `claim.detected` publish per claim, ms of wall clock;
  exposed as p50/p95 in `/run` responses and `/metrics`.
- **Media latency** (M1c E2E): sentence end (`t_end` media clock) →
  card painted in the browser. Measured by the M1c harness against
  this server's WS feed + run log; M1b provides the feed, the
  `claim_id` join key, and `bus_ts` timestamps.

## M1c handoff (E2E player)

1. Point `<video id="player">` at the M1a stream/recording.
2. Drive turns into `POST /run` (or publish `transcript.final` on the
   bus directly) with true media-clock `t_start`/`t_end`.
3. Join `claim.detected.claim_id` → card paint time; report p50/p95 of
   `paint_time − t_end`.
4. Status 2026-11-01: no M1c material delivered yet; M1b E2E numbers
   below use M1b's own recorded fixture (heuristic path, localhost).

## M2 handoff (final states)

`ClaimTracker`: `set_state(id, "investigando" | "veredicto",
actor="m2-…")`; `register_hook(to_state, fn)` for side effects;
`allow_transition(...)` for extra states. UI badges already render all
three states; unknown future states fall back to the `escuchada` style.
