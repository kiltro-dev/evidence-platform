#!/usr/bin/env bash
#
# Evidence Atlas smoke test.
#
# Offline by default (phases 0-4 + teardown); opt-in live API checks
# (--live) and full eval (--eval). Exits 0 only if every executed check
# passes; skipped phases do not fail the run.
#
# Usage:
#   bash scripts/smoke.sh [--port 8765] [--live] [--eval]
#
# Design notes / deviations from the C2 brief:
# - Never creates virtualenvs: phase 1 asserts .venv exists and uses it.
# - The test server boots with MODEL_API_KEY="" so degraded
#   (deterministic) mode is forced even when a .env key exists. This keeps
#   phases 0-4 key-independent and deterministic. load_dotenv() never
#   overrides an existing env var, so the empty value wins over .env.
# - Shape assertions (report_id, audit order incl. persist, cited ids
#   resolve, limitations, actors subset) hold regardless of server mode.
# - Error assertions are envelope-tolerant: the A1 unified envelope
#   {"error": {"code","message","request_id"}} is preferred and its code +
#   32-hex request_id are validated; the legacy {"detail": ...} shape is
#   accepted with a note. Shape is detected, then asserted accordingly.
# - Phase 6 tolerates run_eval.py versions without --mode (current tree):
#   it uses --mode degraded when supported, else --db only (degraded is
#   run_eval's only mode: llm=None) and says so.
# - Phase 6 is network-gated like phase 5: eval cases need live sources,
#   so --eval with no network skips cleanly instead of failing.
# - All temp state (uvicorn log, ATLAS_DB, eval db, checker) lives under
#   /tmp only; the repo's data/atlas.db is never touched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="8765"
WANT_LIVE="0"
WANT_EVAL="0"

usage() {
  cat <<'EOF'
Usage: bash scripts/smoke.sh [--port N] [--live] [--eval]

  --port N   preferred port for the throwaway test server (default 8765);
             if busy, the next free port is used automatically
  --live     run phase 5 live-source API checks (needs network; skipped
             cleanly when the World Bank preflight fails)
  --eval     run phase 6 full eval (needs network; skipped cleanly when
             the World Bank preflight fails)
  -h|--help  this message

Offline phases (0-4) + teardown (7) always run and need no model key.
EOF
}

while [ "$#" -gt "0" ]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#--port=}"; shift ;;
    --live) WANT_LIVE="1"; shift ;;
    --eval) WANT_EVAL="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) echo "error: --port must be a number (got '$PORT')" >&2; exit 2 ;;
esac

# ---------- result bookkeeping ----------

N_PASS="0"; N_FAIL="0"; N_SKIP="0"; OVERALL="0"; SUMMARY_DONE="0"
SERVER_PID=""; TMPDIR_SMOKE=""; PY=""; SERVER_OK="0"; VENV_OK="0"
SERVER_MODE="unknown"

pass_() { N_PASS=$((N_PASS + 1)); echo "PASS: $*"; }
fail_() { N_FAIL=$((N_FAIL + 1)); OVERALL="1"; echo "FAIL: $*"; }
skip_() { N_SKIP=$((N_SKIP + 1)); echo "SKIP: $*"; }
note_() { echo "note: $*"; }

teardown() {
  if [ -n "$SERVER_PID" ]; then
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill "$SERVER_PID" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        sleep 0.3
      done
      kill -9 "$SERVER_PID" 2>/dev/null || true
      wait "$SERVER_PID" 2>/dev/null || true
    fi
    SERVER_PID=""
  fi
  if [ -n "$TMPDIR_SMOKE" ] && [ -d "$TMPDIR_SMOKE" ]; then
    rm -rf "$TMPDIR_SMOKE" || true
  fi
  TMPDIR_SMOKE=""
}

print_summary() {
  if [ "$SUMMARY_DONE" = "1" ]; then
    return 0
  fi
  SUMMARY_DONE="1"
  echo "=== smoke summary: PASS=$N_PASS FAIL=$N_FAIL SKIP=$N_SKIP port=$PORT mode=$SERVER_MODE ==="
}

trap 'teardown; print_summary' EXIT

