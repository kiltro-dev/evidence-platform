"""FastAPI service: JSON API + static single-page UI, one process."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from atlas.core.types import Report
from atlas.models.meta import MetaClient
from atlas.pipeline.run import analyze
from atlas.storage.store import get_report, list_reports

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Evidence Atlas", version="0.1.0")
_llm: MetaClient | None = None


def get_llm() -> MetaClient | None:
    global _llm
    if _llm is None:
        client = MetaClient()
        _llm = client if client.available else None
    # re-check env on each call so a newly exported key is picked up
    if _llm is None and os.environ.get("MODEL_API_KEY"):
        client = MetaClient()
        _llm = client if client.available else None
    return _llm


class AnalyzeRequest(BaseModel):
    text: str
    limit: int = 5


@app.get("/api/status")
def status() -> dict:
    llm = get_llm()
    return {
        "service": "evidence-atlas",
        "version": "0.1.0",
        "llm_available": llm is not None,
        "llm_model": llm.model if llm else None,
        "mode": "full (model + deterministic)" if llm else "degraded (deterministic only)",
        "connectors": ["worldbank", "openalex"],
    }


@app.post("/api/analyze", response_model=Report)
def analyze_claim(req: AnalyzeRequest) -> Report:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty claim text.")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Claim text too long (max 4000 chars).")
    return analyze(text, llm=get_llm(), limit=max(1, min(req.limit, 10)))


@app.get("/api/reports")
def reports(limit: int = 50) -> dict:
    return {"reports": list_reports(limit=max(1, min(limit, 200)))}


@app.get("/api/reports/{report_id}", response_model=Report)
def report_detail(report_id: str) -> Report:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
