# Shipped replay logs (real runs, committed)

`data/runs/` is git-ignored, so these two real event logs ship with the repo
and a fresh clone can replay them at $0 (served as fallback when no local
`data/runs/` copy exists):

- `run-keynote-en-listen.jsonl` — M1 live run over `keynote_en.mp4`
  (transcript + detected claims + states, 51 events).
- `verify-keynote-en-investigate.jsonl` — M2d investigation run over the
  31 verifiable EN claims (states + evidence + verdicts, 155 events).

They are separate runs with separate claim-ID schemes (`clm_*` vs
truth IDs) — replay them independently, never merged. No secrets inside
(verified: only token-usage counters match naive scans).
