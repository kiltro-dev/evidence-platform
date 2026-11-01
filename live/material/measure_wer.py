#!/usr/bin/env python3
"""Measure native-ES keynote intelligibility: WER + ASR-recall (F2-4).

Transcribes ``data/keynote_es/keynote_es_native.mp4`` via
``POST /v1/asr/transcribe`` (language bias ES + keywords mined from ALL ES
entities in ``live/material/truth.json``) and scores:

- WER of the transcription vs the native SRT (word-level Levenshtein).
- ASR-recall over planted claims: fraction of the 41 ES truth.json spans
  appearing literally (or fuzzy >= 0.9) in the transcription.

Pure helpers (normalize/wer/keywords/fuzzy/recall) are offline and covered
by ``live/tests/test_f2_voice.py``. Only ``main()`` touches the network
(one transcribe call, ~4 min audio ~ $0.02 at $0.18/h).

Usage (repo root, key from env — source .env, never printed):
  .venv/bin/python live/material/measure_wer.py [--check]
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata

MATERIAL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(MATERIAL))
sys.path.insert(0, ROOT)

from live.oido.asr_file import transcribe_file  # noqa: E402

TRUTH = os.path.join(MATERIAL, "truth.json")
MP4_DEFAULT = os.path.join(ROOT, "data", "keynote_es", "keynote_es_native.mp4")
SRT_DEFAULT = os.path.join(ROOT, "data", "keynote_es", "keynote_es_native.srt")
WAV_TMP = os.path.join(ROOT, "data", "keynote_es", "work", "asr_16k.wav")
REPORT_DEFAULT = os.path.join(ROOT, "data", "keynote_es", "wer_report.json")

USD_PER_HOUR = 0.18
FUZZY_THRESHOLD = 0.9

NUM_RE = re.compile(r"\d[\d.,]*")
CAP_WORD = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
ACRONYM_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑ]{2,}(?:\.[A-ZÁÉÍÓÚÑ]{1,})*\.?")
MULTI_CAP_RE = re.compile(r"\b(%s(?:\s+%s)+)\b" % (CAP_WORD, CAP_WORD))
MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")
UNIT_WORDS = {"millones", "billones", "metros", "kilometros", "kilómetros",
              "grados", "dólares", "dolares", "personas", "habitantes"}
ARTICLES = {"el", "la", "los", "las", "un", "una"}


# ----------------------------------------------------------------------------
# Normalization + WER (offline).
# ----------------------------------------------------------------------------
def normalize(text):
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------------------
# Spanish number words: the ES ASR path spells numbers out
# ("veintinueve coma tres", "dos mil veinticuatro" — see live/oido/CONTRACT.md),
# so reference digits are expanded to words before scoring.
# ----------------------------------------------------------------------------
_ES_0_29 = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once "
            "doce trece catorce quince dieciseis diecisiete dieciocho "
            "diecinueve veinte veintiuno veintidos veintitres veinticuatro "
            "veinticinco veintiseis veintisiete veintiocho veintinueve").split()
_ES_TENS = {30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
            70: "setenta", 80: "ochenta", 90: "noventa"}
_ES_HUNDREDS = {100: "cien", 200: "doscientos", 300: "trescientos",
                400: "cuatrocientos", 500: "quinientos", 600: "seiscientos",
                700: "setecientos", 800: "ochocientos", 900: "novecientos"}


def int_to_es(n):
    """Integer (0 <= n < 1e12) -> Spanish words, unaccented lowercase."""
    if n < 0 or n >= 10 ** 12:
        raise ValueError(f"out of range: {n}")
    if n < 30:
        return _ES_0_29[n]
    if n < 100:
        tens, rest = (n // 10) * 10, n % 10
        return _ES_TENS[tens] if not rest else f"{_ES_TENS[tens]} y {_ES_0_29[rest]}"
    if n < 1000:
        hundreds, rest = (n // 100) * 100, n % 100
        head = "ciento" if (hundreds == 100 and rest) else _ES_HUNDREDS[hundreds]
        return head if not rest else f"{head} {int_to_es(rest)}"
    if n < 10 ** 6:
        thousands, rest = n // 1000, n % 1000
        head = "mil" if thousands == 1 else f"{int_to_es(thousands)} mil"
        return head if not rest else f"{head} {int_to_es(rest)}"
    if n < 10 ** 9:
        millions, rest = n // 10 ** 6, n % 10 ** 6
        head = "un millon" if millions == 1 else f"{int_to_es(millions)} millones"
        return head if not rest else f"{head} {int_to_es(rest)}"
    billions, rest = n // 10 ** 9, n % 10 ** 9
    head = "mil millones" if billions == 1 else f"{int_to_es(billions)} mil millones"
    return head if not rest else f"{head} {int_to_es(rest)}"


def expand_numbers_es(text):
    """Replace digit tokens with Spanish words (``29,3`` -> ``veintinueve
    coma tres``). Comma/dot = decimal mark; decimals read as an integer."""
    def expand_match(m):
        raw = m.group(0).rstrip(".,")
        for sep in (",", "."):
            if sep in raw:
                int_part, frac_part = raw.split(sep, 1)
                frac_part = frac_part.strip()
                if frac_part.isdigit():
                    return (f"{int_to_es(int(int_part or 0))} coma "
                            f"{int_to_es(int(frac_part))}")
                break
        return int_to_es(int(raw))
    return NUM_RE.sub(expand_match, text)


def score_text(text):
    """Normalized text for scoring: numbers expanded, then normalized."""
    return normalize(expand_numbers_es(text))


def wer_details(ref_text, hyp_text):
    """Word-level Levenshtein; returns dict(wer, sub, ins, del, n_ref).

    Both sides go through score_text (digits expanded to Spanish words),
    because the ES ASR path spells numbers out."""
    ref = score_text(ref_text).split()
    hyp = score_text(hyp_text).split()
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    dist = prev[m]
    # Backtrack for sub/ins/del counts.
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + cost)
    sub = ins = dele = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (
                0 if ref[i - 1] == hyp[j - 1] else 1):
            if ref[i - 1] != hyp[j - 1]:
                sub += 1
            i -= 1
            j -= 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return {"wer": (dist / n) if n else (0.0 if not m else 1.0),
            "sub": sub, "ins": ins, "del": dele, "n_ref": n, "n_hyp": m}


# ----------------------------------------------------------------------------
# SRT (offline).
# ----------------------------------------------------------------------------
SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)\s*\n(.*?)(?=\n\n|\Z)",
    re.S)


def parse_srt(path):
    """[(t0_s, t1_s, text)] from an .srt file."""
    def to_s(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    with open(path, encoding="utf-8") as f:
        body = f.read().strip() + "\n"
    out = []
    for _idx, t0, t1, tx in SRT_BLOCK_RE.findall(body):
        out.append((to_s(t0), to_s(t1), " ".join(tx.split())))
    return out


def srt_plain_text(path):
    return " ".join(tx for _t0, _t1, tx in parse_srt(path))


# ----------------------------------------------------------------------------
# Keywords from ALL ES entities in truth.json (offline).
# ----------------------------------------------------------------------------
def extract_keywords(es_texts):
    """Ordered, deduped ASR keywords: numbers + quantity phrases + entities.

    - Every numeric token (``8140``, ``29,3``, ``2024`` ...).
    - Quantity phrases (``29,3 billones``, ``40 por ciento``).
    - Multiword capitalized entities (``Estados Unidos``, ``Banco Mundial``).
    - Single capitalized words NOT in sentence-initial position
      (``China``, ``Everest``, ``Sídney`` ...) + acronyms (``PIB``).
    - Month names present (``noviembre``).
    """
    kws = []

    def add(k):
        k = k.strip(" .,;:")
        if k and k not in kws:
            kws.append(k)

    for text in es_texts:
        for m in NUM_RE.finditer(text):
            num = m.group(0).rstrip(".,")
            add(num)
            rest = text[m.end():m.end() + 24]
            words = re.findall(r"[A-Za-záéíóúñÁÉÍÓÚÑ]+", rest)
            if words:
                if words[0].lower() == "por" and len(words) > 1:
                    add(f"{num} {words[0]} {words[1]}")
                elif words[0].lower() in UNIT_WORDS:
                    add(f"{num} {words[0]}")
        multi_parts = set()
        for m in MULTI_CAP_RE.finditer(text):
            phrase = m.group(1)
            words = phrase.split()
            while len(words) > 1 and words[0].lower() in ARTICLES:
                words.pop(0)
            phrase = " ".join(words)
            add(phrase)
            multi_parts.update(words)
        for m in ACRONYM_RE.finditer(text):
            if len(m.group(0).rstrip(".")) >= 2:
                add(m.group(0))
        # Single capitalized words, skipping sentence-initial tokens and
        # words already covered by a multiword entity.
        for sent in re.split(r"[.!?;]\s*", text):
            toks = re.findall(r"[A-Za-záéíóúñÁÉÍÓÚÑ0-9.,]+", sent)
            for tok in toks[1:]:
                tok = tok.strip(".,;:()\"'")
                if re.fullmatch(CAP_WORD, tok) and tok not in multi_parts:
                    add(tok)
        low = f" {text.lower()} "
        for mon in MONTHS:
            if f" {mon} " in low:
                add(mon)
    return kws


def load_truth_es(path=TRUTH):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return [r for r in rows if r.get("lang") == "es"]


# ----------------------------------------------------------------------------
# ASR-recall over planted claims (offline).
# ----------------------------------------------------------------------------
def span_found(hyp_norm, hyp_tokens, span_norm, threshold=FUZZY_THRESHOLD):
    """Literal substring, else token-window fuzzy ratio >= threshold."""
    if not span_norm:
        return False
    if span_norm in hyp_norm:
        return True
    span_tokens = span_norm.split()
    if len(span_tokens) < 3 or len(hyp_tokens) < len(span_tokens):
        return False
    n = len(span_tokens)
    span_set = set(span_tokens)
    best = 0.0
    for size in (n - 1, n, n + 1):
        if size < 3 or size > len(hyp_tokens):
            continue
        for i in range(len(hyp_tokens) - size + 1):
            window = hyp_tokens[i:i + size]
            if len(span_set & set(window)) < 0.5 * len(span_set):
                continue  # cheap prefilter
            r = difflib.SequenceMatcher(None, span_tokens, window).ratio()
            if r >= threshold:
                return True
            best = max(best, r)
    return best >= threshold


def asr_recall(es_texts, transcript, threshold=FUZZY_THRESHOLD):
    """(recall, hits, misses) over truth spans in a transcription.

    Scoring is on score_text (digits expanded to Spanish words)."""
    hyp_norm = score_text(transcript)
    hyp_tokens = hyp_norm.split()
    hits, misses = [], []
    for text in es_texts:
        (hits if span_found(hyp_norm, hyp_tokens, score_text(text),
                            threshold) else misses).append(text)
    recall = len(hits) / len(es_texts) if es_texts else 0.0
    return {"recall": recall, "hits": hits, "misses": misses,
            "n": len(es_texts)}


# ----------------------------------------------------------------------------
# Live measurement.
# ----------------------------------------------------------------------------
def extract_wav_16k(mp4_path, wav_path):
    from imageio_ffmpeg import get_ffmpeg_exe
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    cmd = [get_ffmpeg_exe(), "-y", "-v", "error", "-i", mp4_path,
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {r.stderr.decode()[-400:]}")
    return wav_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="WER + ASR-recall for native-ES keynote")
    ap.add_argument("--mp4", default=MP4_DEFAULT)
    ap.add_argument("--srt", default=SRT_DEFAULT)
    ap.add_argument("--truth", default=TRUTH)
    ap.add_argument("--out", default=REPORT_DEFAULT)
    ap.add_argument("--transcript", default=None,
                    help="score a saved transcript instead of calling the API ($0)")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when recall < 0.9")
    args = ap.parse_args(argv)

    rows = load_truth_es(args.truth)
    es_texts = [r["text"] for r in rows]
    keywords = extract_keywords(es_texts)
    print(f"truth ES spans: {len(es_texts)}, keywords: {len(keywords)}")

    transcript_path = os.path.join(ROOT, "data", "keynote_es", "work",
                                   "transcript_es.txt")
    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            transcript = f.read().strip()
        audio_ms, usd = 0, 0.0
        if os.path.exists(WAV_TMP):
            import wave
            with wave.open(WAV_TMP, "rb") as w:
                audio_ms = int(w.getnframes() / w.getframerate() * 1000)
        print(f"offline scoring from {args.transcript}")
    else:
        api_key = os.environ.get("MODEL_API_KEY", "")
        if not api_key:
            raise SystemExit("MODEL_API_KEY not configured; source .env first.")
        wav = extract_wav_16k(args.mp4, WAV_TMP)
        print(f"wav: {wav} ({os.path.getsize(wav)} bytes)")
        resp = transcribe_file(wav, api_key, language_bias=("Spanish",),
                               keywords=tuple(keywords))
        transcript = (resp.get("transcript") or "").strip()
        if not transcript:
            turns = resp.get("turns") or []
            transcript = " ".join((t.get("transcript") or "")
                                  for t in turns).strip()
        audio_ms = resp.get("audioDurationMs", 0) or 0
        usd = (audio_ms / 3600000.0) * USD_PER_HOUR
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(transcript + "\n")
    print(f"transcript chars: {len(transcript)}, audio_ms: {audio_ms}")
    print(f"transcript: {transcript_path if not args.transcript else args.transcript}")

    ref = srt_plain_text(args.srt)
    w = wer_details(ref, transcript)
    rec = asr_recall(es_texts, transcript)
    print(f"WER: {w['wer']:.4f} (sub={w['sub']} ins={w['ins']} "
          f"del={w['del']} n_ref={w['n_ref']})")
    print(f"ASR-recall: {rec['recall']:.4f} ({len(rec['hits'])}/{rec['n']})")
    for miss in rec["misses"]:
        print(f"  MISS: {miss[:110]}")
    print(f"cost: ${usd:.4f}")

    report = {
        "mp4": args.mp4, "srt": args.srt, "n_keywords": len(keywords),
        "scoring": "score_text: digits expanded to ES words + lowercase/unaccented",
        "wer": w["wer"], "sub": w["sub"], "ins": w["ins"], "del": w["del"],
        "n_ref": w["n_ref"], "n_hyp": w["n_hyp"],
        "recall": rec["recall"], "n_claims": rec["n"],
        "misses": rec["misses"],
        "audio_ms": audio_ms, "usd": round(usd, 4),
        "transcript_chars": len(transcript),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"report: {args.out}")
    if args.check and rec["recall"] < 0.9:
        raise SystemExit(f"recall {rec['recall']:.3f} < 0.9")
    return 0


if __name__ == "__main__":
    sys.exit(main())
