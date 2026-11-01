"""ShipCheck sidecar integration example (UT-n).

Runs a real end-to-end verification against a local server:
a true claim, a planted-hallucination claim, and a streamed batch.

    1. PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app \
           --host 127.0.0.1 --port 8765
    2. PYTHONPATH=clients/python .venv/bin/python clients/python/example.py
"""

import os
import sys

from shipcheck import ShipCheck, ShipCheckError

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "SHIPCHECK_BASE", "http://127.0.0.1:8765")
API_KEY = os.environ.get("SHIPCHECK_API_KEY")  # sweep-1: keyed deploys

TRUE_CLAIM = "The United States GDP reached $27.36 trillion in 2023."
HALLUCINATED_CLAIM = "The United States GDP reached $50 trillion in 2023."
BATCH_TEXT = (
    "The United States GDP reached $27.36 trillion in 2023. "
    "Japan's population exceeded 500 million in 2020."
)


def main() -> int:
    try:
        with ShipCheck(BASE, api_key=API_KEY) as sc:
            print("status:", sc.status().get("mode", "?"))

            r1 = sc.analyze(TRUE_CLAIM)
            print("true claim      ->", r1["conclusion"]["stance"])

            r2 = sc.analyze(HALLUCINATED_CLAIM)
            print("hallucinated    ->", r2["conclusion"]["stance"])

            print("batch rollup    ->", sc.check(BATCH_TEXT)["rollup"])

            kinds = [e["type"] for e in sc.check_stream(BATCH_TEXT)]
            print("stream events   ->", kinds)
    except ShipCheckError as exc:
        print(f"API error: {exc} (http {exc.status})", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"cannot reach {BASE}: {exc}", file=sys.stderr)
        print("start the server first (see module docstring).",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
