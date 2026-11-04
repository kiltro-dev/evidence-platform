> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# INGEST — multimodal intake (Pilar B-ingest, Evidence Atlas V3)

`src/atlas/ingest/`: PDF / image / video-frames → **anchored claims**.
Rule of the pillar: a claim survives only with a machine-checkable
provenance anchor (**page / region / t_second**) **plus** a literal span
verbatim in the claim text. Anything else is discarded by
`validate_anchors` with code `provenance_missing` — model proposes,
code disposes, extended to intake itself.

## Modules

| Module | Input | Claim anchor | Model calls |
|---|---|---|---|
| `pdf.py` | PDF bytes (text-based) | `{page, span}` | 1 `complete_json` per page (schema `pdf_page_claims`), heuristic sentence-split fallback |
| `image.py` | PNG/JPEG/GIF/WebP bytes | `{region, resolution, span}` | 1 vision call per image (schema `IMAGE_SCHEMA`); NO fallback — code cannot see, so no client = 0 claims |
| `frames.py` | `list[VideoFrame]` (decoded) | `{t_second, region, resolution, span}` | 1 vision call per frame; `timeline_json()` renders the per-timestamp roll-up (0 calls) |

- Region vocabulary is qualitative (`full/top/bottom/left/right/center`
  + corners): vision models cannot emit reliable bboxes, so free floats
  are refused by construction (schema enum + validator).
- Vision transport: `OpenAIVisionAdapter` wraps a `MetaClient` (data-URL
  `image_url` parts, `max_tokens` floored at **2000**, reasoning `low`).
  It wraps — never modifies — `models/`.
- `transcribe_audio()` raises `AudioNoGoError` unconditionally:
  **video is frames-only, SIN audio, by design.**

## Measured live behavior (2026-11-01, `muse-spark-1.3`)

Fixtures (code-generated, `eval/fixtures/`): `ingest_doc.pdf` (1 page,
702 B), `ingest_chart_a.png` + `ingest_chart_b.png` (480×320 px).

| Probe | Result | Usage (in/out/total) |
|---|---|---|
| PDF structuring (1 page) | 2 claims, 0 discarded | 227 / 523 / 750 |
| Image vision (`chart_a`) | 6 claims, 0 discarded | 517 / 1036 / 1553 |
| Frames vision (`chart_b` @ t=3.0s) | 4 claims, 0 discarded | 532 / 829 / 1361 |

Colors/positions/shapes on synthetic charts were read correctly
(blue/red/green bars, tallest-middle, purple trend + 2 dots, axes).
Second run reproduced PDF claims byte-identically.

## Measured costs

Standard Spark pricing ($1.25/in + $4.25/out per 1M):

- 1 probe run (3 calls: pdf+image+frames): **~$0.012**
- 2 probe runs (re-run for capture): **~$0.024 total**
- Demo (`timeline_json` over results): **$0** (pure code)
- Total live spend this pillar: **~$0.03 (budget ≤$3)**

Per-module marginal cost: PDF ~$0.003/page, image ~$0.005/image,
frames ~$0.005/frame. A 10-frame clip ≈ $0.05.

## Limits (honest)

1. **PDF parser is minimal stdlib** (`Tj`/`TJ` show-strings, raw or
   FlateDecode streams): covers text-based PDFs; exotic encodings,
   CMaps and compressed object streams yield partial/no text, flagged
   via `page_split_unverified` / `no extractable text` notes.
2. **Scanned PDFs need OCR — not supported.** No-extractable-text is a
   disclosed uncertainty, never a silent empty.
3. **Vision digits are unreliable** (pre-V3 spike: misread digits +
   truncation on small images). The prompt forbids guessing
   unresolvable digits; numeric claims off images still need the
   downstream verify stage, same as text.
4. **`max_tokens` floor 2000 + reasoning `low`** on vision: smaller
   budgets truncate into empty content (observed class).
5. **No container decoding in stdlib**: `extract_frames_ffmpeg`
   needs external ffmpeg (absent in this env) or callers supply
   decoded `VideoFrame`s. Frame count capped at 30/video.
6. **SIN audio**: spoken content is invisible; `timeline_json`
   stamps `"audio": "NO-GO..."` so receipts never imply coverage.
7. Heuristic PDF fallback splits sentences on `.`/`!`/`?` + space +
   capital/digit: robust to decimals (`1.8`) but not to
   abbreviations (`"Fig. 3 shows..."` may split).

## Tests

`tests/unit/test_ingest_{pdf,image,frames}.py` — 24 tests, **0 live**:
generated PDF/PNG bytes + scripted vision/LLM doubles. Run:

```bash
.venv/bin/python -m pytest tests/unit/test_ingest_pdf.py \
  tests/unit/test_ingest_image.py tests/unit/test_ingest_frames.py -q
```