ensure_tmpdir() {
  if [ -z "$TMPDIR_SMOKE" ]; then
    if ! TMPDIR_SMOKE="$(mktemp -d /tmp/atlas-smoke-XXXXXX)"; then
      fail_ "cannot create tmp dir under /tmp"
      exit 1
    fi
  fi
}

port_busy() {
  (echo >"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

have_network() {
  curl -m 5 -sf -o /dev/null \
    "https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2023&per_page=1" 2>/dev/null
}

curl_code() {
  # curl_code <outfile> <curl args...>: run curl, print the HTTP code
  # ("000" when curl itself fails). Always exits 0.
  local out=$1
  shift
  local code=""
  code=$(curl -s -o "$out" -w "%{http_code}" "$@" 2>/dev/null || true)
  if [ -z "$code" ]; then code="000"; fi
  printf '%s' "$code"
}

check_error_envelope() {
  # check_error_envelope <body-file> <exp-status> <act-status> <label> <exp-new-code>
  local file=$1 exp_status=$2 act_status=$3 label=$4 exp_code=$5
  if [ "$act_status" != "$exp_status" ]; then
    local snippet=""
    snippet=$(head -c 160 "$file" 2>/dev/null || true)
    fail_ "$label: expected HTTP $exp_status, got $act_status ($snippet)"
    return 0
  fi
  local shape=""
  shape=$("$PY" - "$file" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("unparseable")
else:
    if isinstance(d, dict) and isinstance(d.get("error"), dict):
        keys = set(d["error"].keys())
        print("new" if {"code", "message", "request_id"} <= keys else "new-malformed")
    elif isinstance(d, dict) and "detail" in d:
        print("legacy")
    else:
        print("unknown")
PYEOF
)
  case "$shape" in
    new)
      local code="" rid=""
      code=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["error"]["code"])' "$file" 2>/dev/null || echo "?")
      rid=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["error"].get("request_id",""))' "$file" 2>/dev/null || echo "")
      if [ "$code" != "$exp_code" ]; then
        fail_ "$label: HTTP $act_status new envelope but code='$code' (expected '$exp_code')"
        return 0
      fi
      case "$rid" in
        *[!0-9a-fA-F]*|"")
          fail_ "$label: HTTP $act_status but request_id not hex: '$rid'"
          return 0
          ;;
      esac
      if [ "${#rid}" != "32" ]; then
        fail_ "$label: HTTP $act_status but request_id length ${#rid} != 32"
        return 0
      fi
      pass_ "$label: HTTP $act_status, new {error:{}} envelope (code=$code)"
      ;;
    legacy)
      pass_ "$label: HTTP $act_status, legacy {detail} envelope (pre-A1 tree?)"
      ;;
    *)
      local snippet=""
      snippet=$(head -c 160 "$file" 2>/dev/null || true)
      fail_ "$label: HTTP $act_status but unexpected body ($shape): $snippet"
      ;;
  esac
}

