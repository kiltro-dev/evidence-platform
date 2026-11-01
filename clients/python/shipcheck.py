"""ShipCheck reference Python client (UT-n).

Minimal sidecar integration: verify AI-generated text against the
ShipCheck API from any Python app. Only dependency is httpx.

    from shipcheck import ShipCheck, ShipCheckError

    sc = ShipCheck("http://127.0.0.1:8765", api_key="...")  # key optional locally
    report = sc.analyze("The US GDP reached $27.36 trillion in 2023.")
    print(report["conclusion"]["stance"])  # supported / contradicted / ...

    batch = sc.check("GDP was $27T in 2023. It grew 5%.")
    print(batch["rollup"])

    for event in sc.check_stream("long answer..."):  # live progress
        print(event["type"], event.get("stance", event.get("index", "")))

See example.py for a runnable end-to-end integration.
"""

from __future__ import annotations

import json

import httpx

__version__ = "0.1.0"


class ShipCheckError(Exception):
    """API error with the server's code/message (+ HTTP status)."""

    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.status = status


class ShipCheck:
    """Thin client for the ShipCheck sidecar API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765",
                 api_key: str | None = None,
                 timeout_s: float = 120.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers,
            timeout=timeout_s, transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ShipCheck":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- endpoints -------------------------------------------------
    def status(self) -> dict:
        """Server health + mode (no auth required)."""
        return self._get("/api/status")

    def analyze(self, text: str, limit: int = 5, domain: str = "auto",
                persist: bool = False, fresh: bool = False) -> dict:
        """Verify one claim; returns the full Report dict."""
        return self._post("/api/analyze", {
            "text": text, "limit": limit, "domain": domain,
            "persist": persist, "fresh": fresh,
        })

    def check(self, text: str, limit: int = 5, domain: str = "auto",
              persist: bool = False,
              deadline_s: float | None = None) -> dict:
        """Verify pasted text claim-by-claim; returns the batch dict
        (reports + rollup + errors + meta)."""
        body: dict = {"text": text, "limit": limit, "domain": domain,
                      "persist": persist}
        if deadline_s is not None:
            body["deadline_s"] = deadline_s
        return self._post("/api/check", body)

    def check_stream(self, text: str, limit: int = 5, domain: str = "auto",
                     persist: bool = False,
                     deadline_s: float | None = None) -> object:
        """Streaming batch: yields started/claim events, then the final
        done event (whose ["result"] is the complete batch dict)."""
        body: dict = {"text": text, "limit": limit, "domain": domain,
                      "persist": persist}
        if deadline_s is not None:
            body["deadline_s"] = deadline_s
        with self._client.stream("POST", "/api/check/stream",
                                 json=body) as resp:
            self._raise_for(resp)
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line.startswith("data: "):
                        yield json.loads(line[6:])

    def reports(self, limit: int = 50) -> dict:
        return self._get("/api/reports", params={"limit": limit})

    def report(self, report_id: str) -> dict:
        return self._get(f"/api/reports/{report_id}")

    def delete_report(self, report_id: str) -> dict:
        resp = self._client.delete(f"/api/reports/{report_id}")
        self._raise_for(resp)
        return resp.json()

    # -- internals -------------------------------------------------
    def _headers(self) -> dict:
        return dict(self._client.headers)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client.get(path, params=params)
        self._raise_for(resp)
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self._client.post(path, json=body)
        self._raise_for(resp)
        return resp.json()

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            err = resp.json().get("error", {})
            code = err.get("code", "http_error")
            message = err.get("message", resp.text[:200])
        except (ValueError, AttributeError):
            code, message = "http_error", resp.text[:200]
        raise ShipCheckError(code, message, resp.status_code)