write_checker() {
  # Report-shape checker: prints "OK <msg>" / "BAD <msg>" lines.
  # Mode "shape" = offline honesty; mode "live:<stance>" adds stance + https-URL checks.
  cat >"$TMPDIR_SMOKE/check.py" <<'PYEOF'
import json
import sys


def emit(ok, msg):
    print(("OK " if ok else "BAD ") + msg)


def main():
    path, mode = sys.argv[1], sys.argv[2]
    try:
        r = json.load(open(path))
    except Exception as exc:
        emit(False, "response is not valid JSON: %s" % exc)
        return
    if not isinstance(r, dict) or not r.get("report_id"):
        emit(False, "missing report_id")
        return
    emit(True, "report_id=%s" % r["report_id"])
    if "error" in r or "request_id" in r:
        emit(False, "success body must not carry error/request_id keys")
    else:
        emit(True, "success body has no error/request_id keys")
    steps = [a.get("step") for a in r.get("audit", []) if isinstance(a, dict)]
    want = ["extract", "route", "verify", "synthesize", "persist"]
    i = 0
    for s in steps:
        if i < len(want) and s == want[i]:
            i += 1
    if i == len(want):
        emit(True, "audit includes extract..persist in order (%s)" % "+".join(steps))
    else:
        emit(False, "audit missing ordered %s (got %s)" % (want, steps))
    by_cmp = {c.get("comparison_id"): c for c in r.get("comparisons", [])
              if isinstance(c, dict)}
    ev_ids = {e.get("evidence_id") for e in r.get("evidences", [])
              if isinstance(e, dict)}
    concl = r.get("conclusion") or {}
    cited = (list(concl.get("support_comparison_ids", []))
             + list(concl.get("contradict_comparison_ids", [])))
    unknown = [c for c in cited if c not in by_cmp]
    dangling = [c for c in cited
                if c in by_cmp and by_cmp[c].get("evidence_id") is not None
                and by_cmp[c]["evidence_id"] not in ev_ids]
    if not unknown and not dangling:
        emit(True, "%d cited comparison id(s) resolve to comparisons+evidence"
             % len(cited))
    else:
        emit(False, "cited ids broken: unknown=%s dangling-evidence=%s"
             % (unknown, dangling))
    lims = r.get("limitations", [])
    if isinstance(lims, list) and lims:
        emit(True, "limitations non-empty (%d)" % len(lims))
    else:
        emit(False, "limitations empty")
    actors = set()
    cl = r.get("claim") or {}
    if cl.get("extraction_actor"):
        actors.add(cl["extraction_actor"])
    for c in r.get("comparisons", []):
        if isinstance(c, dict) and c.get("actor"):
            actors.add(c["actor"])
    if concl.get("actor"):
        actors.add(concl["actor"])
    for a in r.get("audit", []):
        if isinstance(a, dict) and a.get("actor"):
            actors.add(str(a["actor"]).split(":")[0])
    rogue = sorted(actors - {"code", "model", "heuristic"})
    if not rogue:
        emit(True, "actors subset of code/model/heuristic (%s)" % sorted(actors))
    else:
        emit(False, "actors outside allowed set: %s" % rogue)
    if mode.startswith("live:"):
        exp = mode.split(":", 1)[1]
        stance = concl.get("stance")
        if stance == exp:
            emit(True, "stance=%s (expected %s)" % (stance, exp))
        else:
            emit(False, "stance=%s (expected %s)" % (stance, exp))
        urls = [e.get("source_url") for e in r.get("evidences", [])
                if isinstance(e, dict) and e.get("source_url")]
        bad = [u for u in urls if not u.startswith("https://")]
        if urls and not bad:
            emit(True, "%d evidence URL(s), all https" % len(urls))
        elif not urls:
            emit(False, "no evidence source_url to check")
        else:
            emit(False, "non-https source_url: %s" % bad[:3])


main()
PYEOF
}

run_check() {
  # run_check <report-json> <label> <checker-mode>
  local file=$1 label=$2 mode=$3
  local line="" n="0"
  while IFS= read -r line; do
    n=$((n + 1))
    case "$line" in
      "OK "*) pass_ "$label: ${line#OK }" ;;
      "BAD "*) fail_ "$label: ${line#BAD }" ;;
      *) fail_ "$label: unexpected checker output: $line" ;;
    esac
  done < <("$PY" "$TMPDIR_SMOKE/check.py" "$file" "$mode" 2>/dev/null || true)
  if [ "$n" = "0" ]; then
    fail_ "$label: checker produced no output"
  fi
}

# ---------- phases ----------

phase0_prereqs() {
  echo "=== Phase 0: prerequisites ==="
  if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
    note_ ".venv/bin/python not found; using $PY for the version probe (phase 1 still requires .venv)"
  else
    fail_ "no python found (.venv/bin/python and python3 both missing)"
    exit 1
  fi
  local ver="unknown"
  ver=$("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo "unknown")
  if "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    pass_ "python $ver via $PY (>= 3.11)"
  else
    fail_ "python $ver via $PY is older than 3.11"
    exit 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    fail_ "curl not found (required for API checks)"
    exit 1
  fi
  pass_ "curl available"
  local want=$PORT p=$PORT
  while port_busy "$p"; do
    p=$((p + 1))
    if [ "$p" -gt $((want + 100)) ]; then
      fail_ "no free port in $want..$((want + 100))"
      exit 1
    fi
  done
  PORT=$p
  if [ "$PORT" = "$want" ]; then
    pass_ "port $PORT free"
  else
    note_ "port $want busy; resolved to free port $PORT"
    pass_ "port $PORT free (resolved from busy $want)"
  fi
}

phase1_install() {
  echo "=== Phase 1: install check (uses current .venv; never creates one) ==="
  if [ ! -x ".venv/bin/python" ]; then
    fail_ "no .venv at $ROOT/.venv - run 'uv venv && uv pip install -r requirements.lock' first (smoke never creates venvs)"
    VENV_OK="0"
    return 0
  fi
  PY=".venv/bin/python"
  local missing=""
  missing=$("$PY" -c 'import importlib.util
mods = ["fastapi", "uvicorn", "httpx", "pydantic", "openai", "dotenv", "pytest"]
print(" ".join(m for m in mods if importlib.util.find_spec(m) is None))' 2>/dev/null || echo "IMPORT-CHECK-CRASHED")
  if [ -z "$missing" ]; then
    pass_ "all 7 runtime imports resolve in .venv (fastapi uvicorn httpx pydantic openai dotenv pytest)"
    VENV_OK="1"
  else
    fail_ "missing/broken imports in .venv: $missing"
    VENV_OK="0"
  fi
}

phase2_offline_gate() {
  echo "=== Phase 2: offline test gate ==="
  if [ "$VENV_OK" != "1" ]; then
    skip_ "offline gate (no working .venv)"
    return 0
  fi
  ensure_tmpdir
  local log="$TMPDIR_SMOKE/pytest.log"
  if "$PY" -m pytest tests/unit tests/e2e -q >"$log" 2>&1; then
    local tail_line=""
    tail_line=$(tail -n 1 "$log" 2>/dev/null || echo "?")
    pass_ "pytest tests/unit tests/e2e -q green ($tail_line)"
  else
    fail_ "pytest tests/unit tests/e2e -q failed"
    tail -n 20 "$log" 2>/dev/null || true
  fi
}

phase3_boot() {
  echo "=== Phase 3: boot throwaway server ==="
  if [ "$VENV_OK" != "1" ]; then
    skip_ "server boot (no working .venv)"
    return 0
  fi
  ensure_tmpdir
  local db="$TMPDIR_SMOKE/atlas.db" slog="$TMPDIR_SMOKE/uvicorn.log"
  note_ "ATLAS_DB=$db (repo data/ untouched); MODEL_API_KEY forced empty for deterministic degraded mode"
  MODEL_API_KEY="" ATLAS_DB="$db" PYTHONPATH="src" \
    "$PY" -m uvicorn atlas.api.app:app --host 127.0.0.1 --port "$PORT" \
    >"$slog" 2>&1 &
  SERVER_PID=$!
  sleep 1
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail_ "uvicorn exited immediately; log tail:"
    tail -n 20 "$slog" 2>/dev/null || true
    SERVER_PID=""
    return 0
  fi
  local status_json="$TMPDIR_SMOKE/status.json" booted="0" i=""
  for i in $(seq 1 60); do
    local code=""
    code=$(curl -s -m 2 -o "$status_json" -w "%{http_code}" "http://127.0.0.1:${PORT}/api/status" 2>/dev/null || true)
    if [ "$code" = "200" ]; then booted="1"; break; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
    sleep 0.5
  done
  if [ "$booted" != "1" ]; then
    fail_ "/api/status did not return 200 within 30s; log tail:"
    tail -n 20 "$slog" 2>/dev/null || true
    return 0
  fi
  SERVER_OK="1"
  pass_ "/api/status 200 within 30s"
  local mode="" llm_info=""
  mode=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode",""))' "$status_json" 2>/dev/null || echo "")
  llm_info=$("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print("llm_available=%s model=%s" % (d.get("llm_available"), d.get("llm_model")))' "$status_json" 2>/dev/null || echo "unparseable")
  if [ -n "$mode" ]; then SERVER_MODE="$mode"; fi
  note_ "server mode: $mode ($llm_info)"
  local index="$TMPDIR_SMOKE/index.html" icode=""
  icode=$(curl -s -m 10 -o "$index" -w "%{http_code}" "http://127.0.0.1:${PORT}/" 2>/dev/null || true)
  if [ "$icode" = "200" ] && grep -qi "<html" "$index" 2>/dev/null; then
    pass_ "GET / 200 with HTML"
  else
    fail_ "GET / expected 200+HTML (got code=$icode)"
  fi
}

phase4_offline_analyze() {
  echo "=== Phase 4: offline analyze + shape honesty + negatives ==="
  if [ "$SERVER_OK" != "1" ]; then
    skip_ "offline analyze (server not running)"
    return 0
  fi
  ensure_tmpdir
  if [ ! -f "$TMPDIR_SMOKE/check.py" ]; then write_checker; fi
  local base="http://127.0.0.1:${PORT}"
  # Distinct texts per call: the API caches identical (text,domain,limit).
  local f1="$TMPDIR_SMOKE/r_moon.json" c1=""
  c1=$(curl_code "$f1" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "The moon is made of cheese.", "persist": true}' "$base/api/analyze")
  if [ "$c1" = "200" ]; then
    pass_ "POST /api/analyze (general claim) 200"
    run_check "$f1" "general-claim shape" "shape"
    local rid=""
    rid=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("report_id",""))' "$f1" 2>/dev/null || echo "")
    if [ -n "$rid" ]; then
      local fget="$TMPDIR_SMOKE/r_get.json" cg="" rid2=""
      cg=$(curl_code "$fget" -m 10 "$base/api/reports/$rid")
      rid2=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("report_id",""))' "$fget" 2>/dev/null || echo "")
      if [ "$cg" = "200" ] && [ -n "$rid2" ] && [ "$rid2" = "$rid" ]; then
        pass_ "persist round-trip: GET /api/reports/$rid returns same report"
      else
        fail_ "persist round-trip failed (get code=$cg, id match: $rid vs $rid2)"
      fi
    else
      fail_ "persist round-trip skipped (no report_id in analyze response)"
    fi
  else
    fail_ "POST /api/analyze (general claim) expected 200, got $c1"
  fi
  local f2="$TMPDIR_SMOKE/r_ger.json" c2=""
  c2=$(curl_code "$f2" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "The GDP of Germany was $4.52 trillion in 2023.", "persist": true}' "$base/api/analyze")
  if [ "$c2" = "200" ]; then
    pass_ "POST /api/analyze (economy claim) 200"
    run_check "$f2" "economy-claim shape" "shape"
  else
    fail_ "POST /api/analyze (economy claim) expected 200, got $c2"
  fi
  # UT-j: persist defaults to false (no user-text leak); without the flag
  # the audit must NOT contain a persist step.
  local fnp="$TMPDIR_SMOKE/r_nopersist.json" cnp=""
  cnp=$(curl_code "$fnp" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "The population of France was 68 million in 2023."}' "$base/api/analyze")
  if [ "$cnp" = "200" ]; then
    if "$PY" -c 'import json,sys; sys.exit(0 if "persist" not in [a.get("step") for a in json.load(open(sys.argv[1])).get("audit",[])] else 1)' "$fnp"; then
      pass_ "persist defaults off: no persist step without flag"
    else
      fail_ "persist defaults off: unexpected persist step without flag"
    fi
  else
    fail_ "POST /api/analyze (no-persist probe) expected 200, got $cnp"
  fi
  # Negatives (envelope-tolerant: new {error:{}} or legacy {detail}).
  local fe="$TMPDIR_SMOKE/r_empty.json" ce=""
  ce=$(curl_code "$fe" -m 30 -H "Content-Type: application/json" \
    -d '{"text": "   "}' "$base/api/analyze")
  check_error_envelope "$fe" "400" "$ce" "empty text" "empty_claim"
  local big=""
  big=$("$PY" -c 'print("x" * 4001)' 2>/dev/null || true)
  if [ "${#big}" != "4001" ]; then
    fail_ "oversize fixture generation failed (got ${#big} chars)"
  else
    local fo="$TMPDIR_SMOKE/r_oversize.json" co=""
    co=$(curl_code "$fo" -m 30 -H "Content-Type: application/json" \
      -d "{\"text\": \"$big\"}" "$base/api/analyze")
    check_error_envelope "$fo" "400" "$co" "oversize text (4001 chars)" "claim_too_long"
  fi
  local fu="$TMPDIR_SMOKE/r_unknown.json" cu=""
  cu=$(curl_code "$fu" -m 10 "$base/api/reports/rep_smoke_nope_123")
  check_error_envelope "$fu" "404" "$cu" "unknown report id" "report_not_found"
  # Limit clamp (documented behavior: clamp, don't reject).
  local fc="$TMPDIR_SMOKE/r_clamp.json" cc="" ridc=""
  cc=$(curl_code "$fc" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "Unemployment in France was 7.3% in 2023.", "limit": 999}' "$base/api/analyze")
  ridc=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("report_id",""))' "$fc" 2>/dev/null || echo "")
  if [ "$cc" = "200" ] && [ -n "$ridc" ]; then
    pass_ "limit 999 clamped, not rejected (200 + report)"
  else
    fail_ "limit 999 expected clamp-to-200 (got code=$cc, report_id='$ridc')"
  fi
  local fz="$TMPDIR_SMOKE/r_clamp0.json" cz="" ridz=""
  cz=$(curl_code "$fz" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "Inflation in Brazil was 4.6% in 2023.", "limit": 0}' "$base/api/analyze")
  ridz=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("report_id",""))' "$fz" 2>/dev/null || echo "")
  if [ "$cz" = "200" ] && [ -n "$ridz" ]; then
    pass_ "limit 0 clamped, not rejected (200 + report)"
  else
    fail_ "limit 0 expected clamp-to-200 (got code=$cz, report_id='$ridz')"
  fi
  local fl="$TMPDIR_SMOKE/r_list.json" cl=""
  cl=$(curl_code "$fl" -m 10 "$base/api/reports?limit=9999")
  if [ "$cl" = "200" ]; then
    pass_ "GET /api/reports?limit=9999 clamped (200)"
  else
    fail_ "GET /api/reports?limit=9999 expected 200, got $cl"
  fi
}

phase4b_check_batch() {
  echo "=== Phase 4b: offline batch check (/api/check) ==="
  if [ "$SERVER_OK" != "1" ]; then
    skip_ "batch check (server not running)"
    return 0
  fi
  ensure_tmpdir
  local base="http://127.0.0.1:${PORT}"
  local fb="$TMPDIR_SMOKE/r_batch.json" cb=""
  cb=$(curl_code "$fb" -m 120 -H "Content-Type: application/json" \
    -d '{"text": "The GDP of Germany was $4.52 trillion in 2023. The population of Japan was 500 million in 2020."}' "$base/api/check")
  if [ "$cb" = "200" ]; then
    local shape=""
    shape=$("$PY" -c '
import json, sys
b = json.load(open(sys.argv[1]))
ok = (b.get("claim_count") == 2
      and set(b.get("rollup", {})) == {"supported", "partially_supported", "contradicted", "unverifiable", "not_assessable"}
      and len(b.get("reports", [])) == 2
      and b.get("errors", 1) == 0
      and all(r.get("report_id", "").startswith("rep_") for r in b["reports"])
      and b.get("meta", {}).get("splitter_actor") in ("model", "heuristic"))
print("ok" if ok else "bad-shape")
' "$fb" 2>/dev/null || echo "unparseable")
    if [ "$shape" = "ok" ]; then
      pass_ "POST /api/check (2-claim batch) 200 + roll-up shape"
    else
      fail_ "POST /api/check batch shape invalid ($shape)"
    fi
  else
    fail_ "POST /api/check (2-claim batch) expected 200, got $cb"
  fi
  local fe="$TMPDIR_SMOKE/r_batch_empty.json" ce=""
  ce=$(curl_code "$fe" -m 30 -H "Content-Type: application/json" \
    -d '{"text": "   "}' "$base/api/check")
  check_error_envelope "$fe" "400" "$ce" "batch empty text" "empty_claim"
}

phase5_live() {
  echo "=== Phase 5: live-source API checks ==="
  if [ "$WANT_LIVE" != "1" ]; then
    skip_ "live checks (pass --live to enable)"
    return 0
  fi
  if [ "$SERVER_OK" != "1" ]; then
    skip_ "live checks (server not running)"
    return 0
  fi
  if ! have_network; then
    skip_ "live checks (no network: World Bank 5s preflight failed)"
    return 0
  fi
  pass_ "network preflight: World Bank reachable"
  ensure_tmpdir
  if [ ! -f "$TMPDIR_SMOKE/check.py" ]; then write_checker; fi
  local base="http://127.0.0.1:${PORT}"
  local fg="$TMPDIR_SMOKE/r_gdp.json" cg=""
  cg=$(curl_code "$fg" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "The GDP of the United States was $27.36 trillion in 2023."}' "$base/api/analyze")
  if [ "$cg" = "200" ]; then
    run_check "$fg" "live GDP claim" "live:partially_supported"
  else
    fail_ "live GDP claim: expected 200, got $cg"
  fi
  local fj="$TMPDIR_SMOKE/r_japan.json" cj=""
  cj=$(curl_code "$fj" -m 60 -H "Content-Type: application/json" \
    -d '{"text": "The population of Japan was 500 million in 2020."}' "$base/api/analyze")
  if [ "$cj" = "200" ]; then
    run_check "$fj" "live Japan claim" "live:contradicted"
  else
    fail_ "live Japan claim: expected 200, got $cj"
  fi
}

phase6_eval() {
  echo "=== Phase 6: eval (degraded) ==="
  if [ "$WANT_EVAL" != "1" ]; then
    skip_ "eval (pass --eval to enable)"
    return 0
  fi
  if [ "$VENV_OK" != "1" ]; then
    skip_ "eval (no working .venv)"
    return 0
  fi
  ensure_tmpdir
  if ! have_network; then
    skip_ "eval (no network: World Bank 5s preflight failed; eval cases need live sources)"
    return 0
  fi
  local evaldb="$TMPDIR_SMOKE/eval.db" elog="$TMPDIR_SMOKE/eval.log"
  local args=()
  if "$PY" scripts/run_eval.py --help 2>/dev/null | grep -q -- "--mode"; then
    note_ "run_eval.py supports --mode; running --mode degraded --db <tmp>"
    args=(--mode degraded --db "$evaldb")
  else
    note_ "run_eval.py on this tree has no --mode flag; running --db <tmp> only (its only mode is degraded: llm=None)"
    args=(--db "$evaldb")
  fi
  local run_cmd=("$PY" scripts/run_eval.py "${args[@]}")
  if command -v timeout >/dev/null 2>&1; then run_cmd=(timeout 300 "${run_cmd[@]}"); fi
  if "${run_cmd[@]}" >"$elog" 2>&1; then
    local summary=""
    summary=$(grep -E -m 1 "cases passed" "$elog" 2>/dev/null || echo "exit 0")
    pass_ "eval exit 0 ($summary)"
  else
    fail_ "eval exit non-zero"
    tail -n 15 "$elog" 2>/dev/null || true
  fi
}

phase7_teardown() {
  echo "=== Phase 7: teardown ==="
  local pid_before="$SERVER_PID" tmp_before="$TMPDIR_SMOKE"
  teardown
  local ok="1"
  if [ -n "$pid_before" ] && kill -0 "$pid_before" 2>/dev/null; then ok="0"; fi
  if [ -n "$tmp_before" ] && [ -d "$tmp_before" ]; then ok="0"; fi
  if [ "$ok" = "1" ]; then
    if [ -n "$pid_before" ] || [ -n "$tmp_before" ]; then
      pass_ "server stopped and temp files removed"
    else
      pass_ "nothing to tear down (server never started)"
    fi
  else
    fail_ "teardown incomplete (pid=$pid_before tmp=$tmp_before)"
  fi
  print_summary
}

phase0_prereqs
phase1_install
phase2_offline_gate
phase3_boot
phase4_offline_analyze
phase4b_check_batch
phase5_live
phase6_eval
phase7_teardown
exit "$OVERALL"
